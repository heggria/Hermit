"""Full governed execution chain integration test.

Exercises the complete governed execution path:
  KernelStore → task → step → attempt → contract → approval → decision →
  grant → lease → receipt → reconciliation → proof

This is THE most important integration test -- it proves GC1+GC2+GC3 together.
"""

from __future__ import annotations

import time

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.kernel.verification.receipts.receipts import ReceiptService


def _make_store(tmp_path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _make_attempt_ctx(
    store: KernelStore,
    *,
    workspace_root: str = "/tmp/workspace",
) -> TaskExecutionContext:
    """Create a full task→step→attempt chain and return the execution context."""
    conv = store.ensure_conversation("conv_gc", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title="Full governed chain test",
        goal="Verify every governed subsystem end-to-end",
        source_channel="test",
        status="running",
        policy_profile="default",
    )
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        attempt=1,
        context={"workspace_root": workspace_root, "execution_mode": "run"},
    )
    return TaskExecutionContext(
        conversation_id=conv.conversation_id,
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        source_channel="test",
        workspace_root=workspace_root,
    )


class TestFullGovernedChain:
    """Exercise the complete Task → ... → Proof chain and verify all cross-references."""

    def test_full_chain_task_to_proof(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        ctx = _make_attempt_ctx(store)

        # 1. Verify task, step, attempt exist
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "running"

        step = store.get_step(ctx.step_id)
        assert step is not None
        assert step.task_id == ctx.task_id

        attempt = store.get_step_attempt(ctx.step_attempt_id)
        assert attempt is not None
        assert attempt.task_id == ctx.task_id
        assert attempt.step_id == ctx.step_id

        # 2. Synthesize execution contract
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="bash: execute_command",
            proposed_action_refs=["action_ref_1"],
            expected_effects=["command:echo hello"],
            success_criteria={
                "tool_name": "bash",
                "action_class": "execute_command",
                "requires_receipt": True,
            },
            reversibility_class="compensatable",
            required_receipt_classes=["execute_command"],
            drift_budget={"resource_scopes": ["/tmp"], "outside_workspace": False},
            status="admissibility_pending",
            risk_budget={"risk_level": "medium", "approval_required": True},
            expected_artifact_shape={"expected_effects": ["command:echo hello"]},
            task_family="runtime_perf",
            verification_requirements={
                "functional": "required",
                "governance_bench": "optional",
            },
        )

        # Verify contract in store
        fetched_contract = store.get_execution_contract(contract.contract_id)
        assert fetched_contract is not None
        assert fetched_contract.task_id == ctx.task_id
        assert fetched_contract.step_id == ctx.step_id
        assert fetched_contract.step_attempt_id == ctx.step_attempt_id
        assert fetched_contract.objective == "bash: execute_command"

        # Link contract to step attempt
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
        )

        # 3. Create approval
        approval = store.create_approval(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            approval_type="operator",
            requested_action={"action_class": "execute_command", "tool_name": "bash"},
            request_packet_ref=None,
            requested_contract_ref=contract.contract_id,
        )

        fetched_approval = store.get_approval(approval.approval_id)
        assert fetched_approval is not None
        assert fetched_approval.task_id == ctx.task_id
        assert fetched_approval.status == "pending"
        assert fetched_approval.requested_contract_ref == contract.contract_id

        # 4. Resolve (approve) the approval
        store.resolve_approval(
            approval.approval_id,
            status="approved",
            resolved_by="operator",
            resolution={"verdict": "approved", "reason": "test approval"},
        )

        approved = store.get_approval(approval.approval_id)
        assert approved is not None
        assert approved.status == "approved"

        # 5. Create decision
        decision = store.create_decision(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="policy_evaluation",
            verdict="approved",
            reason="Risk level acceptable; operator approved",
            approval_ref=approval.approval_id,
            contract_ref=contract.contract_id,
            action_type="execute_command",
            risk_level="medium",
            reversible=True,
        )

        fetched_decision = store.get_decision(decision.decision_id)
        assert fetched_decision is not None
        assert fetched_decision.approval_ref == approval.approval_id
        assert fetched_decision.contract_ref == contract.contract_id
        assert fetched_decision.verdict == "approved"

        # 6. Create workspace lease
        lease = store.create_workspace_lease(
            task_id=ctx.task_id,
            step_attempt_id=ctx.step_attempt_id,
            workspace_id="ws_default",
            root_path="/tmp/workspace",
            holder_principal_id="principal_kernel",
            mode="read_write",
            resource_scope=["/tmp/workspace"],
            environment_ref=None,
            expires_at=time.time() + 300,
        )

        fetched_lease = store.get_workspace_lease(lease.lease_id)
        assert fetched_lease is not None
        assert fetched_lease.task_id == ctx.task_id
        assert fetched_lease.step_attempt_id == ctx.step_attempt_id
        assert fetched_lease.status == "active"

        # 7. Issue capability grant
        grant = store.create_capability_grant(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=approval.approval_id,
            policy_ref=None,
            workspace_lease_ref=lease.lease_id,
            action_class="execute_command",
            resource_scope=["/tmp/workspace"],
            constraints={"max_duration_seconds": 60},
            idempotency_key=None,
            expires_at=time.time() + 300,
        )

        fetched_grant = store.get_capability_grant(grant.grant_id)
        assert fetched_grant is not None
        assert fetched_grant.decision_ref == decision.decision_id
        assert fetched_grant.approval_ref == approval.approval_id
        assert fetched_grant.workspace_lease_ref == lease.lease_id
        assert fetched_grant.action_class == "execute_command"
        assert fetched_grant.status == "issued"

        # 8. Issue receipt via ReceiptService
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            action_type="execute_command",
            receipt_class="execute_command",
            input_refs=["action_ref_1"],
            environment_ref=None,
            policy_result={"verdict": "approved", "risk_level": "medium"},
            approval_ref=approval.approval_id,
            output_refs=["output_ref_1"],
            result_summary="Command executed successfully",
            result_code="succeeded",
            decision_ref=decision.decision_id,
            capability_grant_ref=grant.grant_id,
            workspace_lease_ref=lease.lease_id,
            contract_ref=contract.contract_id,
            observed_effect_summary="echo hello -> hello",
            reconciliation_required=True,
        )

        fetched_receipt = store.get_receipt(receipt_id)
        assert fetched_receipt is not None
        assert fetched_receipt.task_id == ctx.task_id
        assert fetched_receipt.contract_ref == contract.contract_id
        assert fetched_receipt.decision_ref == decision.decision_id
        assert fetched_receipt.capability_grant_ref == grant.grant_id
        assert fetched_receipt.workspace_lease_ref == lease.lease_id
        assert fetched_receipt.approval_ref == approval.approval_id
        assert fetched_receipt.result_code == "succeeded"

        # 9. Reconcile
        reconciliation = store.create_reconciliation(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref=contract.contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=["output_ref_1"],
            intended_effect_summary="Execute echo hello",
            authorized_effect_summary="Execute echo hello within workspace",
            observed_effect_summary="Command completed with exit 0",
            receipted_effect_summary="Command executed successfully",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
            operator_summary="satisfied: Command completed with exit 0",
        )

        fetched_recon = store.get_reconciliation(reconciliation.reconciliation_id)
        assert fetched_recon is not None
        assert fetched_recon.contract_ref == contract.contract_id
        assert fetched_recon.result_class == "satisfied"
        assert receipt_id in fetched_recon.receipt_refs

        # Link reconciliation to step attempt
        store.update_step_attempt(
            ctx.step_attempt_id,
            reconciliation_ref=reconciliation.reconciliation_id,
        )

        # 10. Verify proof chain
        proof_svc = ProofService(store, artifact_store)
        chain_result = proof_svc.verify_task_chain(ctx.task_id)
        assert chain_result["valid"] is True

        # 11. Verify all cross-references are correct
        final_attempt = store.get_step_attempt(ctx.step_attempt_id)
        assert final_attempt is not None
        assert final_attempt.execution_contract_ref == contract.contract_id
        assert final_attempt.reconciliation_ref == reconciliation.reconciliation_id

        # Verify events were recorded
        events = store._rows(
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (ctx.task_id,),
        )
        event_types = [str(e["event_type"]) for e in events]
        assert "task.created" in event_types
        assert "step.started" in event_types
        assert "execution_contract.recorded" in event_types
        assert "approval.requested" in event_types
        assert "decision.recorded" in event_types
        assert "capability_grant.issued" in event_types
        assert "workspace_lease.acquired" in event_types
        assert "reconciliation.recorded" in event_types

    def test_full_chain_objects_have_correct_task_ownership(self, tmp_path) -> None:
        """Every intermediate object must reference the correct task_id."""
        store = _make_store(tmp_path)
        ArtifactStore(tmp_path / "artifacts")
        ctx = _make_attempt_ctx(store)

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write_file: write_local",
            status="admissibility_pending",
        )
        approval = store.create_approval(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            approval_type="auto",
            requested_action={"action_class": "write_local"},
            request_packet_ref=None,
        )
        decision = store.create_decision(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="auto",
            verdict="approved",
            reason="auto approved",
        )
        lease = store.create_workspace_lease(
            task_id=ctx.task_id,
            step_attempt_id=ctx.step_attempt_id,
            workspace_id="ws_test",
            root_path="/tmp/workspace",
            holder_principal_id="principal_kernel",
            mode="read_write",
            resource_scope=["/tmp/workspace"],
            environment_ref=None,
            expires_at=None,
        )
        grant = store.create_capability_grant(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=approval.approval_id,
            policy_ref=None,
            action_class="write_local",
            resource_scope=["/tmp/workspace"],
            constraints={},
            idempotency_key=None,
            expires_at=None,
        )

        # Every object should belong to the same task
        for obj in [contract, approval, decision, lease, grant]:
            assert obj.task_id == ctx.task_id, (
                f"{type(obj).__name__}.task_id mismatch: expected {ctx.task_id}, got {obj.task_id}"
            )

    def test_hash_chain_integrity_after_full_chain(self, tmp_path) -> None:
        """The hash chain must be valid after the full governed chain."""
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        ctx = _make_attempt_ctx(store)

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="test hash chain",
            status="active",
        )
        approval = store.create_approval(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            approval_type="auto",
            requested_action={},
            request_packet_ref=None,
        )
        store.resolve_approval(
            approval.approval_id,
            status="approved",
            resolved_by="kernel",
            resolution={},
        )
        decision = store.create_decision(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="auto",
            verdict="approved",
            reason="auto",
        )
        store.create_capability_grant(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=approval.approval_id,
            policy_ref=None,
            action_class="execute_command",
            resource_scope=[],
            constraints={},
            idempotency_key=None,
            expires_at=None,
        )

        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            action_type="execute_command",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=approval.approval_id,
            output_refs=[],
            result_summary="ok",
            result_code="succeeded",
            contract_ref=contract.contract_id,
        )

        store.create_reconciliation(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            contract_ref=contract.contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=[],
            intended_effect_summary="test",
            authorized_effect_summary="test",
            observed_effect_summary="test",
            receipted_effect_summary="test",
            result_class="satisfied",
            confidence_delta=0.1,
            recommended_resolution="promote_learning",
        )

        # Verify hash chain
        proof_svc = ProofService(store, artifact_store)
        result = proof_svc.verify_task_chain(ctx.task_id)
        assert result["valid"] is True
        assert result["event_count"] > 0

    def test_receipt_references_contract_and_grant(self, tmp_path) -> None:
        """Receipt must carry contract_ref and capability_grant_ref from the governed path."""
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        ctx = _make_attempt_ctx(store)

        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="ref test",
            status="active",
        )
        decision = store.create_decision(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="auto",
            verdict="approved",
            reason="auto",
        )
        grant = store.create_capability_grant(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=None,
            policy_ref=None,
            action_class="read_local",
            resource_scope=[],
            constraints={},
            idempotency_key=None,
            expires_at=None,
        )

        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            action_type="read_local",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="read ok",
            result_code="succeeded",
            contract_ref=contract.contract_id,
            capability_grant_ref=grant.grant_id,
            decision_ref=decision.decision_id,
        )

        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.contract_ref == contract.contract_id
        assert receipt.capability_grant_ref == grant.grant_id
        assert receipt.decision_ref == decision.decision_id

    def test_grant_decision_ref_traces_back_to_approval(self, tmp_path) -> None:
        """CapabilityGrant.decision_ref must point to a Decision that references the Approval."""
        store = _make_store(tmp_path)
        ctx = _make_attempt_ctx(store)

        approval = store.create_approval(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            approval_type="operator",
            requested_action={"action_class": "write_local"},
            request_packet_ref=None,
        )
        store.resolve_approval(
            approval.approval_id,
            status="approved",
            resolved_by="operator",
            resolution={"verdict": "approved"},
        )
        decision = store.create_decision(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="policy_evaluation",
            verdict="approved",
            reason="operator approved",
            approval_ref=approval.approval_id,
        )
        grant = store.create_capability_grant(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision.decision_id,
            approval_ref=approval.approval_id,
            policy_ref=None,
            action_class="write_local",
            resource_scope=[],
            constraints={},
            idempotency_key=None,
            expires_at=None,
        )

        # Trace back: grant -> decision -> approval
        fetched_grant = store.get_capability_grant(grant.grant_id)
        assert fetched_grant is not None
        fetched_decision = store.get_decision(fetched_grant.decision_ref)
        assert fetched_decision is not None
        assert fetched_decision.approval_ref == approval.approval_id
        fetched_approval = store.get_approval(fetched_decision.approval_ref)
        assert fetched_approval is not None
        assert fetched_approval.status == "approved"
