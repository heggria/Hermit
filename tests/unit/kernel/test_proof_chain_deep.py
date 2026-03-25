"""Deep tests for proof chain verification, proof summary generation, and tiered export.

Covers:
- Proof summary generation for single-step tasks
- Proof summary generation for multi-step DAG tasks
- Tiered export: summary (small), standard (medium), full (large)
- Summary tier size < 20 KB
- Full tier includes receipt_bundles, context_manifests, artifact_hash_index
"""

from __future__ import annotations

import json
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> KernelStore:
    return KernelStore(Path(":memory:"))


def _create_task(store: KernelStore, *, title: str = "test-task") -> str:
    conv = store.ensure_conversation("conv-1", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=title,
        goal="Unit test proof chain",
        source_channel="test",
        status="running",
        policy_profile="autonomous",
    )
    return task.task_id


def _create_step_and_receipt(
    store: KernelStore,
    task_id: str,
    *,
    action_type: str = "test_action",
    rollback_supported: bool = False,
    rollback_strategy: str | None = None,
    node_key: str | None = None,
    depends_on: list[str] | None = None,
) -> str:
    """Create a step, step attempt, decision, capability grant, and receipt. Return receipt_id."""
    step = store.create_step(
        task_id=task_id,
        kind="execute",
        status="running",
        node_key=node_key,
        depends_on=depends_on,
    )
    attempt = store.create_step_attempt(
        task_id=task_id,
        step_id=step.step_id,
        status="running",
    )
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
        input_refs=[],
        environment_ref=None,
        policy_result={"verdict": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary=f"Executed {action_type}",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        rollback_supported=rollback_supported,
        rollback_strategy=rollback_strategy,
    )
    return receipt.receipt_id


# ---------------------------------------------------------------------------
# Tests: Proof Summary
# ---------------------------------------------------------------------------


class TestProofSummaryGeneration:
    """Test proof summary generation for various task shapes."""

    def test_single_step_task_proof_summary(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id, action_type="write_local")

        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store)
        summary = proof_svc.build_proof_summary(task_id)

        assert summary["chain_verification"]["valid"] is True
        assert summary["receipt_count"] == 1
        assert summary["event_count"] > 0
        assert summary["head_hash"] is not None
        assert summary["task"]["task_id"] == task_id
        assert summary["latest_receipt"] is not None
        assert summary["latest_decision"] is not None
        assert summary["latest_capability_grant"] is not None

    def test_proof_summary_raises_for_unknown_task(self) -> None:
        store = _make_store()
        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store)

        try:
            proof_svc.build_proof_summary("nonexistent-task-id")
            raise AssertionError("Expected KeyError")
        except KeyError as exc:
            assert "not found" in str(exc).lower()

    def test_multi_step_task_proof_summary(self) -> None:
        store = _make_store()
        task_id = _create_task(store, title="multi-step-task")
        _create_step_and_receipt(store, task_id, action_type="bash")
        _create_step_and_receipt(store, task_id, action_type="write_local")
        _create_step_and_receipt(store, task_id, action_type="read_file")

        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store)
        summary = proof_svc.build_proof_summary(task_id)

        assert summary["chain_verification"]["valid"] is True
        assert summary["receipt_count"] == 3
        assert summary["event_count"] > 0
        # Projection counts
        proj = summary["projection"]
        assert proj["receipt_count"] == 3
        assert proj["step_count"] == 3
        assert proj["step_attempt_count"] == 3
        assert proj["decision_count"] == 3
        assert proj["capability_grant_count"] == 3

    def test_proof_summary_has_proof_coverage(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id)

        proof_svc = ProofService(store, ArtifactStore(store.db_path.parent / "artifacts"))
        summary = proof_svc.build_proof_summary(task_id)

        coverage = summary["proof_coverage"]
        assert "receipt_bundle_coverage" in coverage
        assert "signature_coverage" in coverage
        assert "available_modes" in coverage
        assert len(coverage["available_modes"]) == 4

    def test_proof_summary_proof_mode_without_signing(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id)

        proof_svc = ProofService(store, ArtifactStore(store.db_path.parent / "artifacts"))
        summary = proof_svc.build_proof_summary(task_id)

        # Without signing secret, mode should be hash_chained
        assert summary["proof_mode"] == "hash_chained"
        assert summary["strongest_export_mode"] == "hash_chained"

    def test_proof_summary_proof_mode_with_signing(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id)

        proof_svc = ProofService(
            store,
            ArtifactStore(store.db_path.parent / "artifacts"),
            signing_secret="test-secret-key",
        )
        summary = proof_svc.build_proof_summary(task_id)

        # With signing secret, strongest should include inclusion proof
        assert summary["strongest_export_mode"] == "signed_with_inclusion_proof"


