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
