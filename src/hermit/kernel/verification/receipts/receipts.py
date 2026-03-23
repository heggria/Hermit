from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import structlog

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService

log = structlog.get_logger()


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
        contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        witness_ref: str | None = None,
        idempotency_key: str | None = None,
        verifiability: str | None = None,
        signer_ref: str | None = None,
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_status: str = "not_requested",
        rollback_ref: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
        observed_effect_summary: str | None = None,
        reconciliation_required: bool = False,
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
            contract_ref=contract_ref,
            authorization_plan_ref=authorization_plan_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            verifiability=verifiability,
            signer_ref=signer_ref,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_status=rollback_status,
            rollback_ref=rollback_ref,
            rollback_artifact_refs=rollback_artifact_refs,
            observed_effect_summary=observed_effect_summary,
            reconciliation_required=reconciliation_required,
        )
        self.proofs.ensure_receipt_bundle(receipt.receipt_id)
        return receipt.receipt_id

    def verify_signature(self, receipt_id: str) -> bool:
        """Verify the HMAC signature on a receipt's bundle.

        Returns True if the signature is valid or if no signing secret is configured
        (unsigned receipts are considered valid in environments without signing).
        Returns False if the signature does not match the bundle payload, indicating
        potential tampering.
        """
        signing_secret = str(os.environ.get("HERMIT_PROOF_SIGNING_SECRET", "")).strip()
        receipt = self.store.get_receipt(receipt_id)
        if receipt is None:
            log.warning("receipt.verify_signature.not_found", receipt_id=receipt_id)
            return False
        signature_raw = str(receipt.signature or "").strip()
        if not signature_raw:
            # No signature on record — valid if signing is not configured
            return not bool(signing_secret)
        try:
            signature_meta = json.loads(signature_raw)
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "receipt.verify_signature.invalid_signature_format",
                receipt_id=receipt_id,
            )
            return False
        if not isinstance(signature_meta, dict):
            return False
        stored_signature = str(signature_meta.get("signature", "")).strip()
        stored_payload_hash = str(signature_meta.get("payload_hash", "")).strip()
        if not stored_signature or not stored_payload_hash or not signing_secret:
            return not bool(signing_secret)
        expected_digest = hmac.new(
            signing_secret.encode("utf-8"),
            stored_payload_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(stored_signature, expected_digest)
