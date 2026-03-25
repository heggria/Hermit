"""Tests for kernel/verification/rollbacks/rollbacks.py — coverage for missed lines.

Covers: RollbackService.execute error paths, _mark_unsupported,
_rollback_root_path edge cases, _apply_rollback for each strategy,
_prestate_payload errors, _acquire_workspace_lease.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.kernel.errors import RollbackError
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.rollbacks.rollbacks import RollbackService


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _seed_task(store: KernelStore) -> tuple[str, str, str]:
    """Create a task with step and attempt, return (task_id, step_id, attempt_id)."""
    store.ensure_conversation("conv-rb", source_channel="test")
    task = store.create_task(
        conversation_id="conv-rb",
        title="Rollback test",
        goal="Test rollback",
        source_channel="test",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    return task.task_id, step.step_id, attempt.step_attempt_id


class TestRollbackServiceExecute:
    def test_receipt_not_found_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        service = RollbackService(store)
        with pytest.raises(KeyError, match="not found"):
            service.execute("nonexistent-receipt")

    def test_unsupported_rollback_returns_unsupported(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task_id, step_id, attempt_id = _seed_task(store)
        decision = store.create_decision(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            decision_type="execution_authorization",
            verdict="allow",
            reason="test",
            evidence_refs=[],
            action_type="write_local",
        )
        grant = store.create_capability_grant(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=None,
            policy_ref="p1",
            issued_to_principal_id="user",
            issued_by_principal_id="kernel",
            workspace_lease_ref=None,
            action_class="write_local",
            resource_scope=[],
            constraints={},
            idempotency_key="idem1",
            expires_at=None,
        )
        receipt = store.create_receipt(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            action_type="write_local",
            input_refs=[],
            environment_ref=None,
            policy_result={"decision": "allow"},
            approval_ref=None,
            output_refs=[],
            result_summary="test",
            result_code="succeeded",
            decision_ref=decision.decision_id,
            capability_grant_ref=grant.grant_id,
            policy_ref="p1",
            rollback_supported=False,
        )
        service = RollbackService(store)
        result = service.execute(receipt.receipt_id)
        assert result["status"] == "unsupported"

    def test_empty_strategy_returns_unsupported(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        task_id, step_id, attempt_id = _seed_task(store)
        decision = store.create_decision(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            decision_type="execution_authorization",
            verdict="allow",
            reason="test",
            evidence_refs=[],
            action_type="write_local",
        )
        grant = store.create_capability_grant(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=None,
            policy_ref="p1",
            issued_to_principal_id="user",
            issued_by_principal_id="kernel",
            workspace_lease_ref=None,
            action_class="write_local",
            resource_scope=[],
            constraints={},
            idempotency_key="idem2",
            expires_at=None,
        )
        receipt = store.create_receipt(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=attempt_id,
            action_type="write_local",
            input_refs=[],
            environment_ref=None,
            policy_result={"decision": "allow"},
            approval_ref=None,
            output_refs=[],
            result_summary="test",
            result_code="succeeded",
            decision_ref=decision.decision_id,
            capability_grant_ref=grant.grant_id,
            policy_ref="p1",
            rollback_supported=True,
            rollback_strategy="",
        )
        service = RollbackService(store)
        result = service.execute(receipt.receipt_id)
        assert result["status"] == "unsupported"


class TestMarkUnsupported:
    def test_marks_unsupported_status(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(
            receipt_id="r1",
            rollback_status="pending",
        )
        service = RollbackService(store)
        result = service._mark_unsupported(receipt, "Not supported")
        assert result["status"] == "unsupported"
        assert result["result_summary"] == "Not supported"

    def test_already_unsupported_skips_update(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(
            receipt_id="r2",
            rollback_status="unsupported",
        )
        service = RollbackService(store)
        result = service._mark_unsupported(receipt, "Already unsupported")
        assert result["status"] == "unsupported"


class TestRollbackRootPath:
    def test_workspace_lease_path(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Create a workspace lease in the store
        task_id, _step_id, attempt_id = _seed_task(store)
        lease = store.create_workspace_lease(
            task_id=task_id,
            step_attempt_id=attempt_id,
            workspace_id="main",
            root_path=str(tmp_path / "workspace"),
            holder_principal_id="user",
            mode="mutable",
            resource_scope=[str(tmp_path / "workspace")],
            environment_ref=None,
            expires_at=None,
        )
        receipt = SimpleNamespace(
            workspace_lease_ref=lease.lease_id,
            action_type="write_local",
            rollback_artifact_refs=[],
        )
        service = RollbackService(store)
        result = service._rollback_root_path(receipt)
        assert result == str(tmp_path / "workspace")

    def test_no_lease_no_refs_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(
            workspace_lease_ref=None,
            action_type="read_local",
            rollback_artifact_refs=[],
        )
        service = RollbackService(store)
        assert service._rollback_root_path(receipt) is None


class TestPrestatePayload:
    def test_no_artifact_refs_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(rollback_artifact_refs=[])
        service = RollbackService(store)
        with pytest.raises(RollbackError):
            service._prestate_payload(receipt)

    def test_missing_artifact_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(rollback_artifact_refs=["nonexistent"])
        service = RollbackService(store)
        with pytest.raises(RollbackError):
            service._prestate_payload(receipt)


class TestApplyRollback:
    def test_file_restore_recreates_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(artifacts_dir)

        task_id, step_id, _attempt_id = _seed_task(store)
        target_file = tmp_path / "target.txt"
        prestate = {"path": str(target_file), "existed": True, "content": "original content"}
        uri, chash = artifact_store.store_json(prestate)
        artifact = store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind="prestate",
            uri=uri,
            content_hash=chash,
            producer="test",
            retention_class="task",
            trust_tier="observed",
        )

        receipt = SimpleNamespace(
            receipt_id="r-test",
            task_id=task_id,
            action_type="write_local",
            rollback_artifact_refs=[artifact.artifact_id],
            workspace_lease_ref=None,
            rollback_supported=True,
            rollback_strategy="file_restore",
            receipt_bundle_ref=None,
        )
        service = RollbackService(store, artifact_store=artifact_store)
        result = service._apply_rollback(receipt, "file_restore")
        assert "result_summary" in result
        assert target_file.read_text() == "original content"

    def test_file_restore_removes_new_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(artifacts_dir)

        task_id, step_id, _attempt_id = _seed_task(store)
        target_file = tmp_path / "new_file.txt"
        target_file.write_text("should be removed")
        prestate = {"path": str(target_file), "existed": False}
        uri, chash = artifact_store.store_json(prestate)
        artifact = store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind="prestate",
            uri=uri,
            content_hash=chash,
            producer="test",
            retention_class="task",
            trust_tier="observed",
        )

        receipt = SimpleNamespace(
            receipt_id="r-test2",
            task_id=task_id,
            action_type="write_local",
            rollback_artifact_refs=[artifact.artifact_id],
            workspace_lease_ref=None,
            rollback_supported=True,
            rollback_strategy="file_restore",
            receipt_bundle_ref=None,
        )
        service = RollbackService(store, artifact_store=artifact_store)
        service._apply_rollback(receipt, "file_restore")
        assert not target_file.exists()

    def test_unknown_strategy_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        receipt = SimpleNamespace(
            action_type="unknown_action",
            rollback_artifact_refs=[],
            workspace_lease_ref=None,
        )
        service = RollbackService(store)
        with pytest.raises(RollbackError):
            service._apply_rollback(receipt, "unknown_strategy")

    def test_memory_invalidate_strategy(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(artifacts_dir)

        task_id, step_id, _attempt_id = _seed_task(store)
        prestate = {"memory_ids": ["mem1", "mem2"], "belief_ids": ["b1"]}
        uri, chash = artifact_store.store_json(prestate)
        artifact = store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind="prestate",
            uri=uri,
            content_hash=chash,
            producer="test",
            retention_class="task",
            trust_tier="observed",
        )

        receipt = SimpleNamespace(
            receipt_id="r-mem",
            task_id=task_id,
            action_type="memory_write",
            rollback_artifact_refs=[artifact.artifact_id],
            workspace_lease_ref=None,
            rollback_supported=True,
            rollback_strategy="supersede_or_invalidate",
            receipt_bundle_ref=None,
        )
        service = RollbackService(store, artifact_store=artifact_store)
        result = service._apply_rollback(receipt, "supersede_or_invalidate")
        assert "result_summary" in result
