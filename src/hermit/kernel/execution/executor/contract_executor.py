from __future__ import annotations

import time
from typing import Any

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.executor.execution_helpers import (
    _contract_refs,
    _set_attempt_phase,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import POLICY_RULES_VERSION, ActionRequest, PolicyDecision
from hermit.kernel.policy.permits.authorization_plans import AuthorizationPlanService
from hermit.runtime.capability.registry.tools import ToolSpec


class ContractExecutor:
    """Contract synthesis, admissibility, and lifecycle helpers for governed execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        execution_contracts: ExecutionContractService,
        evidence_cases: EvidenceCaseService,
        authorization_plans: AuthorizationPlanService,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.execution_contracts = execution_contracts
        self.evidence_cases = evidence_cases
        self.authorization_plans = authorization_plans

    def contract_refs(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[str | None, str | None, str | None]:
        return _contract_refs(self.store, attempt_ctx)

    def load_contract_bundle(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[Any | None, Any | None, Any | None]:
        contract_ref, evidence_case_ref, authorization_plan_ref = self.contract_refs(attempt_ctx)
        contract = (
            self.store.get_execution_contract(contract_ref)
            if contract_ref and hasattr(self.store, "get_execution_contract")
            else None
        )
        evidence_case = (
            self.store.get_evidence_case(evidence_case_ref)
            if evidence_case_ref and hasattr(self.store, "get_evidence_case")
            else None
        )
        authorization_plan = (
            self.store.get_authorization_plan(authorization_plan_ref)
            if authorization_plan_ref and hasattr(self.store, "get_authorization_plan")
            else None
        )
        return contract, evidence_case, authorization_plan

    @staticmethod
    def contract_expired(contract: Any) -> bool:
        expiry_at = getattr(contract, "expiry_at", None)
        if expiry_at is None:
            return False
        try:
            return float(expiry_at) < time.time()
        except (TypeError, ValueError):
            return False

    @staticmethod
    def policy_version_drifted(attempt: Any) -> bool:
        recorded_version = str(getattr(attempt, "policy_version", "") or "").strip()
        if not recorded_version:
            return False
        return recorded_version != POLICY_RULES_VERSION

    def synthesize_contract_loop(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool: ToolSpec,
        action_request: ActionRequest,
        policy: PolicyDecision,
        action_request_ref: str | None,
        policy_result_ref: str | None,
        preview_artifact: str | None,
        witness_ref: str | None,
    ) -> tuple[Any, Any, Any]:
        _set_attempt_phase(
            self.store, attempt_ctx, "contracting", reason="contract_synthesis_started"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="contracting")
        contract, _contract_artifact_ref = self.execution_contracts.synthesize_default(
            attempt_ctx=attempt_ctx,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=action_request_ref,
            witness_ref=witness_ref,
        )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        evidence_case, _evidence_artifact_ref = self.evidence_cases.compile_for_contract(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            action_request=action_request,
            policy=policy,
            context_pack_ref=attempt.context_pack_ref if attempt is not None else None,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            witness_ref=witness_ref,
        )
        _set_attempt_phase(
            self.store, attempt_ctx, "preflighting", reason="authorization_preflight_started"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="preflighting")
        authorization_plan, _authorization_artifact_ref = self.authorization_plans.preflight(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            action_request=action_request,
            policy=policy,
            approval_packet_ref=preview_artifact,
            witness_ref=witness_ref,
        )
        next_contract_status = (
            "approval_pending"
            if authorization_plan.status == "awaiting_approval"
            else "admissibility_pending"
        )
        if authorization_plan.status == "preflighted" and evidence_case.status == "sufficient":
            next_contract_status = "authorized"
        if authorization_plan.status == "blocked":
            next_contract_status = "abandoned"
        self.store.update_execution_contract(
            contract.contract_id,
            status=next_contract_status,
            evidence_case_ref=evidence_case.evidence_case_id,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
        )
        return contract, evidence_case, authorization_plan

    @staticmethod
    def admissibility_resolution(evidence_case: Any, authorization_plan: Any) -> str | None:
        if str(evidence_case.status or "") != "sufficient":
            return "gather_more_evidence"
        if str(authorization_plan.status or "") == "blocked":
            return "request_authority"
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_attempt_phase(
        self,
        attempt_ctx: TaskExecutionContext,
        phase: str,
        *,
        reason: str | None = None,
    ) -> None:
        _set_attempt_phase(self.store, attempt_ctx, phase, reason=reason)
