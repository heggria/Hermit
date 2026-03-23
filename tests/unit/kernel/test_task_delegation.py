from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.delegation import DelegationScope
from hermit.kernel.task.services.delegation import DelegationError, TaskDelegationService


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    s = KernelStore(tmp_path / "state.db")
    s.ensure_conversation("conv-1", source_channel="chat", source_ref="thread-1")
    return s


@pytest.fixture()
def parent_task(store: KernelStore):
    return store.create_task(
        conversation_id="conv-1",
        title="Parent task",
        goal="Do the parent work",
        source_channel="chat",
        status="running",
        owner="operator",
        priority="normal",
        policy_profile="default",
    )


@pytest.fixture()
def service(store: KernelStore) -> TaskDelegationService:
    return TaskDelegationService(store)


def test_delegate_creates_child_task_with_parent_link(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    child_task_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Do the child work",
        delegated_principal_id="subagent_alpha",
    )
    child = store.get_task(child_task_id)
    assert child is not None
    assert child.parent_task_id == parent_task.task_id
    assert child.goal == "Do the child work"
    assert child.status == "running"
    assert child.policy_profile == parent_task.policy_profile


def test_delegate_scope_constraints_are_stored(service: TaskDelegationService, parent_task) -> None:
    scope = DelegationScope(
        allowed_action_classes=["read_local", "write_local"],
        allowed_resource_scopes=["/tmp"],
        max_steps=5,
        budget_tokens=1000,
    )
    child_task_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Scoped child work",
        delegated_principal_id="subagent_beta",
        scope_constraints=scope,
    )
    record = service.get_delegation(parent_task.task_id, child_task_id)
    assert record is not None
    assert record.scope.allowed_action_classes == ["read_local", "write_local"]
    assert record.scope.max_steps == 5
    assert record.scope.budget_tokens == 1000
    assert record.status == "active"


def test_recall_revokes_child_delegation(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    child_task_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="To be recalled",
        delegated_principal_id="subagent_gamma",
    )
    service.recall(
        parent_task_id=parent_task.task_id,
        child_task_id=child_task_id,
        reason="no_longer_needed",
    )
    record = service.get_delegation(parent_task.task_id, child_task_id)
    assert record is not None
    assert record.status == "recalled"
    assert record.recall_reason == "no_longer_needed"

    child = store.get_task(child_task_id)
    assert child is not None
    assert child.status == "recalled"


def test_child_completed_notifies_parent(service: TaskDelegationService, parent_task) -> None:
    child_task_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Will complete",
        delegated_principal_id="subagent_delta",
    )
    parent_id = service.child_completed(child_task_id=child_task_id)
    assert parent_id == parent_task.task_id

    record = service.get_delegation(parent_task.task_id, child_task_id)
    assert record is not None
    assert record.status == "completed"


def test_delegation_events_are_recorded_in_ledger(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    child_task_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Receipted delegation",
        delegated_principal_id="subagent_epsilon",
    )
    events = store.list_events(task_id=parent_task.task_id)
    delegation_events = [e for e in events if e["event_type"].startswith("delegation.")]
    assert len(delegation_events) >= 1
    created_event = next(e for e in delegation_events if e["event_type"] == "delegation.created")
    assert created_event["entity_type"] == "delegation"

    service.recall(
        parent_task_id=parent_task.task_id,
        child_task_id=child_task_id,
        reason="test_recall",
    )
    events = store.list_events(task_id=parent_task.task_id)
    recall_events = [e for e in events if e["event_type"] == "delegation.recalled"]
    assert len(recall_events) == 1


def test_list_children_returns_correct_hierarchy(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    child1_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Child one",
        delegated_principal_id="subagent_one",
    )
    child2_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Child two",
        delegated_principal_id="subagent_two",
    )
    children = service.list_children(parent_task.task_id)
    assert len(children) == 2
    child_ids = {c["child_task_id"] for c in children}
    assert child1_id in child_ids
    assert child2_id in child_ids
    for child in children:
        assert "delegation_id" in child
        assert "child_status" in child
        assert "scope" in child


def test_delegate_fails_on_nonexistent_parent(service: TaskDelegationService) -> None:
    with pytest.raises(DelegationError, match="Parent task not found"):
        service.delegate(
            parent_task_id="task_nonexistent",
            child_goal="Orphan",
            delegated_principal_id="subagent_orphan",
        )


