from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.models import (
    ApprovalRecord,
    DecisionRecord,
    ExecutionPermitRecord,
    PathGrantRecord,
    ReceiptRecord,
)
from hermit.kernel.store import KernelStore
from hermit.kernel.store_support import _canonical_json, _canonical_json_from_raw, _sha256_hex

_PROOF_MODE = "hash_chained"


class ProofService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store or ArtifactStore(store.db_path.parent / "artifacts")

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
                actor=str(row["actor"]),
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
        permits = self.store.list_execution_permits(task_id=task_id, limit=20)
        verification = self.verify_task_chain(task_id)
        projection = self.store.build_task_projection(task_id)
        latest_receipt = receipts[0] if receipts else None
        latest_decision = decisions[0] if decisions else None
        latest_permit = permits[0] if permits else None
        missing_receipt_bundles = [
            receipt.receipt_id
            for receipt in receipts
            if not str(receipt.receipt_bundle_ref or "").strip()
        ]
        return {
            "task": task.__dict__,
            "proof_mode": _PROOF_MODE,
            "chain_verification": verification,
            "head_hash": verification["head_hash"],
            "event_count": verification["event_count"],
            "receipt_count": len(receipts),
            "missing_receipt_bundle_count": len(missing_receipt_bundles),
            "missing_receipt_bundle_receipts": missing_receipt_bundles,
            "latest_decision": latest_decision.__dict__ if latest_decision is not None else None,
            "latest_permit": latest_permit.__dict__ if latest_permit is not None else None,
            "latest_receipt": latest_receipt.__dict__ if latest_receipt is not None else None,
            "projection": {
                "events_processed": projection["events_processed"],
                "last_event_seq": projection["last_event_seq"],
                "step_count": len(projection["steps"]),
                "step_attempt_count": len(projection["step_attempts"]),
                "approval_count": len(projection["approvals"]),
                "decision_count": len(projection["decisions"]),
                "permit_count": len(projection["permits"]),
                "receipt_count": len(projection["receipts"]),
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
        receipt_bundle_payload = self._build_receipt_bundle_payload(receipt, context_manifest_ref=context_manifest_ref)
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
            proof_mode=_PROOF_MODE,
            signature=None,
        )
        return receipt_bundle_ref

    def export_task_proof(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        receipts = self.store.list_receipts(task_id=task_id, limit=500)
        receipt_bundle_refs = [self.ensure_receipt_bundle(receipt.receipt_id) for receipt in receipts]
        receipt_bundles = [self._load_artifact_payload(ref_id) for ref_id in receipt_bundle_refs]
        context_manifest_refs = sorted(
            {
                str(bundle.get("context_manifest_ref", "")).strip()
                for bundle in receipt_bundles
                if str(bundle.get("context_manifest_ref", "")).strip()
            }
        )
        context_manifests = [self._load_artifact_payload(ref_id) for ref_id in context_manifest_refs]
        verification = self.verify_task_chain(task_id)
        proof_payload = {
            "task_id": task_id,
            "exported_at": time.time(),
            "proof_mode": _PROOF_MODE,
            "status": "verified" if verification["valid"] else "invalid_chain",
            "chain_verification": verification,
            "task_projection_summary": self.build_proof_summary(task_id)["projection"],
            "receipt_bundles": receipt_bundles,
            "context_manifests": context_manifests,
            "decision_refs": sorted({receipt.decision_ref for receipt in receipts if receipt.decision_ref}),
            "approval_refs": sorted({receipt.approval_ref for receipt in receipts if receipt.approval_ref}),
            "permit_refs": sorted({receipt.permit_ref for receipt in receipts if receipt.permit_ref}),
            "grants": [grant.__dict__ for grant in self._grants_for_receipts(receipts)],
            "artifact_hash_index": self._artifact_hash_index(task_id, receipts, receipt_bundle_refs, context_manifest_refs),
        }
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
        decision = self.store.get_decision(receipt.decision_ref or "") if receipt.decision_ref else None
        action_request_ref = self._action_request_ref(decision)
        evidence_refs = list(decision.evidence_refs) if decision is not None else []
        return {
            "schema": "context.manifest/v1",
            "task_id": receipt.task_id,
            "step_id": receipt.step_id,
            "step_attempt_id": receipt.step_attempt_id,
            "action_type": receipt.action_type,
            "action_request_ref": action_request_ref,
            "policy_ref": receipt.policy_ref,
            "approval_ref": receipt.approval_ref,
            "decision_ref": receipt.decision_ref,
            "permit_ref": receipt.permit_ref,
            "witness_ref": receipt.witness_ref,
            "input_refs": list(receipt.input_refs),
            "output_refs": list(receipt.output_refs),
            "environment_ref": receipt.environment_ref,
            "evidence_refs": evidence_refs,
            "memory_refs": [record.memory_id for record in self.store.list_memory_records(limit=50)],
        }

    def _build_receipt_bundle_payload(
        self,
        receipt: ReceiptRecord,
        *,
        context_manifest_ref: str,
    ) -> dict[str, Any]:
        approval = self.store.get_approval(receipt.approval_ref or "") if receipt.approval_ref else None
        permit = self.store.get_execution_permit(receipt.permit_ref or "") if receipt.permit_ref else None
        grant = self.store.get_path_grant(receipt.grant_ref or "") if receipt.grant_ref else None
        verification = self.verify_task_chain(receipt.task_id)
        return {
            "schema": "receipt.bundle/v1",
            "receipt_id": receipt.receipt_id,
            "proof_mode": _PROOF_MODE,
            "result_code": receipt.result_code,
            "action_type": receipt.action_type,
            "input_hashes": self._artifact_hashes(receipt.input_refs),
            "output_hashes": self._artifact_hashes(receipt.output_refs),
            "environment_hash": self._artifact_hash(receipt.environment_ref),
            "policy_result_hash": _sha256_hex(_canonical_json(receipt.policy_result)),
            "approval_packet_hash": self._approval_packet_hash(approval),
            "capability_grant_hash": self._capability_grant_hash(permit, grant),
            "decision_ref": receipt.decision_ref,
            "permit_ref": receipt.permit_ref,
            "grant_ref": receipt.grant_ref,
            "witness_ref": receipt.witness_ref,
            "idempotency_key": receipt.idempotency_key,
            "context_manifest_ref": context_manifest_ref,
            "task_event_head_hash": verification["head_hash"],
            "rollback_supported": receipt.rollback_supported,
            "rollback_strategy": receipt.rollback_strategy,
            "rollback_status": receipt.rollback_status,
            "rollback_ref": receipt.rollback_ref,
            "rollback_artifact_hashes": self._artifact_hashes(receipt.rollback_artifact_refs),
        }

    def _artifact_hashes(self, artifact_ids: list[str]) -> dict[str, str | None]:
        return {artifact_id: self._artifact_hash(artifact_id) for artifact_id in artifact_ids}

    def _artifact_hash(self, artifact_id: str | None) -> str | None:
        if not artifact_id:
            return None
        artifact = self.store.get_artifact(artifact_id)
        return artifact.content_hash if artifact is not None else None

    def _approval_packet_hash(self, approval: ApprovalRecord | None) -> str | None:
        if approval is None or not approval.request_packet_ref:
            return None
        return self._artifact_hash(approval.request_packet_ref)

    def _capability_grant_hash(
        self,
        permit: ExecutionPermitRecord | None,
        grant: PathGrantRecord | None,
    ) -> str | None:
        if permit is not None:
            return _sha256_hex(_canonical_json(permit.__dict__))
        if grant is not None:
            return _sha256_hex(_canonical_json(grant.__dict__))
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
        artifact_ids = {artifact.artifact_id for artifact in self.store.list_artifacts(task_id=task_id, limit=1000)}
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

    def _grants_for_receipts(self, receipts: list[ReceiptRecord]) -> list[PathGrantRecord]:
        grants: list[PathGrantRecord] = []
        seen: set[str] = set()
        for receipt in receipts:
            if not receipt.grant_ref or receipt.grant_ref in seen:
                continue
            grant = self.store.get_path_grant(receipt.grant_ref)
            if grant is not None:
                seen.add(receipt.grant_ref)
                grants.append(grant)
        return grants

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
                "seal_mode": _PROOF_MODE,
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
