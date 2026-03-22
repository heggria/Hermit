"""DB-backed priority queue for self-iteration spec backlog."""

from __future__ import annotations

import json
import random
import time
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.metaloop.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_PHASES,
    IterationState,
    PipelinePhase,
)

log = structlog.get_logger()

# Backoff parameters for retry scheduling
_BACKOFF_BASE_DELAY = 30  # seconds
_BACKOFF_MAX_DELAY = 600  # seconds


def _parse_metadata(raw: Any) -> dict:
    """Parse metadata from DB entry (string or dict) into a plain dict."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _read_revision_cycle(data: dict) -> int:
    """Extract revision_cycle from entry metadata."""
    meta = _parse_metadata(data.get("metadata"))
    return int(meta.get("revision_cycle", 0))


def _build_state(data: dict) -> IterationState:
    """Build an IterationState from a DB entry dict."""
    return IterationState(
        spec_id=data["spec_id"],
        phase=PipelinePhase(data.get("status", "pending")),
        attempt=int(data.get("attempt", 1)),
        revision_cycle=_read_revision_cycle(data),
        dag_task_id=data.get("dag_task_id"),
        error=data.get("error"),
    )


class SpecBacklog:
    """Priority queue backed by the spec_backlog table in the kernel store.

    All state is persisted in the database; this class is stateless and
    safe to instantiate on every poll tick.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def _check_store(self) -> bool:
        """Return True if the store supports spec backlog operations."""
        return hasattr(self._store, "list_spec_backlog")

    def peek_next(self) -> IterationState | None:
        """Return the highest-priority pending spec without claiming it."""
        if not self._check_store():
            return None
        try:
            specs = self._store.list_spec_backlog(
                status=PipelinePhase.PENDING.value,
                limit=1,
            )
            if not specs:
                return None
            entry = specs[0] if isinstance(specs[0], dict) else specs[0].__dict__
            return _build_state(entry)
        except Exception:
            log.exception("spec_backlog_peek_error")
            return None

    def claim_next(self) -> IterationState | None:
        """Atomically claim the next pending spec for processing.

        Uses store.claim_next_spec() for atomic DB-level claim.
        Falls back to peek + advance if claim_next_spec is unavailable.
        """
        if not self._check_store():
            return None

        if hasattr(self._store, "claim_next_spec"):
            try:
                entry = self._store.claim_next_spec("pending", "planning")
                if entry is None:
                    return None
                data = entry if isinstance(entry, dict) else entry.__dict__
                return IterationState(
                    spec_id=data["spec_id"],
                    phase=PipelinePhase.PLANNING,
                    attempt=int(data.get("attempt", 1)),
                    revision_cycle=_read_revision_cycle(data),
                )
            except Exception:
                log.exception("spec_backlog_claim_error")
                return None

        # Fallback: peek and advance manually
        state = self.peek_next()
        if state is None:
            return None
        return self.advance_phase(state.spec_id, PipelinePhase.PLANNING)

    def advance_phase(
        self,
        spec_id: str,
        new_phase: PipelinePhase,
        *,
        dag_task_id: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> IterationState | None:
        """Update a spec entry to a new phase in the database.

        Uses the explicit ALLOWED_TRANSITIONS map for validation.
        Special cases:
        - IMPLEMENTING -> IMPLEMENTING is allowed (self-transition to write dag_task_id).
        - Any -> FAILED is always allowed.
        Uses conditional SQL UPDATE for idempotent writes.
        """
        if not hasattr(self._store, "update_spec_status"):
            log.warning("spec_backlog_no_update_method")
            return None

        # Determine current phase
        entry = self._store.get_spec_entry(spec_id=spec_id)
        if entry is None:
            return None
        data = entry if isinstance(entry, dict) else entry.__dict__
        current_phase_str = data.get("status", "pending")
        try:
            current_phase = PipelinePhase(current_phase_str)
        except ValueError:
            current_phase = PipelinePhase.PENDING

        # Enforce transition rules via explicit map
        if new_phase != PipelinePhase.FAILED:
            if current_phase == new_phase == PipelinePhase.IMPLEMENTING:
                pass  # allow self-transition for dag_task_id write
            elif current_phase in TERMINAL_PHASES:
                log.debug(
                    "spec_backlog_advance_rejected_terminal",
                    spec_id=spec_id,
                    current=current_phase.value,
                    requested=new_phase.value,
                )
                return _build_state(data)
            else:
                allowed = ALLOWED_TRANSITIONS.get(current_phase, frozenset())
                if new_phase not in allowed:
                    log.warning(
                        "spec_backlog_advance_rejected_invalid_transition",
                        spec_id=spec_id,
                        current=current_phase.value,
                        requested=new_phase.value,
                        allowed=[p.value for p in allowed],
                    )
                    return _build_state(data)

        try:
            # Build extra kwargs for update
            extra: dict[str, Any] = {}
            if dag_task_id is not None:
                extra["dag_task_id"] = dag_task_id
            if error is not None:
                extra["error"] = error
            if metadata is not None:
                # Merge metadata with existing
                existing_meta = _parse_metadata(data.get("metadata"))
                existing_meta.update(metadata)
                extra["metadata"] = existing_meta

            updated = self._store.update_spec_status(
                spec_id=spec_id,
                status=new_phase.value,
                expected_status=current_phase.value,
                **extra,
            )
            if not updated:
                # Conditional write failed — another writer moved the phase
                log.debug(
                    "spec_backlog_advance_noop_race",
                    spec_id=spec_id,
                    expected=current_phase.value,
                    requested=new_phase.value,
                )
                refreshed = self._store.get_spec_entry(spec_id=spec_id)
                if refreshed is None:
                    return None
                rd = refreshed if isinstance(refreshed, dict) else refreshed.__dict__
                return _build_state(rd)

            refreshed = self._store.get_spec_entry(spec_id=spec_id)
            if refreshed is None:
                return None
            rd = refreshed if isinstance(refreshed, dict) else refreshed.__dict__
            return IterationState(
                spec_id=spec_id,
                phase=new_phase,
                attempt=int(rd.get("attempt", 1)),
                revision_cycle=_read_revision_cycle(rd),
                dag_task_id=rd.get("dag_task_id") or dag_task_id,
                error=rd.get("error") or error,
            )
        except Exception:
            log.exception("spec_backlog_advance_error", spec_id=spec_id, phase=new_phase.value)
            return None

    def mark_failed(
        self,
        spec_id: str,
        error: str,
        *,
        max_retries: int = 2,
    ) -> IterationState | None:
        """Increment retry count or mark as permanently failed.

        If the current attempt is below max_retries, resets to PENDING
        with an incremented attempt counter and schedules a retry with
        exponential backoff. Otherwise marks as FAILED.
        """
        if not hasattr(self._store, "get_spec_entry"):
            return None

        try:
            entry = self._store.get_spec_entry(spec_id=spec_id)
            if entry is None:
                return None
            data = entry if isinstance(entry, dict) else entry.__dict__
            current_attempt = int(data.get("attempt", 1))

            if current_attempt < max_retries:
                # Compute backoff: min(base * 2^attempt, max) + jitter
                delay = min(
                    _BACKOFF_BASE_DELAY * (2**current_attempt),
                    _BACKOFF_MAX_DELAY,
                )
                jitter = random.uniform(0, delay * 0.1)
                next_retry_at = time.time() + delay + jitter

                # Reset to pending with incremented attempt + backoff metadata
                current_status = data.get("status", "pending")
                if hasattr(self._store, "update_spec_status"):
                    # Merge next_retry_at into existing metadata
                    meta = _parse_metadata(data.get("metadata"))
                    meta["next_retry_at"] = next_retry_at
                    updated = self._store.update_spec_status(
                        spec_id=spec_id,
                        status=PipelinePhase.PENDING.value,
                        expected_status=current_status,
                        error=error,
                        metadata=meta,
                    )
                    if not updated:
                        log.debug(
                            "spec_backlog_retry_noop_race",
                            spec_id=spec_id,
                            expected_status=current_status,
                        )
                        return self.get_state(spec_id)
                if hasattr(self._store, "increment_spec_attempt"):
                    self._store.increment_spec_attempt(spec_id=spec_id)
                log.info(
                    "spec_backlog_retry",
                    spec_id=spec_id,
                    attempt=current_attempt + 1,
                    max_retries=max_retries,
                    next_retry_at=next_retry_at,
                )
                return IterationState(
                    spec_id=spec_id,
                    phase=PipelinePhase.PENDING,
                    attempt=current_attempt + 1,
                    revision_cycle=_read_revision_cycle(data),
                    error=error,
                )
            else:
                # Final failure
                return self.advance_phase(
                    spec_id,
                    PipelinePhase.FAILED,
                    error=error,
                )
        except Exception:
            log.exception("spec_backlog_mark_failed_error", spec_id=spec_id)
            return None

    def get_state(self, spec_id: str) -> IterationState | None:
        """Load current state for a spec from the database."""
        if not hasattr(self._store, "get_spec_entry"):
            return None
        try:
            entry = self._store.get_spec_entry(spec_id=spec_id)
            if entry is None:
                return None
            data = entry if isinstance(entry, dict) else entry.__dict__
            return _build_state(data)
        except Exception:
            log.exception("spec_backlog_get_state_error", spec_id=spec_id)
            return None
