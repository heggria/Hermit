"""Tests for RollbackDependencyTracker — covers missing lines 69, 130, 156."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ReceiptRecord
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.rollbacks.dependency_tracker import (
    RollbackDependencyTracker,
)


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _start_task(store: KernelStore, tmp_path: Path) -> object:
    controller = TaskController(store)
    return controller.start_task(
        conversation_id="conv-rb",
        goal="rollback test",
        source_channel="test",
        kind="respond",
        workspace_root=str(tmp_path),
    )


def _create_receipt(
    store: KernelStore,
    *,
    task_id: str,
    step_id: str,
    step_attempt_id: str,
    input_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    rollback_supported: bool = True,
    rollback_strategy: str | None = "file_restore",
) -> str:
    artifact = store.create_artifact(
        task_id=task_id,
        step_id=step_id,
        kind="output",
        uri="mem://test",
        content_hash="abc123",
        producer="test",
        retention_class="ephemeral",
        trust_tier="observed",
    )
    receipt = store.create_receipt(
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        action_type="write_local",
        input_refs=input_refs or [],
        output_refs=output_refs or [artifact.artifact_id],
        environment_ref=None,
        policy_result={"verdict": "allow"},
        approval_ref=None,
        result_summary="test receipt",
        result_code="succeeded",
        rollback_supported=rollback_supported,
        rollback_strategy=rollback_strategy,
        rollback_artifact_refs=[artifact.artifact_id] if rollback_supported else [],
    )
    return receipt.receipt_id


class TestGetReceiptReturnsNone:
    """Cover line 69: _get_receipt returns None when receipt not in list."""

    def test_get_receipt_not_found(self) -> None:
        result = RollbackDependencyTracker._get_receipt("nonexistent", [])
        assert result is None

    def test_get_receipt_not_found_in_populated_list(self) -> None:
        receipts = [
            MagicMock(spec=ReceiptRecord, receipt_id="r1"),
            MagicMock(spec=ReceiptRecord, receipt_id="r2"),
        ]
        result = RollbackDependencyTracker._get_receipt("r3", receipts)
        assert result is None


class TestExecutePlanPartialFailure:
    """Cover line 130: rollback returns non-succeeded status."""

    def test_non_succeeded_status_goes_to_failed(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        ctx = _start_task(store, tmp_path)
        rid = _create_receipt(
            store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
        )

        tracker = RollbackDependencyTracker(store)
        plan = tracker.build_plan(rid)

        mock_service = MagicMock()
        mock_service.execute.return_value = {"status": "failed", "error": "rollback error"}

        execution = tracker.execute_plan(plan, mock_service)

        assert execution.status == "partial"
        assert rid in execution.failed_ids
        assert execution.results[rid]["status"] == "failed"


class TestGetReceiptStaticMethod:
    """Cover line 156: _get_receipt returns None for missing receipt_id."""

    def test_returns_none_for_empty_list(self) -> None:
        assert RollbackDependencyTracker._get_receipt("any_id", []) is None
