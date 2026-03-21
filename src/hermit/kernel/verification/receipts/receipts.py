from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService

logger = logging.getLogger(__name__)

# Signature format version prefix for distinguishing v2 (full-field) from legacy.
_SIG_V2_PREFIX = "v2:"


class ReceiptService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")
        self.proofs = ProofService(store, self.artifact_store)

    @staticmethod
    def _canonicalize(receipt_data: dict[str, Any]) -> str:
        """Build a canonical string representation of receipt data for signing.

        Produces deterministic output by:
        - Excluding None values and the ``signature`` field itself
        - Sorting keys alphabetically
        - Using compact JSON with sorted keys and no whitespace
        """
        filtered: dict[str, Any] = {}
        for key in sorted(receipt_data.keys()):
            if key == "signature":
                continue
            value = receipt_data[key]
            if value is None:
                continue
            filtered[key] = value
        return json.dumps(filtered, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _compute_signature(
        receipt_data: dict[str, Any],
    ) -> str | None:
        """Compute HMAC-SHA256 signature covering ALL receipt fields (v2).

        Uses HERMIT_PROOF_SIGNING_SECRET from the environment.
        Returns None if no signing secret is configured.
        The returned signature is prefixed with ``v2:`` so that
        ``verify_signature`` can distinguish it from legacy signatures.
        """
        secret = os.environ.get("HERMIT_PROOF_SIGNING_SECRET")
        if not secret:
            return None
        message = ReceiptService._canonicalize(receipt_data)
        digest = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{_SIG_V2_PREFIX}{digest}"

    @staticmethod
    def _compute_legacy_signature(
        receipt_id: str,
        task_id: str,
        step_id: str,
        action_type: str,
        result_code: str,
    ) -> str | None:
        """Compute legacy HMAC-SHA256 signature covering only 5 core fields.

        Retained for backward-compatible verification of receipts signed
        before the v2 full-field scheme was introduced.
        """
        secret = os.environ.get("HERMIT_PROOF_SIGNING_SECRET")
        if not secret:
            return None
        message = f"{receipt_id}:{task_id}:{step_id}:{action_type}:{result_code}"
        return hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def verify_signature(
        receipt_data: dict[str, Any],
        signature: str,
    ) -> bool:
        """Verify an HMAC-SHA256 receipt signature.

        Supports both v2 (full-field, ``v2:`` prefixed) and legacy
        (5-field) signatures.  When a legacy signature is successfully
        verified a warning is logged recommending re-signing.

        Returns ``True`` if the signature is valid, ``False`` otherwise.
        """
        secret = os.environ.get("HERMIT_PROOF_SIGNING_SECRET")
        if not secret:
            return False

        # --- v2 full-field signature ---
        if signature.startswith(_SIG_V2_PREFIX):
            message = ReceiptService._canonicalize(receipt_data)
            expected = hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(signature[len(_SIG_V2_PREFIX) :], expected)

        # --- Legacy 5-field signature (backward compatibility) ---
        legacy_message = (
            f"{receipt_data.get('receipt_id', '')}:"
            f"{receipt_data.get('task_id', '')}:"
            f"{receipt_data.get('step_id', '')}:"
            f"{receipt_data.get('action_type', '')}:"
            f"{receipt_data.get('result_code', '')}"
        )
        expected_legacy = hmac.new(
            secret.encode("utf-8"),
            legacy_message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected_legacy):
            logger.warning(
                "Receipt %s has a legacy 5-field signature. "
                "Only receipt_id, task_id, step_id, action_type, and result_code "
                "are covered. Re-issue to sign all fields.",
                receipt_data.get("receipt_id", "unknown"),
            )
            return True

        return False

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
        # Pre-generate receipt_id and compute HMAC signature so both are
        # stored atomically in a single create_receipt transaction.
        receipt_id = self.store.generate_id("receipt")

        # Build a dict of ALL receipt fields for the full-coverage signature.
        receipt_data: dict[str, Any] = {
            "receipt_id": receipt_id,
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "action_type": action_type,
            "receipt_class": receipt_class,
            "input_refs": input_refs,
            "environment_ref": environment_ref,
            "policy_result": policy_result,
            "approval_ref": approval_ref,
            "output_refs": output_refs,
            "result_summary": result_summary,
            "result_code": result_code,
            "decision_ref": decision_ref,
            "capability_grant_ref": capability_grant_ref,
            "workspace_lease_ref": workspace_lease_ref,
            "policy_ref": policy_ref,
            "action_request_ref": action_request_ref,
            "policy_result_ref": policy_result_ref,
            "contract_ref": contract_ref,
            "authorization_plan_ref": authorization_plan_ref,
            "witness_ref": witness_ref,
            "idempotency_key": idempotency_key,
            "verifiability": verifiability,
            "signer_ref": signer_ref,
            "rollback_supported": rollback_supported,
            "rollback_strategy": rollback_strategy,
            "rollback_status": rollback_status,
            "rollback_ref": rollback_ref,
            "rollback_artifact_refs": rollback_artifact_refs,
            "observed_effect_summary": observed_effect_summary,
            "reconciliation_required": reconciliation_required,
        }
        signature = self._compute_signature(receipt_data)

        receipt = self.store.create_receipt(
            receipt_id=receipt_id,
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
            signature=signature,
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
