"""Integration tests: cancel cascade through TaskController + StalenessGuard through KernelStore.

Exercises:
  1. Parent cancel → children cancelled via controller.cancel_task()
  2. Deep cascade: parent → child → grandchild
  3. Terminal children skipped during cascade
  4. StalenessGuard integration: sweep fails stale tasks
  5. Guard + Program: guard fails stale tasks under an active program
  6. Guard preserves recent: tasks with current timestamp survive sweep
"""

from __future__ import annotations

import time

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.staleness_guard import StalenessGuard

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _start_task(
    controller: TaskController,
    *,
    conversation_id: str = "conv_test",
    goal: str = "test goal",
    parent_task_id: str | None | object = None,
):
    """Start a running task via the controller and return the context."""
    return controller.start_task(
        conversation_id=conversation_id,
        goal=goal,
        source_channel="chat",
        kind="respond",
        parent_task_id=parent_task_id,
    )


# ---------------------------------------------------------------------------
# 1. Cancel cascade through controller
# ---------------------------------------------------------------------------


def test_cancel_cascade_parent_to_children(tmp_path) -> None:
    """Cancel parent via controller.cancel_task() → all non-terminal children cancelled."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    parent_ctx = _start_task(controller, goal="parent task")
    child1_ctx = _start_task(controller, goal="child 1", parent_task_id=parent_ctx.task_id)
    child2_ctx = _start_task(controller, goal="child 2", parent_task_id=parent_ctx.task_id)

    cascade_cancelled = controller.cancel_task(parent_ctx.task_id)

    parent = store.get_task(parent_ctx.task_id)
    child1 = store.get_task(child1_ctx.task_id)
    child2 = store.get_task(child2_ctx.task_id)

    assert parent is not None and parent.status == "cancelled"
    assert child1 is not None and child1.status == "cancelled"
    assert child2 is not None and child2.status == "cancelled"

    # Both children should be in cascade_cancelled list
    assert child1_ctx.task_id in cascade_cancelled
    assert child2_ctx.task_id in cascade_cancelled

    # Verify task.cascade_cancelled events emitted for children
    child1_events = store.list_events(task_id=child1_ctx.task_id)
    cascade_events_1 = [e for e in child1_events if e["event_type"] == "task.cascade_cancelled"]
    assert len(cascade_events_1) == 1
    assert cascade_events_1[0]["payload"]["cascaded_from"] == parent_ctx.task_id

    child2_events = store.list_events(task_id=child2_ctx.task_id)
    cascade_events_2 = [e for e in child2_events if e["event_type"] == "task.cascade_cancelled"]
    assert len(cascade_events_2) == 1
    assert cascade_events_2[0]["payload"]["cascaded_from"] == parent_ctx.task_id


# ---------------------------------------------------------------------------
# 2. Deep cascade: parent → child → grandchild
# ---------------------------------------------------------------------------


def test_deep_cascade_parent_child_grandchild(tmp_path) -> None:
    """Cancel parent → child → grandchild all get cancelled."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    parent_ctx = _start_task(controller, goal="root")
    child_ctx = _start_task(controller, goal="child", parent_task_id=parent_ctx.task_id)
    grandchild_ctx = _start_task(controller, goal="grandchild", parent_task_id=child_ctx.task_id)

    cascade_cancelled = controller.cancel_task(parent_ctx.task_id)

    assert store.get_task(parent_ctx.task_id).status == "cancelled"
    assert store.get_task(child_ctx.task_id).status == "cancelled"
    assert store.get_task(grandchild_ctx.task_id).status == "cancelled"

    # Grandchild and child should both appear in the cascade list
    assert grandchild_ctx.task_id in cascade_cancelled
    assert child_ctx.task_id in cascade_cancelled

    # Grandchild cascade event should reference child (its immediate parent in the recursion)
    gc_events = store.list_events(task_id=grandchild_ctx.task_id)
    gc_cascade = [e for e in gc_events if e["event_type"] == "task.cascade_cancelled"]
    assert len(gc_cascade) == 1
    assert gc_cascade[0]["payload"]["cascaded_from"] == child_ctx.task_id

    # Child cascade event should reference parent (the root that was explicitly cancelled)
    child_events = store.list_events(task_id=child_ctx.task_id)
    child_cascade = [e for e in child_events if e["event_type"] == "task.cascade_cancelled"]
    assert len(child_cascade) == 1
    assert child_cascade[0]["payload"]["cascaded_from"] == parent_ctx.task_id


# ---------------------------------------------------------------------------
# 3. Terminal children skipped during cascade
# ---------------------------------------------------------------------------


