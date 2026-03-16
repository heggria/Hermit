from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from hermit.capabilities.models import CapabilityGrantRecord
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.models import ApprovalRecord, DecisionRecord, ReceiptRecord
from hermit.kernel.store import KernelStore
from hermit.kernel.store_support import _canonical_json, _canonical_json_from_raw, _sha256_hex
from hermit.workspaces.models import WorkspaceLeaseRecord

_PROOF_MODE_HASH_ONLY = "hash_only"
_PROOF_MODE_HASH_CHAINED = "hash_chained"
_PROOF_MODE_SIGNED = "signed"
_PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF = "signed_with_inclusion_proof"
_MISSING_PROOF_FEATURES = ("signature", "inclusion_proof")


def proof_capabilities(*, signing_secret: str | None = None) -> dict[str, Any]:
    configured_secret = str(
        signing_secret or os.environ.get("HERMIT_PROOF_SIGNING_SECRET", "")
    ).strip()
    signing_configured = bool(configured_secret)
    return {
        "baseline_verifiable_available": True,
        "signing_configured": signing_configured,
        "strong_signed_proofs_available": signing_configured,
    }


class ProofService:
    def __init__(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore | None = None,
        *,
        signing_secret: str | None = None,
        signing_key_id: str | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")
        self.signing_secret = str(
            signing_secret or os.environ.get("HERMIT_PROOF_SIGNING_SECRET", "")
        ).strip()
        self.signing_key_id = (
            str(signing_key_id or os.environ.get("HERMIT_PROOF_SIGNING_KEY_ID", "")).strip()
            or "local-hmac"
        )

    def verify_task_chain(self, task_id: str) -> dict[str, Any]:
        rows = self.store._rows(  # type: ignore[attr-defined]
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_id,),
        )
        previous_hash = ""
        head_hash = ""
        for row in rows:
            observed_prev_hash = str(row["prev_event_hash"] or "")
            expected_prev_hash = previous_hash
            payload_json = _canonical_json_from_raw(str(row["payload_json"]))
            computed_hash = self.store._compute_event_hash(  # type: ignore[attr-defined]
                event_id=str(row["event_id"]),
                task_id=row["task_id"],
                step_id=row["step_id"],
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                event_type=str(row["event_type"]),
                actor=str(row["actor_principal_id"]),
                payload_json=payload_json,
                occurred_at=float(row["occurred_at"]),
                causation_id=row["causation_id"],
                correlation_id=row["correlation_id"],
                prev_event_hash=expected_prev_hash or None,
            )
            stored_hash = str(row["event_hash"] or "")
            if observed_prev_hash != expected_prev_hash or stored_hash != computed_hash:
                return {
                    "valid": False,
                    "broken_at_event_id": str(row["event_id"]),
                    "expected_prev_hash": expected_prev_hash or None,
                    "observed_prev_hash": observed_prev_hash or None,
                    "head_hash": head_hash or None,
                    "event_count": len(rows),
                }
            previous_hash = stored_hash
            head_hash = stored_hash
        return {
            "valid": True,
            "broken_at_event_id": None,
            "expected_prev_hash": None,
            "observed_prev_hash": None,
            "head_hash": head_hash or None,
            "event_count": len(rows),
        }

    def build_proof_summary(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        receipts = self.store.list_receipts(task_id=task_id, limit=200)
        decisions = self.store.list_decisions(task_id=task_id, limit=20)
        grants = self.store.list_capability_grants(task_id=task_id, limit=20)
        leases = self.store.list_workspace_leases(task_id=task_id, limit=20)
        contracts = (
            self.store.list_execution_contracts(task_id=task_id, limit=20)
            if hasattr(self.store, "list_execution_contracts")
            else []
        )
        evidence_cases = (
            self.store.list_evidence_cases(task_id=task_id, limit=20)
            if hasattr(self.store, "list_evidence_cases")
            else []
        )
        authorization_plans = (
            self.store.list_authorization_plans(task_id=task_id, limit=20)
            if hasattr(self.store, "list_authorization_plans")
            else []
        )
        reconciliations = (
            self.store.list_reconciliations(task_id=task_id, limit=20)
            if hasattr(self.store, "list_reconciliations")
            else []
        )
        verification = self.verify_task_chain(task_id)
        projection = self.store.build_task_projection(task_id)
        latest_receipt = receipts[0] if receipts else None
        latest_decision = decisions[0] if decisions else None
        latest_grant = grants[0] if grants else None
        latest_lease = leases[0] if leases else None
        latest_contract = contracts[0] if contracts else None
        latest_evidence_case = evidence_cases[0] if evidence_cases else None
        latest_authorization_plan = authorization_plans[0] if authorization_plans else None
        latest_reconciliation = reconciliations[0] if reconciliations else None
        missing_receipt_bundles = [
            receipt.receipt_id
            for receipt in receipts
            if not str(receipt.receipt_bundle_ref or "").strip()
        ]
        return {
            "task": task.__dict__,
            "proof_mode": self._summary_proof_mode(receipts),
            "strongest_export_mode": self._strongest_export_mode(receipts),
            "proof_capabilities": proof_capabilities(signing_secret=self.signing_secret),
            "proof_coverage": self._proof_coverage(receipts),
            "chain_verification": verification,
            "head_hash": verification["head_hash"],
            "event_count": verification["event_count"],
            "receipt_count": len(receipts),
            "missing_receipt_bundle_count": len(missing_receipt_bundles),
            "missing_receipt_bundle_receipts": missing_receipt_bundles,
            "latest_decision": latest_decision.__dict__ if latest_decision is not None else None,
            "latest_capability_grant": latest_grant.__dict__ if latest_grant is not None else None,
            "latest_workspace_lease": latest_lease.__dict__ if latest_lease is not None else None,
            "latest_receipt": latest_receipt.__dict__ if latest_receipt is not None else None,
            "latest_execution_contract": latest_contract.__dict__
            if latest_contract is not None
            else None,
            "latest_evidence_case": latest_evidence_case.__dict__
            if latest_evidence_case is not None
            else None,
            "latest_authorization_plan": latest_authorization_plan.__dict__
            if latest_authorization_plan is not None
            else None,
            "latest_reconciliation": latest_reconciliation.__dict__
            if latest_reconciliation is not None
            else None,
            "projection": {
                "events_processed": projection["events_processed"],
                "last_event_seq": projection["last_event_seq"],
                "step_count": len(projection["steps"]),
                "step_attempt_count": len(projection["step_attempts"]),
                "approval_count": len(projection["approvals"]),
                "decision_count": len(projection["decisions"]),
                "capability_grant_count": len(projection["capability_grants"]),
                "workspace_lease_count": len(projection["workspace_leases"]),
                "receipt_count": len(projection["receipts"]),
                "execution_contract_count": len(projection["execution_contracts"]),
                "evidence_case_count": len(projection["evidence_cases"]),
                "authorization_plan_count": len(projection["authorization_plans"]),
                "reconciliation_count": len(projection["reconciliations"]),
            },
        }

    def ensure_receipt_bundle(self, receipt_id: str) -> str:
        receipt = self.store.get_receipt(receipt_id)
        if receipt is None:
            raise KeyError(f"Receipt not found: {receipt_id}")
        existing = str(receipt.receipt_bundle_ref or "").strip()
        if existing:
            return existing

        context_manifest_ref = self._create_context_manifest(receipt)
        receipt_bundle_payload = self._build_receipt_bundle_payload(
            receipt, context_manifest_ref=context_manifest_ref
        )
        signature_meta = self._signature_metadata(
            receipt_bundle_payload, artifact_kind="receipt.bundle"
        )
        if signature_meta is not None:
            receipt_bundle_payload["signature"] = signature_meta
            receipt_bundle_payload["proof_mode"] = _PROOF_MODE_SIGNED
        receipt_bundle_ref = self._store_sealed_artifact(
            task_id=receipt.task_id,
            step_id=receipt.step_id,
            kind="receipt.bundle",
            payload=receipt_bundle_payload,
            producer="proof_service",
        )
        self.store.update_receipt_proof_fields(
            receipt_id,
            receipt_bundle_ref=receipt_bundle_ref,
            proof_mode=_PROOF_MODE_SIGNED
            if signature_meta is not None
            else _PROOF_MODE_HASH_CHAINED,
            verifiability="signed_receipt" if signature_meta is not None else "baseline_verifiable",
            signature=json.dumps(signature_meta, ensure_ascii=False)
            if signature_meta is not None
            else None,
            signer_ref=signature_meta.get("key_id") if signature_meta is not None else None,
        )
        return receipt_bundle_ref

    def export_task_proof(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        receipts = self.store.list_receipts(task_id=task_id, limit=500)
        receipt_bundle_refs = [
            self.ensure_receipt_bundle(receipt.receipt_id) for receipt in receipts
        ]
        receipt_bundles = [self._load_artifact_payload(ref_id) for ref_id in receipt_bundle_refs]
        context_manifest_refs = sorted(
            {
                str(bundle.get("context_manifest_ref", "")).strip()
                for bundle in receipt_bundles
                if str(bundle.get("context_manifest_ref", "")).strip()
            }
        )
        context_manifests = [
            self._load_artifact_payload(ref_id) for ref_id in context_manifest_refs
        ]
        contracts = (
            self.store.list_execution_contracts(task_id=task_id, limit=500)
            if hasattr(self.store, "list_execution_contracts")
            else []
        )
        evidence_cases = (
            self.store.list_evidence_cases(task_id=task_id, limit=500)
            if hasattr(self.store, "list_evidence_cases")
            else []
        )
        authorization_plans = (
            self.store.list_authorization_plans(task_id=task_id, limit=500)
            if hasattr(self.store, "list_authorization_plans")
            else []
        )
        reconciliations = (
            self.store.list_reconciliations(task_id=task_id, limit=500)
            if hasattr(self.store, "list_reconciliations")
            else []
        )
        verification = self.verify_task_chain(task_id)
        proof_payload = {
            "task_id": task_id,
            "exported_at": time.time(),
            "proof_mode": _PROOF_MODE_HASH_CHAINED,
            "proof_coverage": self._proof_coverage(receipts),
            "status": "verified" if verification["valid"] else "invalid_chain",
            "chain_verification": verification,
            "task_projection_summary": self.build_proof_summary(task_id)["projection"],
            "receipt_bundles": receipt_bundles,
            "context_manifests": context_manifests,
            "decision_refs": sorted(
                {receipt.decision_ref for receipt in receipts if receipt.decision_ref}
            ),
            "approval_refs": sorted(
                {receipt.approval_ref for receipt in receipts if receipt.approval_ref}
            ),
            "capability_grant_refs": sorted(
                {
                    receipt.capability_grant_ref
                    for receipt in receipts
                    if receipt.capability_grant_ref
                }
            ),
            "workspace_lease_refs": sorted(
                {receipt.workspace_lease_ref for receipt in receipts if receipt.workspace_lease_ref}
            ),
            "capability_grants": [
                grant.__dict__ for grant in self._capability_grants_for_receipts(receipts)
            ],
            "workspace_leases": [
                lease.__dict__ for lease in self._workspace_leases_for_receipts(receipts)
            ],
            "execution_contract_refs": [record.contract_id for record in contracts],
            "evidence_case_refs": [record.evidence_case_id for record in evidence_cases],
            "authorization_plan_refs": [
                record.authorization_plan_id for record in authorization_plans
            ],
            "reconciliation_refs": [record.reconciliation_id for record in reconciliations],
            "execution_contracts": [record.__dict__ for record in contracts],
            "evidence_cases": [record.__dict__ for record in evidence_cases],
            "authorization_plans": [record.__dict__ for record in authorization_plans],
            "reconciliations": [record.__dict__ for record in reconciliations],
            "artifact_hash_index": self._artifact_hash_index(
                task_id, receipts, receipt_bundle_refs, context_manifest_refs
            ),
        }
        inclusion = self._receipt_inclusion_proofs(receipt_bundles)
        proof_payload["receipt_merkle_root"] = inclusion["root"]
        proof_payload["receipt_inclusion_proofs"] = inclusion["proofs"]
        proof_payload["proof_mode"] = self._export_proof_mode(
            receipts, inclusion_enabled=bool(inclusion["proofs"])
        )
        if proof_payload["proof_mode"] == _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF:
            for receipt in receipts:
                self.store.update_receipt_proof_fields(
                    receipt.receipt_id,
                    proof_mode=_PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF,
                    verifiability="strong_signed_with_inclusion_proof",
                    signer_ref=self.signing_key_id if self.signing_secret else receipt.signer_ref,
                )
        signature_meta = self._signature_metadata(proof_payload, artifact_kind="proof.bundle")
        if signature_meta is not None:
            proof_payload["signature"] = signature_meta
        proof_bundle_ref = self._store_sealed_artifact(
            task_id=task_id,
            step_id=receipts[0].step_id if receipts else None,
            kind="proof.bundle",
            payload=proof_payload,
            producer="proof_service",
        )
        proof_payload["proof_bundle_ref"] = proof_bundle_ref
        return proof_payload

    def _create_context_manifest(self, receipt: ReceiptRecord) -> str:
        context_payload = self._build_context_manifest_payload(receipt)
        return self._store_sealed_artifact(
            task_id=receipt.task_id,
            step_id=receipt.step_id,
            kind="context.manifest",
            payload=context_payload,
            producer="proof_service",
        )

    def _build_context_manifest_payload(self, receipt: ReceiptRecord) -> dict[str, Any]:
        decision = (
            self.store.get_decision(receipt.decision_ref or "") if receipt.decision_ref else None
        )
        task = self.store.get_task(receipt.task_id)
        conversation_id = getattr(task, "conversation_id", None)
        memory_refs: list[str] = []
        if conversation_id:
            for record in self.store.list_memory_records(
                status="active",
                conversation_id=conversation_id,
                limit=200,
            ):
                if record.task_id == receipt.task_id or record.conversation_id == conversation_id:
                    memory_refs.append(record.memory_id)
                if len(memory_refs) >= 50:
                    break
        action_request_ref = receipt.action_request_ref or self._action_request_ref(decision)
        evidence_refs = list(decision.evidence_refs) if decision is not None else []
        return {
            "schema": "context.manifest/v1",
            "task_id": receipt.task_id,
            "step_id": receipt.step_id,
            "step_attempt_id": receipt.step_attempt_id,
            "action_type": receipt.receipt_class or receipt.action_type,
            "action_request_ref": action_request_ref,
            "policy_ref": receipt.policy_result_ref or receipt.policy_ref,
            "approval_ref": receipt.approval_ref,
            "decision_ref": receipt.decision_ref,
            "capability_grant_ref": receipt.capability_grant_ref,
            "workspace_lease_ref": receipt.workspace_lease_ref,
            "witness_ref": receipt.witness_ref,
            "input_refs": list(receipt.input_refs),
            "output_refs": list(receipt.output_refs),
            "environment_ref": receipt.environment_ref,
            "evidence_refs": evidence_refs,
            "memory_refs": memory_refs,
        }

    def _build_receipt_bundle_payload(
        self,
        receipt: ReceiptRecord,
        *,
        context_manifest_ref: str,
    ) -> dict[str, Any]:
        approval = (
            self.store.get_approval(receipt.approval_ref or "") if receipt.approval_ref else None
        )
        capability_grant = (
            self.store.get_capability_grant(receipt.capability_grant_ref or "")
            if receipt.capability_grant_ref
            else None
        )
        workspace_lease = (
            self.store.get_workspace_lease(receipt.workspace_lease_ref or "")
            if receipt.workspace_lease_ref
            else None
        )
        verification = self.verify_task_chain(receipt.task_id)
        return {
            "schema": "receipt.bundle/v1",
            "receipt_id": receipt.receipt_id,
            "proof_mode": _PROOF_MODE_HASH_CHAINED,
            "proof_coverage": self._proof_coverage([receipt]),
            "result_code": receipt.result_code,
            "action_type": receipt.receipt_class or receipt.action_type,
            "receipt_class": receipt.receipt_class or receipt.action_type,
            "action_request_ref": receipt.action_request_ref,
            "input_hashes": self._artifact_hashes(receipt.input_refs),
            "output_hashes": self._artifact_hashes(receipt.output_refs),
            "environment_hash": self._artifact_hash(receipt.environment_ref),
            "policy_result_hash": _sha256_hex(_canonical_json(receipt.policy_result)),
            "approval_packet_hash": self._approval_packet_hash(approval),
            "capability_grant_hash": self._capability_grant_hash(capability_grant, workspace_lease),
            "decision_ref": receipt.decision_ref,
            "capability_grant_ref": receipt.capability_grant_ref,
            "workspace_lease_ref": receipt.workspace_lease_ref,
            "witness_ref": receipt.witness_ref,
            "idempotency_key": receipt.idempotency_key,
            "context_manifest_ref": context_manifest_ref,
            "task_event_head_hash": verification["head_hash"],
            "policy_result_ref": receipt.policy_result_ref or receipt.policy_ref,
            "verifiability": receipt.verifiability,
            "signer_ref": receipt.signer_ref,
            "rollback_supported": receipt.rollback_supported,
            "rollback_strategy": receipt.rollback_strategy,
            "rollback_status": receipt.rollback_status,
            "rollback_ref": receipt.rollback_ref,
            "rollback_artifact_hashes": self._artifact_hashes(receipt.rollback_artifact_refs),
        }

    def _proof_coverage(self, receipts: list[ReceiptRecord]) -> dict[str, Any]:
        receipt_count = len(receipts)
        bundle_count = sum(
            bool(str(receipt.receipt_bundle_ref or "").strip()) for receipt in receipts
        )
        signed_count = sum(bool(str(receipt.signature or "").strip()) for receipt in receipts)
        inclusion_count = sum(
            str(receipt.proof_mode or "").strip() == _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF
            for receipt in receipts
        )
        missing_features: list[str] = []
        if signed_count < receipt_count:
            missing_features.append("signature")
        if inclusion_count < receipt_count:
            missing_features.append("inclusion_proof")
        return {
            "available_modes": [
                _PROOF_MODE_HASH_ONLY,
                _PROOF_MODE_HASH_CHAINED,
                _PROOF_MODE_SIGNED,
                _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF,
            ],
            "current_mode": self._summary_proof_mode(receipts),
            "receipt_bundle_coverage": {
                "bundled_receipts": bundle_count,
                "total_receipts": receipt_count,
            },
            "signature_coverage": {
                "signed_receipts": signed_count,
                "total_receipts": receipt_count,
            },
            "inclusion_proof_coverage": {
                "proved_receipts": inclusion_count,
                "total_receipts": receipt_count,
            },
            "missing_features": list(dict.fromkeys(missing_features)),
            "unsigned_receipts": [
                receipt.receipt_id
                for receipt in receipts
                if not str(receipt.signature or "").strip()
            ],
            "notes": {
                "baseline": "hash-linked events with sealed receipt bundles",
                "missing": list(dict.fromkeys(missing_features or list(_MISSING_PROOF_FEATURES))),
            },
        }

    def _summary_proof_mode(self, receipts: list[ReceiptRecord]) -> str:
        if not receipts:
            return _PROOF_MODE_HASH_ONLY
        signed_count = sum(bool(str(receipt.signature or "").strip()) for receipt in receipts)
        inclusion_count = sum(
            str(receipt.proof_mode or "").strip() == _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF
            for receipt in receipts
        )
        if inclusion_count == len(receipts):
            return _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF
        if signed_count == len(receipts):
            return _PROOF_MODE_SIGNED
        return _PROOF_MODE_HASH_CHAINED

    def _strongest_export_mode(self, receipts: list[ReceiptRecord]) -> str:
        if not receipts:
            return _PROOF_MODE_HASH_ONLY
        if self.signing_secret:
            return _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF
        return _PROOF_MODE_HASH_CHAINED

    def _export_proof_mode(self, receipts: list[ReceiptRecord], *, inclusion_enabled: bool) -> str:
        if not receipts:
            return _PROOF_MODE_HASH_ONLY
        if self.signing_secret and inclusion_enabled:
            return _PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF
        if self.signing_secret:
            return _PROOF_MODE_SIGNED
        return _PROOF_MODE_HASH_CHAINED

    def _signature_metadata(
        self, payload: dict[str, Any], *, artifact_kind: str
    ) -> dict[str, Any] | None:
        if not self.signing_secret:
            return None
        payload_hash = _sha256_hex(_canonical_json(payload))
        digest = hmac.new(
            self.signing_secret.encode("utf-8"),
            payload_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "kind": "hmac-sha256",
            "key_id": self.signing_key_id,
            "artifact_kind": artifact_kind,
            "payload_hash": payload_hash,
            "signature": digest,
        }

    def _receipt_inclusion_proofs(self, receipt_bundles: list[dict[str, Any]]) -> dict[str, Any]:
        if not receipt_bundles:
            return {"root": None, "proofs": {}}
        leaves = [
            {
                "receipt_id": str(bundle.get("receipt_id", "") or ""),
                "hash": _sha256_hex(_canonical_json(bundle)),
            }
            for bundle in receipt_bundles
        ]
        levels: list[list[str]] = [[leaf["hash"] for leaf in leaves]]
        while len(levels[-1]) > 1:
            current = levels[-1]
            next_level: list[str] = []
            for index in range(0, len(current), 2):
                left = current[index]
                right = current[index + 1] if index + 1 < len(current) else current[index]
                next_level.append(_sha256_hex(_canonical_json({"left": left, "right": right})))
            levels.append(next_level)
        proofs: dict[str, list[dict[str, Any]]] = {}
        for leaf_index, leaf in enumerate(leaves):
            siblings: list[dict[str, Any]] = []
            index = leaf_index
            for level in levels[:-1]:
                sibling_index = index + 1 if index % 2 == 0 else index - 1
                sibling_hash = level[sibling_index] if sibling_index < len(level) else level[index]
                siblings.append(
                    {
                        "position": "right" if index % 2 == 0 else "left",
                        "hash": sibling_hash,
                    }
                )
                index //= 2
            proofs[leaf["receipt_id"]] = siblings
        return {"root": levels[-1][0], "proofs": proofs}

    def _artifact_hashes(self, artifact_ids: list[str]) -> dict[str, str | None]:
        return {artifact_id: self._artifact_hash(artifact_id) for artifact_id in artifact_ids}

    def _artifact_hash(self, artifact_id: str | None) -> str | None:
        if not artifact_id:
            return None
        artifact = self.store.get_artifact(artifact_id)
        return artifact.content_hash if artifact is not None else None

    def _approval_packet_hash(self, approval: ApprovalRecord | None) -> str | None:
        if approval is None:
            return None
        packet_ref = approval.approval_packet_ref or approval.request_packet_ref
        if not packet_ref:
            return None
        return self._artifact_hash(packet_ref)

    def _capability_grant_hash(
        self,
        grant: CapabilityGrantRecord | None,
        lease: WorkspaceLeaseRecord | None,
    ) -> str | None:
        if grant is not None and lease is not None:
            return _sha256_hex(
                _canonical_json(
                    {"capability_grant": grant.__dict__, "workspace_lease": lease.__dict__}
                )
            )
        if grant is not None:
            return _sha256_hex(_canonical_json(grant.__dict__))
        if lease is not None:
            return _sha256_hex(_canonical_json(lease.__dict__))
        return None

    def _action_request_ref(self, decision: DecisionRecord | None) -> str | None:
        if decision is None:
            return None
        for ref_id in decision.evidence_refs:
            artifact = self.store.get_artifact(ref_id)
            if artifact is not None and artifact.kind == "action_request":
                return ref_id
        return None

    def _artifact_hash_index(
        self,
        task_id: str,
        receipts: list[ReceiptRecord],
        receipt_bundle_refs: list[str],
        context_manifest_refs: list[str],
    ) -> dict[str, dict[str, Any]]:
        artifact_ids = {
            artifact.artifact_id
            for artifact in self.store.list_artifacts(task_id=task_id, limit=1000)
        }
        artifact_ids.update(receipt_bundle_refs)
        artifact_ids.update(context_manifest_refs)
        for receipt in receipts:
            artifact_ids.update(receipt.input_refs)
            artifact_ids.update(receipt.output_refs)
            if receipt.environment_ref:
                artifact_ids.add(receipt.environment_ref)
            if receipt.witness_ref:
                artifact_ids.add(receipt.witness_ref)
            artifact_ids.update(receipt.rollback_artifact_refs)
        index: dict[str, dict[str, Any]] = {}
        for artifact_id in sorted(artifact_ids):
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None:
                continue
            index[artifact_id] = {
                "kind": artifact.kind,
                "content_hash": artifact.content_hash,
                "uri": artifact.uri,
                "metadata": artifact.metadata,
            }
        return index

    def _capability_grants_for_receipts(
        self, receipts: list[ReceiptRecord]
    ) -> list[CapabilityGrantRecord]:
        grants: list[CapabilityGrantRecord] = []
        seen: set[str] = set()
        for receipt in receipts:
            if not receipt.capability_grant_ref or receipt.capability_grant_ref in seen:
                continue
            grant = self.store.get_capability_grant(receipt.capability_grant_ref)
            if grant is not None:
                seen.add(receipt.capability_grant_ref)
                grants.append(grant)
        return grants

    def _workspace_leases_for_receipts(
        self, receipts: list[ReceiptRecord]
    ) -> list[WorkspaceLeaseRecord]:
        leases: list[WorkspaceLeaseRecord] = []
        seen: set[str] = set()
        for receipt in receipts:
            if not receipt.workspace_lease_ref or receipt.workspace_lease_ref in seen:
                continue
            lease = self.store.get_workspace_lease(receipt.workspace_lease_ref)
            if lease is not None:
                seen.add(receipt.workspace_lease_ref)
                leases.append(lease)
        return leases

    def _store_sealed_artifact(
        self,
        *,
        task_id: str,
        step_id: str | None,
        kind: str,
        payload: dict[str, Any],
        producer: str,
    ) -> str:
        sealed_at = time.time()
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer=producer,
            retention_class="audit",
            trust_tier="observed",
            metadata={
                "sealed": True,
                "sealed_at": sealed_at,
                "seal_mode": _PROOF_MODE_HASH_CHAINED,
                "payload_hash": content_hash,
            },
        )
        return artifact.artifact_id

    def _load_artifact_payload(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"Artifact not found: {artifact_id}")
        payload = json.loads(self.artifact_store.read_text(artifact.uri))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Artifact {artifact_id} is not a JSON object")
        return payload
