from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.models import (
    ApprovalRecord,
    ArtifactRecord,
    BeliefRecord,
    DecisionRecord,
    ExecutionPermitRecord,
    MemoryRecord,
    PathGrantRecord,
    ReceiptRecord,
    RollbackRecord,
)
from hermit.kernel.store_support import _UNSET


class KernelLedgerStoreMixin:
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
    ) -> ArtifactRecord:
        artifact_id = self._id("artifact")
        created_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, task_id, step_id, kind, uri, content_hash, producer,
                    retention_class, trust_tier, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            metadata=metadata or {},
            created_at=created_at,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return self._artifact_from_row(row) if row is not None else None

    def list_artifacts(self, *, task_id: str | None = None, limit: int = 200) -> list[ArtifactRecord]:
        if task_id:
            query = "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [self._artifact_from_row(row) for row in rows]

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
        action_type: str | None = None,
        decided_by: str = "kernel",
    ) -> DecisionRecord:
        decision_id = self._id("decision")
        created_at = time.time()
        payload = {
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "decision_type": decision_type,
            "verdict": verdict,
            "reason": reason,
            "evidence_refs": list(evidence_refs or []),
            "policy_ref": policy_ref,
            "approval_ref": approval_ref,
            "action_type": action_type,
            "decided_by": decided_by,
            "created_at": created_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, task_id, step_id, step_attempt_id, decision_type, verdict, reason,
                    evidence_refs_json, policy_ref, approval_ref, action_type, decided_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    decision_type,
                    verdict,
                    reason,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    policy_ref,
                    approval_ref,
                    action_type,
                    decided_by,
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
        with self._lock:
            row = self._row("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,))
        return self._decision_from_row(row) if row is not None else None

    def list_decisions(self, *, task_id: str | None = None, limit: int = 50) -> list[DecisionRecord]:
        if task_id:
            query = "SELECT * FROM decisions WHERE task_id = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [self._decision_from_row(row) for row in rows]

    def create_execution_permit(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        decision_ref: str,
        approval_ref: str | None,
        policy_ref: str | None,
        action_class: str,
        resource_scope: list[str],
        constraints: dict[str, Any] | None,
        idempotency_key: str | None,
        expires_at: float | None,
        status: str = "issued",
    ) -> ExecutionPermitRecord:
        permit_id = self._id("permit")
        issued_at = time.time()
        payload = {
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "decision_ref": decision_ref,
            "approval_ref": approval_ref,
            "policy_ref": policy_ref,
            "action_class": action_class,
            "resource_scope": list(resource_scope),
            "constraints": dict(constraints or {}),
            "idempotency_key": idempotency_key,
            "status": status,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO execution_permits (
                    permit_id, task_id, step_id, step_attempt_id, decision_ref, approval_ref, policy_ref,
                    action_class, resource_scope_json, constraints_json, idempotency_key, status, issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    permit_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    decision_ref,
                    approval_ref,
                    policy_ref,
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
                event_type="permit.issued",
                entity_type="execution_permit",
                entity_id=permit_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload=payload,
            )
        permit = self.get_execution_permit(permit_id)
        assert permit is not None
        return permit

    def get_execution_permit(self, permit_id: str) -> ExecutionPermitRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM execution_permits WHERE permit_id = ?", (permit_id,))
        return self._execution_permit_from_row(row) if row is not None else None

    def update_execution_permit(
        self,
        permit_id: str,
        *,
        status: str,
        consumed_at: float | None | object = _UNSET,
    ) -> None:
        permit = self.get_execution_permit(permit_id)
        if permit is None:
            return
        updated_consumed_at = permit.consumed_at if consumed_at is _UNSET else consumed_at
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE execution_permits
                SET status = ?, consumed_at = ?
                WHERE permit_id = ?
                """,
                (status, updated_consumed_at, permit_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"permit.{status}",
                entity_type="execution_permit",
                entity_id=permit_id,
                task_id=permit.task_id,
                step_id=permit.step_id,
                actor="kernel",
                payload={
                    "task_id": permit.task_id,
                    "step_id": permit.step_id,
                    "step_attempt_id": permit.step_attempt_id,
                    "decision_ref": permit.decision_ref,
                    "approval_ref": permit.approval_ref,
                    "policy_ref": permit.policy_ref,
                    "action_class": permit.action_class,
                    "resource_scope": list(permit.resource_scope),
                    "constraints": dict(permit.constraints),
                    "status": status,
                    "issued_at": permit.issued_at,
                    "expires_at": permit.expires_at,
                    "consumed_at": updated_consumed_at,
                },
            )

    def list_execution_permits(self, *, task_id: str | None = None, limit: int = 50) -> list[ExecutionPermitRecord]:
        if task_id:
            query = "SELECT * FROM execution_permits WHERE task_id = ? ORDER BY issued_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM execution_permits ORDER BY issued_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [self._execution_permit_from_row(row) for row in rows]

    def create_capability_grant(self, **kwargs: Any) -> ExecutionPermitRecord:
        return self.create_execution_permit(**kwargs)

    def get_capability_grant(self, permit_id: str) -> ExecutionPermitRecord | None:
        return self.get_execution_permit(permit_id)

    def update_capability_grant(self, permit_id: str, **kwargs: Any) -> None:
        self.update_execution_permit(permit_id, **kwargs)

    def list_capability_grants(self, *, task_id: str | None = None, limit: int = 50) -> list[ExecutionPermitRecord]:
        return self.list_execution_permits(task_id=task_id, limit=limit)

    def create_path_grant(
        self,
        *,
        subject_kind: str,
        subject_ref: str,
        action_class: str,
        path_prefix: str,
        path_display: str,
        created_by: str,
        approval_ref: str | None,
        decision_ref: str | None,
        policy_ref: str | None,
        status: str = "active",
        expires_at: float | None = None,
    ) -> PathGrantRecord:
        grant_id = self._id("grant")
        created_at = time.time()
        payload = {
            "subject_kind": subject_kind,
            "subject_ref": subject_ref,
            "action_class": action_class,
            "path_prefix": path_prefix,
            "path_display": path_display,
            "created_by": created_by,
            "approval_ref": approval_ref,
            "decision_ref": decision_ref,
            "policy_ref": policy_ref,
            "status": status,
            "expires_at": expires_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO path_grants (
                    grant_id, subject_kind, subject_ref, action_class, path_prefix, path_display,
                    created_by, approval_ref, decision_ref, policy_ref, status, created_at, expires_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    grant_id,
                    subject_kind,
                    subject_ref,
                    action_class,
                    path_prefix,
                    path_display,
                    created_by,
                    approval_ref,
                    decision_ref,
                    policy_ref,
                    status,
                    created_at,
                    expires_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="grant.created",
                entity_type="path_grant",
                entity_id=grant_id,
                task_id=None,
                step_id=None,
                actor=created_by,
                payload=payload,
            )
        grant = self.get_path_grant(grant_id)
        assert grant is not None
        return grant

    def get_path_grant(self, grant_id: str) -> PathGrantRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM path_grants WHERE grant_id = ?", (grant_id,))
        return self._path_grant_from_row(row) if row is not None else None

    def list_path_grants(
        self,
        *,
        subject_kind: str | None = None,
        subject_ref: str | None = None,
        status: str | None = None,
        action_class: str | None = None,
        limit: int = 50,
    ) -> list[PathGrantRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_kind:
            clauses.append("subject_kind = ?")
            params.append(subject_kind)
        if subject_ref:
            clauses.append("subject_ref = ?")
            params.append(subject_ref)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if action_class:
            clauses.append("action_class = ?")
            params.append(action_class)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._rows(
                f"SELECT * FROM path_grants {where} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            )
        return [self._path_grant_from_row(row) for row in rows]

    def update_path_grant(
        self,
        grant_id: str,
        *,
        status: str | object = _UNSET,
        expires_at: float | None | object = _UNSET,
        last_used_at: float | None | object = _UNSET,
        actor: str = "kernel",
        event_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        grant = self.get_path_grant(grant_id)
        if grant is None:
            return
        updated_status = grant.status if status is _UNSET else str(status)
        updated_expires_at = grant.expires_at if expires_at is _UNSET else expires_at
        updated_last_used_at = grant.last_used_at if last_used_at is _UNSET else last_used_at
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE path_grants
                SET status = ?, expires_at = ?, last_used_at = ?
                WHERE grant_id = ?
                """,
                (updated_status, updated_expires_at, updated_last_used_at, grant_id),
            )
            if event_type:
                self._append_event_tx(
                    event_id=self._id("event"),
                    event_type=event_type,
                    entity_type="path_grant",
                    entity_id=grant_id,
                    task_id=None,
                    step_id=None,
                    actor=actor,
                    payload={
                        "subject_kind": grant.subject_kind,
                        "subject_ref": grant.subject_ref,
                        "action_class": grant.action_class,
                        "path_prefix": grant.path_prefix,
                        "path_display": grant.path_display,
                        "created_by": grant.created_by,
                        "approval_ref": grant.approval_ref,
                        "decision_ref": grant.decision_ref,
                        "policy_ref": grant.policy_ref,
                        "status": updated_status,
                        "expires_at": updated_expires_at,
                        "last_used_at": updated_last_used_at,
                        **(payload or {}),
                    },
                )

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
        supersedes: list[str] | None = None,
        contradicts: list[str] | None = None,
        memory_ref: str | None = None,
        invalidated_at: float | None = None,
    ) -> BeliefRecord:
        belief_id = self._id("belief")
        created_at = time.time()
        claim = (claim_text or content or "").strip()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO beliefs (
                    belief_id, task_id, conversation_id, scope_kind, scope_ref, category, content, claim_text,
                    structured_assertion_json, promotion_candidate, status, confidence, trust_tier,
                    evidence_refs_json, supersedes_json, contradicts_json, memory_ref, invalidated_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(list(supersedes or []), ensure_ascii=False),
                    json.dumps(list(contradicts or []), ensure_ascii=False),
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
                    "supersedes": list(supersedes or []),
                    "contradicts": list(contradicts or []),
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
        with self._lock:
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
        with self._lock:
            rows = self._rows(f"SELECT * FROM beliefs {where} ORDER BY updated_at DESC LIMIT ?", params)
        return [self._belief_from_row(row) for row in rows]

    def update_belief(
        self,
        belief_id: str,
        *,
        status: str | object = _UNSET,
        memory_ref: str | None | object = _UNSET,
        contradicts: list[str] | object = _UNSET,
        supersedes: list[str] | object = _UNSET,
        invalidated_at: float | None | object = _UNSET,
        promotion_candidate: bool | object = _UNSET,
    ) -> None:
        belief = self.get_belief(belief_id)
        if belief is None:
            return
        updated_at = time.time()
        next_status = belief.status if status is _UNSET else str(status)
        next_memory_ref = belief.memory_ref if memory_ref is _UNSET else memory_ref
        next_contradicts = belief.contradicts if contradicts is _UNSET else list(contradicts)
        next_supersedes = belief.supersedes if supersedes is _UNSET else list(supersedes)
        next_invalidated_at = belief.invalidated_at if invalidated_at is _UNSET else invalidated_at
        next_promotion_candidate = (
            belief.promotion_candidate if promotion_candidate is _UNSET else bool(promotion_candidate)
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE beliefs
                SET status = ?, memory_ref = ?, contradicts_json = ?, supersedes_json = ?,
                    invalidated_at = ?, promotion_candidate = ?, updated_at = ?
                WHERE belief_id = ?
                """,
                (
                    next_status,
                    next_memory_ref,
                    json.dumps(list(next_contradicts), ensure_ascii=False),
                    json.dumps(list(next_supersedes), ensure_ascii=False),
                    next_invalidated_at,
                    int(next_promotion_candidate),
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
                    "contradicts": list(next_contradicts),
                    "supersedes": list(next_supersedes),
                    "invalidated_at": next_invalidated_at,
                    "promotion_candidate": next_promotion_candidate,
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
        supersedes: list[str] | None = None,
        supersedes_memory_ids: list[str] | None = None,
        superseded_by_memory_id: str | None = None,
        source_belief_ref: str | None = None,
        invalidation_reason: str | None = None,
        invalidated_at: float | None = None,
        expires_at: float | None = None,
    ) -> MemoryRecord:
        from hermit.kernel.memory_governance import MemoryGovernanceService

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
        normalized_reason = invalidation_reason or ("superseded" if status == "superseded" else None)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_records (
                    memory_id, task_id, conversation_id, category, content, claim_text,
                    structured_assertion_json, scope_kind, scope_ref, promotion_reason, retention_class,
                    status, confidence, trust_tier, evidence_refs_json, supersedes_json,
                    supersedes_memory_ids_json, superseded_by_memory_id, source_belief_ref,
                    invalidation_reason, invalidated_at, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        with self._lock:
            row = self._row("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,))
        return self._memory_record_from_row(row) if row is not None else None

    def list_memory_records(
        self,
        *,
        status: str | None = None,
        conversation_id: str | None = None,
        scope_kind: str | None = None,
        scope_ref: str | None = None,
        limit: int = 200,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
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
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._rows(f"SELECT * FROM memory_records {where} ORDER BY updated_at DESC LIMIT ?", params)
        return [self._memory_record_from_row(row) for row in rows]

    def update_memory_record(
        self,
        memory_id: str,
        *,
        status: str | object = _UNSET,
        supersedes: list[str] | object = _UNSET,
        supersedes_memory_ids: list[str] | object = _UNSET,
        superseded_by_memory_id: str | None | object = _UNSET,
        invalidation_reason: str | None | object = _UNSET,
        invalidated_at: float | None | object = _UNSET,
        expires_at: float | None | object = _UNSET,
    ) -> None:
        record = self.get_memory_record(memory_id)
        if record is None:
            return
        updated_at = time.time()
        requested_status = record.status if status is _UNSET else str(status)
        next_status = "invalidated" if requested_status == "superseded" else requested_status
        next_supersedes = record.supersedes if supersedes is _UNSET else list(supersedes)
        next_supersedes_memory_ids = (
            record.supersedes_memory_ids if supersedes_memory_ids is _UNSET else list(supersedes_memory_ids)
        )
        next_superseded_by_memory_id = (
            record.superseded_by_memory_id if superseded_by_memory_id is _UNSET else superseded_by_memory_id
        )
        next_invalidation_reason = (
            record.invalidation_reason
            if invalidation_reason is _UNSET
            else invalidation_reason
        )
        if requested_status == "superseded" and next_invalidation_reason is None:
            next_invalidation_reason = "superseded"
        next_invalidated_at = record.invalidated_at if invalidated_at is _UNSET else invalidated_at
        next_expires_at = record.expires_at if expires_at is _UNSET else expires_at
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE memory_records
                SET status = ?, supersedes_json = ?, supersedes_memory_ids_json = ?,
                    superseded_by_memory_id = ?, invalidation_reason = ?, invalidated_at = ?,
                    expires_at = ?, updated_at = ?
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
        with self._lock, self._conn:
            self._conn.execute(
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
        with self._lock:
            row = self._row("SELECT * FROM rollbacks WHERE rollback_id = ?", (rollback_id,))
        return self._rollback_from_row(row) if row is not None else None

    def get_rollback_for_receipt(self, receipt_ref: str) -> RollbackRecord | None:
        with self._lock:
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
        executed_at: float | None | object = _UNSET,
    ) -> None:
        record = self.get_rollback(rollback_id)
        if record is None:
            return
        final_executed_at = (
            time.time()
            if executed_at is _UNSET and status in {"succeeded", "failed"}
            else (record.executed_at if executed_at is _UNSET else executed_at)
        )
        with self._lock, self._conn:
            self._conn.execute(
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
        decision_ref: str | None = None,
        state_witness_ref: str | None = None,
    ) -> ApprovalRecord:
        approval_id = self._id("approval")
        requested_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, task_id, step_id, step_attempt_id, status,
                    approval_type, requested_action_json, request_packet_ref, decision_ref, state_witness_ref,
                    requested_at, resolution_json
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    approval_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    approval_type,
                    json.dumps(requested_action, ensure_ascii=False),
                    request_packet_ref,
                    decision_ref,
                    state_witness_ref,
                    requested_at,
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
                    "decision_ref": decision_ref,
                    "request_packet_ref": request_packet_ref,
                    "state_witness_ref": state_witness_ref,
                    "requested_at": requested_at,
                    "resolved_at": None,
                    "resolved_by": None,
                    "resolution": {},
                },
            )
        approval = self.get_approval(approval_id)
        assert approval is not None
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
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
        clauses = []
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
        with self._lock:
            rows = self._rows(f"SELECT * FROM approvals {where} ORDER BY requested_at DESC LIMIT ?", params)
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
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by = ?, resolution_json = ?
                WHERE approval_id = ?
                """,
                (status, now, resolved_by, json.dumps(resolution, ensure_ascii=False), approval_id),
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
                    "resolved_by": resolved_by,
                    "resolution": resolution,
                },
            )

    def update_approval_resolution(self, approval_id: str, resolution: dict[str, Any]) -> None:
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE approvals SET resolution_json = ? WHERE approval_id = ?",
                (json.dumps(resolution, ensure_ascii=False), approval_id),
            )

    def consume_approval(self, approval_id: str, *, actor: str = "kernel") -> None:
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        resolution = dict(approval.resolution or {})
        resolution["status"] = "consumed"
        with self._lock, self._conn:
            self._conn.execute(
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
        input_refs: list[str],
        environment_ref: str | None,
        policy_result: dict[str, Any],
        approval_ref: str | None,
        output_refs: list[str],
        result_summary: str,
        result_code: str = "succeeded",
        decision_ref: str | None = None,
        permit_ref: str | None = None,
        grant_ref: str | None = None,
        policy_ref: str | None = None,
        witness_ref: str | None = None,
        idempotency_key: str | None = None,
        receipt_bundle_ref: str | None = None,
        proof_mode: str = "none",
        signature: str | None = None,
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_status: str = "not_requested",
        rollback_ref: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
    ) -> ReceiptRecord:
        receipt_id = self._id("receipt")
        created_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO receipts (
                    receipt_id, task_id, step_id, step_attempt_id, action_type,
                    input_refs_json, environment_ref, policy_result_json,
                    approval_ref, output_refs_json, result_summary, result_code,
                    decision_ref, permit_ref, grant_ref, policy_ref, witness_ref, idempotency_key,
                    receipt_bundle_ref, proof_mode, signature, rollback_supported, rollback_strategy,
                    rollback_status, rollback_ref, rollback_artifact_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    action_type,
                    json.dumps(input_refs, ensure_ascii=False),
                    environment_ref,
                    json.dumps(policy_result, ensure_ascii=False),
                    approval_ref,
                    json.dumps(output_refs, ensure_ascii=False),
                    result_summary,
                    result_code,
                    decision_ref,
                    permit_ref,
                    grant_ref,
                    policy_ref,
                    witness_ref,
                    idempotency_key,
                    receipt_bundle_ref,
                    proof_mode,
                    signature,
                    int(rollback_supported),
                    rollback_strategy,
                    rollback_status,
                    rollback_ref,
                    json.dumps(list(rollback_artifact_refs or []), ensure_ascii=False),
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
                    "input_refs": input_refs,
                    "environment_ref": environment_ref,
                    "policy_result": policy_result,
                    "approval_ref": approval_ref,
                    "output_refs": output_refs,
                    "result_summary": result_summary,
                    "result_code": result_code,
                    "decision_ref": decision_ref,
                    "permit_ref": permit_ref,
                    "grant_ref": grant_ref,
                    "policy_ref": policy_ref,
                    "witness_ref": witness_ref,
                    "idempotency_key": idempotency_key,
                    "receipt_bundle_ref": receipt_bundle_ref,
                    "proof_mode": proof_mode,
                    "signature": signature,
                    "rollback_supported": rollback_supported,
                    "rollback_strategy": rollback_strategy,
                    "rollback_status": rollback_status,
                    "rollback_ref": rollback_ref,
                    "rollback_artifact_refs": list(rollback_artifact_refs or []),
                },
            )
        return ReceiptRecord(
            receipt_id=receipt_id,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            action_type=action_type,
            input_refs=input_refs,
            environment_ref=environment_ref,
            policy_result=policy_result,
            approval_ref=approval_ref,
            output_refs=output_refs,
            result_summary=result_summary,
            result_code=result_code,
            decision_ref=decision_ref,
            permit_ref=permit_ref,
            grant_ref=grant_ref,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            receipt_bundle_ref=receipt_bundle_ref,
            proof_mode=proof_mode,
            signature=signature,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_status=rollback_status,
            rollback_ref=rollback_ref,
            rollback_artifact_refs=list(rollback_artifact_refs or []),
            created_at=created_at,
        )

    def update_receipt_proof_fields(
        self,
        receipt_id: str,
        *,
        receipt_bundle_ref: str | object = _UNSET,
        proof_mode: str | object = _UNSET,
        signature: str | None | object = _UNSET,
    ) -> None:
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return
        updated_bundle_ref = receipt.receipt_bundle_ref if receipt_bundle_ref is _UNSET else receipt_bundle_ref
        updated_proof_mode = receipt.proof_mode if proof_mode is _UNSET else proof_mode
        updated_signature = receipt.signature if signature is _UNSET else signature
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE receipts
                SET receipt_bundle_ref = ?, proof_mode = ?, signature = ?
                WHERE receipt_id = ?
                """,
                (updated_bundle_ref, updated_proof_mode, updated_signature, receipt_id),
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
                    "signature": updated_signature,
                },
            )

    def update_receipt_rollback_fields(
        self,
        receipt_id: str,
        *,
        rollback_supported: bool | object = _UNSET,
        rollback_strategy: str | None | object = _UNSET,
        rollback_status: str | object = _UNSET,
        rollback_ref: str | None | object = _UNSET,
        rollback_artifact_refs: list[str] | object = _UNSET,
    ) -> None:
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return
        updated_supported = receipt.rollback_supported if rollback_supported is _UNSET else bool(rollback_supported)
        updated_strategy = receipt.rollback_strategy if rollback_strategy is _UNSET else rollback_strategy
        updated_status = receipt.rollback_status if rollback_status is _UNSET else str(rollback_status)
        updated_ref = receipt.rollback_ref if rollback_ref is _UNSET else rollback_ref
        updated_artifact_refs = (
            receipt.rollback_artifact_refs
            if rollback_artifact_refs is _UNSET
            else list(rollback_artifact_refs)
        )
        with self._lock, self._conn:
            self._conn.execute(
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
        with self._lock:
            row = self._row("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,))
        return self._receipt_from_row(row) if row is not None else None

    def list_receipts(self, *, task_id: str | None = None, limit: int = 50) -> list[ReceiptRecord]:
        if task_id:
            query = "SELECT * FROM receipts WHERE task_id = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM receipts ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [self._receipt_from_row(row) for row in rows]
