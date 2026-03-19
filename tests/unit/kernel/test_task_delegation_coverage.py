"""Tests for TaskDelegationService — covers missing lines 118, 159, 161, 193, 222."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
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


class TestRecallAlreadyRecalled:
    """Cover line 118: recall on a non-active delegation raises DelegationError."""

    def test_recall_on_recalled_delegation_raises(
        self, service: TaskDelegationService, parent_task
    ) -> None:
        child_id = service.delegate(
            parent_task_id=parent_task.task_id,
            child_goal="Will be recalled twice",
            delegated_principal_id="subagent_a",
        )
        service.recall(
            parent_task_id=parent_task.task_id,
            child_task_id=child_id,
            reason="first_recall",
        )
        with pytest.raises(DelegationError, match="cannot recall"):
            service.recall(
                parent_task_id=parent_task.task_id,
                child_task_id=child_id,
                reason="second_recall",
            )


class TestChildCompletedNoRecord:
    """Cover line 159: child_completed returns None when no delegation found."""

    def test_child_completed_returns_none_for_unknown_child(
        self, service: TaskDelegationService
    ) -> None:
        result = service.child_completed(child_task_id="task_nonexistent")
        assert result is None


class TestChildCompletedInactiveRecord:
    """Cover line 161: child_completed returns None when delegation is not active."""

    def test_child_completed_returns_none_for_recalled_delegation(
        self, service: TaskDelegationService, parent_task
    ) -> None:
        child_id = service.delegate(
            parent_task_id=parent_task.task_id,
            child_goal="Will be recalled then completed",
            delegated_principal_id="subagent_b",
        )
        service.recall(
            parent_task_id=parent_task.task_id,
            child_task_id=child_id,
            reason="recalled",
        )
        result = service.child_completed(child_task_id=child_id)
        assert result is None


class TestListChildrenWithMissingTask:
    """Cover line 193: list_children when child task doesn't exist in store."""

    def test_list_children_with_deleted_child_shows_unknown(
        self, store: KernelStore, service: TaskDelegationService, parent_task
    ) -> None:
        child_id = service.delegate(
            parent_task_id=parent_task.task_id,
            child_goal="Will have its task deleted",
            delegated_principal_id="subagent_c",
        )
        # Forcefully remove the child task from the store to simulate missing task
        store._get_conn().execute("DELETE FROM tasks WHERE task_id = ?", (child_id,))
        store._get_conn().commit()

        children = service.list_children(parent_task.task_id)
        assert len(children) == 1
        assert children[0]["child_status"] == "unknown"
        assert children[0]["child_goal"] == ""


class TestFindDelegationByChildNotFound:
    """Cover line 222: _find_delegation_by_child returns None when no match."""

    def test_find_delegation_by_child_returns_none(self, service: TaskDelegationService) -> None:
        result = service._find_delegation_by_child("nonexistent_child")
        assert result is None
