from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, cast

from hermit.runtime.control.lifecycle.budgets import ExecutionBudget, get_runtime_budget

_OBSERVATION_ENVELOPE_KEY = "_hermit_observation"


@dataclass
class ObservationProgress:
    phase: str
    summary: str
    detail: str | None = None
    progress_percent: int | None = None
    ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "summary": self.summary,
            "detail": self.detail,
            "progress_percent": self.progress_percent,
            "ready": bool(self.ready),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationProgress:
        percent = data.get("progress_percent")
        try:
            progress_percent = int(percent) if percent is not None else None
        except (TypeError, ValueError):
            progress_percent = None
        return cls(
            phase=str(data.get("phase", "") or ""),
            summary=str(data.get("summary", "") or ""),
            detail=str(data.get("detail", "") or "") or None,
            progress_percent=progress_percent,
            ready=bool(data.get("ready", False)),
        )

    def signature(self) -> tuple[str, str, str | None, int | None, bool]:
        return (
            self.phase,
            self.summary,
            self.detail,
            self.progress_percent,
            bool(self.ready),
        )


def normalize_observation_progress(value: Any) -> ObservationProgress | None:
    if isinstance(value, ObservationProgress):
        return value
    if not isinstance(value, dict):
        return None
    d: dict[str, Any] = cast(dict[str, Any], value)
    summary = str(d.get("summary", "") or "").strip()
    phase = str(d.get("phase", "") or "").strip()
    if not summary and not phase:
        return None
    return ObservationProgress.from_dict(d)


@dataclass
class ObservationTicket:
    observer_kind: str
    job_id: str
    status_ref: str
    poll_after_seconds: float
    cancel_supported: bool
    resume_token: str
    topic_summary: str
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    display_name: str = ""
    ready_patterns: list[Any] | None = None
    failure_patterns: list[Any] | None = None
    progress_patterns: list[dict[str, Any]] | None = None
    ready_return: bool = False
    status_tool_name: str | None = None
    status_tool_input: dict[str, Any] | None = None
    cancel_tool_name: str | None = None
    cancel_tool_input: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    progress_summary: dict[str, Any] | None = None
    started_at: float | None = None
    hard_deadline_at: float | None = None
    next_poll_at: float | None = None
    last_progress_summary_at: float | None = None
    last_status: str | None = None
    last_status_summary: str | None = None
    terminal_status: str | None = None
    final_result: Any = None
    final_model_content: Any = None
    final_is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "observer_kind": self.observer_kind,
            "job_id": self.job_id,
            "status_ref": self.status_ref,
            "poll_after_seconds": float(self.poll_after_seconds),
            "cancel_supported": bool(self.cancel_supported),
            "resume_token": self.resume_token,
            "topic_summary": self.topic_summary,
            "tool_name": self.tool_name,
            "tool_input": dict(self.tool_input or {}),
            "display_name": self.display_name,
            "ready_patterns": list(self.ready_patterns or []),
            "failure_patterns": list(self.failure_patterns or []),
            "progress_patterns": list(self.progress_patterns or []),
            "ready_return": bool(self.ready_return),
            "status_tool_name": self.status_tool_name,
            "status_tool_input": dict(self.status_tool_input or {})
            if self.status_tool_input
            else None,
            "cancel_tool_name": self.cancel_tool_name,
            "cancel_tool_input": dict(self.cancel_tool_input or {})
            if self.cancel_tool_input
            else None,
            "progress": dict(self.progress or {}) if self.progress else None,
            "progress_summary": dict(self.progress_summary or {})
            if self.progress_summary
            else None,
            "started_at": self.started_at,
            "hard_deadline_at": self.hard_deadline_at,
            "next_poll_at": self.next_poll_at,
            "last_progress_summary_at": self.last_progress_summary_at,
            "last_status": self.last_status,
            "last_status_summary": self.last_status_summary,
            "terminal_status": self.terminal_status,
            "final_result": self.final_result,
            "final_model_content": self.final_model_content,
            "final_is_error": bool(self.final_is_error),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationTicket:
        return cls(
            observer_kind=str(data.get("observer_kind", "")),
            job_id=str(data.get("job_id", "")),
            status_ref=str(data.get("status_ref", "")),
            poll_after_seconds=float(data.get("poll_after_seconds", 0) or 0),
            cancel_supported=bool(data.get("cancel_supported", False)),
            resume_token=str(data.get("resume_token", "")),
            topic_summary=str(data.get("topic_summary", "")),
            tool_name=str(data.get("tool_name", "")),
            tool_input=dict(data.get("tool_input", {}) or {}),
            display_name=str(data.get("display_name", "") or ""),
            ready_patterns=list(data.get("ready_patterns", []) or []),
            failure_patterns=list(data.get("failure_patterns", []) or []),
            progress_patterns=list(data.get("progress_patterns", []) or []),
            ready_return=bool(data.get("ready_return", False)),
            status_tool_name=str(data.get("status_tool_name", "") or "") or None,
            status_tool_input=dict(data.get("status_tool_input", {}) or {}) or None,
            cancel_tool_name=str(data.get("cancel_tool_name", "") or "") or None,
            cancel_tool_input=dict(data.get("cancel_tool_input", {}) or {}) or None,
            progress=dict(data.get("progress", {}) or {}) or None,
            progress_summary=dict(data.get("progress_summary", {}) or {}) or None,
            started_at=float(data.get("started_at", 0) or 0) or None,
            hard_deadline_at=float(data.get("hard_deadline_at", 0) or 0) or None,
            next_poll_at=float(data.get("next_poll_at", 0) or 0) or None,
            last_progress_summary_at=float(data.get("last_progress_summary_at", 0) or 0) or None,
            last_status=str(data.get("last_status", "") or "") or None,
            last_status_summary=str(data.get("last_status_summary", "") or "") or None,
            terminal_status=str(data.get("terminal_status", "") or "") or None,
            final_result=data.get("final_result"),
            final_model_content=data.get("final_model_content"),
            final_is_error=bool(data.get("final_is_error", False)),
        )

    def schedule_next_poll(self, *, now: float | None = None) -> ObservationTicket:
        current = time.time() if now is None else now
        self.next_poll_at = current + max(self.poll_after_seconds, 0.0)
        return self