# ---------------------------------------------------------------------------
# Tests: Chain Verification
# ---------------------------------------------------------------------------


class TestChainVerification:
    """Test verify_task_chain on well-formed and tampered chains."""

    def test_valid_chain_verification(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id)
        _create_step_and_receipt(store, task_id)

        proof_svc = ProofService(store, ArtifactStore(store.db_path.parent / "artifacts"))
        result = proof_svc.verify_task_chain(task_id)

        assert result["valid"] is True
        assert result["broken_at_event_id"] is None
        assert result["event_count"] > 0
        assert result["head_hash"] is not None

    def test_empty_chain_verification(self) -> None:
        store = _make_store()
        proof_svc = ProofService(store, ArtifactStore(store.db_path.parent / "artifacts"))
        result = proof_svc.verify_task_chain("nonexistent-task")

        assert result["valid"] is True
        assert result["event_count"] == 0
        assert result["head_hash"] is None


# ---------------------------------------------------------------------------
# Tests: Tiered Export
# ---------------------------------------------------------------------------


class TestTieredExport:
    """Test the three verbosity tiers: summary, standard, full."""

    def _setup_task(self) -> tuple[KernelStore, str, ProofService]:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id, action_type="bash")
        _create_step_and_receipt(store, task_id, action_type="write_local")
        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store)
        return store, task_id, proof_svc

    def test_summary_tier_content(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="summary")

        assert proof["detail"] == "summary"
        assert proof["task_id"] == task_id
        assert proof["chain_verification"]["valid"] is True
        assert proof["receipt_count"] == 2
        assert "chain_completeness" in proof

        # Summary tier should NOT have heavy payload sections
        assert "receipt_bundles" not in proof
        assert "context_manifests" not in proof
        assert "artifact_hash_index" not in proof
        assert "receipt_inclusion_proofs" not in proof
        # Summary tier should NOT have full governance records
        assert "capability_grants" not in proof
        assert "workspace_leases" not in proof

    def test_summary_tier_size_under_20kb(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="summary")

        serialized = json.dumps(proof, default=str)
        size_kb = len(serialized.encode("utf-8")) / 1024
        assert size_kb < 20, f"Summary tier is {size_kb:.1f} KB, expected < 20 KB"

    def test_summary_chain_completeness_is_condensed(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="summary")

        cc = proof["chain_completeness"]
        assert "total_receipts" in cc
        assert "complete_chains" in cc
        assert "incomplete_chains" in cc
        assert "completeness_percent" in cc
        # Summary tier should NOT contain per-receipt chain details
        assert "chains" not in cc

    def test_standard_tier_content(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="standard")

        assert proof["detail"] == "standard"
        # Standard includes governance records
        assert "capability_grants" in proof
        assert "workspace_leases" in proof
        assert "execution_contracts" in proof
        assert "evidence_cases" in proof
        assert "authorization_plans" in proof
        assert "reconciliations" in proof
        # But NOT the heavy full-tier sections
        assert "receipt_bundles" not in proof
        assert "context_manifests" not in proof
        assert "artifact_hash_index" not in proof

    def test_standard_chain_completeness_has_chains(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="standard")

        cc = proof["chain_completeness"]
        assert "chains" in cc
        assert len(cc["chains"]) == 2

    def test_full_tier_content(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="full")

        assert proof["detail"] == "full"
        # Full tier includes everything
        assert "receipt_bundles" in proof
        assert isinstance(proof["receipt_bundles"], list)
        assert len(proof["receipt_bundles"]) == 2
        assert "context_manifests" in proof
        assert isinstance(proof["context_manifests"], list)
        assert "artifact_hash_index" in proof
        assert isinstance(proof["artifact_hash_index"], dict)
        assert "receipt_inclusion_proofs" in proof
        assert "proof_bundle_ref" in proof
        # Also includes governance records
        assert "capability_grants" in proof
        assert "workspace_leases" in proof

    def test_full_tier_receipt_bundles_have_expected_fields(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="full")

        for bundle in proof["receipt_bundles"]:
            assert "schema" in bundle
            assert bundle["schema"] == "receipt.bundle/v1"
            assert "receipt_id" in bundle
            assert "proof_mode" in bundle
            assert "result_code" in bundle
            assert "context_manifest_ref" in bundle
            assert "task_event_head_hash" in bundle

    def test_full_tier_artifact_hash_index(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="full")

        index = proof["artifact_hash_index"]
        for _artifact_id, info in index.items():
            assert "kind" in info
            assert "content_hash" in info
            assert "uri" in info

    def test_full_tier_inclusion_proofs(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="full")

        proofs = proof["receipt_inclusion_proofs"]
        assert isinstance(proofs, dict)
        # Should have a proof path for each receipt
        for _receipt_id, siblings in proofs.items():
            assert isinstance(siblings, list)

    def test_full_tier_has_merkle_root(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="full")

        assert proof["receipt_merkle_root"] is not None
        assert len(proof["receipt_merkle_root"]) == 64  # SHA-256 hex

    def test_invalid_detail_falls_back_to_full(self) -> None:
        _, task_id, proof_svc = self._setup_task()
        proof = proof_svc.export_task_proof(task_id, detail="bogus")

        # Invalid detail should fall back to "full"
        assert proof["detail"] == "full"
        assert "receipt_bundles" in proof

    def test_signed_full_export(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        _create_step_and_receipt(store, task_id, action_type="bash")

        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store, signing_secret="my-signing-key")
        proof = proof_svc.export_task_proof(task_id, detail="full")

        assert proof["proof_mode"] == "signed_with_inclusion_proof"
        assert "signature" in proof
        sig = proof["signature"]
        assert sig["kind"] == "hmac-sha256"
        assert sig["key_id"] == "local-hmac"
        assert "payload_hash" in sig
        assert "signature" in sig

    def test_standard_tier_size_between_summary_and_full(self) -> None:
        store = _make_store()
        task_id = _create_task(store)
        for i in range(5):
            _create_step_and_receipt(store, task_id, action_type=f"action_{i}")

        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        proof_svc = ProofService(store, artifact_store)

        summary = proof_svc.export_task_proof(task_id, detail="summary")
        standard = proof_svc.export_task_proof(task_id, detail="standard")
        full = proof_svc.export_task_proof(task_id, detail="full")

        size_summary = len(json.dumps(summary, default=str).encode("utf-8"))
        size_standard = len(json.dumps(standard, default=str).encode("utf-8"))
        size_full = len(json.dumps(full, default=str).encode("utf-8"))

        assert size_summary <= size_standard <= size_full


# ---------------------------------------------------------------------------
# Tests: Proof Capabilities
# ---------------------------------------------------------------------------


class TestProofCapabilities:
    """Test proof_capabilities function."""

    def test_capabilities_without_secret(self) -> None:
        from hermit.kernel.verification.proofs.proofs import proof_capabilities

        caps = proof_capabilities()
        assert caps["baseline_verifiable_available"] is True
        assert caps["signing_configured"] is False
        assert caps["strong_signed_proofs_available"] is False

    def test_capabilities_with_secret(self) -> None:
        from hermit.kernel.verification.proofs.proofs import proof_capabilities

        caps = proof_capabilities(signing_secret="my-key")
        assert caps["signing_configured"] is True
        assert caps["strong_signed_proofs_available"] is True
