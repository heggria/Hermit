from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any, cast

from hermit.kernel.authority.grants.models import CapabilityGrantRecord
from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.authority.workspaces.models import WorkspaceLeaseRecord
from hermit.kernel.ledger.journal.store_support import UNSET
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.records import (
    ApprovalRecord,
    ArtifactRecord,
    BeliefRecord,
    DecisionRecord,
    MemoryRecord,
    ReceiptRecord,
    RollbackRecord,
)


class KernelLedgerStoreMixin(KernelStoreTypingBase):
    def create_artifact(
        self,
        *,
        task_id: str | None,
        step_id: str | None,
        kind: str,
        uri: str,
        content_hash: str,
        producer: str,
        retention_class: str = "default",
        trust_tier: str = "observed",
        metadata: dict[str, Any] | None = None,
        artifact_class: str | None = None,
        media_type: str | None = None,
        byte_size: int | None = None,
        sensitivity_class: str | None = None,
        lineage_ref: str | None = None,
    ) -> ArtifactRecord:
        artifact_id = self._id("artifact")
        created_at = time.time()
        artifact_class_value = artifact_class or self._artifact_class_for_kind(kind)
        media_type_value = media_type or self._artifact_media_type(kind=kind, uri=uri)
        byte_size_value = byte_size if byte_size is not None else self._artifact_byte_size(uri)
        sensitivity_class_value = sensitivity_class or self._artifact_sensitivity(retention_class)
        lineage_ref_value = (
            lineage_ref or str((metadata or {}).get("lineage_ref", "") or "") or None
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO artifacts (
                    artifact_id, task_id, step_id, kind, uri, content_hash, producer,
                    retention_class, trust_tier, artifact_class, media_type, byte_size,
                    sensitivity_class, lineage_ref, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    step_id,
                    kind,
                    uri,
                    content_hash,
                    producer,
                    retention_class,
                    trust_tier,
                    artifact_class_value,
                    media_type_value,
                    byte_size_value,
                    sensitivity_class_value,
                    lineage_ref_value,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at,
                ),
            )
        return ArtifactRecord(
            artifact_id=artifact_id,
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer=producer,
            retention_class=retention_class,
            trust_tier=trust_tier,
            artifact_class=artifact_class_value,
            media_type=media_type_value,
            byte_size=byte_size_value,
            sensitivity_class=sensitivity_class_value,
            lineage_ref=lineage_ref_value,
            metadata=metadata or {},
            created_at=created_at,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = self._row("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return self._artifact_from_row(row) if row is not None else None

    def list_artifacts(
        self, *, task_id: str | None = None, limit: int = 200
    ) -> list[ArtifactRecord]:
        if task_id:
            query = "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        rows = self._rows(query, params)
        return [self._artifact_from_row(row) for row in rows]

    def list_artifacts_for_tasks(
        self,
        task_ids: list[str],
        *,
        limit_per_task: int = 50,
    ) -> dict[str, list[ArtifactRecord]]:
        """Bulk-fetch artifacts for multiple tasks in a single query.

        Returns a mapping of ``task_id -> list[ArtifactRecord]`` ordered by
        ``created_at ASC`` within each task.  Tasks with no artifacts are omitted.
        """
        if not task_ids:
            return {}
        placeholders = ",".join("?" * len(task_ids))
        total_limit = limit_per_task * len(task_ids)
        rows = self._rows(
            f"SELECT * FROM artifacts WHERE task_id IN ({placeholders})"
            f" ORDER BY created_at ASC LIMIT ?",
            (*task_ids, total_limit),
        )
        result: dict[str, list[ArtifactRecord]] = {}
        per_task_counts: dict[str, int] = {}
        for row in rows:
            tid = str(row["task_id"])
            if per_task_counts.get(tid, 0) >= limit_per_task:
                continue
            per_task_counts[tid] = per_task_counts.get(tid, 0) + 1
            result.setdefault(tid, []).append(self._artifact_from_row(row))
        return result

    @staticmethod
    def _artifact_class_for_kind(kind: str) -> str:
        prefix = str(kind or "").split("/", 1)[0]
        return prefix.replace(".", "_") or "artifact"

    @staticmethod
    def _artifact_media_type(*, kind: str, uri: str) -> str | None:
        guessed, _encoding = mimetypes.guess_type(uri)
        if guessed:
            return guessed
        if str(kind).startswith("context.pack/"):
            return "application/json"
        if kind in {"action_request", "policy_evaluation", "environment", "environment.snapshot"}:
            return "application/json"
        if kind in {"approval_packet", "receipt.bundle", "context.manifest"}:
            return "application/json"
        if kind.startswith("runtime."):
            return "application/json"
        return None

    @staticmethod
    def _artifact_byte_size(uri: str) -> int | None:
        try:
            return Path(uri).expanduser().stat().st_size
        except OSError:
            return None

    @staticmethod
    def _artifact_sensitivity(retention_class: str) -> str:
        if retention_class in {"audit", "task"}:
            return "operator_internal"
        return "default"

    def get_principal(self, principal_id: str) -> PrincipalRecord | None:
        row = self._row("SELECT * FROM principals WHERE principal_id = ?", (principal_id,))
        return self._principal_from_row(row) if row is not None else None

    def list_principals(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[PrincipalRecord]:
        if status:
            query = "SELECT * FROM principals WHERE status = ? ORDER BY updated_at DESC LIMIT ?"
            params: tuple[Any, ...] = (status, limit)
        else:
            query = "SELECT * FROM principals ORDER BY updated_at DESC LIMIT ?"
            params = (limit,)
        rows = self._rows(query, params)
        return [self._principal_from_row(row) for row in rows]

    def create_decision(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        decision_type: str,
        verdict: str,
        reason: str,
        evidence_refs: list[str] | None = None,
        policy_ref: str | None = None,
        approval_ref: str | None = None,
        contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        evidence_case_ref: str | None = None,
        reconciliation_ref: str | None = None,
        action_type: str | None = None,
        summary: str | None = None,
        rationale: str | None = None,
        risk_level: str | None = None,
        reversible: bool | None = None,
        decided_by: str = "kernel",
    ) -> DecisionRecord:
        decision_id = self._id("decision")
        created_at = time.time()
        decided_by_principal_id = self._ensure_principal_id(decided_by)
        rationale_text = str(rationale or reason or "").strip()
        summary_text = str(summary or rationale_text or reason or "").strip()
        payload = {
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "decision_type": decision_type,
            "verdict": verdict,
            "reason": rationale_text,
            "summary": summary_text,
            "rationale": rationale_text,
            "evidence_refs": list(evidence_refs or []),
            "policy_ref": policy_ref,
            "approval_ref": approval_ref,
            "contract_ref": contract_ref,
            "authorization_plan_ref": authorization_plan_ref,
            "evidence_case_ref": evidence_case_ref,
            "reconciliation_ref": reconciliation_ref,
            "action_type": action_type,
            "risk_level": risk_level,
            "reversible": reversible,
            "decided_by_principal_id": decided_by_principal_id,
            "created_at": created_at,
        }
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO decisions (
                    decision_id, task_id, step_id, step_attempt_id, decision_type, verdict, reason,
                    summary, rationale, evidence_refs_json, policy_ref, approval_ref, contract_ref,
                    authorization_plan_ref, evidence_case_ref, reconciliation_ref, action_type,
                    risk_level, reversible, decided_by_principal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    decision_type,
                    verdict,
                    rationale_text,
                    summary_text,
                    rationale_text,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    policy_ref,
                    approval_ref,
                    contract_ref,
                    authorization_plan_ref,
                    evidence_case_ref,
                    reconciliation_ref,
                    action_type,
                    risk_level,
                    None if reversible is None else int(bool(reversible)),
                    decided_by_principal_id,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="decision.recorded",
                entity_type="decision",
                entity_id=decision_id,
                task_id=task_id,
                step_id=step_id,
                actor=decided_by,
                payload=payload,
            )
        decision = self.get_decision(decision_id)
        assert decision is not None
        return decision

    def get_decision(self, decision_id: str) -> DecisionRecord | None:
        row = self._row("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,))
        return self._decision_from_row(row) if row is not None else None

    def list_decisions(
        self, *, task_id: str | None = None, limit: int = 50
    ) -> list[DecisionRecord]:
        if task_id:
            query = "SELECT * FROM decisions WHERE task_id = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        rows = self._rows(query, params)
        return [self._decision_from_row(row) for row in rows]

    def create_capability_grant(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        decision_ref: str,
        approval_ref: str | None,
        policy_ref: str | None,
        issued_to_principal_id: str | None = None,
        issued_by_principal_id: str | None = None,
        workspace_lease_ref: str | None = None,
        action_class: str,
        resource_scope: list[str],
        constraints: dict[str, Any] | None,
        idempotency_key: str | None,
        expires_at: float | None,
        status: str = "issued",
    ) -> CapabilityGrantRecord:
        grant_id = self._id("grant")
        issued_at = time.time()
        issued_to = self._ensure_principal_id(issued_to_principal_id or "kernel")
        issued_by = self._ensure_principal_id(issued_by_principal_id or "kernel")
        payload = {
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "decision_ref": decision_ref,
            "approval_ref": approval_ref,
            "policy_ref": policy_ref,
            "issued_to_principal_id": issued_to,
            "issued_by_principal_id": issued_by,
            "workspace_lease_ref": workspace_lease_ref,
            "action_class": action_class,
            "resource_scope": list(resource_scope),
            "constraints": dict(constraints or {}),
            "idempotency_key": idempotency_key,
            "status": status,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "consumed_at": None,
            "revoked_at": None,
        }
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO capability_grants (
                    grant_id, task_id, step_id, step_attempt_id, decision_ref, approval_ref, policy_ref,
                    issued_to_principal_id, issued_by_principal_id, workspace_lease_ref,
                    action_class, resource_scope_json, constraints_json, idempotency_key,
                    status, issued_at, expires_at, consumed_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    grant_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    decision_ref,
                    approval_ref,
                    policy_ref,
                    issued_to,
                    issued_by,
                    workspace_lease_ref,
                    action_class,
                    json.dumps(list(resource_scope), ensure_ascii=False),
                    json.dumps(dict(constraints or {}), ensure_ascii=False),
                    idempotency_key,
                    status,
                    issued_at,
                    expires_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="capability_grant.issued",
                entity_type="capability_grant",
                entity_id=grant_id,
                task_id=task_id,
                step_id=step_id,
                actor=issued_by,
                payload=payload,
            )
        grant = self.get_capability_grant(grant_id)
        assert grant is not None
        return grant

    def get_capability_grant(self, grant_id: str) -> CapabilityGrantRecord | None:
        row = self._row("SELECT * FROM capability_grants WHERE grant_id = ?", (grant_id,))
        return self._capability_grant_from_row(row) if row is not None else None

    def update_capability_grant(
        self,
        grant_id: str,
        *,
        status: str,
        consumed_at: float | None | object = UNSET,
        revoked_at: float | None | object = UNSET,
    ) -> None:
        grant = self.get_capability_grant(grant_id)
        if grant is None:
            return
        updated_consumed_at = grant.consumed_at if consumed_at is UNSET else consumed_at
        updated_revoked_at = grant.revoked_at if revoked_at is UNSET else revoked_at
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE capability_grants
                SET status = ?, consumed_at = ?, revoked_at = ?
                WHERE grant_id = ?
                """,
                (status, updated_consumed_at, updated_revoked_at, grant_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"capability_grant.{status}",
                entity_type="capability_grant",
                entity_id=grant_id,
                task_id=grant.task_id,
                step_id=grant.step_id,
                actor=grant.issued_by_principal_id,
                payload={
                    "task_id": grant.task_id,
                    "step_id": grant.step_id,
                    "step_attempt_id": grant.step_attempt_id,
                    "decision_ref": grant.decision_ref,
                    "approval_ref": grant.approval_ref,
                    "policy_ref": grant.policy_ref,
                    "issued_to_principal_id": grant.issued_to_principal_id,
                    "issued_by_principal_id": grant.issued_by_principal_id,
                    "workspace_lease_ref": grant.workspace_lease_ref,
                    "action_class": grant.action_class,
                    "resource_scope": list(grant.resource_scope),
                    "constraints": dict(grant.constraints),
                    "status": status,
                    "issued_at": grant.issued_at,
                    "expires_at": grant.expires_at,
                    "consumed_at": updated_consumed_at,
                    "revoked_at": updated_revoked_at,
                },
            )

    def list_capability_grants(
        self, *, task_id: str | None = None, limit: int = 50
    ) -> list[CapabilityGrantRecord]:
        if task_id:
            query = (
                "SELECT * FROM capability_grants WHERE task_id = ? ORDER BY issued_at DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM capability_grants ORDER BY issued_at DESC LIMIT ?"
            params = (limit,)
        rows = self._rows(query, params)
        return [self._capability_grant_from_row(row) for row in rows]

    def create_workspace_lease(
        self,
        *,
        task_id: str,
        step_attempt_id: str,
        workspace_id: str,
        root_path: str,
        holder_principal_id: str,
        mode: str,
        resource_scope: list[str],
        environment_ref: str | None,
        expires_at: float | None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceLeaseRecord:
        lease_id = self._id("lease")
        acquired_at = time.time()
        payload = {
            "task_id": task_id,
            "step_attempt_id": step_attempt_id,
            "workspace_id": workspace_id,
            "root_path": root_path,
            "holder_principal_id": holder_principal_id,
            "mode": mode,
            "resource_scope": list(resource_scope),
            "environment_ref": environment_ref,
            "status": status,
            "metadata": dict(metadata or {}),
            "acquired_at": acquired_at,
            "expires_at": expires_at,
            "released_at": None,
        }
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO workspace_leases (
                    lease_id, task_id, step_attempt_id, workspace_id, root_path, holder_principal_id,
                    mode, resource_scope_json, environment_ref, status, metadata_json,
                    acquired_at, expires_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    lease_id,
                    task_id,
                    step_attempt_id,
                    workspace_id,
                    root_path,
                    holder_principal_id,
                    mode,
                    json.dumps(list(resource_scope), ensure_ascii=False),
                    environment_ref,
                    status,
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                    acquired_at,
                    expires_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="workspace_lease.acquired",
                entity_type="workspace_lease",
                entity_id=lease_id,
                task_id=task_id,
                actor=holder_principal_id,
                payload=payload,
            )
        lease = self.get_workspace_lease(lease_id)
        assert lease is not None
        return lease

    def get_workspace_lease(self, lease_id: str) -> WorkspaceLeaseRecord | None:
        row = self._row("SELECT * FROM workspace_leases WHERE lease_id = ?", (lease_id,))
        return self._workspace_lease_from_row(row) if row is not None else None

    def update_workspace_lease(
        self,
        lease_id: str,
        *,
        status: str | object = UNSET,
        expires_at: float | None | object = UNSET,
        released_at: float | None | object = UNSET,
        actor: str = "kernel",
    ) -> None:
        lease = self.get_workspace_lease(lease_id)
        if lease is None:
            return
        updated_status = lease.status if status is UNSET else str(status)
        updated_expires_at = lease.expires_at if expires_at is UNSET else expires_at
        updated_released_at = lease.released_at if released_at is UNSET else released_at
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE workspace_leases
                SET status = ?, expires_at = ?, released_at = ?
                WHERE lease_id = ?
                """,
                (updated_status, updated_expires_at, updated_released_at, lease_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"workspace_lease.{updated_status}",
                entity_type="workspace_lease",
                entity_id=lease_id,
                task_id=lease.task_id,
                actor=actor,
                payload={
                    "task_id": lease.task_id,
                    "step_attempt_id": lease.step_attempt_id,
                    "workspace_id": lease.workspace_id,
                    "root_path": lease.root_path,
                    "holder_principal_id": lease.holder_principal_id,
                    "mode": lease.mode,
                    "resource_scope": list(lease.resource_scope),
                    "environment_ref": lease.environment_ref,
                    "status": updated_status,
                    "acquired_at": lease.acquired_at,
                    "expires_at": updated_expires_at,
                    "released_at": updated_released_at,
                    "metadata": dict(lease.metadata),
                },
            )

    def list_workspace_leases(
        self,
        *,
        task_id: str | None = None,
        step_attempt_id: str | None = None,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkspaceLeaseRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if step_attempt_id:
            clauses.append("step_attempt_id = ?")
            params.append(step_attempt_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM workspace_leases {where} ORDER BY acquired_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._workspace_lease_from_row(row) for row in rows]

    def create_belief(
        self,
        *,
        task_id: str,
        conversation_id: str | None,
        scope_kind: str,
        scope_ref: str,
        category: str,
        content: str | None = None,
        claim_text: str | None = None,
        structured_assertion: dict[str, Any] | None = None,
        promotion_candidate: bool = True,
        status: str = "active",
        confidence: float = 0.5,
        trust_tier: str = "observed",
        evidence_refs: list[str] | None = None,
        evidence_case_ref: str | None = None,
        supersedes: list[str] | None = None,
        contradicts: list[str] | None = None,
        epistemic_origin: str = "observed",
        freshness_class: str | None = None,
        last_validated_at: float | None = None,
        validation_basis: str | None = None,
        supersession_reason: str | None = None,
        memory_ref: str | None = None,
        invalidated_at: float | None = None,
    ) -> BeliefRecord:
        belief_id = self._id("belief")
        created_at = time.time()
        claim = (claim_text or content or "").strip()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO beliefs (
                    belief_id, task_id, conversation_id, scope_kind, scope_ref, category, content, claim_text,
                    structured_assertion_json, promotion_candidate, status, confidence, trust_tier,
                    evidence_refs_json, evidence_case_ref, supersedes_json, contradicts_json,
                    epistemic_origin, freshness_class, last_validated_at, validation_basis,
                    supersession_reason, memory_ref, invalidated_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    belief_id,
                    task_id,
                    conversation_id,
                    scope_kind,
                    scope_ref,
                    category,
                    claim,
                    claim,
                    json.dumps(structured_assertion or {}, ensure_ascii=False),
                    int(bool(promotion_candidate)),
                    status,
                    confidence,
                    trust_tier,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    evidence_case_ref,
                    json.dumps(list(supersedes or []), ensure_ascii=False),
                    json.dumps(list(contradicts or []), ensure_ascii=False),
                    epistemic_origin,
                    freshness_class,
                    last_validated_at,
                    validation_basis,
                    supersession_reason,
                    memory_ref,
                    invalidated_at,
                    created_at,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="belief.recorded",
                entity_type="belief",
                entity_id=belief_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "conversation_id": conversation_id,
                    "scope_kind": scope_kind,
                    "scope_ref": scope_ref,
                    "category": category,
                    "claim_text": claim,
                    "structured_assertion": structured_assertion or {},
                    "promotion_candidate": bool(promotion_candidate),
                    "status": status,
                    "confidence": confidence,
                    "trust_tier": trust_tier,
                    "evidence_refs": list(evidence_refs or []),
                    "evidence_case_ref": evidence_case_ref,
                    "supersedes": list(supersedes or []),
                    "contradicts": list(contradicts or []),
                    "epistemic_origin": epistemic_origin,
                    "freshness_class": freshness_class,
                    "last_validated_at": last_validated_at,
                    "validation_basis": validation_basis,
                    "supersession_reason": supersession_reason,
                    "memory_ref": memory_ref,
                    "invalidated_at": invalidated_at,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )
        belief = self.get_belief(belief_id)
        assert belief is not None
        return belief

    def get_belief(self, belief_id: str) -> BeliefRecord | None:
        row = self._row("SELECT * FROM beliefs WHERE belief_id = ?", (belief_id,))
        return self._belief_from_row(row) if row is not None else None

    def list_beliefs(
        self,
        *,
        task_id: str | None = None,
        scope_kind: str | None = None,
        scope_ref: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[BeliefRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if scope_kind:
            clauses.append("scope_kind = ?")
            params.append(scope_kind)
        if scope_ref:
            clauses.append("scope_ref = ?")
            params.append(scope_ref)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(f"SELECT * FROM beliefs {where} ORDER BY updated_at DESC LIMIT ?", params)
        return [self._belief_from_row(row) for row in rows]

    def update_belief(
        self,
        belief_id: str,
        *,
        status: str | object = UNSET,
        memory_ref: str | None | object = UNSET,
        evidence_case_ref: str | None | object = UNSET,
        contradicts: list[str] | object = UNSET,
        supersedes: list[str] | object = UNSET,
        invalidated_at: float | None | object = UNSET,
        promotion_candidate: bool | object = UNSET,
        last_validated_at: float | None | object = UNSET,
        validation_basis: str | None | object = UNSET,
        supersession_reason: str | None | object = UNSET,
    ) -> None:
        belief = self.get_belief(belief_id)
        if belief is None:
            return
        updated_at = time.time()
        next_status = belief.status if status is UNSET else str(status)
        next_memory_ref = belief.memory_ref if memory_ref is UNSET else memory_ref
        next_evidence_case_ref = (
            belief.evidence_case_ref if evidence_case_ref is UNSET else evidence_case_ref
        )
        next_contradicts = (
            belief.contradicts if contradicts is UNSET else list(cast(list[str], contradicts))
        )
        next_supersedes = (
            belief.supersedes if supersedes is UNSET else list(cast(list[str], supersedes))
        )
        next_invalidated_at = belief.invalidated_at if invalidated_at is UNSET else invalidated_at
        next_promotion_candidate = (
            belief.promotion_candidate
            if promotion_candidate is UNSET
            else bool(promotion_candidate)
        )
        next_last_validated_at = (
            belief.last_validated_at if last_validated_at is UNSET else last_validated_at
        )
        next_validation_basis = (
            belief.validation_basis if validation_basis is UNSET else validation_basis
        )
        next_supersession_reason = (
            belief.supersession_reason if supersession_reason is UNSET else supersession_reason
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE beliefs
                SET status = ?, memory_ref = ?, evidence_case_ref = ?, contradicts_json = ?, supersedes_json = ?,
                    invalidated_at = ?, promotion_candidate = ?, last_validated_at = ?, validation_basis = ?,
                    supersession_reason = ?, updated_at = ?
                WHERE belief_id = ?
                """,
                (
                    next_status,
                    next_memory_ref,
                    next_evidence_case_ref,
                    json.dumps(list(next_contradicts), ensure_ascii=False),
                    json.dumps(list(next_supersedes), ensure_ascii=False),
                    next_invalidated_at,
                    int(next_promotion_candidate),
                    next_last_validated_at,
                    next_validation_basis,
                    next_supersession_reason,
                    updated_at,
                    belief_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="belief.updated",
                entity_type="belief",
                entity_id=belief_id,
                task_id=belief.task_id,
                actor="kernel",
                payload={
                    "status": next_status,
                    "memory_ref": next_memory_ref,
                    "evidence_case_ref": next_evidence_case_ref,
                    "contradicts": list(next_contradicts),
                    "supersedes": list(next_supersedes),
                    "invalidated_at": next_invalidated_at,
                    "promotion_candidate": next_promotion_candidate,
                    "last_validated_at": next_last_validated_at,
                    "validation_basis": next_validation_basis,
                    "supersession_reason": next_supersession_reason,
                    "updated_at": updated_at,
                },
            )

    def create_memory_record(
        self,
        *,
        task_id: str,
        conversation_id: str | None,
        category: str,
        content: str | None = None,
        claim_text: str | None = None,
        structured_assertion: dict[str, Any] | None = None,
        scope_kind: str = "conversation",
        scope_ref: str = "",
        promotion_reason: str = "belief_promotion",
        retention_class: str = "volatile_fact",
        status: str = "active",
        confidence: float = 0.5,
        trust_tier: str = "durable",
        evidence_refs: list[str] | None = None,
        memory_kind: str = "durable_fact",
        validation_basis: str | None = None,
        last_validated_at: float | None = None,
        supersession_reason: str | None = None,
        learned_from_reconciliation_ref: str | None = None,
        supersedes: list[str] | None = None,
        supersedes_memory_ids: list[str] | None = None,
        superseded_by_memory_id: str | None = None,
        source_belief_ref: str | None = None,
        invalidation_reason: str | None = None,
        invalidated_at: float | None = None,
        expires_at: float | None = None,
    ) -> MemoryRecord:
        from hermit.kernel.context.memory.governance import MemoryGovernanceService

        memory_id = self._id("memory")
        created_at = time.time()
        claim = (claim_text or content or "").strip()
        governance = MemoryGovernanceService()
        if (
            scope_kind == "conversation"
            and not scope_ref
            and promotion_reason == "belief_promotion"
            and retention_class == "volatile_fact"
        ):
            classification = governance.classify_claim(
                category=category,
                claim_text=claim,
                conversation_id=conversation_id,
                promotion_reason=promotion_reason,
            )
            scope_kind = classification.scope_kind
            scope_ref = classification.scope_ref
            category = classification.category
            retention_class = classification.retention_class
            structured_assertion = {
                **dict(classification.structured_assertion or {}),
                **dict(structured_assertion or {}),
            }
            if expires_at is None:
                expires_at = classification.expires_at
        normalized_status = "invalidated" if status == "superseded" else status
        normalized_reason = invalidation_reason or (
            "superseded" if status == "superseded" else None
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO memory_records (
                    memory_id, task_id, conversation_id, category, content, claim_text,
                    structured_assertion_json, scope_kind, scope_ref, promotion_reason, retention_class,
                    status, confidence, trust_tier, evidence_refs_json, memory_kind,
                    validation_basis, last_validated_at, supersession_reason,
                    learned_from_reconciliation_ref, supersedes_json, supersedes_memory_ids_json,
                    superseded_by_memory_id, source_belief_ref, invalidation_reason,
                    invalidated_at, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    task_id,
                    conversation_id,
                    category,
                    claim,
                    claim,
                    json.dumps(structured_assertion or {}, ensure_ascii=False),
                    scope_kind,
                    scope_ref,
                    promotion_reason,
                    retention_class,
                    normalized_status,
                    confidence,
                    trust_tier,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    memory_kind,
                    validation_basis,
                    last_validated_at,
                    supersession_reason,
                    learned_from_reconciliation_ref,
                    json.dumps(list(supersedes or []), ensure_ascii=False),
                    json.dumps(list(supersedes_memory_ids or []), ensure_ascii=False),
                    superseded_by_memory_id,
                    source_belief_ref,
                    normalized_reason,
                    invalidated_at,
                    expires_at,
                    created_at,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="memory.recorded",
                entity_type="memory_record",
                entity_id=memory_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "conversation_id": conversation_id,
                    "category": category,
                    "claim_text": claim,
                    "structured_assertion": structured_assertion or {},
                    "scope_kind": scope_kind,
                    "scope_ref": scope_ref,
                    "promotion_reason": promotion_reason,
                    "retention_class": retention_class,
                    "status": normalized_status,
                    "confidence": confidence,
                    "trust_tier": trust_tier,
                    "evidence_refs": list(evidence_refs or []),
                    "memory_kind": memory_kind,
                    "validation_basis": validation_basis,
                    "last_validated_at": last_validated_at,
                    "supersession_reason": supersession_reason,
                    "learned_from_reconciliation_ref": learned_from_reconciliation_ref,
                    "supersedes": list(supersedes or []),
                    "supersedes_memory_ids": list(supersedes_memory_ids or []),
                    "superseded_by_memory_id": superseded_by_memory_id,
                    "source_belief_ref": source_belief_ref,
                    "invalidation_reason": normalized_reason,
                    "invalidated_at": invalidated_at,
                    "expires_at": expires_at,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )
        record = self.get_memory_record(memory_id)
        assert record is not None
        return record

    def get_memory_record(self, memory_id: str) -> MemoryRecord | None:
        row = self._row("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,))
        return self._memory_record_from_row(row) if row is not None else None

    def list_memory_records(
        self,
        *,
        status: str | None = None,
        conversation_id: str | None = None,
        scope_kind: str | None = None,
        scope_ref: str | None = None,
        task_id: str | None = None,
        memory_kind: str | None = None,
        limit: int = 200,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status:
            if status == "active":
                clauses.append("status = 'active' AND (expires_at IS NULL OR expires_at > ?)")
                params.append(time.time())
            else:
                clauses.append("status = ?")
                params.append(status)
        if conversation_id:
            clauses.append("(conversation_id = ? OR conversation_id IS NULL)")
            params.append(conversation_id)
        if scope_kind:
            clauses.append("scope_kind = ?")
            params.append(scope_kind)
        if scope_ref:
            clauses.append("scope_ref = ?")
            params.append(scope_ref)
        if memory_kind:
            clauses.append("memory_kind = ?")
            params.append(memory_kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM memory_records {where} ORDER BY updated_at DESC LIMIT ?", params
        )
        return [self._memory_record_from_row(row) for row in rows]

    def update_memory_record(
        self,
        memory_id: str,
        *,
        status: str | object = UNSET,
        supersedes: list[str] | object = UNSET,
        supersedes_memory_ids: list[str] | object = UNSET,
        superseded_by_memory_id: str | None | object = UNSET,
        invalidation_reason: str | None | object = UNSET,
        invalidated_at: float | None | object = UNSET,
        expires_at: float | None | object = UNSET,
        validation_basis: str | None | object = UNSET,
        last_validated_at: float | None | object = UNSET,
        supersession_reason: str | None | object = UNSET,
        learned_from_reconciliation_ref: str | None | object = UNSET,
        structured_assertion: dict[str, Any] | None | object = UNSET,
        freshness_class: str | None | object = UNSET,
        last_accessed_at: float | None | object = UNSET,
        confidence: float | object = UNSET,
    ) -> None:
        record = self.get_memory_record(memory_id)
        if record is None:
            return
        updated_at = time.time()
        requested_status = record.status if status is UNSET else str(status)
        next_status = "invalidated" if requested_status == "superseded" else requested_status
        next_supersedes = (
            record.supersedes if supersedes is UNSET else list(cast(list[str], supersedes))
        )
        next_supersedes_memory_ids = (
            record.supersedes_memory_ids
            if supersedes_memory_ids is UNSET
            else list(cast(list[str], supersedes_memory_ids))
        )
        next_superseded_by_memory_id = (
            record.superseded_by_memory_id
            if superseded_by_memory_id is UNSET
            else superseded_by_memory_id
        )
        next_invalidation_reason = (
            record.invalidation_reason if invalidation_reason is UNSET else invalidation_reason
        )
        if requested_status == "superseded" and next_invalidation_reason is None:
            next_invalidation_reason = "superseded"
        next_invalidated_at = record.invalidated_at if invalidated_at is UNSET else invalidated_at
        next_expires_at = record.expires_at if expires_at is UNSET else expires_at
        next_validation_basis = (
            record.validation_basis if validation_basis is UNSET else validation_basis
        )
        next_last_validated_at = (
            record.last_validated_at if last_validated_at is UNSET else last_validated_at
        )
        next_supersession_reason = (
            record.supersession_reason if supersession_reason is UNSET else supersession_reason
        )
        next_reconciliation_ref = (
            record.learned_from_reconciliation_ref
            if learned_from_reconciliation_ref is UNSET
            else learned_from_reconciliation_ref
        )
        next_structured_assertion = (
            record.structured_assertion if structured_assertion is UNSET else structured_assertion
        )
        next_freshness_class = (
            record.freshness_class if freshness_class is UNSET else freshness_class
        )
        next_last_accessed_at = (
            record.last_accessed_at if last_accessed_at is UNSET else last_accessed_at
        )
        next_confidence = record.confidence if confidence is UNSET else float(confidence)
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE memory_records
                SET status = ?, supersedes_json = ?, supersedes_memory_ids_json = ?,
                    superseded_by_memory_id = ?, invalidation_reason = ?, invalidated_at = ?,
                    expires_at = ?, validation_basis = ?, last_validated_at = ?, supersession_reason = ?,
                    learned_from_reconciliation_ref = ?, structured_assertion_json = ?,
                    freshness_class = ?, last_accessed_at = ?,
                    confidence = ?,
                    updated_at = ?
                WHERE memory_id = ?
                """,
                (
                    next_status,
                    json.dumps(list(next_supersedes), ensure_ascii=False),
                    json.dumps(list(next_supersedes_memory_ids), ensure_ascii=False),
                    next_superseded_by_memory_id,
                    next_invalidation_reason,
                    next_invalidated_at,
                    next_expires_at,
                    next_validation_basis,
                    next_last_validated_at,
                    next_supersession_reason,
                    next_reconciliation_ref,
                    json.dumps(next_structured_assertion or {}, ensure_ascii=False),
                    next_freshness_class,
                    next_last_accessed_at,
                    next_confidence,
                    updated_at,
                    memory_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="memory.updated",
                entity_type="memory_record",
                entity_id=memory_id,
                task_id=record.task_id,
                actor="kernel",
                payload={
                    "status": next_status,
                    "supersedes": list(next_supersedes),
                    "supersedes_memory_ids": list(next_supersedes_memory_ids),
                    "superseded_by_memory_id": next_superseded_by_memory_id,
                    "invalidation_reason": next_invalidation_reason,
                    "invalidated_at": next_invalidated_at,
                    "expires_at": next_expires_at,
                    "validation_basis": next_validation_basis,
                    "last_validated_at": next_last_validated_at,
                    "supersession_reason": next_supersession_reason,
                    "learned_from_reconciliation_ref": next_reconciliation_ref,
                    "structured_assertion": next_structured_assertion or {},
                    "freshness_class": next_freshness_class,
                    "last_accessed_at": next_last_accessed_at,
                    "updated_at": updated_at,
                },
            )

    def create_rollback(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        receipt_ref: str,
        action_type: str,
        strategy: str,
        status: str = "not_requested",
        result_summary: str | None = None,
        artifact_refs: list[str] | None = None,
    ) -> RollbackRecord:
        rollback_id = self._id("rollback")
        created_at = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO rollbacks (
                    rollback_id, task_id, step_id, step_attempt_id, receipt_ref,
                    action_type, strategy, status, result_summary, artifact_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rollback_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    receipt_ref,
                    action_type,
                    strategy,
                    status,
                    result_summary,
                    json.dumps(list(artifact_refs or []), ensure_ascii=False),
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="rollback.recorded",
                entity_type="rollback",
                entity_id=rollback_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "receipt_ref": receipt_ref,
                    "action_type": action_type,
                    "strategy": strategy,
                    "status": status,
                    "result_summary": result_summary,
                    "artifact_refs": list(artifact_refs or []),
                    "created_at": created_at,
                },
            )
        record = self.get_rollback(rollback_id)
        assert record is not None
        return record

    def get_rollback(self, rollback_id: str) -> RollbackRecord | None:
        row = self._row("SELECT * FROM rollbacks WHERE rollback_id = ?", (rollback_id,))
        return self._rollback_from_row(row) if row is not None else None

    def get_rollback_for_receipt(self, receipt_ref: str) -> RollbackRecord | None:
        row = self._row(
            "SELECT * FROM rollbacks WHERE receipt_ref = ? ORDER BY created_at DESC LIMIT 1",
            (receipt_ref,),
        )
        return self._rollback_from_row(row) if row is not None else None

    def update_rollback(
        self,
        rollback_id: str,
        *,
        status: str,
        result_summary: str | None = None,
        executed_at: float | None | object = UNSET,
    ) -> None:
        record = self.get_rollback(rollback_id)
        if record is None:
            return
        final_executed_at = (
            time.time()
            if executed_at is UNSET and status in {"succeeded", "failed"}
            else (record.executed_at if executed_at is UNSET else executed_at)
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE rollbacks
                SET status = ?, result_summary = ?, executed_at = ?
                WHERE rollback_id = ?
                """,
                (status, result_summary, final_executed_at, rollback_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="rollback.updated",
                entity_type="rollback",
                entity_id=rollback_id,
                task_id=record.task_id,
                step_id=record.step_id,
                actor="kernel",
                payload={
                    "status": status,
                    "result_summary": result_summary,
                    "executed_at": final_executed_at,
                },
            )

    def create_approval(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        approval_type: str,
        requested_action: dict[str, Any],
        request_packet_ref: str | None,
        requested_action_ref: str | None = None,
        approval_packet_ref: str | None = None,
        policy_result_ref: str | None = None,
        requested_contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        evidence_case_ref: str | None = None,
        drift_expiry: float | None = None,
        fallback_contract_refs: list[str] | None = None,
        decision_ref: str | None = None,
        state_witness_ref: str | None = None,
        expires_at: float | None = None,
    ) -> ApprovalRecord:
        approval_id = self._id("approval")
        requested_at = time.time()
        approval_packet_ref = approval_packet_ref or request_packet_ref
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO approvals (
                    approval_id, task_id, step_id, step_attempt_id, status,
                    approval_type, requested_action_json, request_packet_ref, requested_action_ref,
                    approval_packet_ref, policy_result_ref, requested_contract_ref,
                    authorization_plan_ref, evidence_case_ref, drift_expiry,
                    fallback_contract_refs_json, decision_ref, state_witness_ref,
                    requested_at, expires_at, resolution_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    "pending",
                    approval_type,
                    json.dumps(requested_action, ensure_ascii=False),
                    request_packet_ref,
                    requested_action_ref,
                    approval_packet_ref,
                    policy_result_ref,
                    requested_contract_ref,
                    authorization_plan_ref,
                    evidence_case_ref,
                    drift_expiry,
                    json.dumps(list(fallback_contract_refs or []), ensure_ascii=False),
                    decision_ref,
                    state_witness_ref,
                    requested_at,
                    expires_at,
                    "{}",
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="approval.requested",
                entity_type="approval",
                entity_id=approval_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    **requested_action,
                    "status": "pending",
                    "requested_action_ref": requested_action_ref,
                    "decision_ref": decision_ref,
                    "request_packet_ref": request_packet_ref,
                    "approval_packet_ref": approval_packet_ref,
                    "policy_result_ref": policy_result_ref,
                    "requested_contract_ref": requested_contract_ref,
                    "authorization_plan_ref": authorization_plan_ref,
                    "evidence_case_ref": evidence_case_ref,
                    "drift_expiry": drift_expiry,
                    "fallback_contract_refs": list(fallback_contract_refs or []),
                    "state_witness_ref": state_witness_ref,
                    "requested_at": requested_at,
                    "expires_at": expires_at,
                    "resolved_at": None,
                    "resolved_by": None,
                    "resolution": {},
                },
            )
        approval = self.get_approval(approval_id)
        assert approval is not None
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        row = self._row("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
        return self._approval_from_row(row) if row is not None else None

    def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("task_id IN (SELECT task_id FROM tasks WHERE conversation_id = ?)")
            params.append(conversation_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM approvals {where} ORDER BY requested_at DESC LIMIT ?", params
        )
        return [self._approval_from_row(row) for row in rows]

    def get_latest_pending_approval(self, conversation_id: str) -> ApprovalRecord | None:
        approvals = self.list_approvals(conversation_id=conversation_id, status="pending", limit=1)
        return approvals[0] if approvals else None

    def resolve_approval(
        self,
        approval_id: str,
        *,
        status: str,
        resolved_by: str,
        resolution: dict[str, Any],
    ) -> None:
        now = time.time()
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        resolved_by_principal_id = self._ensure_principal_id(resolved_by)
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by_principal_id = ?, resolution_json = ?
                WHERE approval_id = ?
                """,
                (
                    status,
                    now,
                    resolved_by_principal_id,
                    json.dumps(resolution, ensure_ascii=False),
                    approval_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"approval.{status}",
                entity_type="approval",
                entity_id=approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                actor=resolved_by,
                payload={
                    "status": status,
                    "resolved_at": now,
                    "resolved_by_principal_id": resolved_by_principal_id,
                    "resolution": resolution,
                },
            )

    def update_approval_resolution(self, approval_id: str, resolution: dict[str, Any]) -> None:
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        with self._get_conn():
            self._get_conn().execute(
                "UPDATE approvals SET resolution_json = ? WHERE approval_id = ?",
                (json.dumps(resolution, ensure_ascii=False), approval_id),
            )

    def consume_approval(self, approval_id: str, *, actor: str = "kernel") -> None:
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        resolution = dict(approval.resolution or {})
        resolution["status"] = "consumed"
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE approvals
                SET status = ?, resolution_json = ?
                WHERE approval_id = ?
                """,
                ("consumed", json.dumps(resolution, ensure_ascii=False), approval_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="approval.consumed",
                entity_type="approval",
                entity_id=approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                actor=actor,
                payload={
                    "status": "consumed",
                    "resolved_at": approval.resolved_at,
                    "resolved_by": actor,
                    "resolution": resolution,
                },
            )

    def create_receipt(
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
        receipt_bundle_ref: str | None = None,
        proof_mode: str = "hash_only",
        verifiability: str | None = None,
        signature: str | None = None,
        signer_ref: str | None = None,
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_status: str = "not_requested",
        rollback_ref: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
        observed_effect_summary: str | None = None,
        reconciliation_required: bool = False,
    ) -> ReceiptRecord:
        receipt_id = self._id("receipt")
        created_at = time.time()
        receipt_class = receipt_class or action_type
        policy_result_ref = policy_result_ref or policy_ref
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO receipts (
                    receipt_id, task_id, step_id, step_attempt_id, action_type, receipt_class,
                    input_refs_json, environment_ref, policy_result_json,
                    approval_ref, output_refs_json, result_summary, result_code,
                    decision_ref, capability_grant_ref, workspace_lease_ref, policy_ref, action_request_ref,
                    policy_result_ref, contract_ref, authorization_plan_ref, witness_ref, idempotency_key,
                    receipt_bundle_ref, proof_mode, verifiability, signature, signer_ref,
                    rollback_supported, rollback_strategy, rollback_status, rollback_ref,
                    rollback_artifact_refs_json, observed_effect_summary, reconciliation_required, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    action_type,
                    receipt_class,
                    json.dumps(input_refs, ensure_ascii=False),
                    environment_ref,
                    json.dumps(policy_result, ensure_ascii=False),
                    approval_ref,
                    json.dumps(output_refs, ensure_ascii=False),
                    result_summary,
                    result_code,
                    decision_ref,
                    capability_grant_ref,
                    workspace_lease_ref,
                    policy_ref,
                    action_request_ref,
                    policy_result_ref,
                    contract_ref,
                    authorization_plan_ref,
                    witness_ref,
                    idempotency_key,
                    receipt_bundle_ref,
                    proof_mode,
                    verifiability,
                    signature,
                    signer_ref,
                    int(rollback_supported),
                    rollback_strategy,
                    rollback_status,
                    rollback_ref,
                    json.dumps(list(rollback_artifact_refs or []), ensure_ascii=False),
                    observed_effect_summary,
                    int(reconciliation_required),
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="receipt.issued",
                entity_type="receipt",
                entity_id=receipt_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
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
                    "receipt_bundle_ref": receipt_bundle_ref,
                    "proof_mode": proof_mode,
                    "verifiability": verifiability,
                    "signature": signature,
                    "signer_ref": signer_ref,
                    "rollback_supported": rollback_supported,
                    "rollback_strategy": rollback_strategy,
                    "rollback_status": rollback_status,
                    "rollback_ref": rollback_ref,
                    "rollback_artifact_refs": list(rollback_artifact_refs or []),
                    "observed_effect_summary": observed_effect_summary,
                    "reconciliation_required": bool(reconciliation_required),
                },
            )
        return ReceiptRecord(
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
            receipt_bundle_ref=receipt_bundle_ref,
            proof_mode=proof_mode,
            verifiability=verifiability,
            signature=signature,
            signer_ref=signer_ref,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_status=rollback_status,
            rollback_ref=rollback_ref,
            rollback_artifact_refs=list(rollback_artifact_refs or []),
            observed_effect_summary=observed_effect_summary,
            reconciliation_required=bool(reconciliation_required),
            created_at=created_at,
        )

    def update_receipt_proof_fields(
        self,
        receipt_id: str,
        *,
        receipt_bundle_ref: str | object = UNSET,
        proof_mode: str | object = UNSET,
        verifiability: str | object = UNSET,
        signature: str | None | object = UNSET,
        signer_ref: str | None | object = UNSET,
    ) -> None:
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return
        updated_bundle_ref = (
            receipt.receipt_bundle_ref if receipt_bundle_ref is UNSET else receipt_bundle_ref
        )
        updated_proof_mode = receipt.proof_mode if proof_mode is UNSET else proof_mode
        updated_verifiability = receipt.verifiability if verifiability is UNSET else verifiability
        updated_signature = receipt.signature if signature is UNSET else signature
        updated_signer_ref = receipt.signer_ref if signer_ref is UNSET else signer_ref
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE receipts
                SET receipt_bundle_ref = ?, proof_mode = ?, verifiability = ?, signature = ?, signer_ref = ?
                WHERE receipt_id = ?
                """,
                (
                    updated_bundle_ref,
                    updated_proof_mode,
                    updated_verifiability,
                    updated_signature,
                    updated_signer_ref,
                    receipt_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="receipt.proof_updated",
                entity_type="receipt",
                entity_id=receipt_id,
                task_id=receipt.task_id,
                step_id=receipt.step_id,
                actor="kernel",
                payload={
                    "receipt_bundle_ref": updated_bundle_ref,
                    "proof_mode": updated_proof_mode,
                    "verifiability": updated_verifiability,
                    "signature": updated_signature,
                    "signer_ref": updated_signer_ref,
                },
            )

    def update_receipt_rollback_fields(
        self,
        receipt_id: str,
        *,
        rollback_supported: bool | object = UNSET,
        rollback_strategy: str | None | object = UNSET,
        rollback_status: str | object = UNSET,
        rollback_ref: str | None | object = UNSET,
        rollback_artifact_refs: list[str] | object = UNSET,
    ) -> None:
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return
        updated_supported = (
            receipt.rollback_supported if rollback_supported is UNSET else bool(rollback_supported)
        )
        updated_strategy = (
            receipt.rollback_strategy if rollback_strategy is UNSET else rollback_strategy
        )
        updated_status = (
            receipt.rollback_status if rollback_status is UNSET else str(rollback_status)
        )
        updated_ref = receipt.rollback_ref if rollback_ref is UNSET else rollback_ref
        updated_artifact_refs = (
            receipt.rollback_artifact_refs
            if rollback_artifact_refs is UNSET
            else list(cast(list[str], rollback_artifact_refs))
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE receipts
                SET rollback_supported = ?,
                    rollback_strategy = ?,
                    rollback_status = ?,
                    rollback_ref = ?,
                    rollback_artifact_refs_json = ?
                WHERE receipt_id = ?
                """,
                (
                    int(updated_supported),
                    updated_strategy,
                    updated_status,
                    updated_ref,
                    json.dumps(list(updated_artifact_refs), ensure_ascii=False),
                    receipt_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="receipt.rollback_updated",
                entity_type="receipt",
                entity_id=receipt_id,
                task_id=receipt.task_id,
                step_id=receipt.step_id,
                actor="kernel",
                payload={
                    "rollback_supported": updated_supported,
                    "rollback_strategy": updated_strategy,
                    "rollback_status": updated_status,
                    "rollback_ref": updated_ref,
                    "rollback_artifact_refs": list(updated_artifact_refs),
                },
            )

    def get_receipt(self, receipt_id: str) -> ReceiptRecord | None:
        row = self._row("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,))
        return self._receipt_from_row(row) if row is not None else None

    def list_receipts(self, *, task_id: str | None = None, limit: int = 50) -> list[ReceiptRecord]:
        if task_id:
            query = "SELECT * FROM receipts WHERE task_id = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM receipts ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        rows = self._rows(query, params)
        return [self._receipt_from_row(row) for row in rows]
