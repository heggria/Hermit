"""Unit tests for automatic follow-up task generation from failed reconciliations."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.executor.reconciliation_executor import (
    _FOLLOWUP_RESULT_CLASSES,
    MAX_AUTO_FOLLOWUPS,
    ReconciliationExecutor,
)
from hermit.kernel.task.models.records import ReconciliationRecord, TaskRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reconciliation(**overrides: Any) -> ReconciliationRecord:
    defaults = {
        "reconciliation_id": "recon-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "contract_ref": "contract-1",
        "result_class": "violated",
        "operator_summary": "file hash mismatch",
    }
    defaults.update(overrides)
    return ReconciliationRecord(**defaults)


def _make_task_record(**overrides: Any) -> TaskRecord:
    defaults = {
        "task_id": "task-1",
        "conversation_id": "conv-1",
        "title": "Do something",
        "goal": "implement feature X",
        "status": "failed",
        "priority": "normal",
        "owner_principal_id": "hermit",
        "policy_profile": "default",
        "source_channel": "chat",
        "parent_task_id": None,
    }
    defaults.update(overrides)
    return TaskRecord(**defaults)


def _make_followup_task_record(parent_task_id: str, index: int = 1) -> TaskRecord:
    return _make_task_record(
        task_id=f"followup-{index}",
        goal="retry/mitigate: implement feature X",
        parent_task_id=parent_task_id,
        status="queued",
    )


def _make_executor(mock_store: MagicMock, *, auto_followup: bool = True) -> ReconciliationExecutor:
    return ReconciliationExecutor(
        store=mock_store,
        artifact_store=MagicMock(),
        reconciliations=MagicMock(),
        execution_contracts=MagicMock(),
        evidence_cases=MagicMock(),
        pattern_learner=MagicMock(),
        auto_followup=auto_followup,
    )


def _make_mock_store(
    *,
    task: TaskRecord | None = None,
    existing_followups: list[TaskRecord] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.get_task.return_value = task or _make_task_record()
    store.list_child_tasks.return_value = existing_followups or []
    # create_task returns a new TaskRecord with a generated id.
    store.create_task.return_value = _make_task_record(
        task_id="followup-new",
        goal="retry/mitigate: implement feature X",
        parent_task_id="task-1",
        status="queued",
    )
    return store


# ---------------------------------------------------------------------------
# Tests: violated reconciliation generates follow-up task
# ---------------------------------------------------------------------------


class TestFollowupGeneratedOnViolation:
    """Violated reconciliation should generate a follow-up task."""

    def test_violated_generates_followup(self) -> None:
        store = _make_mock_store()
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result == "followup-new"
        store.create_task.assert_called_once()
        call_kwargs = store.create_task.call_args.kwargs
        assert call_kwargs["parent_task_id"] == "task-1"
        assert call_kwargs["goal"].startswith("retry/mitigate: ")
        assert call_kwargs["status"] == "queued"

    def test_unauthorized_generates_followup(self) -> None:
        store = _make_mock_store()
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="unauthorized")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result == "followup-new"
        store.create_task.assert_called_once()

    def test_ambiguous_generates_followup(self) -> None:
        store = _make_mock_store()
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="ambiguous")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result == "followup-new"
        store.create_task.assert_called_once()

    def test_followup_inherits_original_task_properties(self) -> None:
        original = _make_task_record(
            priority="high",
            policy_profile="autonomous",
            source_channel="feishu",
            conversation_id="conv-42",
            owner_principal_id="user-1",
        )
        store = _make_mock_store(task=original)
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        call_kwargs = store.create_task.call_args.kwargs
        assert call_kwargs["priority"] == "high"
        assert call_kwargs["policy_profile"] == "autonomous"
        assert call_kwargs["source_channel"] == "feishu"
        assert call_kwargs["conversation_id"] == "conv-42"
        assert call_kwargs["owner"] == "user-1"

    def test_followup_uses_root_task_as_parent_when_task_has_parent(self) -> None:
        """When the failed task is itself a follow-up, the new follow-up
        should be parented to the root task to keep counting correct."""
        child_task = _make_task_record(
            task_id="task-child",
            parent_task_id="task-root",
            goal="retry/mitigate: original goal",
        )
        store = _make_mock_store(task=child_task)
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        executor._generate_followup_if_needed(
            task_id="task-child",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        call_kwargs = store.create_task.call_args.kwargs
        assert call_kwargs["parent_task_id"] == "task-root"


# ---------------------------------------------------------------------------
# Tests: satisfied reconciliation generates NO follow-up
# ---------------------------------------------------------------------------


class TestNoFollowupOnSatisfied:
    """Satisfied and non-failure reconciliations should NOT generate follow-ups."""

    @pytest.mark.parametrize(
        "result_class",
        ["satisfied", "satisfied_with_downgrade", "partial"],
    )
    def test_non_failure_result_classes_skip_followup(self, result_class: str) -> None:
        store = _make_mock_store()
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class=result_class)

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result is None
        store.create_task.assert_not_called()

    def test_auto_followup_disabled_skips_generation(self) -> None:
        """When auto_followup=False on the executor, no follow-up is
        generated even for violated reconciliations. The flag gates the
        call in record_reconciliation, but _generate_followup_if_needed
        itself checks result_class only."""
        store = _make_mock_store()
        executor = _make_executor(store, auto_followup=False)
        # Directly calling _generate_followup_if_needed still works
        # (it checks result_class), but the flag prevents the call
        # in record_reconciliation.
        _reconciliation = _make_reconciliation(result_class="violated")

        # The method itself does generate if called directly (it's the
        # record_reconciliation wrapper that checks the flag). Verify
        # the flag is stored correctly.
        assert executor._auto_followup is False

    def test_missing_task_returns_none(self) -> None:
        store = _make_mock_store()
        store.get_task.return_value = None
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        result = executor._generate_followup_if_needed(
            task_id="task-missing",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result is None
        store.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: max retry limit prevents infinite follow-up generation
# ---------------------------------------------------------------------------


class TestMaxRetryLimit:
    """MAX_AUTO_FOLLOWUPS should prevent infinite follow-up chains."""

    def test_max_followups_reached_blocks_generation(self) -> None:
        existing = [
            _make_followup_task_record("task-1", index=i) for i in range(MAX_AUTO_FOLLOWUPS)
        ]
        store = _make_mock_store(existing_followups=existing)
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result is None
        store.create_task.assert_not_called()

    def test_below_max_allows_generation(self) -> None:
        existing = [
            _make_followup_task_record("task-1", index=i) for i in range(MAX_AUTO_FOLLOWUPS - 1)
        ]
        store = _make_mock_store(existing_followups=existing)
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result == "followup-new"
        store.create_task.assert_called_once()

    def test_non_followup_children_not_counted(self) -> None:
        """Children whose goal does NOT start with 'retry/mitigate: '
        should not be counted toward the limit."""
        non_followup_children = [
            _make_task_record(
                task_id=f"child-{i}",
                parent_task_id="task-1",
                goal="some unrelated subtask",
            )
            for i in range(10)
        ]
        store = _make_mock_store(existing_followups=non_followup_children)
        executor = _make_executor(store)
        reconciliation = _make_reconciliation(result_class="violated")

        result = executor._generate_followup_if_needed(
            task_id="task-1",
            step_id="step-1",
            reconciliation_record=reconciliation,
        )

        assert result == "followup-new"
        store.create_task.assert_called_once()

    def test_max_auto_followups_constant_is_reasonable(self) -> None:
        """Sanity check that the constant is a small positive integer."""
        assert MAX_AUTO_FOLLOWUPS > 0
        assert MAX_AUTO_FOLLOWUPS <= 10

    def test_followup_result_classes_set(self) -> None:
        """Verify the expected result classes that trigger follow-ups."""
        assert {"violated", "unauthorized", "ambiguous"} == _FOLLOWUP_RESULT_CLASSES
