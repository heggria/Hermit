"""Unit tests for proof chain completeness and bundle validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService


class TestContextManifestExtension:
    """Test that context manifest includes contract/evidence/auth/reconciliation refs."""

    def test_context_manifest_includes_contract_loop_refs(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-proof",
            goal="test proof chain",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write a file",
            status="executing",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
            evidence_case_ref="ev-case-1",
            authorization_plan_ref="auth-plan-1",
            reconciliation_ref="recon-1",
        )

        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        proof_service = ProofService(store, artifacts)
        manifest = proof_service._build_context_manifest_payload(receipt)

        assert manifest["contract_ref"] == contract.contract_id
        assert manifest["evidence_case_ref"] == "ev-case-1"
        assert manifest["authorization_plan_ref"] == "auth-plan-1"
        assert manifest["reconciliation_ref"] == "recon-1"
        assert manifest["schema"] == "context.manifest/v1"


class TestChainCompleteness:
    """Test chain_completeness reporting in proof export."""

    def test_chain_completeness_reports_gaps(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-chain",
            goal="test chain gaps",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        proof_service = ProofService(store, artifacts)
        completeness = proof_service._chain_completeness([receipt], [], [], [], [])

        assert completeness["total_receipts"] == 1
        assert completeness["incomplete_chains"] == 1
        assert completeness["complete_chains"] == 0
        assert completeness["completeness_percent"] == 0.0
        chain = completeness["chains"][0]
        assert "contract" in chain["gaps"]
        assert "evidence_case" in chain["gaps"]
        assert "authorization_plan" in chain["gaps"]
        assert "reconciliation" in chain["gaps"]

    def test_chain_completeness_with_full_chain(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-full-chain",
            goal="test full chain",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write a file",
            status="satisfied",
        )
        evidence_case = store.create_evidence_case(
            task_id=ctx.task_id,
            subject_kind="contract",
            subject_ref=contract.contract_id,
            support_refs=[],
            contradiction_refs=[],
            sufficiency_score=1.0,
            status="sufficient",
        )
        authorization_plan = store.create_authorization_plan(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref=contract.contract_id,
            policy_profile_ref="strict-task-first-v2",
            status="authorized",
        )
        reconciliation = store.create_reconciliation(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref=contract.contract_id,
            receipt_refs=["r-1"],
            result_class="satisfied",
            intended_effect_summary="write file",
            authorized_effect_summary="write file",
            observed_effect_summary="wrote file",
            receipted_effect_summary="wrote file",
        )

        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
            evidence_case_ref=evidence_case.evidence_case_id,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
            reconciliation_ref=reconciliation.reconciliation_id,
        )

        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        proof_service = ProofService(store, artifacts)
        completeness = proof_service._chain_completeness(
            [receipt],
            [contract],
            [evidence_case],
            [authorization_plan],
            [reconciliation],
        )

        assert completeness["total_receipts"] == 1
        assert completeness["complete_chains"] == 1
        assert completeness["incomplete_chains"] == 0
        assert completeness["completeness_percent"] == 100.0


class TestBundleArtifactValidation:
    """Test that broken artifact hashes emit proof.validation_warning events."""

    def test_missing_artifact_emits_warning_event(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-validate",
            goal="test validation",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=["nonexistent-artifact-id"],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        proof_service = ProofService(store, artifacts)
        proof_service._validate_bundle_artifact_hashes(
            receipt, {"input_hashes": {"nonexistent-artifact-id": "abc123"}}
        )

        events = store.list_events(task_id=ctx.task_id, limit=100)
        warning_events = [e for e in events if e["event_type"] == "proof.validation_warning"]
        assert len(warning_events) >= 1
        assert warning_events[0]["payload"]["warning"] == "referenced_artifact_missing"

    def test_artifact_hash_mismatch_emits_warning(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-mismatch",
            goal="test hash mismatch",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        art_uri, art_hash = artifacts.store_json({"data": "original"})
        artifact = store.create_artifact(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="test",
            uri=art_uri,
            content_hash=art_hash,
            producer="test",
            retention_class="audit",
            trust_tier="observed",
        )
        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[artifact.artifact_id],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )

        proof_service = ProofService(store, artifacts)
        proof_service._validate_bundle_artifact_hashes(
            receipt, {"input_hashes": {artifact.artifact_id: "wrong-hash-value"}}
        )

        events = store.list_events(task_id=ctx.task_id, limit=100)
        mismatch_events = [
            e
            for e in events
            if e["event_type"] == "proof.validation_warning"
            and e["payload"].get("warning") == "artifact_hash_mismatch"
        ]
        assert len(mismatch_events) == 1
        assert mismatch_events[0]["payload"]["expected_hash"] == "wrong-hash-value"
        assert mismatch_events[0]["payload"]["actual_hash"] == art_hash

    def test_rollback_artifact_refs_validated(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-rollback-validate",
            goal="test rollback validation",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )

        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )
        mock_receipt = SimpleNamespace(
            **{**receipt.__dict__, "rollback_artifact_refs": ["nonexistent-rollback-ref"]}
        )

        proof_service = ProofService(store, artifacts)
        proof_service._validate_bundle_artifact_hashes(
            mock_receipt,
            {"rollback_artifact_hashes": {"nonexistent-rollback-ref": "abc123"}},
        )

        events = store.list_events(task_id=ctx.task_id, limit=100)
        warning_events = [
            e
            for e in events
            if e["event_type"] == "proof.validation_warning"
            and e["payload"].get("warning") == "referenced_artifact_missing"
        ]
        assert any(
            e["payload"].get("artifact_ref") == "nonexistent-rollback-ref" for e in warning_events
        )


class TestProofModes:
    """Test proof mode determination methods."""

    def test_summary_proof_mode_hash_only_for_empty(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ProofService(store, artifacts)
        assert service._summary_proof_mode([]) == "hash_only"

    def test_export_proof_mode_signed_with_inclusion(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-mode",
            goal="test mode",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        receipt = store.create_receipt(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            receipt_class="write_local",
            action_type="write_local",
            result_code="succeeded",
            input_refs=[],
            output_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            result_summary="wrote file",
        )
        service = ProofService(store, artifacts, signing_secret="test-secret")
        mode = service._export_proof_mode([receipt], inclusion_enabled=True)
        assert mode == "signed_with_inclusion_proof"


class TestMerkleTree:
    """Test _receipt_inclusion_proofs merkle tree construction."""

    def test_empty_bundles_returns_no_proofs(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ProofService(store, artifacts)
        result = service._receipt_inclusion_proofs([])
        assert result["root"] is None
        assert result["proofs"] == {}

    def test_two_bundles_have_consistent_root(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ProofService(store, artifacts)
        bundles = [
            {"receipt_id": "r-1", "data": "a"},
            {"receipt_id": "r-2", "data": "b"},
        ]
        result = service._receipt_inclusion_proofs(bundles)
        assert result["root"] is not None
        assert "r-1" in result["proofs"]
        assert "r-2" in result["proofs"]
        assert len(result["proofs"]["r-1"]) == 1
        assert result["proofs"]["r-1"][0]["position"] == "right"
        assert result["proofs"]["r-2"][0]["position"] == "left"

    def test_three_bundles_odd_padding(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        service = ProofService(store, artifacts)
        bundles = [{"receipt_id": f"r-{i}", "data": f"d-{i}"} for i in range(3)]
        result = service._receipt_inclusion_proofs(bundles)
        assert result["root"] is not None
        for i in range(3):
            assert f"r-{i}" in result["proofs"]


class TestMemoryInvalidation:
    """Test memory invalidation when reconciliation result is violated."""

    def test_violated_reconciliation_invalidates_learned_memory(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-mem-inv",
            goal="test memory invalidation",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        reconciliation = store.create_reconciliation(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref="contract-1",
            receipt_refs=["receipt-1"],
            result_class="satisfied",
            intended_effect_summary="write file",
            authorized_effect_summary="write file",
            observed_effect_summary="wrote file",
            receipted_effect_summary="wrote file",
        )
        from hermit.kernel.context.memory.knowledge import MemoryRecordService

        memory_service = MemoryRecordService(store)
        memory = store.create_memory_record(
            task_id=ctx.task_id,
            conversation_id="chat-mem-inv",
            category="project_fact",
            claim_text="test memory",
            structured_assertion={},
            scope_kind="workspace",
            scope_ref=str(tmp_path),
            promotion_reason="verified",
            retention_class="durable",
            status="active",
            confidence=0.9,
            trust_tier="durable",
            evidence_refs=[],
            supersedes=[],
            supersedes_memory_ids=[],
            source_belief_ref="belief-1",
            memory_kind="durable_fact",
            learned_from_reconciliation_ref=reconciliation.reconciliation_id,
        )

        invalidated = memory_service.invalidate_by_reconciliation(
            reconciliation.reconciliation_id, "violated"
        )
        assert memory.memory_id in invalidated

    def test_non_violated_reconciliation_does_not_invalidate(self, tmp_path: Path) -> None:
        from hermit.kernel.context.memory.knowledge import MemoryRecordService

        store = KernelStore(tmp_path / "kernel" / "state.db")
        memory_service = MemoryRecordService(store)
        result = memory_service.invalidate_by_reconciliation("recon-1", "satisfied")
        assert result == []
