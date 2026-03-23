"""Tests for Observation Durability — durable tickets, timeout, restart recovery."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.execution.coordination.observation import ObservationService
from hermit.kernel.ledger.journal.store import KernelStore


def _setup(tmp_path: Path) -> KernelStore:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    return store


def _mk_task(store: KernelStore, **kwargs: Any):
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "title": "Test Task",
        "goal": "Observation test",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


# -- Schema version --------------------------------------------------------


def test_schema_version_is_16(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert int(store.schema_version()) >= 16


def test_observation_tickets_table_exists(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    tables = store._existing_tables()
    assert "observation_tickets" in tables


# -- create_observation_ticket ---------------------------------------------


def test_create_observation_ticket(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    deadline = time.time() + 3600
    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="background_job",
        poll_after_seconds=10.0,
        hard_deadline_at=deadline,
        ready_patterns=[{"field": "status", "value": "done"}],
        failure_patterns=[{"field": "status", "value": "error"}],
        ticket_data={"custom": "data"},
    )

    assert ticket_id.startswith("obs_")
    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["task_id"] == task.task_id
    assert ticket["step_id"] == step.step_id
    assert ticket["step_attempt_id"] == attempt.step_attempt_id
    assert ticket["observer_kind"] == "background_job"
    assert ticket["status"] == "active"
    assert ticket["poll_after_seconds"] == 10.0
    assert ticket["hard_deadline_at"] is not None
    assert ticket["ready_patterns"] == [{"field": "status", "value": "done"}]
    assert ticket["failure_patterns"] == [{"field": "status", "value": "error"}]
    assert ticket["ticket_data"] == {"custom": "data"}
    assert ticket["resolved_at"] is None


def test_create_observation_ticket_emits_event(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    events = store.list_events(task_id=task.task_id)
    obs_events = [e for e in events if e["event_type"] == "observation.created"]
    assert len(obs_events) >= 1
    assert obs_events[-1]["entity_id"] == ticket_id


def test_create_observation_ticket_defaults(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["poll_after_seconds"] == 5.0
    assert ticket["hard_deadline_at"] is None
    assert ticket["ready_patterns"] == []
    assert ticket["failure_patterns"] == []
    assert ticket["ticket_data"] == {}


# -- update_observation_progress -------------------------------------------


def test_update_observation_progress(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    poll_time = time.time()
    store.update_observation_progress(ticket_id, now=poll_time)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["last_polled_at"] == pytest.approx(poll_time, abs=0.01)

    events = store.list_events(task_id=task.task_id)
    polled_events = [e for e in events if e["event_type"] == "observation.polled"]
    assert len(polled_events) >= 1


# -- resolve_observation ---------------------------------------------------


def test_resolve_observation(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    resolve_time = time.time()
    store.resolve_observation(ticket_id, status="completed", now=resolve_time)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "completed"
    assert ticket["resolved_at"] == pytest.approx(resolve_time, abs=0.01)

    events = store.list_events(task_id=task.task_id)
    resolved_events = [e for e in events if e["event_type"] == "observation.resolved"]
    assert len(resolved_events) >= 1


def test_resolve_observation_cancelled(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    store.resolve_observation(ticket_id, status="cancelled")
    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "cancelled"


# -- timeout_observation ---------------------------------------------------


def test_timeout_observation(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="test",
    )

    timeout_time = time.time()
    store.timeout_observation(ticket_id, now=timeout_time)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "timed_out"
    assert ticket["resolved_at"] == pytest.approx(timeout_time, abs=0.01)

    events = store.list_events(task_id=task.task_id)
    timeout_events = [e for e in events if e["event_type"] == "observation.timed_out"]
    assert len(timeout_events) >= 1


# -- list_active_observation_tickets ---------------------------------------


def test_list_active_observation_tickets(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    t1 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="kind_a",
    )
    t2 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="kind_b",
    )

    store.resolve_observation(t1, status="completed")

    active = store.list_active_observation_tickets()
    assert len(active) == 1
    assert active[0]["ticket_id"] == t2


def test_list_active_empty(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    active = store.list_active_observation_tickets()
    assert active == []


# -- get_observation_ticket ------------------------------------------------


def test_get_observation_ticket_not_found(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.get_observation_ticket("nonexistent") is None


# -- Restart recovery ------------------------------------------------------


def test_restart_recovery_loads_active_tickets(tmp_path: Path) -> None:
    """Simulate restart: create tickets, reopen store, verify active list."""
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    t1 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="restart_test",
    )
    t2 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="restart_test2",
    )
    store.resolve_observation(t1, status="completed")

    # "Restart" — new store from same db
    store2 = KernelStore(tmp_path / "state.db")
    active = store2.list_active_observation_tickets()
    assert len(active) == 1
    assert active[0]["ticket_id"] == t2
    assert active[0]["observer_kind"] == "restart_test2"


def test_restart_recovery_with_timed_out(tmp_path: Path) -> None:
    """Timed out tickets should not appear in active list after restart."""
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="timeout_test",
    )
    store.timeout_observation(ticket_id)

    store2 = KernelStore(tmp_path / "state.db")
    active = store2.list_active_observation_tickets()
    assert len(active) == 0


# -- ObservationService with store -----------------------------------------


def test_observation_service_persist_ticket(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner, store=store)

    ticket_id = svc.persist_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="svc_test",
        poll_after_seconds=15.0,
    )

    assert ticket_id is not None
    assert ticket_id.startswith("obs_")
    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["observer_kind"] == "svc_test"


def test_observation_service_persist_ticket_no_store() -> None:
    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner)
    result = svc.persist_ticket(
        task_id="t1",
        step_id="s1",
        step_attempt_id="sa1",
        observer_kind="test",
    )
    assert result is None


def test_observation_service_resolve_ticket(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner, store=store)

    ticket_id = svc.persist_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="resolve_test",
    )
    assert ticket_id is not None

    svc.resolve_ticket(ticket_id, status="completed")
    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "completed"


def test_observation_service_resolve_ticket_no_store() -> None:
    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner)
    # Should not raise
    svc.resolve_ticket("nonexistent")


def test_observation_service_timeout_enforcement(tmp_path: Path) -> None:
    """Test that _enforce_timeouts marks expired tickets as timed_out."""
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.update_step_attempt(attempt.step_attempt_id, status="observing")

    # Create ticket with deadline in the past
    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="timeout_enforce",
        hard_deadline_at=time.time() - 10,
    )

    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store), agent=None)
    svc = ObservationService(runner, store=store)
    svc._enforce_timeouts(runner.task_controller)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "timed_out"

    # Step attempt should be failed
    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt is not None
    assert updated_attempt.status == "failed"
    assert updated_attempt.status_reason == "observation_timeout"


def test_observation_service_no_timeout_for_future_deadline(
    tmp_path: Path,
) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="future_deadline",
        hard_deadline_at=time.time() + 3600,
    )

    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store), agent=None)
    svc = ObservationService(runner, store=store)
    svc._enforce_timeouts(runner.task_controller)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "active"


def test_observation_service_no_timeout_without_deadline(
    tmp_path: Path,
) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    ticket_id = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="no_deadline",
    )

    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store), agent=None)
    svc = ObservationService(runner, store=store)
    svc._enforce_timeouts(runner.task_controller)

    ticket = store.get_observation_ticket(ticket_id)
    assert ticket is not None
    assert ticket["status"] == "active"


def test_observation_service_recovery_on_start(tmp_path: Path) -> None:
    """Test that start() calls _recover_active_tickets."""
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="recovery_test",
    )

    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner, store=store)
    svc.start()
    svc.stop()


def test_observation_service_enforce_timeouts_no_store() -> None:
    runner = SimpleNamespace(task_controller=SimpleNamespace(store=None), agent=None)
    svc = ObservationService(runner)
    # Should not raise
    svc._enforce_timeouts(runner.task_controller)


def test_observation_service_recover_no_store() -> None:
    runner = SimpleNamespace(task_controller=None, agent=None)
    svc = ObservationService(runner)
    # Should not raise
    svc._recover_active_tickets()


# -- Migration compatibility -----------------------------------------------


def test_migration_from_v15_compatible(tmp_path: Path) -> None:
    """Verify that opening a store creates v16 schema with observation_tickets."""
    store = _setup(tmp_path)
    assert int(store.schema_version()) >= 16
    assert "observation_tickets" in store._existing_tables()


def test_multiple_tickets_per_attempt(tmp_path: Path) -> None:
    """Multiple observation tickets can exist for the same step attempt."""
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    t1 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="first",
    )
    t2 = store.create_observation_ticket(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        observer_kind="second",
    )

    assert t1 != t2
    active = store.list_active_observation_tickets()
    assert len(active) == 2


def test_observation_ticket_record_dataclass() -> None:
    """Verify the ObservationTicketRecord dataclass can be instantiated."""
    from hermit.kernel.task.models.records import ObservationTicketRecord

    rec = ObservationTicketRecord(
        ticket_id="obs_test",
        task_id="task_1",
        step_id="step_1",
        step_attempt_id="sa_1",
        observer_kind="test",
        status="active",
        poll_after_seconds=5.0,
    )
    assert rec.ticket_id == "obs_test"
    assert rec.status == "active"
    assert rec.hard_deadline_at is None
    assert rec.ready_patterns == []
    assert rec.failure_patterns == []
    assert rec.ticket_data == {}
    assert rec.last_polled_at is None
    assert rec.resolved_at is None


# -- Stale observation auto-resolution ------------------------------------


def test_resolve_stale_observations_auto_fails(tmp_path: Path) -> None:
    """Stale observations exceeding observation_window are auto-resolved as failed."""
    from hermit.runtime.control.lifecycle.budgets import ExecutionBudget

    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.update_step_attempt(attempt.step_attempt_id, status="observing")
    store.update_step(step.step_id, status="blocked")
    store.update_task_status(task.task_id, "blocked")

    # Use a tiny observation_window so the attempt is immediately stale.
    budget = ExecutionBudget(observation_window=10.0)
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=None,
    )
    svc = ObservationService(runner, budget=budget, store=store)

    # Simulate the attempt being old enough to be stale.
    now = time.time()
    # Set claimed_at artificially in the past.
    store._get_conn().execute(
        "UPDATE step_attempts SET claimed_at = ? WHERE step_attempt_id = ?",
        (now - 20.0, attempt.step_attempt_id),
    )

    attempts = store.list_step_attempts(status="observing", limit=200)
    resolved = svc._resolve_stale_observations(store, attempts, now)

    assert attempt.step_attempt_id in resolved

    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt is not None
    assert updated_attempt.status == "failed"
    assert updated_attempt.status_reason == "observation_stale_timeout"

    updated_step = store.get_step(step.step_id)
    assert updated_step is not None
    assert updated_step.status == "failed"

    # Event should be recorded.
    events = store.list_events(task_id=task.task_id)
    stale_events = [e for e in events if e["event_type"] == "observation.stale_auto_resolved"]
    assert len(stale_events) >= 1
    assert stale_events[-1]["payload"]["reason"] == "observation_stale_timeout"


def test_resolve_stale_observations_not_stale(tmp_path: Path) -> None:
    """Observations within observation_window should not be resolved."""
    from hermit.runtime.control.lifecycle.budgets import ExecutionBudget

    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.update_step_attempt(attempt.step_attempt_id, status="observing")

    budget = ExecutionBudget(observation_window=600.0)
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=None,
    )
    svc = ObservationService(runner, budget=budget, store=store)

    now = time.time()
    # Set claimed_at to just now (within the window).
    store._get_conn().execute(
        "UPDATE step_attempts SET claimed_at = ? WHERE step_attempt_id = ?",
        (now - 10.0, attempt.step_attempt_id),
    )

    attempts = store.list_step_attempts(status="observing", limit=200)
    resolved = svc._resolve_stale_observations(store, attempts, now)

    assert len(resolved) == 0
    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt is not None
    assert updated_attempt.status == "observing"


def test_resolve_stale_observations_propagates_dag_failure(tmp_path: Path) -> None:
    """Stale observation resolution propagates failure to downstream DAG steps."""
    from hermit.runtime.control.lifecycle.budgets import ExecutionBudget

    store = _setup(tmp_path)
    task = _mk_task(store)
    step_a = store.create_step(task_id=task.task_id, kind="execute")
    step_b = store.create_step(
        task_id=task.task_id,
        kind="execute",
        depends_on=[step_a.step_id],
    )
    attempt_a = store.create_step_attempt(task_id=task.task_id, step_id=step_a.step_id)
    store.update_step_attempt(attempt_a.step_attempt_id, status="observing")
    store.update_step(step_a.step_id, status="blocked")
    store.update_step(step_b.step_id, status="waiting")
    store.update_task_status(task.task_id, "blocked")

    budget = ExecutionBudget(observation_window=5.0)
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=None,
    )
    svc = ObservationService(runner, budget=budget, store=store)

    now = time.time()
    store._get_conn().execute(
        "UPDATE step_attempts SET claimed_at = ? WHERE step_attempt_id = ?",
        (now - 10.0, attempt_a.step_attempt_id),
    )

    attempts = store.list_step_attempts(status="observing", limit=200)
    resolved = svc._resolve_stale_observations(store, attempts, now)

    assert attempt_a.step_attempt_id in resolved

    # Downstream step_b should be cascaded to failed.
    updated_step_b = store.get_step(step_b.step_id)
    assert updated_step_b is not None
    assert updated_step_b.status == "failed"

    # Task should be failed since all steps are terminal.
    updated_task = store.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == "failed"


def test_resolve_stale_observations_zero_window(tmp_path: Path) -> None:
    """With observation_window=0, no observations should be resolved."""
    from hermit.runtime.control.lifecycle.budgets import ExecutionBudget

    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.update_step_attempt(attempt.step_attempt_id, status="observing")

    budget = ExecutionBudget(observation_window=0.0)
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=None,
    )
    svc = ObservationService(runner, budget=budget, store=store)

    now = time.time()
    store._get_conn().execute(
        "UPDATE step_attempts SET claimed_at = ? WHERE step_attempt_id = ?",
        (now - 99999.0, attempt.step_attempt_id),
    )

    attempts = store.list_step_attempts(status="observing", limit=200)
    resolved = svc._resolve_stale_observations(store, attempts, now)

    assert len(resolved) == 0

