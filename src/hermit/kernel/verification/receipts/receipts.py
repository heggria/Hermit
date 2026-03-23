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

from hermit.kernel.ledger.journal.store_support import canonical_json as _canonical_json

log = structlog.get_logger()


def _get_signing_secret() -> str:
    """Return the configured proof signing secret, or empty string if unset."""
    return str(os.environ.get("HERMIT_PROOF_SIGNING_SECRET", "")).strip()


class ReceiptService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")
        self.proofs = ProofService(store, self.artifact_store)

    # ------------------------------------------------------------------
    # Static signature helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonicalize(receipt_data: dict[str, Any]) -> str:
        """Produce a deterministic canonical JSON string for signing.

        Excludes the ``signature`` key and any keys whose value is ``None``
        so that the canonical form is stable across optional-field changes.
        """
        filtered = {
            k: v for k, v in receipt_data.items() if k != "signature" and v is not None
        }
        return _canonical_json(filtered)

    @staticmethod
    def _compute_signature(receipt_data: dict[str, Any]) -> str | None:
        """Compute a v2 HMAC-SHA256 signature over canonical JSON of *receipt_data*.

        Returns ``"v2:<hex>"`` if a signing secret is configured, otherwise ``None``.
        """
        secret = _get_signing_secret()
        if not secret:
            return None
        payload_json = _canonical_json(receipt_data)
        digest = hmac.new(
            secret.encode("utf-8"),
            payload_json.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"v2:{digest}"

    @staticmethod
    def _compute_legacy_signature(
        receipt_id: str,
        task_id: str,
        step_id: str,
        action_type: str,
        result_code: str,
    ) -> str | None:
        """Compute a legacy 5-field HMAC-SHA256 signature.

        Returns the raw hex digest if a signing secret is configured, otherwise ``None``.
        """
        secret = _get_signing_secret()
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
        signature: str | None,
    ) -> bool:
        """Verify a receipt signature (v2 canonical or legacy 5-field format).

        Accepts both the v2 (``"v2:<hex>"``) format produced by
        ``_compute_signature`` and the legacy raw hex format produced by
        ``_compute_legacy_signature``.

        Returns ``True`` if the signature matches, ``False`` otherwise.
        If no signing secret is configured, returns ``False``.
        """
        secret = _get_signing_secret()
        if not secret or not signature:
            return False

        # v2 canonical signature
        if signature.startswith("v2:"):
            expected = ReceiptService._compute_signature(receipt_data)
            return expected is not None and hmac.compare_digest(signature, expected)

        # Legacy 5-field signature
        rid = str(receipt_data.get("receipt_id", ""))
        tid = str(receipt_data.get("task_id", ""))
        sid = str(receipt_data.get("step_id", ""))
        atype = str(receipt_data.get("action_type", ""))
        rcode = str(receipt_data.get("result_code", ""))
        expected_legacy = ReceiptService._compute_legacy_signature(rid, tid, sid, atype, rcode)
        return expected_legacy is not None and hmac.compare_digest(signature, expected_legacy)

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

    def verify_receipt_bundle_signature(self, receipt_id: str) -> bool:
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
