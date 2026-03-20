"""DB-backed priority queue for self-iteration spec backlog."""

from __future__ import annotations

import json
import random
import time
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.metaloop.models import (
    PHASE_ORDER,
    TERMINAL_PHASES,
    IterationPhase,
    IterationState,
)

log = structlog.get_logger()

# Backoff parameters for retry scheduling
_BACKOFF_BASE_DELAY = 30  # seconds
_BACKOFF_MAX_DELAY = 600  # seconds


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
                status=IterationPhase.PENDING.value,
                limit=1,
            )
            if not specs:
                return None
            entry = specs[0] if isinstance(specs[0], dict) else specs[0].__dict__
            return IterationState(
                spec_id=entry["spec_id"],
                phase=IterationPhase(entry.get("status", "pending")),
                attempt=int(entry.get("attempt", 1)),
                dag_task_id=entry.get("dag_task_id"),
                error=entry.get("error"),
            )
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
                entry = self._store.claim_next_spec("pending", "researching")
                if entry is None:
                    return None
                data = entry if isinstance(entry, dict) else entry.__dict__
                return IterationState(
                    spec_id=data["spec_id"],
                    phase=IterationPhase.RESEARCHING,
                    attempt=int(data.get("attempt", 1)),
                )
            except Exception:
                log.exception("spec_backlog_claim_error")
                return None

        # Fallback: peek and advance manually
        state = self.peek_next()
        if state is None:
            return None
        return self.advance_phase(state.spec_id, IterationPhase.RESEARCHING)

    def advance_phase(
        self,
        spec_id: str,
        new_phase: IterationPhase,
        *,
        dag_task_id: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> IterationState | None:
        """Update a spec entry to a new phase in the database.

        Enforces forward-only phase transitions (Fix 6).  Special cases:
        - IMPLEMENTING → IMPLEMENTING is allowed (self-transition to write dag_task_id).
        - Any → FAILED is always allowed.
        Uses conditional SQL UPDATE (Fix 5) for idempotent writes.
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
            current_phase = IterationPhase(current_phase_str)
        except ValueError:
            current_phase = IterationPhase.PENDING

        # Enforce forward-only transitions
        if new_phase != IterationPhase.FAILED:
            if current_phase == new_phase == IterationPhase.IMPLEMENTING:
                pass  # allow self-transition for dag_task_id write
            elif current_phase in TERMINAL_PHASES:
                log.debug(
                    "spec_backlog_advance_rejected_terminal",
                    spec_id=spec_id,
                    current=current_phase.value,
                    requested=new_phase.value,
                )
                return IterationState(
                    spec_id=spec_id,
                    phase=current_phase,
                    attempt=int(data.get("attempt", 1)),
                    dag_task_id=data.get("dag_task_id"),
                    error=data.get("error"),
                )
            else:
                try:
                    cur_idx = PHASE_ORDER.index(current_phase)
                    new_idx = PHASE_ORDER.index(new_phase)
                except ValueError:
                    cur_idx, new_idx = 0, 1
                if new_idx < cur_idx:
                    log.warning(
                        "spec_backlog_advance_rejected_backward",
                        spec_id=spec_id,
                        current=current_phase.value,
                        requested=new_phase.value,
                    )
                    return IterationState(
                        spec_id=spec_id,
                        phase=current_phase,
                        attempt=int(data.get("attempt", 1)),
                        dag_task_id=data.get("dag_task_id"),
                        error=data.get("error"),
                    )

        try:
            # Build extra kwargs for update
            extra: dict[str, Any] = {}
            if dag_task_id is not None:
                extra["dag_task_id"] = dag_task_id
            if error is not None:
                extra["error"] = error
            if metadata is not None:
                # Merge metadata with existing
                existing_meta_raw = data.get("metadata")
                existing_meta: dict = {}
                if existing_meta_raw:
                    if isinstance(existing_meta_raw, str):
                        try:
                            existing_meta = json.loads(existing_meta_raw)
                        except (json.JSONDecodeError, TypeError):
                            existing_meta = {}
                    elif isinstance(existing_meta_raw, dict):
                        existing_meta = existing_meta_raw
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
                return IterationState(
                    spec_id=spec_id,
                    phase=IterationPhase(rd.get("status", "pending")),
                    attempt=int(rd.get("attempt", 1)),
                    dag_task_id=rd.get("dag_task_id"),
                    error=rd.get("error"),
                )

            refreshed = self._store.get_spec_entry(spec_id=spec_id)
            if refreshed is None:
                return None
            rd = refreshed if isinstance(refreshed, dict) else refreshed.__dict__
            return IterationState(
                spec_id=spec_id,
                phase=new_phase,
                attempt=int(rd.get("attempt", 1)),
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
        exponential backoff (Fix 8). Otherwise marks as FAILED.
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
                    existing_meta_raw = data.get("metadata")
                    meta: dict = {}
                    if existing_meta_raw:
                        if isinstance(existing_meta_raw, str):
                            try:
                                meta = json.loads(existing_meta_raw)
                            except (json.JSONDecodeError, TypeError):
                                meta = {}
                        elif isinstance(existing_meta_raw, dict):
                            meta = dict(existing_meta_raw)
                    meta["next_retry_at"] = next_retry_at
                    updated = self._store.update_spec_status(
                        spec_id=spec_id,
                        status=IterationPhase.PENDING.value,
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
                    phase=IterationPhase.PENDING,
                    attempt=current_attempt + 1,
                    error=error,
                )
            else:
                # Final failure
                return self.advance_phase(
                    spec_id,
                    IterationPhase.FAILED,
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
            return IterationState(
                spec_id=data["spec_id"],
                phase=IterationPhase(data.get("status", "pending")),
                attempt=int(data.get("attempt", 1)),
                dag_task_id=data.get("dag_task_id"),
                error=data.get("error"),
            )
        except Exception:
            log.exception("spec_backlog_get_state_error", spec_id=spec_id)
            return None