def test_terminal_children_skipped_in_cascade(tmp_path) -> None:
    """Completed child is NOT re-cancelled; only running child is cancelled."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    parent_ctx = _start_task(controller, goal="parent")
    completed_child_ctx = _start_task(
        controller, goal="completed child", parent_task_id=parent_ctx.task_id
    )
    running_child_ctx = _start_task(
        controller, goal="running child", parent_task_id=parent_ctx.task_id
    )

    # Finalize the first child so it reaches a terminal state
    controller.finalize_result(completed_child_ctx, status="succeeded")
    completed_child = store.get_task(completed_child_ctx.task_id)
    assert completed_child is not None and completed_child.status == "completed"

    cascade_cancelled = controller.cancel_task(parent_ctx.task_id)

    assert store.get_task(parent_ctx.task_id).status == "cancelled"
    assert store.get_task(running_child_ctx.task_id).status == "cancelled"
    # Completed child should remain completed (not re-cancelled)
    assert store.get_task(completed_child_ctx.task_id).status == "completed"

    # Only the running child should appear in cascade_cancelled
    assert running_child_ctx.task_id in cascade_cancelled
    assert completed_child_ctx.task_id not in cascade_cancelled


# ---------------------------------------------------------------------------
# 4. StalenessGuard integration: sweep fails stale tasks
# ---------------------------------------------------------------------------


def test_staleness_guard_sweep_fails_stale_task(tmp_path) -> None:
    """Task with updated_at older than TTL is transitioned to failed by guard."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    ctx = _start_task(controller, goal="going stale")
    task_id = ctx.task_id

    # Block the task so it enters a WATCHABLE_STATE
    controller.mark_blocked(ctx)
    blocked = store.get_task(task_id)
    assert blocked is not None and blocked.status == "blocked"

    # Manually backdate updated_at via direct SQL
    two_seconds_ago = time.time() - 2
    store._get_conn().execute(
        "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
        (two_seconds_ago, task_id),
    )
    store._get_conn().commit()

    guard = StalenessGuard(store, ttl_seconds=1)
    affected = guard.sweep()

    assert task_id in affected

    refreshed = store.get_task(task_id)
    assert refreshed is not None and refreshed.status == "failed"

    # Verify a task.failed event was emitted with the timeout reason
    events = store.list_events(task_id=task_id)
    failed_events = [e for e in events if e["event_type"] == "task.failed"]
    assert len(failed_events) >= 1
    last_failed = failed_events[-1]
    assert last_failed["payload"]["reason"] == "state_timeout_exceeded"
    assert last_failed["payload"]["original_status"] == "blocked"


# ---------------------------------------------------------------------------
# 5. Guard + Program: guard fails stale tasks under an active program
# ---------------------------------------------------------------------------


def test_staleness_guard_with_program(tmp_path) -> None:
    """Create a program, activate it, then guard fails stale tasks under it."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    # Create and activate a program
    program = store.create_program(title="Test Program", goal="test guard + program")
    store.update_program_status(program.program_id, "active")
    program_refreshed = store.get_program(program.program_id)
    assert program_refreshed is not None and program_refreshed.status == "active"

    # Create a task and block it
    ctx = _start_task(controller, goal="program task going stale")
    controller.mark_blocked(ctx)

    # Backdate updated_at
    three_seconds_ago = time.time() - 3
    store._get_conn().execute(
        "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
        (three_seconds_ago, ctx.task_id),
    )
    store._get_conn().commit()

    guard = StalenessGuard(store, ttl_seconds=2)
    affected = guard.sweep()

    assert ctx.task_id in affected
    refreshed = store.get_task(ctx.task_id)
    assert refreshed is not None and refreshed.status == "failed"

    # Program itself should remain active (guard only affects tasks)
    program_after = store.get_program(program.program_id)
    assert program_after is not None and program_after.status == "active"


# ---------------------------------------------------------------------------
# 6. Guard preserves recent: tasks with current timestamp survive sweep
# ---------------------------------------------------------------------------


def test_staleness_guard_preserves_recent_tasks(tmp_path) -> None:
    """Tasks with a recent updated_at are NOT affected by sweep."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    ctx = _start_task(controller, goal="fresh task")
    controller.mark_blocked(ctx)

    blocked = store.get_task(ctx.task_id)
    assert blocked is not None and blocked.status == "blocked"

    guard = StalenessGuard(store, ttl_seconds=1)
    affected = guard.sweep()

    assert ctx.task_id not in affected

    refreshed = store.get_task(ctx.task_id)
    assert refreshed is not None and refreshed.status == "blocked"


# ---------------------------------------------------------------------------
# 7. Bonus: StalenessGuard transitions paused tasks to cancelled
# ---------------------------------------------------------------------------


def test_staleness_guard_paused_task_becomes_cancelled(tmp_path) -> None:
    """Paused tasks transition to cancelled (not failed) per state machine rules."""
    store = _make_store(tmp_path)
    controller = TaskController(store)

    ctx = _start_task(controller, goal="will be paused")
    controller.pause_task(ctx.task_id)

    paused = store.get_task(ctx.task_id)
    assert paused is not None and paused.status == "paused"

    # Backdate
    store._get_conn().execute(
        "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
        (time.time() - 3, ctx.task_id),
    )
    store._get_conn().commit()

    guard = StalenessGuard(store, ttl_seconds=2)
    affected = guard.sweep()

    assert ctx.task_id in affected
    refreshed = store.get_task(ctx.task_id)
    assert refreshed is not None and refreshed.status == "cancelled"
