from __future__ import annotations

from typing import Any

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.proofs import ProofService
from hermit.kernel.store import KernelStore


class ReceiptService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")
        self.proofs = ProofService(store, self.artifact_store)

    def issue(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        action_type: str,
        receipt_class: str | None = None,
        input_refs: list[str],
        environment_ref: str | None,
        policy_result: dict[str, Any],
        approval_ref: str | None,
        output_refs: list[str],
        result_summary: str,
        result_code: str = "succeeded",
        decision_ref: str | None = None,
        capability_grant_ref: str | None = None,
        workspace_lease_ref: str | None = None,
        policy_ref: str | None = None,
        action_request_ref: str | None = None,
        policy_result_ref: str | None = None,
        witness_ref: str | None = None,
        idempotency_key: str | None = None,
        verifiability: str | None = None,
        signer_ref: str | None = None,
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_status: str = "not_requested",
        rollback_ref: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
    ) -> str:
        receipt = self.store.create_receipt(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            action_type=action_type,
            receipt_class=receipt_class,
            input_refs=input_refs,
            environment_ref=environment_ref,
            policy_result=policy_result,
            approval_ref=approval_ref,
            output_refs=output_refs,
            result_summary=result_summary,
            result_code=result_code,
            decision_ref=decision_ref,
            capability_grant_ref=capability_grant_ref,
            workspace_lease_ref=workspace_lease_ref,
            policy_ref=policy_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            verifiability=verifiability,
            signer_ref=signer_ref,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_status=rollback_status,
            rollback_ref=rollback_ref,
            rollback_artifact_refs=rollback_artifact_refs,
        )
        self.proofs.ensure_receipt_bundle(receipt.receipt_id)
        return receipt.receipt_id
