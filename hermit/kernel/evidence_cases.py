from __future__ import annotations

from typing import Any

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.policy import ActionRequest, PolicyDecision
from hermit.kernel.store import KernelStore


class EvidenceCaseService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def compile_for_contract(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        contract_ref: str,
        action_request: ActionRequest,
        policy: PolicyDecision,
        context_pack_ref: str | None,
        action_request_ref: str | None,
        policy_result_ref: str | None,
        witness_ref: str | None,
    ):
        support_refs = [
            ref
            for ref in [context_pack_ref, action_request_ref, policy_result_ref, witness_ref]
            if ref
        ]
        unresolved_gaps: list[str] = []
        if policy.obligations.require_evidence and witness_ref is None:
            unresolved_gaps.append("missing_required_witness")
        sufficiency_score = max(
            0.0, min(1.0, 0.25 * len(support_refs) - 0.2 * len(unresolved_gaps))
        )
        status = (
            "sufficient" if sufficiency_score >= 0.5 and not unresolved_gaps else "insufficient"
        )
        evidence_case = self.store.create_evidence_case(
            task_id=attempt_ctx.task_id,
            subject_kind="contract",
            subject_ref=contract_ref,
            support_refs=support_refs,
            contradiction_refs=[],
            freshness_window={
                "context_pack_ref": context_pack_ref,
                "witness_ref": witness_ref,
            },
            sufficiency_score=sufficiency_score,
            drift_sensitivity="high" if witness_ref else "medium",
            unresolved_gaps=unresolved_gaps,
            status=status,
            witness_refs=[ref for ref in [witness_ref] if ref],
            invalidates_refs=[],
            last_checked_at=None,
            confidence_interval={
                "lower": max(0.0, sufficiency_score - 0.15),
                "upper": min(1.0, sufficiency_score + 0.15),
            },
            freshness_basis="context_pack+witness" if witness_ref else "context_pack",
            operator_summary=(
                f"support={len(support_refs)} gap={','.join(unresolved_gaps) or 'none'} "
                f"risk={policy.risk_level} action={action_request.action_class}"
            ),
        )
        artifact_ref = self._store_artifact(
            evidence_case_ref=evidence_case.evidence_case_id,
            attempt_ctx=attempt_ctx,
            payload={
                "evidence_case_id": evidence_case.evidence_case_id,
                "contract_ref": contract_ref,
                "support_refs": evidence_case.support_refs,
                "unresolved_gaps": evidence_case.unresolved_gaps,
                "status": evidence_case.status,
                "sufficiency_score": evidence_case.sufficiency_score,
                "operator_summary": evidence_case.operator_summary,
            },
        )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            evidence_case_ref=evidence_case.evidence_case_id,
            context={
                **(dict(attempt.context or {}) if attempt is not None else {}),
                "evidence_case_artifact_ref": artifact_ref,
            },
        )
        self.store.append_event(
            event_type="evidence_case.selected",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "evidence_case_ref": evidence_case.evidence_case_id,
                "artifact_ref": artifact_ref,
                "status": evidence_case.status,
                "sufficiency_score": evidence_case.sufficiency_score,
            },
        )
        self.store.update_execution_contract(
            contract_ref,
            evidence_case_ref=evidence_case.evidence_case_id,
            status="admissibility_pending",
        )
        return evidence_case, artifact_ref

    def invalidate(
        self,
        evidence_case_id: str,
        *,
        contradictions: list[str],
        summary: str,
        status: str = "invalidated",
    ) -> None:
        record = self.store.get_evidence_case(evidence_case_id)
        if record is None:
            return
        self.store.update_evidence_case(
            evidence_case_id,
            status=status,
            contradiction_refs=contradictions,
            operator_summary=summary,
        )
        self.store.append_event(
            event_type="evidence_case.invalidated"
            if status == "invalidated"
            else "evidence_case.contradicted",
            entity_type="evidence_case",
            entity_id=evidence_case_id,
            task_id=record.task_id,
            actor="kernel",
            payload={
                "contradictions": list(contradictions),
                "status": status,
                "summary": summary,
            },
        )

    def _store_artifact(
        self,
        *,
        evidence_case_ref: str,
        attempt_ctx: TaskExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="evidence.case",
            uri=uri,
            content_hash=content_hash,
            producer="evidence_case_service",
            retention_class="audit",
            trust_tier="derived",
            metadata={"evidence_case_id": evidence_case_ref},
        )
        return artifact.artifact_id
