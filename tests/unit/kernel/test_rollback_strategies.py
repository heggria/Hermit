"""Tests for rollback strategies: record creation, dependency tracking, leaf-first planning.

Covers:
- Rollback record creation
- Recursive dependency tracking (step B depends on step A, rolling back A flags B)
- Leaf-first rollback planning
- Rollback for different action types (write_file -> file_restore, vcs_mutation -> git_revert)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.rollbacks.dependency_tracker import RollbackDependencyTracker
from hermit.kernel.verification.rollbacks.rollback_models import (
    DependentReceipt,
    RollbackPlan,
    RollbackPlanExecution,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> KernelStore:
    return KernelStore(Path(":memory:"))


def _create_task(store: KernelStore) -> str:
    conv = store.ensure_conversation("conv-1", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title="rollback-test-task",
        goal="Test rollback strategies",
        source_channel="test",
        status="running",
        policy_profile="autonomous",
    )
    return task.task_id


def _create_receipt(
    store: KernelStore,
    task_id: str,
    *,
    action_type: str = "test_action",
    rollback_supported: bool = False,
    rollback_strategy: str | None = None,
    input_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    rollback_artifact_refs: list[str] | None = None,
) -> str:
    """Create a step, step attempt, decision, grant, and receipt. Return receipt_id."""
    step = store.create_step(task_id=task_id, kind="execute", status="running")
    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
    decision = store.create_decision(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="policy_evaluation",
        verdict="allow",
        reason="Test action allowed",
        evidence_refs=[],
        action_type=action_type,
        decided_by="kernel",
    )
    grant = store.create_capability_grant(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="principal_hermit",
        issued_by_principal_id="principal_kernel",
        action_class=action_type,
        resource_scope=[],
        constraints=None,
        idempotency_key=None,
        expires_at=None,
    )
    receipt = store.create_receipt(
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type=action_type,
        input_refs=input_refs or [],
        environment_ref=None,
        policy_result={"verdict": "allow"},
        approval_ref=None,
        output_refs=output_refs or [],
        result_summary=f"Executed {action_type}",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        rollback_supported=rollback_supported,
        rollback_strategy=rollback_strategy,
        rollback_artifact_refs=rollback_artifact_refs or [],
    )
    return receipt.receipt_id


# ---------------------------------------------------------------------------
# Tests: Rollback Record Creation
# ---------------------------------------------------------------------------


class TestRollbackRecordCreation:
    """Test that rollback records are created correctly in the store."""

    def test_create_rollback_record(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        step = store.create_step(task_id=task_id, kind="rollback", status="running")
        attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
        rollback = store.create_rollback(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            receipt_ref="receipt-001",
            action_type="write_local",
            strategy="file_restore",
            status="executing",
            artifact_refs=["art-001", "art-002"],
        )

        assert rollback.rollback_id is not None
        assert rollback.task_id == task_id
        assert rollback.receipt_ref == "receipt-001"
        assert rollback.action_type == "write_local"
        assert rollback.strategy == "file_restore"
        assert rollback.status == "executing"
        assert rollback.artifact_refs == ["art-001", "art-002"]

    def test_update_rollback_status(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        step = store.create_step(task_id=task_id, kind="rollback", status="running")
        attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
        rollback = store.create_rollback(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            receipt_ref="receipt-001",
            action_type="write_local",
            strategy="file_restore",
            status="executing",
        )
        store.update_rollback(
            rollback.rollback_id, status="succeeded", result_summary="Restored file"
        )

        updated = store.get_rollback(rollback.rollback_id)
        assert updated is not None
        assert updated.status == "succeeded"
        assert updated.result_summary == "Restored file"

    def test_receipt_rollback_fields(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        receipt_id = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
        )

        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.rollback_supported is True
        assert receipt.rollback_strategy == "file_restore"
        assert receipt.rollback_status == "not_requested"

    def test_update_receipt_rollback_fields(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        receipt_id = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
        )

        store.update_receipt_rollback_fields(
            receipt_id,
            rollback_status="executing",
            rollback_ref="rb-001",
        )

        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.rollback_status == "executing"
        assert receipt.rollback_ref == "rb-001"


# ---------------------------------------------------------------------------
# Tests: Dependency Tracking
# ---------------------------------------------------------------------------


class TestDependencyTracking:
    """Test recursive dependency tracking via output_refs -> input_refs overlap."""

    def test_no_dependencies(self) -> None:
        """A receipt with no dependents produces a plan with one node."""
        store = _make_store()
        task_id = _create_task(store)
        receipt_id = _create_receipt(
            store, task_id, rollback_supported=True, rollback_strategy="file_restore"
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(receipt_id)

        assert plan.root_receipt_id == receipt_id
        assert len(plan.nodes) == 1
        assert plan.execution_order == [receipt_id]
        assert plan.cycle_detected is False

    def test_linear_dependency_chain(self) -> None:
        """A -> B -> C: rolling back A must first roll back C, then B."""
        store = _make_store()
        task_id = _create_task(store)

        # A produces art-1, B consumes art-1 and produces art-2, C consumes art-2
        receipt_a = _create_receipt(
            store,
            task_id,
            action_type="write_local",
            rollback_supported=True,
            rollback_strategy="file_restore",
            output_refs=["art-1"],
        )
        receipt_b = _create_receipt(
            store,
            task_id,
            action_type="patch_file",
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-1"],
            output_refs=["art-2"],
        )
        receipt_c = _create_receipt(
            store,
            task_id,
            action_type="read_file",
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-2"],
            output_refs=["art-3"],
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(receipt_a)

        assert len(plan.nodes) == 3
        assert plan.nodes[receipt_a].depth == 0
        assert plan.nodes[receipt_b].depth == 1
        assert plan.nodes[receipt_c].depth == 2

        # Leaf-first: C should come first, then B, then A
        assert plan.execution_order[0] == receipt_c
        assert plan.execution_order[-1] == receipt_a

    def test_diamond_dependency(self) -> None:
        """A -> B, A -> C, B -> D, C -> D: diamond graph."""
        store = _make_store()
        task_id = _create_task(store)

        receipt_a = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            output_refs=["art-a-out"],
        )
        receipt_b = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-a-out"],
            output_refs=["art-b-out"],
        )
        receipt_c = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-a-out"],
            output_refs=["art-c-out"],
        )
        receipt_d = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-b-out", "art-c-out"],
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(receipt_a)

        assert len(plan.nodes) == 4
        assert plan.nodes[receipt_a].depth == 0
        assert plan.nodes[receipt_b].depth == 1
        assert plan.nodes[receipt_c].depth == 1
        # D depends on both B and C; BFS will reach it from whichever was queued first
        assert plan.nodes[receipt_d].depth == 2

        # D should come before B and C in the execution order (leaf-first)
        d_idx = plan.execution_order.index(receipt_d)
        b_idx = plan.execution_order.index(receipt_b)
        c_idx = plan.execution_order.index(receipt_c)
        a_idx = plan.execution_order.index(receipt_a)
        assert d_idx < b_idx
        assert d_idx < c_idx
        assert a_idx == len(plan.execution_order) - 1

    def test_manual_review_for_unsupported_rollback(self) -> None:
        """Receipts without rollback support are flagged for manual review."""
        store = _make_store()
        task_id = _create_task(store)

        receipt_a = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            output_refs=["art-1"],
        )
        receipt_b = _create_receipt(
            store,
            task_id,
            rollback_supported=False,  # No rollback support
            input_refs=["art-1"],
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(receipt_a)

        assert receipt_b in plan.manual_review_ids
        assert plan.nodes[receipt_b].manual_review_required is True

    def test_raises_for_unknown_receipt(self) -> None:
        store = _make_store()
        tracker = RollbackDependencyTracker(store)

        try:
            tracker.build_plan("nonexistent-receipt")
            raise AssertionError("Expected KeyError")
        except KeyError as exc:
            assert "not found" in str(exc).lower()


# ---------------------------------------------------------------------------
# Tests: Leaf-First Rollback Planning
# ---------------------------------------------------------------------------


class TestLeafFirstRollbackPlanning:
    """Test that execution_order is leaf-first (reverse topological)."""

    def test_execution_order_is_depth_descending(self) -> None:
        store = _make_store()
        task_id = _create_task(store)

        receipt_a = _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            output_refs=["art-1"],
        )
        _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-1"],
            output_refs=["art-2"],
        )
        _create_receipt(
            store,
            task_id,
            rollback_supported=True,
            rollback_strategy="file_restore",
            input_refs=["art-2"],
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(receipt_a)

        depths = [plan.nodes[rid].depth for rid in plan.execution_order]
        # Should be in descending depth order (leaf-first)
        assert depths == sorted(depths, reverse=True)


# ---------------------------------------------------------------------------
# Tests: RollbackPlan Data Models
# ---------------------------------------------------------------------------


class TestRollbackPlanModels:
    """Test rollback data model defaults and construction."""

    def test_dependent_receipt_defaults(self) -> None:
        node = DependentReceipt(
            receipt_id="r1",
            depth=0,
            rollback_supported=True,
            rollback_strategy="file_restore",
        )
        assert node.manual_review_required is False
        assert node.dependent_ids == []

    def test_rollback_plan_defaults(self) -> None:
        plan = RollbackPlan(root_receipt_id="r1")
        assert plan.execution_order == []
        assert plan.nodes == {}
        assert plan.manual_review_ids == []
        assert plan.cycle_detected is False

    def test_rollback_plan_execution_defaults(self) -> None:
        plan = RollbackPlan(root_receipt_id="r1")
        execution = RollbackPlanExecution(plan=plan)
        assert execution.succeeded_ids == []
        assert execution.failed_ids == []
        assert execution.skipped_ids == []
        assert execution.results == {}
        assert execution.status == "pending"


# ---------------------------------------------------------------------------
# Tests: Plan Execution
# ---------------------------------------------------------------------------


class TestRollbackPlanExecution:
    """Test executing a rollback plan via the dependency tracker."""

    def test_execute_skips_manual_review(self) -> None:
        """Manual review receipts should be skipped in execution."""
        plan = RollbackPlan(root_receipt_id="r1")
        plan.nodes["r1"] = DependentReceipt(
            receipt_id="r1",
            depth=0,
            rollback_supported=False,
            rollback_strategy=None,
            manual_review_required=True,
        )
        plan.execution_order = ["r1"]
        plan.manual_review_ids = ["r1"]

        store = _make_store()
        tracker = RollbackDependencyTracker(store)
        mock_rollback_service = MagicMock()
        execution = tracker.execute_plan(plan, mock_rollback_service)

        assert execution.status == "skipped"
        assert "r1" in execution.skipped_ids
        assert execution.results["r1"]["status"] == "skipped"
        mock_rollback_service.execute.assert_not_called()

    def test_execute_records_failed_rollbacks(self) -> None:
        """Failed rollbacks should set partial status."""
        plan = RollbackPlan(root_receipt_id="r1")
        plan.nodes["r1"] = DependentReceipt(
            receipt_id="r1",
            depth=0,
            rollback_supported=True,
            rollback_strategy="file_restore",
        )
        plan.execution_order = ["r1"]

        store = _make_store()
        tracker = RollbackDependencyTracker(store)
        mock_rollback_service = MagicMock()
        mock_rollback_service.execute.side_effect = RuntimeError("rollback failed")
        execution = tracker.execute_plan(plan, mock_rollback_service)

        assert execution.status == "partial"
        assert "r1" in execution.failed_ids

    def test_execute_succeeded_status(self) -> None:
        """All succeeded -> status is 'succeeded'."""
        plan = RollbackPlan(root_receipt_id="r1")
        plan.nodes["r1"] = DependentReceipt(
            receipt_id="r1",
            depth=0,
            rollback_supported=True,
            rollback_strategy="file_restore",
        )
        plan.execution_order = ["r1"]

        store = _make_store()
        tracker = RollbackDependencyTracker(store)
        mock_rollback_service = MagicMock()
        mock_rollback_service.execute.return_value = {"status": "succeeded"}
        execution = tracker.execute_plan(plan, mock_rollback_service)

        assert execution.status == "succeeded"
        assert "r1" in execution.succeeded_ids
