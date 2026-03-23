"""Additive event sink for the Trace-Contract-Driven Assurance System.

TraceRecorder collects TraceEnvelopes in-memory per run, providing
monotonic sequencing, filtered retrieval, windowed slicing, and export.

When a KernelStore is provided, envelopes are also persisted to the
assurance_trace_envelopes table for durable storage and cross-session
retrieval.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.verification.assurance.models import TraceEnvelope

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


def _run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


def _envelope_from_dict(d: dict[str, Any]) -> TraceEnvelope:
    """Reconstruct a TraceEnvelope from a plain dict."""
    return TraceEnvelope(
        trace_id=d["trace_id"],
        run_id=d["run_id"],
        task_id=d["task_id"],
        event_type=d["event_type"],
        event_seq=d["event_seq"],
        wallclock_at=d["wallclock_at"],
        logical_clock=d.get("logical_clock", 0),
        scenario_id=d.get("scenario_id"),
        step_id=d.get("step_id"),
        step_attempt_id=d.get("step_attempt_id"),
        phase=d.get("phase"),
        actor_id=d.get("actor_id"),
        causation_id=d.get("causation_id"),
        correlation_id=d.get("correlation_id"),
        artifact_refs=d.get("artifact_refs", []),
        approval_ref=d.get("approval_ref"),
        decision_ref=d.get("decision_ref"),
        grant_ref=d.get("grant_ref"),
        lease_ref=d.get("lease_ref"),
        receipt_ref=d.get("receipt_ref"),
        restart_epoch=d.get("restart_epoch", 0),
        payload=d.get("payload", {}),
    )


class TraceRecorder:
    """In-memory append-only trace recorder with optional durable persistence.

    Each *run* is an independent stream of :class:`TraceEnvelope` events with
    monotonically increasing ``event_seq`` values.  Multiple runs are stored
    concurrently and can be queried / exported independently.

    When a :class:`KernelStore` is provided, each recorded envelope is also
    persisted to the ``assurance_trace_envelopes`` table.  In-memory storage
    is always maintained for fast access.
    """

    def __init__(self, store: KernelStore | None = None) -> None:
        self._traces: dict[str, list[TraceEnvelope]] = defaultdict(list)
        self._seq_counters: dict[str, int] = {}
        self._logical_clocks: dict[str, int] = {}
        self._scenario_ids: dict[str, str] = {}
        self._store = store

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, scenario_id: str = "", *, run_id: str | None = None) -> str:
        """Create a new run and return its unique ``run_id``.

        Resets the event-sequence counter for the new run so that subsequent
        :meth:`record` calls start from ``event_seq=0``.

        If *run_id* is provided it is reused; otherwise a fresh one is
        generated.
        """
        run_id = run_id or _run_id()
        self._seq_counters[run_id] = 0
        self._logical_clocks[run_id] = 0
        if scenario_id:
            self._scenario_ids[run_id] = scenario_id
        log.info("assurance.run_started", run_id=run_id, scenario_id=scenario_id or None)
        return run_id

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_envelope(self, envelope: TraceEnvelope) -> TraceEnvelope:
        """Record a pre-built :class:`TraceEnvelope` directly.

        The envelope is stored as-is (no new ``trace_id`` or ``event_seq``
        is generated).  This is useful when replaying or importing traces.
        """
        run_id = envelope.run_id
        self._traces.setdefault(run_id, []).append(envelope)
        self._persist_envelope(envelope)
        return envelope

    def record(
        self,
        event_type: str,
        task_id: str,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
        step_attempt_id: str | None = None,
        phase: str | None = None,
        actor_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        artifact_refs: list[str] | None = None,
        approval_ref: str | None = None,
        decision_ref: str | None = None,
        grant_ref: str | None = None,
        lease_ref: str | None = None,
        receipt_ref: str | None = None,
        restart_epoch: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> TraceEnvelope:
        """Create and store a :class:`TraceEnvelope`.

        If *run_id* is not given, the most recently started run is used.

        Returns the newly created envelope.

        Raises ``ValueError`` if no run has been started and *run_id* is not
        provided, or if the given *run_id* is unknown.
        """
        if run_id is None:
            if not self._seq_counters:
                raise ValueError("No run has been started. Call start_run() first.")
            run_id = next(reversed(self._seq_counters))

        if run_id not in self._seq_counters:
            raise ValueError(f"Unknown run_id: {run_id}")

        event_seq = self._seq_counters[run_id]
        self._seq_counters[run_id] = event_seq + 1

        logical_clock = self._logical_clocks[run_id]
        self._logical_clocks[run_id] = logical_clock + 1

        envelope = TraceEnvelope(
            trace_id=_trace_id(),
            run_id=run_id,
            task_id=task_id,
            event_type=event_type,
            event_seq=event_seq,
            wallclock_at=time.time(),
            logical_clock=logical_clock,
            scenario_id=self._scenario_ids.get(run_id),
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            phase=phase,
            actor_id=actor_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            artifact_refs=artifact_refs or [],
            approval_ref=approval_ref,
            decision_ref=decision_ref,
            grant_ref=grant_ref,
            lease_ref=lease_ref,
            receipt_ref=receipt_ref,
            restart_epoch=restart_epoch,
            payload=payload or {},
        )

        self._traces[run_id].append(envelope)

        # Persist to store if available
        if self._store is not None:
            self._persist_envelope(envelope)

        log.debug(
            "assurance.event_recorded",
            run_id=run_id,
            event_type=event_type,
            event_seq=event_seq,
            task_id=task_id,
        )
        return envelope

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_envelope(self, envelope: TraceEnvelope) -> None:
        """Persist a single envelope to the store."""
        if self._store is None:
            return
        envelope_json = json.dumps(asdict(envelope), default=str, sort_keys=True)
        self._store.create_trace_envelope(
            trace_id=envelope.trace_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            event_seq=envelope.event_seq,
            event_type=envelope.event_type,
            envelope_json=envelope_json,
            wallclock_at=envelope.wallclock_at,
            scenario_id=envelope.scenario_id,
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_trace(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
        phase: str | None = None,
    ) -> list[TraceEnvelope]:
        """Return envelopes for *run_id*, optionally filtered.

        Filters are conjunctive — all specified predicates must match.
        """
        envelopes = list(self._traces.get(run_id, []))

        if task_id is not None:
            envelopes = [e for e in envelopes if e.task_id == task_id]
        if event_type is not None:
            envelopes = [e for e in envelopes if e.event_type == event_type]
        if phase is not None:
            envelopes = [e for e in envelopes if e.phase == phase]

        return envelopes

    def get_trace_slice(
        self,
        run_id: str,
        center_event_seq: int,
        window: int = 10,
    ) -> list[TraceEnvelope]:
        """Return a window of events centred on *center_event_seq*.

        The returned slice contains events whose ``event_seq`` falls within
        ``[center_event_seq - window, center_event_seq + window]`` (inclusive),
        ordered by ``event_seq``.
        """
        lo = center_event_seq - window
        hi = center_event_seq + window
        return [
            e
            for e in self._traces.get(run_id, [])
            if lo <= e.event_seq <= hi
        ]

    def load_trace(self, run_id: str) -> list[TraceEnvelope]:
        """Load trace envelopes for a run.

        If a store is available, loads from the persisted
        ``assurance_trace_envelopes`` table and converts rows back to
        :class:`TraceEnvelope` objects.  Otherwise falls back to in-memory
        storage.
        """
        if self._store is not None:
            rows = self._store.get_trace_envelopes(run_id)
            return [_envelope_from_stored_row(row) for row in rows]
        return list(self._traces.get(run_id, []))

    def load_task_trace(self, task_id: str) -> list[TraceEnvelope]:
        """Load all trace envelopes for a specific *task_id*.

        If a store is available, queries the persisted table filtered by
        ``task_id``.  Otherwise filters the in-memory envelopes across all
        runs.
        """
        if self._store is not None:
            rows = self._store.get_trace_envelopes_by_task(task_id)
            return [_envelope_from_stored_row(row) for row in rows]
        # Fall back to in-memory: scan all runs
        result: list[TraceEnvelope] = []
        for envelopes in self._traces.values():
            result.extend(e for e in envelopes if e.task_id == task_id)
        return result

    def persist_run(self, run_id: str) -> int:
        """Batch-persist all in-memory envelopes for a run to the store.

        Returns the count of envelopes persisted.  Useful for batch
        persistence after a run completes.

        Raises ``RuntimeError`` if no store is configured.
        """
        if self._store is None:
            raise RuntimeError("No store configured for persistence")

        envelopes = self._traces.get(run_id, [])
        count = 0
        for envelope in envelopes:
            try:
                self._persist_envelope(envelope)
                count += 1
            except Exception:
                # Envelope may already be persisted (e.g. duplicate trace_id);
                # skip silently so batch persist is idempotent-safe.
                log.debug(
                    "assurance.persist_run_skip",
                    run_id=run_id,
                    trace_id=envelope.trace_id,
                )
        return count

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_trace(self, run_id: str) -> list[dict[str, Any]]:
        """Export all envelopes for *run_id* as a list of plain dicts.

        Each dict mirrors the dataclass fields of :class:`TraceEnvelope`.
        Returns an empty list for unknown run IDs.
        """
        return [asdict(e) for e in self._traces.get(run_id, [])]


def _envelope_from_stored_row(row: dict[str, Any]) -> TraceEnvelope:
    """Convert a store row dict (with ``envelope_json`` key) to a TraceEnvelope."""
    raw = row.get("envelope_json", "{}")
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    return _envelope_from_dict(data)