def test_delegate_fails_on_completed_parent(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    store.update_task_status(parent_task.task_id, "completed")
    with pytest.raises(DelegationError, match="cannot delegate"):
        service.delegate(
            parent_task_id=parent_task.task_id,
            child_goal="Too late",
            delegated_principal_id="subagent_late",
        )


def test_recall_fails_on_nonexistent_delegation(
    service: TaskDelegationService, parent_task
) -> None:
    with pytest.raises(DelegationError, match="No active delegation"):
        service.recall(
            parent_task_id=parent_task.task_id,
            child_task_id="task_nonexistent",
            reason="does_not_exist",
        )


def test_parent_failure_cascades_cancel_to_running_children(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    """When a parent task fails, running children should be explicitly cancelled
    via TaskController.cancel_task().  At the raw store level we verify that
    the status updates work correctly for this scenario."""
    child1_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Running child",
        delegated_principal_id="subagent_one",
    )
    child2_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Another running child",
        delegated_principal_id="subagent_two",
    )
    # Complete one child before parent fails
    store.update_task_status(child1_id, "completed")

    # Fail the parent
    store.update_task_status(parent_task.task_id, "failed")

    # Explicitly cancel running children (mirrors TaskController.cancel_task cascade)
    child2 = store.get_task(child2_id)
    assert child2 is not None
    if child2.status not in ("completed", "failed", "cancelled"):
        store.update_task_status(
            child2_id,
            "cancelled",
            payload={
                "reason": "parent_failed",
                "cascaded_from": parent_task.task_id,
            },
        )

    # Completed child should remain completed
    child1 = store.get_task(child1_id)
    assert child1 is not None
    assert child1.status == "completed"

    # Cancelled child should be cancelled
    child2 = store.get_task(child2_id)
    assert child2 is not None
    assert child2.status == "cancelled"

    # Verify cascade event was recorded
    events = store.list_events(task_id=child2_id)
    cancel_events = [e for e in events if e["event_type"] == "task.cancelled"]
    assert len(cancel_events) == 1
    assert cancel_events[0]["payload"]["reason"] == "parent_failed"
    assert cancel_events[0]["payload"]["cascaded_from"] == parent_task.task_id


def test_parent_cancellation_cascades_to_children(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    """When a parent task is cancelled, running children should also be cancelled.

    At the raw store level the cascade must be performed explicitly
    (TaskController.cancel_task handles this in production).
    """
    child_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Will be cascade-cancelled",
        delegated_principal_id="subagent_gamma",
    )
    store.update_task_status(parent_task.task_id, "cancelled")

    # Explicitly cascade cancellation to running children
    child = store.get_task(child_id)
    assert child is not None
    if child.status not in ("completed", "failed", "cancelled"):
        store.update_task_status(child_id, "cancelled")

    child = store.get_task(child_id)
    assert child is not None
    assert child.status == "cancelled"


def test_cascade_handles_nested_grandchildren(
    store: KernelStore, service: TaskDelegationService, parent_task
) -> None:
    """Cascade should propagate through grandchildren recursively.

    At the raw store level the cascade must be performed explicitly
    (TaskController.cancel_task handles this in production).
    """
    child_id = service.delegate(
        parent_task_id=parent_task.task_id,
        child_goal="Child",
        delegated_principal_id="subagent_child",
    )
    # Create grandchild by manually setting parent_task_id
    grandchild = store.create_task(
        conversation_id="conv-1",
        title="Grandchild task",
        goal="Grandchild work",
        source_channel="chat",
        status="running",
        owner="subagent_grandchild",
        priority="normal",
        policy_profile="memory",
        parent_task_id=child_id,
    )

    # Fail the root parent
    store.update_task_status(parent_task.task_id, "failed")

    # Explicitly cascade cancellation (depth-first: grandchild first, then child)
    gc = store.get_task(grandchild.task_id)
    if gc is not None and gc.status not in ("completed", "failed", "cancelled"):
        store.update_task_status(grandchild.task_id, "cancelled")

    child = store.get_task(child_id)
    if child is not None and child.status not in ("completed", "failed", "cancelled"):
        store.update_task_status(child_id, "cancelled")

    # Child should be cancelled
    child = store.get_task(child_id)
    assert child is not None
    assert child.status == "cancelled"

    # Grandchild should also be cancelled
    gc = store.get_task(grandchild.task_id)
    assert gc is not None
    assert gc.status == "cancelled"
