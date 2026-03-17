"""E2E: Signed proofs — HMAC-SHA256 receipt bundles and proof exports.

Exercises the signed proof path: configure signing secret → execute governed
actions → verify receipt bundles are signed → export task proof with inclusion
proofs → verify signature metadata.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService


def test_signed_receipt_bundle_with_hmac(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Receipt bundles include HMAC-SHA256 signatures when signing secret is configured."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-signed-receipt",
        goal="Write file and verify signed receipt",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    result = executor.execute(
        ctx, "write_file", {"path": "signed.txt", "content": "signed content\n"}
    )
    assert result.receipt_id is not None

    # Create ProofService with signing secret and export (export triggers signing)
    proof_service = ProofService(
        store, artifacts, signing_secret="e2e-test-secret", signing_key_id="e2e-key"
    )
    export = proof_service.export_task_proof(ctx.task_id)

    # Verify the export has signed proof mode
    assert export["status"] == "verified"
    assert export["chain_verification"]["valid"] is True

    # Verify signature metadata in the export
    assert export["signature"] is not None
    assert export["signature"]["kind"] == "hmac-sha256"
    assert export["signature"]["key_id"] == "e2e-key"
    assert export["signature"]["payload_hash"] is not None
    assert export["signature"]["signature"] is not None

    # Receipt should be updated with signed proof fields after export
    receipt = store.get_receipt(result.receipt_id)
    assert receipt is not None
    assert receipt.receipt_bundle_ref is not None
    assert receipt.signer_ref == "e2e-key"


def test_export_signed_proof_with_merkle_inclusion(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Exported task proof includes Merkle inclusion proofs and top-level signature."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-signed-export",
        goal="Write files and export signed proof",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Create multiple receipts for Merkle tree
    receipt_ids = []
    for i in range(3):
        result = executor.execute(
            ctx, "write_file", {"path": f"doc{i}.txt", "content": f"doc {i}\n"}
        )
        assert result.receipt_id is not None
        receipt_ids.append(result.receipt_id)

    # Export with signing
    proof_service = ProofService(
        store, artifacts, signing_secret="e2e-export-secret", signing_key_id="export-key"
    )
    export = proof_service.export_task_proof(ctx.task_id)

    # Verify export structure
    assert export["status"] == "verified"
    assert export["chain_verification"]["valid"] is True
    assert export["proof_bundle_ref"] is not None
    assert export["proof_mode"] == "signed_with_inclusion_proof"

    # Verify Merkle root and inclusion proofs
    assert export["receipt_merkle_root"] is not None
    assert len(export["receipt_inclusion_proofs"]) == 3

    # Verify top-level proof signature
    assert export["signature"]["kind"] == "hmac-sha256"
    assert export["signature"]["key_id"] == "export-key"

    # All receipts should be upgraded to signed_with_inclusion_proof
    for rid in receipt_ids:
        r = store.get_receipt(rid)
        assert r is not None
        assert r.proof_mode == "signed_with_inclusion_proof"
        assert r.verifiability == "strong_signed_with_inclusion_proof"


def test_unsigned_proof_uses_hash_chained_mode(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Without signing secret, proofs use hash_chained mode (baseline verifiable)."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-unsigned",
        goal="Write and verify unsigned proof",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    executor.execute(ctx, "write_file", {"path": "plain.txt", "content": "plain\n"})

    # No signing secret
    proof_service = ProofService(store, artifacts)
    export = proof_service.export_task_proof(ctx.task_id)

    assert export["status"] == "verified"
    assert export["chain_verification"]["valid"] is True
    # Without signing, mode should be hash_chained
    assert export["proof_mode"] in ("hash_chained", "hash_only")
    assert "signature" not in export or export.get("signature") is None


def test_proof_summary_reports_signing_coverage(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Proof summary includes signature coverage metrics for signed receipts."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-proof-coverage",
        goal="Write files and check proof summary",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    for i in range(2):
        executor.execute(ctx, "write_file", {"path": f"cov{i}.txt", "content": f"cov {i}\n"})

    proof_service = ProofService(
        store, artifacts, signing_secret="coverage-secret", signing_key_id="cov-key"
    )

    # Build summary
    summary = proof_service.build_proof_summary(ctx.task_id)
    assert summary["chain_verification"]["valid"] is True
    assert summary["receipt_count"] == 2
    assert summary["missing_receipt_bundle_count"] == 0

    # Export proof — produces signed export with inclusion proofs
    export = proof_service.export_task_proof(ctx.task_id)
    assert export["status"] == "verified"
    assert export["signature"] is not None
    assert export["signature"]["key_id"] == "cov-key"
    assert export["receipt_merkle_root"] is not None
