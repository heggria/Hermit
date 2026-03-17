"""End-to-end scenario test for the governed execution lifecycle.

Exercises the complete path:
  Task → Policy → Approval → CapabilityGrant → Execution → Receipt → Proof
"""

from __future__ import annotations

import json
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService


def test_governed_write_lifecycle(
    kernel_runtime: tuple[
        KernelStore,
        ArtifactStore,
        TaskController,
        ToolExecutor,
        TaskExecutionContext,
        Path,
    ],
) -> None:
    store, artifacts, _controller, executor, ctx, workspace = kernel_runtime

    # ---- 1. Task exists after start_task ----
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "running"

    # ---- 2. Execute a write_local action (preview-required, no approval needed) ----
    result = executor.execute(
        ctx,
        "write_file",
        {"path": "hello.txt", "content": "governed write\n"},
    )

    # write_local with supports_preview=True gets preview_required verdict,
    # which proceeds without explicit approval
    assert result.blocked is False
    assert result.denied is False
    assert result.receipt_id is not None

    # ---- 3. Verify file was written ----
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "governed write\n"

    # ---- 4. Verify receipt was issued ----
    receipts = store.list_receipts(task_id=ctx.task_id, limit=10)
    assert len(receipts) >= 1
    receipt = receipts[0]
    assert receipt.receipt_id == result.receipt_id
    assert receipt.action_type == "write_local"
    assert receipt.result_code == "succeeded"
    assert len(receipt.input_refs) == 1
    assert len(receipt.output_refs) == 1

    # ---- 5. Verify decision was recorded ----
    assert result.decision_id is not None
    decision = store.get_decision(result.decision_id)
    assert decision is not None
    assert decision.verdict == "allow"

    # ---- 6. Verify capability grant was issued and consumed ----
    assert result.capability_grant_id is not None
    grant = store.get_capability_grant(result.capability_grant_id)
    assert grant is not None
    assert grant.status == "consumed"
    assert grant.action_class == "write_local"

    # ---- 7. Verify execution contract was created ----
    contracts = store.list_execution_contracts(task_id=ctx.task_id, limit=10)
    assert len(contracts) >= 1
    assert receipt.contract_ref is not None

    # ---- 8. Verify evidence case was created ----
    evidence_cases = store.list_evidence_cases(task_id=ctx.task_id, limit=10)
    assert len(evidence_cases) >= 1

    # ---- 9. Verify authorization plan was created ----
    auth_plans = store.list_authorization_plans(task_id=ctx.task_id, limit=10)
    assert len(auth_plans) >= 1

    # ---- 10. Verify receipt bundle (proof chain) ----
    assert receipt.receipt_bundle_ref is not None
    bundle_artifact = store.get_artifact(receipt.receipt_bundle_ref)
    assert bundle_artifact is not None
    assert bundle_artifact.kind == "receipt.bundle"
    bundle_payload = json.loads(artifacts.read_text(bundle_artifact.uri))
    assert bundle_payload["receipt_id"] == receipt.receipt_id
    assert bundle_payload["context_manifest_ref"]

    # ---- 11. Verify proof chain integrity ----
    proof_service = ProofService(store, artifacts)
    chain_result = proof_service.verify_task_chain(ctx.task_id)
    assert chain_result["valid"] is True

    # ---- 12. Verify reconciliation was recorded ----
    reconciliations = store.list_reconciliations(task_id=ctx.task_id, limit=10)
    assert len(reconciliations) >= 1
    assert reconciliations[0].result_class in {"satisfied", "partial"}

    # ---- 13. Verify event audit trail ----
    events = store.list_events(task_id=ctx.task_id)
    event_types = {event["event_type"] for event in events}
    assert "receipt.issued" in event_types
    assert "witness.captured" in event_types


def test_governed_write_sensitive_path_requires_approval(
    kernel_runtime: tuple[
        KernelStore,
        ArtifactStore,
        TaskController,
        ToolExecutor,
        TaskExecutionContext,
        Path,
    ],
) -> None:
    store, _artifacts, _controller, executor, ctx, workspace = kernel_runtime

    # Write to a sensitive path (.env) — should require approval
    target = workspace / ".env"
    target.write_text("SECRET=old\n", encoding="utf-8")

    result = executor.execute(
        ctx,
        "write_file",
        {"path": ".env", "content": "SECRET=new\n"},
    )

    # ---- 1. Execution is blocked awaiting approval ----
    assert result.blocked is True
    assert result.approval_id is not None

    # ---- 2. Verify approval record ----
    approval = store.get_approval(result.approval_id)
    assert approval is not None
    assert approval.status == "pending"

    # ---- 3. Verify step and task state ----
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert attempt.status == "awaiting_approval"

    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"

    # ---- 4. Simulate operator approval ----
    executor.approval_service.approve(result.approval_id, resolved_by="operator")

    # ---- 5. Verify approval is now granted ----
    updated_approval = store.get_approval(result.approval_id)
    assert updated_approval is not None
    assert updated_approval.status == "granted"
