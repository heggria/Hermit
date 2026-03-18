"""Integration test: auto-park focus switching when a task blocks on approval."""

from __future__ import annotations

from hermit.kernel.execution.coordination.auto_park import AutoParkService
from hermit.kernel.execution.coordination.prioritizer import TaskPrioritizer
from hermit.kernel.ledger.journal.store import KernelStore


def _make_store() -> KernelStore:
    return KernelStore(":memory:")


def _setup_conversation(store: KernelStore, conversation_id: str) -> None:
    """Ensure conversation exists before creating tasks."""
    store.ensure_conversation(conversation_id, source_channel="test")


def test_auto_park_switches_focus_on_approval_block():
    """Two tasks in same conversation. Task A blocks → focus auto-switches to Task B."""
    store = _make_store()
    conversation_id = "conv-test-autopark"
    _setup_conversation(store, conversation_id)

    # Create two tasks (default status is "running")
    task_a = store.create_task(
        conversation_id=conversation_id,
        title="Task A (will block)",
        goal="Fix something",
        source_channel="test",
    )
    task_b = store.create_task(
        conversation_id=conversation_id,
        title="Task B (should get focus)",
        goal="Fix something else",
        source_channel="test",
    )

    # Set initial focus to task A
    store.set_conversation_focus(conversation_id, task_id=task_a.task_id, reason="initial")

    prioritizer = TaskPrioritizer(store)
    auto_park = AutoParkService(store, prioritizer)

    # Simulate task A blocking (suspended)
    store.update_task_status(task_a.task_id, "suspended")

    # Auto-park should switch focus to task B
    new_focus = auto_park.on_task_parked(conversation_id, task_a.task_id)
    assert new_focus == task_b.task_id, f"Expected focus on {task_b.task_id}, got {new_focus}"

    # Verify focus record via get_conversation
    conv = store.get_conversation(conversation_id)
    assert conv is not None
    assert conv.focus_task_id == task_b.task_id


def test_auto_unpark_restores_higher_priority_focus():
    """After approval, resumed task with blocked_bonus gets focus back."""
    store = _make_store()
    conversation_id = "conv-test-unpark"
    _setup_conversation(store, conversation_id)

    task_a = store.create_task(
        conversation_id=conversation_id,
        title="Task A (was blocked, should regain focus)",
        goal="Important fix",
        source_channel="test",
    )
    task_b = store.create_task(
        conversation_id=conversation_id,
        title="Task B (lower priority)",
        goal="Minor fix",
        source_channel="test",
    )

    # Give task A a "task.blocked" event so the prioritizer awards it blocked_bonus
    store.append_event(
        event_type="task.blocked",
        entity_type="task",
        entity_id=task_a.task_id,
        task_id=task_a.task_id,
        payload={"reason": "awaiting_approval"},
    )

    prioritizer = TaskPrioritizer(store)
    auto_park = AutoParkService(store, prioritizer)

    # Task A suspends → focus switches to B
    store.update_task_status(task_a.task_id, "suspended")
    new_focus = auto_park.on_task_parked(conversation_id, task_a.task_id)
    assert new_focus == task_b.task_id

    # Task A resumes (approval granted) — it now has blocked_bonus (+10)
    store.update_task_status(task_a.task_id, "running")
    auto_park.on_task_unparked(conversation_id, task_a.task_id)

    # Focus should return to task A (higher priority due to blocked_bonus)
    conv = store.get_conversation(conversation_id)
    assert conv is not None
    assert conv.focus_task_id == task_a.task_id


def test_auto_park_no_candidate_when_all_blocked():
    """When all tasks are suspended, on_task_parked returns None."""
    store = _make_store()
    conversation_id = "conv-test-nocandidate"
    _setup_conversation(store, conversation_id)

    task_a = store.create_task(
        conversation_id=conversation_id,
        title="Task A",
        goal="Fix A",
        source_channel="test",
    )
    task_b = store.create_task(
        conversation_id=conversation_id,
        title="Task B",
        goal="Fix B",
        source_channel="test",
    )

    # Suspend task B first
    store.update_task_status(task_b.task_id, "suspended")

    prioritizer = TaskPrioritizer(store)
    auto_park = AutoParkService(store, prioritizer)

    # Suspend task A — task B is already suspended, no candidate
    store.update_task_status(task_a.task_id, "suspended")
    new_focus = auto_park.on_task_parked(conversation_id, task_a.task_id)
    assert new_focus is None
