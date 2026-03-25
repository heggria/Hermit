from __future__ import annotations

import json
import time
from typing import Any, cast

from hermit.kernel.ledger.journal.store_support import UNSET
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.records import (
    AuthorizationPlanRecord,
    EvidenceCaseRecord,
    ExecutionContractRecord,
    ReconciliationRecord,
)


class KernelV2StoreMixin(KernelStoreTypingBase):
    def create_execution_contract(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        objective: str,
        proposed_action_refs: list[str] | None = None,
        expected_effects: list[str] | None = None,
        success_criteria: dict[str, Any] | None = None,
        evidence_case_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        reversibility_class: str = "reversible",
        required_receipt_classes: list[str] | None = None,
        drift_budget: dict[str, Any] | None = None,
        expiry_at: float | None = None,
        status: str = "draft",
        fallback_contract_refs: list[str] | None = None,
        operator_summary: str | None = None,
        risk_budget: dict[str, Any] | None = None,
        expected_artifact_shape: dict[str, Any] | None = None,
        contract_version: int = 1,
        action_contract_refs: list[str] | None = None,
        state_witness_ref: str | None = None,
        rollback_expectation: str | None = None,
        selected_template_ref: str | None = None,
        superseded_by_contract_id: str | None = None,
        task_family: str | None = None,
        verification_requirements: dict[str, Any] | None = None,
    ) -> ExecutionContractRecord:
        contract_id = self._id("contract")
        created_at = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO execution_contracts (
                    contract_id, task_id, step_id, step_attempt_id, objective,
                    proposed_action_refs_json, expected_effects_json, success_criteria_json,
                    evidence_case_ref, authorization_plan_ref, reversibility_class,
                    required_receipt_classes_json, drift_budget_json, expiry_at, status,
                    fallback_contract_refs_json, operator_summary, risk_budget_json,
                    expected_artifact_shape_json, contract_version, action_contract_refs_json,
                    state_witness_ref, rollback_expectation, selected_template_ref,
                    superseded_by_contract_id, task_family, verification_requirements_json,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?
                )
                """,
                (
                    contract_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    objective,
                    json.dumps(list(proposed_action_refs or []), ensure_ascii=False),
                    json.dumps(list(expected_effects or []), ensure_ascii=False),
                    json.dumps(dict(success_criteria or {}), ensure_ascii=False),
                    evidence_case_ref,
                    authorization_plan_ref,
                    reversibility_class,
                    json.dumps(list(required_receipt_classes or []), ensure_ascii=False),
                    json.dumps(dict(drift_budget or {}), ensure_ascii=False),
                    expiry_at,
                    status,
                    json.dumps(list(fallback_contract_refs or []), ensure_ascii=False),
                    operator_summary,
                    json.dumps(dict(risk_budget or {}), ensure_ascii=False),
                    json.dumps(dict(expected_artifact_shape or {}), ensure_ascii=False),
                    int(contract_version),
                    json.dumps(list(action_contract_refs or []), ensure_ascii=False),
                    state_witness_ref,
                    rollback_expectation,
                    selected_template_ref,
                    superseded_by_contract_id,
                    task_family,
                    json.dumps(dict(verification_requirements or {}), ensure_ascii=False)
                    if verification_requirements
                    else None,
                    created_at,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="execution_contract.recorded",
                entity_type="execution_contract",
                entity_id=contract_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "objective": objective,
                    "proposed_action_refs": list(proposed_action_refs or []),
                    "expected_effects": list(expected_effects or []),
                    "success_criteria": dict(success_criteria or {}),
                    "evidence_case_ref": evidence_case_ref,
                    "authorization_plan_ref": authorization_plan_ref,
                    "reversibility_class": reversibility_class,
                    "required_receipt_classes": list(required_receipt_classes or []),
                    "drift_budget": dict(drift_budget or {}),
                    "expiry_at": expiry_at,
                    "status": status,
                    "fallback_contract_refs": list(fallback_contract_refs or []),
                    "operator_summary": operator_summary,
                    "risk_budget": dict(risk_budget or {}),
                    "expected_artifact_shape": dict(expected_artifact_shape or {}),
                    "contract_version": int(contract_version),
                    "action_contract_refs": list(action_contract_refs or []),
                    "state_witness_ref": state_witness_ref,
                    "rollback_expectation": rollback_expectation,
                    "selected_template_ref": selected_template_ref,
                    "superseded_by_contract_id": superseded_by_contract_id,
                    "task_family": task_family,
                    "verification_requirements": dict(verification_requirements or {})
                    if verification_requirements
                    else None,
                },
            )
        contract = self.get_execution_contract(contract_id)
        assert contract is not None
        return contract

    def get_execution_contract(self, contract_id: str) -> ExecutionContractRecord | None:
        row = self._row(
            "SELECT * FROM execution_contracts WHERE contract_id = ?",
            (contract_id,),
        )
        return self._execution_contract_from_row(row) if row is not None else None

    def list_execution_contracts(
        self, *, task_id: str | None = None, step_attempt_id: str | None = None, limit: int = 200
    ) -> list[ExecutionContractRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if step_attempt_id:
            clauses.append("step_attempt_id = ?")
            params.append(step_attempt_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM execution_contracts {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._execution_contract_from_row(row) for row in rows]

    def update_execution_contract(
        self,
        contract_id: str,
        *,
        evidence_case_ref: str | None | object = UNSET,
        authorization_plan_ref: str | None | object = UNSET,
        status: str | object = UNSET,
        operator_summary: str | None | object = UNSET,
        superseded_by_contract_id: str | None | object = UNSET,
    ) -> None:
        contract = self.get_execution_contract(contract_id)
        if contract is None:
            raise ValueError(
                f"update_execution_contract: no contract found with id={contract_id!r}"
            )
        updated_at = time.time()
        payload = {
            "evidence_case_ref": contract.evidence_case_ref
            if evidence_case_ref is UNSET
            else evidence_case_ref,
            "authorization_plan_ref": contract.authorization_plan_ref
            if authorization_plan_ref is UNSET
            else authorization_plan_ref,
            "status": contract.status if status is UNSET else status,
            "operator_summary": contract.operator_summary
            if operator_summary is UNSET
            else operator_summary,
            "superseded_by_contract_id": contract.superseded_by_contract_id
            if superseded_by_contract_id is UNSET
            else superseded_by_contract_id,
        }
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE execution_contracts
                SET evidence_case_ref = ?, authorization_plan_ref = ?, status = ?,
                    operator_summary = ?, superseded_by_contract_id = ?, updated_at = ?
                WHERE contract_id = ?
                """,
                (
                    payload["evidence_case_ref"],
                    payload["authorization_plan_ref"],
                    payload["status"],
                    payload["operator_summary"],
                    payload["superseded_by_contract_id"],
                    updated_at,
                    contract_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="execution_contract.updated",
                entity_type="execution_contract",
                entity_id=contract_id,
                task_id=contract.task_id,
                step_id=contract.step_id,
                actor="kernel",
                payload={**payload, "updated_at": updated_at},
            )

    def create_evidence_case(
        self,
        *,
        task_id: str,
        subject_kind: str,
        subject_ref: str,
        support_refs: list[str] | None = None,
        contradiction_refs: list[str] | None = None,
        freshness_window: dict[str, Any] | None = None,
        sufficiency_score: float = 0.0,
        drift_sensitivity: str = "medium",
        unresolved_gaps: list[str] | None = None,
        status: str = "insufficient",
        witness_refs: list[str] | None = None,
        invalidates_refs: list[str] | None = None,
        last_checked_at: float | None = None,
        confidence_interval: dict[str, Any] | None = None,
        freshness_basis: str | None = None,
        operator_summary: str | None = None,
    ) -> EvidenceCaseRecord:
        evidence_case_id = self._id("evidence_case")
        created_at = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO evidence_cases (
                    evidence_case_id, task_id, subject_kind, subject_ref, support_refs_json,
                    contradiction_refs_json, freshness_window_json, sufficiency_score,
                    drift_sensitivity, unresolved_gaps_json, status, witness_refs_json,
                    invalidates_refs_json, last_checked_at, confidence_interval_json,
                    freshness_basis, operator_summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_case_id,
                    task_id,
                    subject_kind,
                    subject_ref,
                    json.dumps(list(support_refs or []), ensure_ascii=False),
                    json.dumps(list(contradiction_refs or []), ensure_ascii=False),
                    json.dumps(dict(freshness_window or {}), ensure_ascii=False),
                    float(sufficiency_score),
                    drift_sensitivity,
                    json.dumps(list(unresolved_gaps or []), ensure_ascii=False),
                    status,
                    json.dumps(list(witness_refs or []), ensure_ascii=False),
                    json.dumps(list(invalidates_refs or []), ensure_ascii=False),
                    last_checked_at,
                    json.dumps(dict(confidence_interval or {}), ensure_ascii=False),
                    freshness_basis,
                    operator_summary,
                    created_at,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="evidence_case.recorded",
                entity_type="evidence_case",
                entity_id=evidence_case_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "subject_kind": subject_kind,
                    "subject_ref": subject_ref,
                    "support_refs": list(support_refs or []),
                    "contradiction_refs": list(contradiction_refs or []),
                    "freshness_window": dict(freshness_window or {}),
                    "sufficiency_score": float(sufficiency_score),
                    "drift_sensitivity": drift_sensitivity,
                    "unresolved_gaps": list(unresolved_gaps or []),
                    "status": status,
                    "witness_refs": list(witness_refs or []),
                    "invalidates_refs": list(invalidates_refs or []),
                    "last_checked_at": last_checked_at,
                    "confidence_interval": dict(confidence_interval or {}),
                    "freshness_basis": freshness_basis,
                    "operator_summary": operator_summary,
                },
            )
        record = self.get_evidence_case(evidence_case_id)
        assert record is not None
        return record

    def get_evidence_case(self, evidence_case_id: str) -> EvidenceCaseRecord | None:
        row = self._row(
            "SELECT * FROM evidence_cases WHERE evidence_case_id = ?",
            (evidence_case_id,),
        )
        return self._evidence_case_from_row(row) if row is not None else None

    def list_evidence_cases(
        self, *, task_id: str | None = None, subject_ref: str | None = None, limit: int = 200
    ) -> list[EvidenceCaseRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if subject_ref:
            clauses.append("subject_ref = ?")
            params.append(subject_ref)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM evidence_cases {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._evidence_case_from_row(row) for row in rows]

    def update_evidence_case(
        self,
        evidence_case_id: str,
        *,
        status: str | object = UNSET,
        contradiction_refs: list[str] | object = UNSET,
        unresolved_gaps: list[str] | object = UNSET,
        operator_summary: str | None | object = UNSET,
        sufficiency_score: float | object = UNSET,
        last_checked_at: float | object = UNSET,
    ) -> None:
        record = self.get_evidence_case(evidence_case_id)
        if record is None:
            raise ValueError(
                f"update_evidence_case: no evidence case found with id={evidence_case_id!r}"
            )
        updated_at = time.time()
        next_status = record.status if status is UNSET else str(status)
        next_contradiction_refs = (
            record.contradiction_refs
            if contradiction_refs is UNSET
            else list(cast(list[str], contradiction_refs))
        )
        next_unresolved_gaps = (
            record.unresolved_gaps
            if unresolved_gaps is UNSET
            else list(cast(list[str], unresolved_gaps))
        )
        next_operator_summary = (
            record.operator_summary
            if operator_summary is UNSET
            else cast(str | None, operator_summary)
        )
        next_sufficiency_score = (
            record.sufficiency_score
            if sufficiency_score is UNSET
            else float(cast(float, sufficiency_score))
        )
        next_last_checked_at = (
            record.last_checked_at
            if last_checked_at is UNSET
            else cast(float | None, last_checked_at)
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE evidence_cases
                SET contradiction_refs_json = ?, sufficiency_score = ?, unresolved_gaps_json = ?,
                    status = ?, operator_summary = ?, last_checked_at = ?, updated_at = ?
                WHERE evidence_case_id = ?
                """,
                (
                    json.dumps(next_contradiction_refs, ensure_ascii=False),
                    next_sufficiency_score,
                    json.dumps(next_unresolved_gaps, ensure_ascii=False),
                    next_status,
                    next_operator_summary,
                    next_last_checked_at,
                    updated_at,
                    evidence_case_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="evidence_case.updated",
                entity_type="evidence_case",
                entity_id=evidence_case_id,
                task_id=record.task_id,
                actor="kernel",
                payload={
                    "status": next_status,
                    "contradiction_refs": next_contradiction_refs,
                    "unresolved_gaps": next_unresolved_gaps,
                    "operator_summary": next_operator_summary,
                    "sufficiency_score": next_sufficiency_score,
                    "last_checked_at": next_last_checked_at,
                    "updated_at": updated_at,
                },
            )

    def create_authorization_plan(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        contract_ref: str,
        policy_profile_ref: str,
        requested_action_classes: list[str] | None = None,
        required_decision_refs: list[str] | None = None,
        approval_route: str = "none",
        witness_requirements: list[str] | None = None,
        proposed_grant_shape: dict[str, Any] | None = None,
        downgrade_options: list[str] | None = None,
        current_gaps: list[str] | None = None,
        status: str = "draft",
        estimated_authority_cost: float | None = None,
        expiry_constraints: dict[str, Any] | None = None,
        revalidation_rules: dict[str, Any] | None = None,
        operator_packet_ref: str | None = None,
        required_workspace_mode: str | None = None,
        required_secret_policy: str | None = None,
        proposed_lease_shape: dict[str, Any] | None = None,
    ) -> AuthorizationPlanRecord:
        authorization_plan_id = self._id("auth_plan")
        created_at = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO authorization_plans (
                    authorization_plan_id, task_id, step_id, step_attempt_id, contract_ref,
                    policy_profile_ref, requested_action_classes_json, required_decision_refs_json,
                    approval_route, witness_requirements_json, proposed_grant_shape_json,
                    downgrade_options_json, current_gaps_json, status, estimated_authority_cost,
                    expiry_constraints_json, revalidation_rules_json, operator_packet_ref,
                    required_workspace_mode, required_secret_policy, proposed_lease_shape_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_plan_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    contract_ref,
                    policy_profile_ref,
                    json.dumps(list(requested_action_classes or []), ensure_ascii=False),
                    json.dumps(list(required_decision_refs or []), ensure_ascii=False),
                    approval_route,
                    json.dumps(list(witness_requirements or []), ensure_ascii=False),
                    json.dumps(dict(proposed_grant_shape or {}), ensure_ascii=False),
                    json.dumps(list(downgrade_options or []), ensure_ascii=False),
                    json.dumps(list(current_gaps or []), ensure_ascii=False),
                    status,
                    estimated_authority_cost,
                    json.dumps(dict(expiry_constraints or {}), ensure_ascii=False),
                    json.dumps(dict(revalidation_rules or {}), ensure_ascii=False),
                    operator_packet_ref,
                    required_workspace_mode,
                    required_secret_policy,
                    json.dumps(dict(proposed_lease_shape or {}), ensure_ascii=False),
                    created_at,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="authorization_plan.recorded",
                entity_type="authorization_plan",
                entity_id=authorization_plan_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "contract_ref": contract_ref,
                    "policy_profile_ref": policy_profile_ref,
                    "requested_action_classes": list(requested_action_classes or []),
                    "required_decision_refs": list(required_decision_refs or []),
                    "approval_route": approval_route,
                    "witness_requirements": list(witness_requirements or []),
                    "proposed_grant_shape": dict(proposed_grant_shape or {}),
                    "downgrade_options": list(downgrade_options or []),
                    "current_gaps": list(current_gaps or []),
                    "status": status,
                    "estimated_authority_cost": estimated_authority_cost,
                    "expiry_constraints": dict(expiry_constraints or {}),
                    "revalidation_rules": dict(revalidation_rules or {}),
                    "operator_packet_ref": operator_packet_ref,
                    "required_workspace_mode": required_workspace_mode,
                    "required_secret_policy": required_secret_policy,
                    "proposed_lease_shape": dict(proposed_lease_shape or {}),
                },
            )
        record = self.get_authorization_plan(authorization_plan_id)
        assert record is not None
        return record

    def get_authorization_plan(self, authorization_plan_id: str) -> AuthorizationPlanRecord | None:
        row = self._row(
            "SELECT * FROM authorization_plans WHERE authorization_plan_id = ?",
            (authorization_plan_id,),
        )
        return self._authorization_plan_from_row(row) if row is not None else None

    def list_authorization_plans(
        self, *, task_id: str | None = None, step_attempt_id: str | None = None, limit: int = 200
    ) -> list[AuthorizationPlanRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if step_attempt_id:
            clauses.append("step_attempt_id = ?")
            params.append(step_attempt_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM authorization_plans {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._authorization_plan_from_row(row) for row in rows]

    def update_authorization_plan(
        self,
        authorization_plan_id: str,
        *,
        status: str | object = UNSET,
        current_gaps: list[str] | object = UNSET,
        operator_packet_ref: str | None | object = UNSET,
    ) -> None:
        record = self.get_authorization_plan(authorization_plan_id)
        if record is None:
            raise ValueError(
                f"update_authorization_plan: no authorization plan found with id={authorization_plan_id!r}"
            )
        updated_at = time.time()
        next_status = record.status if status is UNSET else str(status)
        next_current_gaps = (
            record.current_gaps if current_gaps is UNSET else list(cast(list[str], current_gaps))
        )
        next_operator_packet_ref = (
            record.operator_packet_ref
            if operator_packet_ref is UNSET
            else cast(str | None, operator_packet_ref)
        )
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE authorization_plans
                SET status = ?, current_gaps_json = ?, operator_packet_ref = ?, updated_at = ?
                WHERE authorization_plan_id = ?
                """,
                (
                    next_status,
                    json.dumps(next_current_gaps, ensure_ascii=False),
                    next_operator_packet_ref,
                    updated_at,
                    authorization_plan_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="authorization_plan.updated",
                entity_type="authorization_plan",
                entity_id=authorization_plan_id,
                task_id=record.task_id,
                step_id=record.step_id,
                actor="kernel",
                payload={
                    "status": next_status,
                    "current_gaps": next_current_gaps,
                    "operator_packet_ref": next_operator_packet_ref,
                    "updated_at": updated_at,
                },
            )

    def create_reconciliation(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        contract_ref: str,
        receipt_refs: list[str] | None = None,
        observed_output_refs: list[str] | None = None,
        intended_effect_summary: str,
        authorized_effect_summary: str,
        observed_effect_summary: str,
        receipted_effect_summary: str,
        result_class: str,
        confidence_delta: float = 0.0,
        recommended_resolution: str = "",
        rollback_recommendation_ref: str | None = None,
        invalidated_belief_refs: list[str] | None = None,
        superseded_memory_refs: list[str] | None = None,
        promoted_template_ref: str | None = None,
        promoted_memory_refs: list[str] | None = None,
        operator_summary: str | None = None,
        final_state_witness_ref: str | None = None,
    ) -> ReconciliationRecord:
        reconciliation_id = self._id("reconciliation")
        created_at = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO reconciliations (
                    reconciliation_id, task_id, step_id, step_attempt_id, contract_ref,
                    receipt_refs_json, observed_output_refs_json, intended_effect_summary,
                    authorized_effect_summary, observed_effect_summary, receipted_effect_summary,
                    result_class, confidence_delta, recommended_resolution,
                    rollback_recommendation_ref, invalidated_belief_refs_json,
                    superseded_memory_refs_json, promoted_template_ref,
                    promoted_memory_refs_json, operator_summary, final_state_witness_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reconciliation_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    contract_ref,
                    json.dumps(list(receipt_refs or []), ensure_ascii=False),
                    json.dumps(list(observed_output_refs or []), ensure_ascii=False),
                    intended_effect_summary,
                    authorized_effect_summary,
                    observed_effect_summary,
                    receipted_effect_summary,
                    result_class,
                    float(confidence_delta),
                    recommended_resolution,
                    rollback_recommendation_ref,
                    json.dumps(list(invalidated_belief_refs or []), ensure_ascii=False),
                    json.dumps(list(superseded_memory_refs or []), ensure_ascii=False),
                    promoted_template_ref,
                    json.dumps(list(promoted_memory_refs or []), ensure_ascii=False),
                    operator_summary,
                    final_state_witness_ref,
                    created_at,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="reconciliation.recorded",
                entity_type="reconciliation",
                entity_id=reconciliation_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "contract_ref": contract_ref,
                    "receipt_refs": list(receipt_refs or []),
                    "observed_output_refs": list(observed_output_refs or []),
                    "intended_effect_summary": intended_effect_summary,
                    "authorized_effect_summary": authorized_effect_summary,
                    "observed_effect_summary": observed_effect_summary,
                    "receipted_effect_summary": receipted_effect_summary,
                    "result_class": result_class,
                    "confidence_delta": float(confidence_delta),
                    "recommended_resolution": recommended_resolution,
                    "rollback_recommendation_ref": rollback_recommendation_ref,
                    "invalidated_belief_refs": list(invalidated_belief_refs or []),
                    "superseded_memory_refs": list(superseded_memory_refs or []),
                    "promoted_template_ref": promoted_template_ref,
                    "promoted_memory_refs": list(promoted_memory_refs or []),
                    "operator_summary": operator_summary,
                    "final_state_witness_ref": final_state_witness_ref,
                },
            )
        record = self.get_reconciliation(reconciliation_id)
        assert record is not None
        return record

    def get_reconciliation(self, reconciliation_id: str) -> ReconciliationRecord | None:
        row = self._row(
            "SELECT * FROM reconciliations WHERE reconciliation_id = ?",
            (reconciliation_id,),
        )
        return self._reconciliation_from_row(row) if row is not None else None

    def list_reconciliations(
        self, *, task_id: str | None = None, step_attempt_id: str | None = None, limit: int = 200
    ) -> list[ReconciliationRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if step_attempt_id:
            clauses.append("step_attempt_id = ?")
            params.append(step_attempt_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM reconciliations {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._reconciliation_from_row(row) for row in rows]
