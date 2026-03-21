"""Tests for DAGExecutionService — cover compute_task_status, failure, depth computation."""

from __future__ import annotations

from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_execution import DAGExecutionService


def _svc(store: KernelStore) -> DAGExecutionService:
    return DAGExecutionService(store)


def _mk_task(store: KernelStore, conv_id: str) -> Any:
    return store.create_task(
        conversation_id=conv_id,
        title="DAG Task",
        goal="test",
        source_channel="chat",
    )


# ── compute_task_status ─────────────────────────────────────────


def test_compute_task_status_all_done_success(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="succeeded")
    shared_store.update_step(step.step_id, status="succeeded")
    result = svc.compute_task_status(task_id=task.task_id, step_status="succeeded")
    assert result == "completed"


def test_compute_task_status_all_done_failed(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="failed")
    shared_store.update_step(step.step_id, status="failed")
    result = svc.compute_task_status(task_id=task.task_id, step_status="failed")
    assert result == "failed"


def test_compute_task_status_has_non_terminal(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    shared_store.create_step(task_id=task.task_id, kind="respond", status="succeeded")
    result = svc.compute_task_status(task_id=task.task_id, step_status="succeeded")
    assert result == "running"


# ── _compute_depth ──────────────────────────────────────────────


def test_compute_depth_root() -> None:
    from types import SimpleNamespace

    step_a = SimpleNamespace(step_id="a", depends_on=[])
    step_by_id = {"a": step_a}
    depth: dict[str, int] = {}
    result = DAGExecutionService._compute_depth("a", step_by_id, depth)
    assert result == 0
    assert depth["a"] == 0


def test_compute_depth_chain() -> None:
    from types import SimpleNamespace

    step_a = SimpleNamespace(step_id="a", depends_on=[])
    step_b = SimpleNamespace(step_id="b", depends_on=["a"])
    step_c = SimpleNamespace(step_id="c", depends_on=["b"])
    step_by_id = {"a": step_a, "b": step_b, "c": step_c}
    depth: dict[str, int] = {}
    result = DAGExecutionService._compute_depth("c", step_by_id, depth)
    assert result == 2
    assert depth["a"] == 0
    assert depth["b"] == 1
    assert depth["c"] == 2


def test_compute_depth_diamond() -> None:
    from types import SimpleNamespace

    step_a = SimpleNamespace(step_id="a", depends_on=[])
    step_b = SimpleNamespace(step_id="b", depends_on=["a"])
    step_c = SimpleNamespace(step_id="c", depends_on=["a"])
    step_d = SimpleNamespace(step_id="d", depends_on=["b", "c"])
    step_by_id = {"a": step_a, "b": step_b, "c": step_c, "d": step_d}
    depth: dict[str, int] = {}
    result = DAGExecutionService._compute_depth("d", step_by_id, depth)
    assert result == 2


def test_compute_depth_unknown_step() -> None:
    depth: dict[str, int] = {}
    result = DAGExecutionService._compute_depth("unknown", {}, depth)
    assert result == 0


# ── _handle_failure ─────────────────────────────────────────────


def test_handle_failure_retries(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(
        task_id=task.task_id, kind="respond", status="running", max_attempts=3
    )
    attempt = shared_store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="running"
    )
    shared_store.update_step(step.step_id, status="failed")
    shared_store.update_step_attempt(attempt.step_attempt_id, status="failed")
    svc._handle_failure(task_id=task.task_id, step_id=step.step_id)
    # Should have created a retry attempt
    step_after = shared_store.get_step(step.step_id)
    assert step_after.attempt >= 1


def test_handle_failure_propagates(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    # max_attempts=1 means no retry
    step = shared_store.create_step(
        task_id=task.task_id, kind="respond", status="failed", max_attempts=1
    )
    shared_store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="failed")
    shared_store.update_step(step.step_id, status="failed")
    svc._handle_failure(task_id=task.task_id, step_id=step.step_id)
    # Failure should be propagated (no crash)


# ── advance: success path ──────────────────────────────────────


def test_advance_success(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="succeeded")
    attempt = shared_store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="succeeded"
    )
    svc.advance(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        status="succeeded",
    )
    # No crash expected


def test_advance_skipped(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="skipped")
    attempt = shared_store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="skipped"
    )
    svc.advance(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        status="skipped",
    )


def test_advance_cancelled(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="cancelled")
    attempt = shared_store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="cancelled"
    )
    svc.advance(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        status="cancelled",
    )
    # Cancelled status should do nothing to DAG graph