def observation_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {_OBSERVATION_ENVELOPE_KEY: payload}


def normalize_observation_ticket(value: Any) -> ObservationTicket | None:
    raw: Any = value
    if isinstance(value, dict):
        d: dict[str, Any] = cast(dict[str, Any], value)
        if _OBSERVATION_ENVELOPE_KEY in d:
            raw = d.get(_OBSERVATION_ENVELOPE_KEY)
    if isinstance(raw, ObservationTicket):
        return raw
    if not isinstance(raw, dict):
        return None
    raw_d: dict[str, Any] = cast(dict[str, Any], raw)
    required = {
        "observer_kind",
        "job_id",
        "status_ref",
        "poll_after_seconds",
        "cancel_supported",
        "resume_token",
        "topic_summary",
    }
    if not required.issubset(raw_d.keys()):
        return None
    ticket = ObservationTicket.from_dict(raw_d)
    ticket.schedule_next_poll()
    return ticket


@dataclass
class ObservationPollResult:
    ticket: ObservationTicket
    should_resume: bool = False


_SUBTASK_JOIN_OBSERVATION_KIND = "subtask_join"


@dataclass
class SubtaskJoinObservation:
    """Observation ticket for a parent step waiting on spawned child steps.

    Carried in the parent StepAttemptRecord's context under the key
    ``"subtask_join_observation"`` so the JoinBarrierService can evaluate
    completion without re-scanning the full DAG on every poll cycle.

    Attributes:
        child_step_ids: Ordered list of step_ids spawned by the parent step.
        join_strategy: Determines when the barrier is considered satisfied.
            Mirrors ``JoinStrategy`` values from ``join_barrier.py``.
        parent_step_id: The step_id of the spawning (parent) step.
        parent_attempt_id: The step_attempt_id of the parent attempt that
            transitioned to 'observing' status.
    """

    child_step_ids: list[str]
    join_strategy: str
    parent_step_id: str
    parent_attempt_id: str

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": _SUBTASK_JOIN_OBSERVATION_KIND,
            "child_step_ids": list(self.child_step_ids),
            "join_strategy": self.join_strategy,
            "parent_step_id": self.parent_step_id,
            "parent_attempt_id": self.parent_attempt_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubtaskJoinObservation:
        child_ids = data.get("child_step_ids")
        if not isinstance(child_ids, list):
            child_ids = []
        return cls(
            child_step_ids=[str(s) for s in child_ids],
            join_strategy=str(data.get("join_strategy", "all_required") or "all_required"),
            parent_step_id=str(data.get("parent_step_id", "") or ""),
            parent_attempt_id=str(data.get("parent_attempt_id", "") or ""),
        )


def normalize_subtask_join_observation(value: Any) -> SubtaskJoinObservation | None:
    """Coerce *value* to a :class:`SubtaskJoinObservation`, or return ``None``.

    Accepts either an already-constructed instance or a plain ``dict`` whose
    ``"kind"`` field equals ``"subtask_join"``.
    """
    if isinstance(value, SubtaskJoinObservation):
        return value
    if not isinstance(value, dict):
        return None
    d: dict[str, Any] = cast(dict[str, Any], value)
    if d.get("kind") != _SUBTASK_JOIN_OBSERVATION_KIND:
        return None
    child_ids = d.get("child_step_ids")
    if not isinstance(child_ids, list) or not child_ids:
        return None
    return SubtaskJoinObservation.from_dict(d)


class ObservationService:
    def __init__(self, runner: Any, *, budget: ExecutionBudget | None = None) -> None:
        self._runner = runner
        self._budget = budget or get_runtime_budget()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resuming: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="hermit-observation",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(self._budget.observation_poll_interval):
            try:
                self._tick()
            except Exception:
                continue

    def _tick(self) -> None:
        controller = getattr(self._runner, "task_controller", None)
        agent = getattr(self._runner, "agent", None)
        tool_executor = getattr(agent, "tool_executor", None)
        if controller is None or tool_executor is None:
            return
        attempts = controller.store.list_step_attempts(status="observing", limit=200)
        now = time.time()
        for attempt in attempts:
            with self._lock:
                if attempt.step_attempt_id in self._resuming:
                    continue
            result = tool_executor.poll_observation(attempt.step_attempt_id, now=now)
            if result is None or not result.should_resume:
                continue
            with self._lock:
                if attempt.step_attempt_id in self._resuming:
                    continue
                self._resuming.add(attempt.step_attempt_id)
            try:
                self._runner.task_controller.enqueue_resume(attempt.step_attempt_id)
                self._runner.wake_dispatcher()
            finally:
                with self._lock:
                    self._resuming.discard(attempt.step_attempt_id)
