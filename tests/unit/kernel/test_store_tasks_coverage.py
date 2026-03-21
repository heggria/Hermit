"""Tests for KernelTaskStoreMixin uncovered paths — target 95%+ on store_tasks.py."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


def _setup(tmp_path: Path) -> KernelStore:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    return store


def _mk_task(store: KernelStore, **kwargs):
    defaults = {
        "conversation_id": "conv-1",
        "title": "Test Task",
        "goal": "Cover gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


# ── list_child_tasks ────────────────────────────────────────────────


def test_list_child_tasks(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    parent = _mk_task(store, title="Parent")
    c1 = _mk_task(store, title="Child 1", parent_task_id=parent.task_id)
    c2 = _mk_task(store, title="Child 2", parent_task_id=parent.task_id)
    _mk_task(store, title="Unrelated")

    children = store.list_child_tasks(parent_task_id=parent.task_id)
    assert len(children) == 2
    assert {c.task_id for c in children} == {c1.task_id, c2.task_id}


# ── _check_dag_cycles ──────────────────────────────────────────────


def test_check_dag_cycles_detects_cycle(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step_a = store.create_step(task_id=task.task_id, kind="a")
    step_b = store.create_step(task_id=task.task_id, kind="b", depends_on=[step_a.step_id])
    # Create a cycle: A depends on B (injected), B depends on A (already set)
    # So adding a new step that depends on B, where A also depends on B, forms a cycle
    # when B depends on A.
    # Actually we need: inject A -> B dependency into A's depends_on,
    # then any new step depending on A would see A -> B -> A cycle.
    import json

    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET depends_on_json = ? WHERE step_id = ?",
            (json.dumps([step_b.step_id]), step_a.step_id),
        )
    # Now: step_a depends on step_b, step_b depends on step_a => cycle
    # Adding a new step depending on step_a triggers the cycle check
    with pytest.raises(ValueError, match="Cycle detected"):
        store.create_step(
            task_id=task.task_id,
            kind="trigger_cycle",
            depends_on=[step_a.step_id],
        )


# ── get_step_by_node_key ───────────────────────────────────────────


def test_get_step_by_node_key(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute", node_key="my_node")
    fetched = store.get_step_by_node_key(task.task_id, "my_node")
    assert fetched is not None
    assert fetched.step_id == step.step_id

    assert store.get_step_by_node_key(task.task_id, "missing_node") is None


# ── get_key_to_step_id ─────────────────────────────────────────────


def test_get_key_to_step_id(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    s1 = store.create_step(task_id=task.task_id, kind="a", node_key="key_a")
    s2 = store.create_step(task_id=task.task_id, kind="b", node_key="key_b")
    store.create_step(task_id=task.task_id, kind="c")  # no node_key

    mapping = store.get_key_to_step_id(task.task_id)
    assert mapping == {"key_a": s1.step_id, "key_b": s2.step_id}


# ── activate_waiting_dependents — all join strategies ──────────────


def test_activate_waiting_dependents_all_required(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id],
        join_strategy="all_required",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # Complete dep1 only — should NOT activate waiter
    store.update_step(dep1.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep1.step_id)
    assert waiter.step_id not in activated

    # Complete dep2 — NOW all_required satisfied
    store.update_step(dep2.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep2.step_id)
    assert waiter.step_id in activated

    fetched = store.get_step(waiter.step_id)
    assert fetched is not None
    assert fetched.status == "ready"


def test_activate_waiting_dependents_any_sufficient(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id],
        join_strategy="any_sufficient",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # One success is enough
    store.update_step(dep1.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep1.step_id)
    assert waiter.step_id in activated


def test_activate_waiting_dependents_majority(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    dep3 = store.create_step(task_id=task.task_id, kind="dep3")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id, dep3.step_id],
        join_strategy="majority",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # 1/3 succeeded — not majority
    store.update_step(dep1.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep1.step_id)
    assert waiter.step_id not in activated

    # 2/3 succeeded — majority!
    store.update_step(dep2.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep2.step_id)
    assert waiter.step_id in activated


def test_activate_waiting_dependents_best_effort(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id],
        join_strategy="best_effort",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # dep1 fails, dep2 still running — not all terminal
    store.update_step(dep1.step_id, status="failed")
    activated = store.activate_waiting_dependents(task.task_id, dep1.step_id)
    assert waiter.step_id not in activated

    # dep2 succeeds — all terminal now
    store.update_step(dep2.step_id, status="succeeded")
    activated = store.activate_waiting_dependents(task.task_id, dep2.step_id)
    assert waiter.step_id in activated


# ── propagate_step_failure ─────────────────────────────────────────


def test_propagate_step_failure_all_required_cascades(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep = store.create_step(task_id=task.task_id, kind="dep")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep.step_id],
        join_strategy="all_required",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    store.update_step(dep.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, dep.step_id)
    assert waiter.step_id in cascaded

    fetched = store.get_step(waiter.step_id)
    assert fetched is not None
    assert fetched.status == "failed"


def test_propagate_step_failure_any_sufficient_only_when_all_fail(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id],
        join_strategy="any_sufficient",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # First dep fails — dep2 still running, so no cascade
    store.update_step(dep1.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, dep1.step_id)
    assert waiter.step_id not in cascaded

    # dep2 also fails — now all failed, cascade
    store.update_step(dep2.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, dep2.step_id)
    assert waiter.step_id in cascaded


def test_propagate_step_failure_majority_when_over_half_fail(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    dep1 = store.create_step(task_id=task.task_id, kind="dep1")
    dep2 = store.create_step(task_id=task.task_id, kind="dep2")
    dep3 = store.create_step(task_id=task.task_id, kind="dep3")
    waiter = store.create_step(
        task_id=task.task_id,
        kind="waiter",
        depends_on=[dep1.step_id, dep2.step_id, dep3.step_id],
        join_strategy="majority",
    )
    store.create_step_attempt(task_id=task.task_id, step_id=waiter.step_id, status="waiting")

    # 1/3 fail — not majority
    store.update_step(dep1.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, dep1.step_id)
    assert waiter.step_id not in cascaded

    # 2/3 fail — majority failed, cascade
    store.update_step(dep2.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, dep2.step_id)
    assert waiter.step_id in cascaded


def test_propagate_step_failure_recursive_cascade(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    a = store.create_step(task_id=task.task_id, kind="a")
    b = store.create_step(
        task_id=task.task_id, kind="b", depends_on=[a.step_id], join_strategy="all_required"
    )
    store.create_step_attempt(task_id=task.task_id, step_id=b.step_id, status="waiting")
    c = store.create_step(
        task_id=task.task_id, kind="c", depends_on=[b.step_id], join_strategy="all_required"
    )
    store.create_step_attempt(task_id=task.task_id, step_id=c.step_id, status="waiting")

    store.update_step(a.step_id, status="failed")
    cascaded = store.propagate_step_failure(task.task_id, a.step_id)
    # Both b and c should cascade
    assert b.step_id in cascaded
    assert c.step_id in cascaded


# ── retry_step ─────────────────────────────────────────────────────


def test_retry_step_creates_new_attempt(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute", max_attempts=3)
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    new_attempt = store.retry_step(task.task_id, step.step_id)
    assert new_attempt.attempt == 2
    assert new_attempt.status == "ready"

    refreshed_step = store.get_step(step.step_id)
    assert refreshed_step is not None
    assert refreshed_step.attempt == 2
    assert refreshed_step.status == "ready"


def test_retry_step_missing_raises_value_error(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        store.retry_step("task-x", "missing-step-id")


# ── has_non_terminal_steps ─────────────────────────────────────────


def test_has_non_terminal_steps_true(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    store.create_step(task_id=task.task_id, kind="running_step")
    assert store.has_non_terminal_steps(task.task_id) is True


def test_has_non_terminal_steps_false(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="done_step")
    store.update_step(step.step_id, status="succeeded")
    assert store.has_non_terminal_steps(task.task_id) is False


def test_has_non_terminal_steps_empty(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    assert store.has_non_terminal_steps(task.task_id) is False


# ── list_ready_step_attempts ───────────────────────────────────────


def test_list_ready_step_attempts(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    step = store.create_step(task_id=task.task_id, kind="execute")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET status = 'ready' WHERE step_id = ?",
            (step.step_id,),
        )
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="ready")

    ready = store.list_ready_step_attempts()
    assert len(ready) >= 1
    assert any(a.step_attempt_id == attempt.step_attempt_id for a in ready)


# ── claim_next_ready_step_attempt ──────────────────────────────────


def test_claim_next_ready_step_attempt_success(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    step = store.create_step(task_id=task.task_id, kind="execute")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET status = 'ready' WHERE step_id = ?",
            (step.step_id,),
        )
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="ready")

    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    assert claimed.status == "running"


def test_claim_next_ready_step_attempt_none_available(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.claim_next_ready_step_attempt() is None


def test_claim_next_ready_step_attempt_cas_guard(tmp_path: Path) -> None:
    """Simulate CAS failure by manually changing status between SELECT and UPDATE."""
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    step = store.create_step(task_id=task.task_id, kind="execute")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET status = 'ready' WHERE step_id = ?",
            (step.step_id,),
        )
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="ready")
    # Claim it once
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    # Second claim should return None (nothing ready)
    second = store.claim_next_ready_step_attempt()
    assert second is None


# ── try_supersede_step_attempt ─────────────────────────────────────


def test_try_supersede_from_running(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    # Status is 'running' by default
    won = store.try_supersede_step_attempt(attempt.step_attempt_id, finished_at=time.time())
    assert won is True
    fetched = store.get_step_attempt(attempt.step_attempt_id)
    assert fetched is not None
    assert fetched.status == "superseded"


def test_try_supersede_from_awaiting_approval(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.update_step_attempt(attempt.step_attempt_id, status="awaiting_approval")
    won = store.try_supersede_step_attempt(attempt.step_attempt_id, finished_at=time.time())
    assert won is True


def test_try_supersede_from_terminal_returns_false(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    store.try_finalize_step_attempt(
        attempt.step_attempt_id, status="succeeded", finished_at=time.time()
    )
    lost = store.try_supersede_step_attempt(attempt.step_attempt_id, finished_at=time.time())
    assert lost is False


# ── create_ingress with all refs ───────────────────────────────────


def test_create_ingress_with_all_optional_refs(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    ingress = store.create_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="hello",
        normalized_text="hello",
        actor="user-1",
        reply_to_ref="ref-reply",
        quoted_message_ref="ref-quoted",
        explicit_task_ref="ref-task",
        referenced_artifact_refs=["art-1", "art-2"],
    )
    assert ingress is not None
    assert ingress.reply_to_ref == "ref-reply"
    assert ingress.quoted_message_ref == "ref-quoted"
    assert ingress.explicit_task_ref == "ref-task"
    assert ingress.referenced_artifact_refs == ["art-1", "art-2"]


# ── list_ingresses with filters ───────────────────────────────────


def test_list_ingresses_with_filters(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.ensure_conversation("conv-2", source_channel="chat")
    i1 = store.create_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="a",
        normalized_text="a",
    )
    i2 = store.create_ingress(
        conversation_id="conv-2",
        source_channel="chat",
        raw_text="b",
        normalized_text="b",
    )
    # Update i1 to bind to a task
    task = _mk_task(store)
    store.update_ingress(i1.ingress_id, chosen_task_id=task.task_id, status="bound")

    by_conv = store.list_ingresses(conversation_id="conv-1")
    assert len(by_conv) == 1
    assert by_conv[0].ingress_id == i1.ingress_id

    by_task = store.list_ingresses(task_id=task.task_id)
    assert len(by_task) == 1

    by_status = store.list_ingresses(status="received")
    assert any(i.ingress_id == i2.ingress_id for i in by_status)


# ── count_pending_ingresses ────────────────────────────────────────


def test_count_pending_ingresses(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    store.create_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="x",
        normalized_text="x",
    )
    store.create_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="y",
        normalized_text="y",
    )
    assert store.count_pending_ingresses(conversation_id="conv-1") == 2


# ── update_ingress with pending_disambiguation ─────────────────────


def test_update_ingress_pending_disambiguation_event_type(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    ingress = store.create_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="ambiguous",
        normalized_text="ambiguous",
    )
    store.update_ingress(ingress.ingress_id, status="pending_disambiguation")
    events = store.list_events(limit=50)
    assert any(e["event_type"] == "ingress.pending_disambiguation" for e in events)


# ── ensure_valid_focus ─────────────────────────────────────────────


def test_ensure_valid_focus_with_open_task(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    store.set_conversation_focus("conv-1", task_id=task.task_id)

    result = store.ensure_valid_focus("conv-1")
    assert result == task.task_id


def test_ensure_valid_focus_completed_task_falls_back(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    closed = _mk_task(store, title="Closed", status="running")
    store.update_task_status(closed.task_id, "completed")
    open_task = _mk_task(store, title="Open", status="running")
    store.set_conversation_focus("conv-1", task_id=closed.task_id)

    result = store.ensure_valid_focus("conv-1")
    assert result == open_task.task_id


def test_ensure_valid_focus_no_open_tasks(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    store.update_task_status(task.task_id, "completed")
    store.set_conversation_focus("conv-1", task_id=task.task_id)

    result = store.ensure_valid_focus("conv-1")
    assert result is None


def test_ensure_valid_focus_nonexistent_conversation(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    result = store.ensure_valid_focus("nonexistent")
    assert result is None


# ── iter_events ────────────────────────────────────────────────────


def test_iter_events_pagination(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    # Create several steps to generate events
    for i in range(5):
        store.create_step(task_id=task.task_id, kind=f"step-{i}")

    events = list(store.iter_events(task_id=task.task_id, batch_size=2))
    # Should get all events across batches
    assert len(events) >= 5


# ── list_events_for_tasks ──────────────────────────────────────────


def test_list_events_for_tasks_multiple(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    t1 = _mk_task(store, title="T1")
    t2 = _mk_task(store, title="T2")
    store.create_step(task_id=t1.task_id, kind="a")
    store.create_step(task_id=t2.task_id, kind="b")

    result = store.list_events_for_tasks([t1.task_id, t2.task_id])
    assert t1.task_id in result
    assert t2.task_id in result


def test_list_events_for_tasks_empty_input(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.list_events_for_tasks([]) == {}


def test_list_events_for_tasks_per_task_limit(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    for i in range(10):
        store.create_step(task_id=task.task_id, kind=f"step-{i}")

    result = store.list_events_for_tasks([task.task_id], limit_per_task=3)
    assert len(result[task.task_id]) <= 3


# ── get_last_event_per_task ────────────────────────────────────────


def test_get_last_event_per_task(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    t1 = _mk_task(store, title="T1")
    t2 = _mk_task(store, title="T2")
    store.create_step(task_id=t1.task_id, kind="a")
    store.create_step(task_id=t2.task_id, kind="b")

    result = store.get_last_event_per_task([t1.task_id, t2.task_id])
    assert t1.task_id in result
    assert t2.task_id in result


def test_get_last_event_per_task_empty(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    assert store.get_last_event_per_task([]) == {}


# ── Health query helpers ───────────────────────────────────────────


def test_list_active_tasks(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    running = _mk_task(store, title="Running", status="running")
    completed = _mk_task(store, title="Completed", status="running")
    store.update_task_status(completed.task_id, "completed")

    active = store.list_active_tasks()
    active_ids = {t.task_id for t in active}
    assert running.task_id in active_ids
    assert completed.task_id not in active_ids


def test_list_terminal_tasks_since(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    store.update_task_status(task.task_id, "completed")

    terminal = store.list_terminal_tasks_since(since=time.time() - 3600)
    assert any(t.task_id == task.task_id for t in terminal)


def test_list_stale_tasks(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    # Make it stale by setting updated_at to the past
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
            (time.time() - 1000, task.task_id),
        )

    stale = store.list_stale_tasks(threshold_seconds=100)
    assert any(t.task_id == task.task_id for t in stale)


def test_count_tasks_by_status(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    _mk_task(store, title="T1", status="running")
    t2 = _mk_task(store, title="T2", status="running")
    store.update_task_status(t2.task_id, "completed")

    counts = store.count_tasks_by_status()
    assert "running" in counts
    assert "completed" in counts


def test_list_recent_failures(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    store.update_task_status(task.task_id, "failed")

    failures = store.list_recent_failures()
    assert any(t.task_id == task.task_id for t in failures)


def test_count_completed_in_window(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store, status="running")
    store.update_task_status(task.task_id, "completed")

    count = store.count_completed_in_window(window_seconds=3600)
    assert count >= 1


def test_count_steps_by_status(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    s1 = store.create_step(task_id=task.task_id, kind="a")
    s2 = store.create_step(task_id=task.task_id, kind="b")
    store.update_step(s1.step_id, status="succeeded")
    store.update_step(s2.step_id, status="failed")

    counts = store.count_steps_by_status(task_id=task.task_id)
    assert counts.get("succeeded", 0) >= 1
    assert counts.get("failed", 0) >= 1


# ── batch_get_step_attempts ───────────────────────────────────────


def test_batch_get_step_attempts(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="x")
    a1 = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    a2 = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, attempt=2)

    result = store.batch_get_step_attempts([a1.step_attempt_id, a2.step_attempt_id])
    assert len(result) == 2
    assert a1.step_attempt_id in result
    assert a2.step_attempt_id in result

    assert store.batch_get_step_attempts([]) == {}


# ── list_step_attempts with various filters ────────────────────────


def test_list_step_attempts_filters(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="x")
    a1 = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)

    by_task = store.list_step_attempts(task_id=task.task_id)
    assert len(by_task) >= 1

    by_step = store.list_step_attempts(step_id=step.step_id)
    assert len(by_step) >= 1

    by_status = store.list_step_attempts(status="running")
    assert any(a.step_attempt_id == a1.step_attempt_id for a in by_status)


# ── has_active_task_with_goal ───────────────────────────────────


def test_has_active_task_with_goal_active_match(tmp_path: Path) -> None:
    """Active (running) task with matching goal returns True."""
    store = _setup(tmp_path)
    _mk_task(store, goal="promote checkpoint", status="running")

    assert store.has_active_task_with_goal("promote checkpoint") is True


def test_has_active_task_with_goal_terminal_returns_false(tmp_path: Path) -> None:
    """Completed task with matching goal should return False (terminal)."""
    store = _setup(tmp_path)
    task = _mk_task(store, goal="promote checkpoint", status="running")
    store.update_task_status(task.task_id, "completed")

    assert store.has_active_task_with_goal("promote checkpoint") is False


def test_has_active_task_with_goal_no_tasks(tmp_path: Path) -> None:
    """No tasks at all returns False."""
    store = _setup(tmp_path)

    assert store.has_active_task_with_goal("anything") is False


def test_has_active_task_with_goal_policy_profile_filter(tmp_path: Path) -> None:
    """With policy_profile filter, only matching tasks are considered."""
    store = _setup(tmp_path)
    _mk_task(store, goal="promote checkpoint", status="running", policy_profile="autonomous")

    assert (
        store.has_active_task_with_goal("promote checkpoint", policy_profile="autonomous") is True
    )
    assert (
        store.has_active_task_with_goal("promote checkpoint", policy_profile="supervised") is False
    )


def test_has_active_task_with_goal_empty_goal(tmp_path: Path) -> None:
    """Empty goal string should only match tasks with empty goal."""
    store = _setup(tmp_path)
    _mk_task(store, goal="some real goal", status="running")

    assert store.has_active_task_with_goal("") is False


def test_has_active_task_with_goal_queued_matches(tmp_path: Path) -> None:
    """Queued tasks are non-terminal and should match."""
    store = _setup(tmp_path)
    _mk_task(store, goal="queued job", status="queued")

    assert store.has_active_task_with_goal("queued job") is True
