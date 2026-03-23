from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

import structlog

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
from hermit.kernel.authority.workspaces import (
    WorkspaceLeaseQueued,
    WorkspaceLeaseService,
)
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.competition.deliberation_integration import (
    DeliberationIntegration,
)
from hermit.kernel.execution.competition.deliberation_service import DeliberationService
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.coordination.observation import (
    ObservationPollResult,
    ObservationTicket,
    normalize_observation_ticket,
)
from hermit.kernel.execution.executor.authorization_handler import AuthorizationHandler
from hermit.kernel.execution.executor.execution_helpers import (
    is_governed_action,
    load_witness_payload,
)
from hermit.kernel.execution.executor.formatting import (
    format_model_content as _format_model_content,
)
from hermit.kernel.execution.executor.phase_tracker import needs_witness as _needs_witness
from hermit.kernel.execution.executor.receipt_handler import ReceiptHandler
from hermit.kernel.execution.executor.reconciliation_executor import ReconciliationExecutor
from hermit.kernel.execution.executor.request_builder import RequestBuilder
from hermit.kernel.execution.executor.snapshot import RuntimeSnapshotManager
from hermit.kernel.execution.executor.state_persistence import StatePersistence
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.execution.recovery.reconcile import ReconcileService
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import (
    POLICY_RULES_VERSION,
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
)
from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.policy.approvals.decisions import DecisionService
from hermit.kernel.policy.evaluators.enrichment import PolicyEvidenceEnricher
from hermit.kernel.policy.permits.authorization_plans import AuthorizationPlanService
from hermit.kernel.policy.trust.models import RiskAdjustment
from hermit.kernel.policy.trust.scoring import TrustScorer
from hermit.kernel.verification.assurance.recorder import TraceRecorder
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import (
    ToolRegistry,
    invoke_tool_handler,
)

log = structlog.get_logger()


@dataclass
class ToolExecutionResult:
    model_content: Any
    raw_result: Any = None
    blocked: bool = False
    suspended: bool = False
    waiting_kind: str | None = None
    denied: bool = False
    approval_id: str | None = None
    approval_message: str | None = None
    observation: ObservationTicket | None = None
    policy_decision: PolicyDecision | None = None
    receipt_id: str | None = None
    decision_id: str | None = None
    capability_grant_id: str | None = None
    workspace_lease_id: str | None = None
    policy_ref: str | None = None
    witness_ref: str | None = None
    deliberation_ref: str | None = None
    result_code: str = "succeeded"
    execution_status: str = "succeeded"
    state_applied: bool = False


class ToolExecutor:
    """Governed tool execution orchestrator.

    Delegates to extracted handler classes for specific concerns:
    - RequestBuilder: action request, policy recording, preview artifacts
    - ReceiptHandler: receipt issuance
    - AuthorizationHandler: workspace leases, capability constraints, rollback plans
    - ReconciliationExecutor: reconciliation recording, template learning
    - StatePersistence: suspend/resume state management
    - ApprovalHandler: approval matching and drift detection
    - DriftHandler: drift supersession
    - ObservationHandler: observation lifecycle
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        store: KernelStore,
        artifact_store: ArtifactStore,
        policy_engine: PolicyEngine,
        approval_service: ApprovalService,
        approval_copy_service: ApprovalCopyService | None = None,
        receipt_service: ReceiptService,
        decision_service: DecisionService | None = None,
        capability_service: CapabilityGrantService | None = None,
        workspace_lease_service: WorkspaceLeaseService | None = None,
        reconcile_service: ReconcileService | None = None,
        git_worktree: Any | None = None,
        progress_summarizer: Any | None = None,
        progress_summary_keepalive_seconds: float = 15.0,
        tool_output_limit: int = 4000,
        deliberation: DeliberationIntegration | None = None,
        trace_recorder: TraceRecorder | None = None,
        signal_protocol: Any | None = None,
    ) -> None:
        from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector

        self.registry = registry
        self.store = store
        self.artifact_store = artifact_store
        self.policy_engine = policy_engine
        self.approval_service = approval_service
        self.approval_copy = approval_copy_service or ApprovalCopyService()
        self.receipt_service = receipt_service
        self.decision_service = decision_service or DecisionService(store)
        self.capability_service = capability_service or CapabilityGrantService(store)
        self.workspace_lease_service = workspace_lease_service or WorkspaceLeaseService(
            store, artifact_store
        )
        self.git_worktree = git_worktree or GitWorktreeInspector()
        self.reconcile_service = reconcile_service or ReconcileService(
            git_worktree=self.git_worktree
        )
        self.execution_contracts = ExecutionContractService(store, artifact_store)
        self.evidence_cases = EvidenceCaseService(store, artifact_store)
        self.authorization_plans = AuthorizationPlanService(store, artifact_store)
        self.reconciliations = ReconciliationService(store, artifact_store, self.reconcile_service)
        self._witness = WitnessCapture(
            store=store, artifact_store=artifact_store, git_worktree=self.git_worktree
        )
        self._snapshot = RuntimeSnapshotManager(store=store, artifact_store=artifact_store)
        self._evidence_enricher = PolicyEvidenceEnricher(store)
        self._trust_scorer = TrustScorer(store)
        self._pattern_learner = TaskPatternLearner(store)
        self.progress_summarizer = progress_summarizer
        self.progress_summary_keepalive_seconds = max(
            float(progress_summary_keepalive_seconds or 0.0), 0.0
        )
        self.tool_output_limit = tool_output_limit
        self._deliberation = deliberation
        self.trace_recorder = trace_recorder
        self.signal_protocol = signal_protocol

        # -- Delegate handlers --
        self._request_builder = RequestBuilder(
            store=store,
            artifact_store=artifact_store,
            policy_engine=policy_engine,
            registry=registry,
            tool_output_limit=tool_output_limit,
        )
        self._receipt_handler = ReceiptHandler(
            store=store,
            artifact_store=artifact_store,
            receipt_service=self.receipt_service,
            registry=registry,
            policy_engine=policy_engine,
            workspace_lease_service=self.workspace_lease_service,
        )
        self._auth_handler = AuthorizationHandler(
            store=store,
            artifact_store=artifact_store,
            capability_service=self.capability_service,
            workspace_lease_service=self.workspace_lease_service,
            authorization_plans=self.authorization_plans,
            registry=registry,
            policy_engine=policy_engine,
            git_worktree=self.git_worktree,
        )
        self._reconciliation_executor = ReconciliationExecutor(
            store=store,
            artifact_store=artifact_store,
            reconciliations=self.reconciliations,
            execution_contracts=self.execution_contracts,
            evidence_cases=self.evidence_cases,
            pattern_learner=self._pattern_learner,
        )
        self._state_persistence = StatePersistence(
            store=store,
            artifact_store=artifact_store,
            _snapshot=self._snapshot,
            _store_json_artifact=self._request_builder.store_json_artifact,
        )

    def _trace(
        self,
        event_type: str,
        attempt_ctx: TaskExecutionContext,
        *,
        phase: str | None = None,
        approval_ref: str | None = None,
        decision_ref: str | None = None,
        grant_ref: str | None = None,
        lease_ref: str | None = None,
        receipt_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        **_extra: Any,
    ) -> None:
        if not self.trace_recorder:
            return
        try:
            self.trace_recorder.record(
                event_type=event_type,
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                phase=phase,
                approval_ref=approval_ref,
                decision_ref=decision_ref,
                grant_ref=grant_ref,
                lease_ref=lease_ref,
                receipt_ref=receipt_ref,
                payload=payload,
            )
        except Exception:
            log.debug("assurance.trace_failed", event_type=event_type, exc_info=True)

    def _set_attempt_phase(
        self, attempt_ctx: TaskExecutionContext, phase: str, *, reason: str | None = None
    ) -> None:
        from hermit.kernel.execution.executor.execution_helpers import set_attempt_phase

        set_attempt_phase(self.store, attempt_ctx, phase, reason=reason)

    def _contract_refs(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[str | None, str | None, str | None]:
        from hermit.kernel.execution.executor.execution_helpers import contract_refs

        return contract_refs(self.store, attempt_ctx)

    def _load_contract_bundle(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[Any | None, Any | None, Any | None]:
        cr, ecr, apr = self._contract_refs(attempt_ctx)
        c = (
            self.store.get_execution_contract(cr)
            if cr and hasattr(self.store, "get_execution_contract")
            else None
        )
        e = (
            self.store.get_evidence_case(ecr)
            if ecr and hasattr(self.store, "get_evidence_case")
            else None
        )
        a = (
            self.store.get_authorization_plan(apr)
            if apr and hasattr(self.store, "get_authorization_plan")
            else None
        )
        return c, e, a

    @staticmethod
    def _contract_expired(contract: Any) -> bool:
        expiry_at = getattr(contract, "expiry_at", None)
        if expiry_at is None:
            return False
        try:
            return float(expiry_at) < time.time()
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _policy_version_drifted(attempt: Any) -> bool:
        v = str(getattr(attempt, "policy_version", "") or "").strip()
        return bool(v) and v != POLICY_RULES_VERSION

    def _synthesize_contract_loop(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        action_request_ref: str | None,
        policy_result_ref: str | None,
        preview_artifact: str | None,
        witness_ref: str | None,
    ) -> tuple[Any, Any, Any]:
        self._set_attempt_phase(attempt_ctx, "contracting", reason="contract_synthesis_started")
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="contracting")
        contract, _ = self.execution_contracts.synthesize_default(
            attempt_ctx=attempt_ctx,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=action_request_ref,
            witness_ref=witness_ref,
        )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        evidence_case, _ = self.evidence_cases.compile_for_contract(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            action_request=action_request,
            policy=policy,
            context_pack_ref=attempt.context_pack_ref if attempt is not None else None,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            witness_ref=witness_ref,
        )
        self._set_attempt_phase(
            attempt_ctx, "preflighting", reason="authorization_preflight_started"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="preflighting")
        authorization_plan, _ = self.authorization_plans.preflight(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            action_request=action_request,
            policy=policy,
            approval_packet_ref=preview_artifact,
            witness_ref=witness_ref,
        )
        next_status = (
            "approval_pending"
            if authorization_plan.status == "awaiting_approval"
            else "admissibility_pending"
        )
        if authorization_plan.status == "preflighted" and evidence_case.status == "sufficient":
            next_status = "authorized"
        if authorization_plan.status == "blocked":
            next_status = "abandoned"
        self.store.update_execution_contract(
            contract.contract_id,
            status=next_status,
            evidence_case_ref=evidence_case.evidence_case_id,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
        )
        return contract, evidence_case, authorization_plan

    @staticmethod
    def _admissibility_resolution(evidence_case: Any, authorization_plan: Any) -> str | None:
        if str(evidence_case.status or "") != "sufficient":
            return "gather_more_evidence"
        if str(authorization_plan.status or "") == "blocked":
            return "request_authority"
        return None

    def _log_trust_feedback(
        self, action_request: ActionRequest, attempt_ctx: TaskExecutionContext, result_code: str
    ) -> None:
        adj_data = action_request.context.get("trust_risk_adjustment")
        if not adj_data:
            return
        try:
            adj = RiskAdjustment(
                subject_kind=str(adj_data.get("subject_kind", "")),
                subject_ref=str(adj_data.get("subject_ref", "")),
                current_risk_band=str(adj_data.get("current_risk_band", "")),
                suggested_risk_band=str(adj_data.get("suggested_risk_band", "")),
                reason=str(adj_data.get("reason", "")),
                trust_score_ref=float(adj_data.get("trust_score_ref", 0.0)),
            )
            self._trust_scorer.log_adjustment_decision(
                adj,
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
            )
        except Exception:
            log.warning(
                "trust_feedback.log_failed", action_class=action_request.action_class, exc_info=True
            )

    # ================================================================
    # Main execution flow
    # ================================================================

    def execute(
        self,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        request_overrides: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        tool = self.registry.get(tool_name)
        action_request = self.policy_engine.build_action_request(
            tool, tool_input, attempt_ctx=attempt_ctx
        )
        if request_overrides:
            action_request = self._request_builder.apply_request_overrides(
                action_request, request_overrides
            )
        action_ref = self._request_builder.record_action_request(action_request, attempt_ctx)
        action_request = self._evidence_enricher.enrich(action_request)
        self._set_attempt_phase(attempt_ctx, "policy_pending", reason="policy_evaluation_started")
        policy = self.policy_engine.evaluate(action_request)
        policy_ref = self._request_builder.record_policy_evaluation(
            action_request, policy, attempt_ctx
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            action_request_ref=action_ref,
            policy_result_ref=policy_ref,
            idempotency_key=action_request.idempotency_key,
            executor_mode="tool_executor",
            policy_version=POLICY_RULES_VERSION,
        )
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        governed = is_governed_action(tool, policy)
        self._trace(
            "policy.evaluated",
            attempt_ctx,
            phase="policy_pending",
            payload={"verdict": policy.verdict, "action_class": str(action_type)},
        )

        attempt_record = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        approval_record = None
        if attempt_record is not None and attempt_record.approval_id:
            approval_record = self.store.get_approval(attempt_record.approval_id)

        preview_artifact = None
        if policy.obligations.require_preview:
            preview_artifact = self._request_builder.build_preview_artifact(
                tool, tool_input, attempt_ctx
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id, approval_packet_ref=preview_artifact
            )

        matched_approval, witness_ref, drift_reason = self._matching_approval(
            approval_record, action_request, policy, preview_artifact, attempt_ctx=attempt_ctx
        )
        if drift_reason is not None:
            return self._supersede_attempt_for_drift(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
                drift_reason=drift_reason,
            )

        if (
            governed
            and witness_ref is None
            and matched_approval is None
            and _needs_witness(action_type)
        ):
            witness_ref = self._witness.capture(
                action_request,
                attempt_ctx,
                store_artifact=self._request_builder.store_json_artifact,
            )
        if (
            witness_ref is not None
            and matched_approval is None
            and _needs_witness(action_type)
            and not self._witness.validate(witness_ref, action_request, attempt_ctx)
        ):
            return self._supersede_attempt_for_drift(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
                drift_reason="witness_drift",
            )

        contract, evidence_case, authorization_plan = None, None, None
        if governed:
            contract, evidence_case, authorization_plan = self._load_contract_bundle(attempt_ctx)
            if contract is None or evidence_case is None or authorization_plan is None:
                contract, evidence_case, authorization_plan = self._synthesize_contract_loop(
                    attempt_ctx=attempt_ctx,
                    tool=tool,
                    action_request=action_request,
                    policy=policy,
                    action_request_ref=action_ref,
                    policy_result_ref=policy_ref,
                    preview_artifact=preview_artifact,
                    witness_ref=witness_ref,
                )
            if contract is not None and self._contract_expired(contract):
                return self._supersede_attempt_for_drift(
                    attempt_ctx=attempt_ctx,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    drift_reason="contract_expiry",
                )
            ap = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
            if ap is not None and self._policy_version_drifted(ap):
                return self._supersede_attempt_for_drift(
                    attempt_ctx=attempt_ctx,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    drift_reason="policy_version_drift",
                )
            resolution = self._admissibility_resolution(evidence_case, authorization_plan)
            if resolution is not None and policy.verdict != "deny":
                return self._handle_admissibility_block(
                    attempt_ctx=attempt_ctx,
                    resolution=resolution,
                    action_ref=action_ref,
                    policy_ref=policy_ref,
                    preview_artifact=preview_artifact,
                    witness_ref=witness_ref,
                    contract=contract,
                    evidence_case=evidence_case,
                    authorization_plan=authorization_plan,
                    action_type=action_type,
                    policy=policy,
                )

        deliberation_ref: str | None = None
        if governed and self._deliberation is not None:
            dlb_result = self._run_deliberation_gate(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
                action_type=action_type,
                action_ref=action_ref,
                policy=policy,
                policy_ref=policy_ref,
                witness_ref=witness_ref,
            )
            if isinstance(dlb_result, ToolExecutionResult):
                return dlb_result
            if isinstance(dlb_result, dict):
                deliberation_ref = dlb_result.get("deliberation_ref")

        if policy.verdict == "deny":
            return self._handle_policy_deny(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                action_ref=action_ref,
                policy_ref=policy_ref,
                preview_artifact=preview_artifact,
                witness_ref=witness_ref,
                contract=contract,
                evidence_case=evidence_case,
                authorization_plan=authorization_plan,
                action_type=action_type,
                policy=policy,
            )

        if policy.obligations.require_approval and matched_approval is None:
            return self._handle_approval_required(
                attempt_ctx=attempt_ctx,
                action_request=action_request,
                action_ref=action_ref,
                policy=policy,
                policy_ref=policy_ref,
                preview_artifact=preview_artifact,
                witness_ref=witness_ref,
                contract=contract,
                evidence_case=evidence_case,
                authorization_plan=authorization_plan,
                action_type=action_type,
            )

        decision_id, capability_grant_id, workspace_lease_id, environment_ref = (
            None,
            None,
            None,
            None,
        )
        approval_mode = ""
        if matched_approval is not None:
            approval_mode = str(
                cast(dict[str, Any], matched_approval.resolution or {}).get("mode", "once")
                or "once"
            )
            self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="approval_resolution",
                verdict=approval_mode,
                reason="User approval was applied before execution.",
                policy_ref=policy_ref,
                approval_ref=matched_approval.approval_id,
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
                action_type=action_type,
                decided_by=str(matched_approval.resolved_by_principal_id or "principal_user"),
            )
        if governed:
            auth_result = self._authorize_and_grant(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
                action_request=action_request,
                action_ref=action_ref,
                policy=policy,
                policy_ref=policy_ref,
                preview_artifact=preview_artifact,
                witness_ref=witness_ref,
                contract=contract,
                evidence_case=evidence_case,
                authorization_plan=authorization_plan,
                action_type=action_type,
                approval_mode=approval_mode,
                matched_approval=matched_approval,
            )
            if isinstance(auth_result, ToolExecutionResult):
                return auth_result
            decision_id = auth_result["decision_id"]
            capability_grant_id = auth_result["capability_grant_id"]
            workspace_lease_id = auth_result["workspace_lease_id"]
            environment_ref = auth_result["environment_ref"]

        if governed and capability_grant_id is not None:
            try:
                self.capability_service.enforce(
                    capability_grant_id,
                    task_id=attempt_ctx.task_id,
                    action_class=action_type,
                    resource_scope=list(action_request.resource_scopes),
                    constraints=self._auth_handler.capability_constraints(
                        action_request, workspace_lease_id=workspace_lease_id
                    ),
                )
            except CapabilityGrantError as exc:
                return self._handle_dispatch_denied(
                    tool=tool,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    attempt_ctx=attempt_ctx,
                    policy=policy,
                    policy_ref=policy_ref,
                    decision_id=decision_id,
                    capability_grant_id=capability_grant_id,
                    workspace_lease_id=workspace_lease_id,
                    approval_ref=matched_approval.approval_id
                    if matched_approval is not None
                    else None,
                    witness_ref=witness_ref,
                    error=exc,
                    idempotency_key=action_request.idempotency_key,
                    action_request_ref=action_ref,
                    policy_result_ref=policy_ref,
                    environment_ref=environment_ref,
                )
            if matched_approval is not None:
                self.store.consume_approval(matched_approval.approval_id)

        rollback_plan = self._auth_handler.prepare_rollback_plan(
            action_type=action_type,
            tool_name=tool_name,
            tool_input=tool_input,
            attempt_ctx=attempt_ctx,
        )

        try:
            self._set_attempt_phase(attempt_ctx, "executing", reason="tool_handler_invoked")
            if contract is not None:
                self.store.update_execution_contract(contract.contract_id, status="executing")
            self._trace(
                "tool_call.start",
                attempt_ctx,
                phase="executing",
                grant_ref=capability_grant_id if governed else None,
                payload={"tool_name": tool_name},
            )
            raw_result = invoke_tool_handler(tool.handler, tool_input, task_context=attempt_ctx)
        except Exception as exc:
            if governed and capability_grant_id is not None:
                self.capability_service.mark_uncertain(capability_grant_id)
                return self._handle_uncertain_outcome(
                    tool=tool,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    attempt_ctx=attempt_ctx,
                    policy=policy,
                    policy_ref=policy_ref,
                    decision_id=decision_id,
                    capability_grant_id=capability_grant_id,
                    workspace_lease_id=workspace_lease_id,
                    approval_ref=matched_approval.approval_id
                    if matched_approval is not None
                    else None,
                    witness_ref=witness_ref,
                    exc=exc,
                    idempotency_key=action_request.idempotency_key,
                    action_request=action_request,
                    action_request_ref=action_ref,
                    policy_result_ref=policy_ref,
                    environment_ref=environment_ref,
                )
            raise

        observation = normalize_observation_ticket(raw_result)
        if observation is not None:
            observation.tool_name = tool_name
            observation.tool_input = dict(tool_input)
            return self._handle_observation_submission(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                attempt_ctx=attempt_ctx,
                observation=observation,
                policy=policy,
                policy_ref=policy_ref,
                decision_id=decision_id,
                capability_grant_id=capability_grant_id,
                workspace_lease_id=workspace_lease_id,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                witness_ref=witness_ref,
                action_request=action_request,
                action_request_ref=action_ref,
                approval_packet_ref=preview_artifact,
                environment_ref=environment_ref,
                approval_mode=approval_mode,
                rollback_plan=rollback_plan,
            )

        return self._finalize_successful_execution(
            tool=tool,
            tool_name=tool_name,
            tool_input=tool_input,
            raw_result=raw_result,
            attempt_ctx=attempt_ctx,
            action_request=action_request,
            action_ref=action_ref,
            policy=policy,
            policy_ref=policy_ref,
            preview_artifact=preview_artifact,
            witness_ref=witness_ref,
            contract=contract,
            evidence_case=evidence_case,
            authorization_plan=authorization_plan,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            environment_ref=environment_ref,
            approval_mode=approval_mode,
            matched_approval=matched_approval,
            rollback_plan=rollback_plan,
            governed=governed,
            deliberation_ref=deliberation_ref,
        )

    def spawn_subtasks(
        self, *, attempt_ctx: TaskExecutionContext, descriptors: list[dict[str, Any]]
    ) -> ToolExecutionResult:
        from hermit.kernel.execution.executor.subtask_handler import SubtaskSpawner

        spawner = SubtaskSpawner(store=self.store, executor=self)
        return spawner.handle_spawn(attempt_ctx=attempt_ctx, descriptors=descriptors)

    # -- State persistence delegates --
    def persist_suspended_state(self, attempt_ctx: TaskExecutionContext, **kw: Any) -> None:
        self._state_persistence.persist_suspended_state(attempt_ctx, **kw)

    def persist_blocked_state(self, attempt_ctx: TaskExecutionContext, **kw: Any) -> None:
        self._state_persistence.persist_blocked_state(attempt_ctx, **kw)

    def load_suspended_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self._state_persistence.load_suspended_state(step_attempt_id)

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self._state_persistence.load_blocked_state(step_attempt_id)

    def clear_suspended_state(self, step_attempt_id: str) -> None:
        self._state_persistence.clear_suspended_state(step_attempt_id)

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        self._state_persistence.clear_blocked_state(step_attempt_id)

    def current_note_cursor(self, step_attempt_id: str) -> int:
        return self._state_persistence.current_note_cursor(step_attempt_id)

    def consume_appended_notes(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[list[dict[str, Any]], int]:
        return self._state_persistence.consume_appended_notes(attempt_ctx)

    # -- Observation delegates --
    def poll_observation(
        self, step_attempt_id: str, *, now: float | None = None
    ) -> ObservationPollResult | None:
        from hermit.kernel.execution.executor.observation_handler import ObservationHandler

        h = ObservationHandler(
            store=self.store,
            registry=self.registry,
            policy_engine=self.policy_engine,
            receipt_service=self.receipt_service,
            decision_service=self.decision_service,
            capability_service=self.capability_service,
            reconciliations=self.reconciliations,
            _snapshot=self._snapshot,
            progress_summarizer=self.progress_summarizer,
            progress_summary_keepalive_seconds=self.progress_summary_keepalive_seconds,
            tool_output_limit=self.tool_output_limit,
            executor=self,
        )
        return h.poll_observation(step_attempt_id, now=now)

    def finalize_observation(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        terminal_status: str,
        raw_result: Any,
        is_error: bool,
        summary: str,
        model_content_override: Any = None,
    ) -> dict[str, Any]:
        from hermit.kernel.execution.executor.observation_handler import ObservationHandler

        h = ObservationHandler(
            store=self.store,
            registry=self.registry,
            policy_engine=self.policy_engine,
            receipt_service=self.receipt_service,
            decision_service=self.decision_service,
            capability_service=self.capability_service,
            reconciliations=self.reconciliations,
            _snapshot=self._snapshot,
            progress_summarizer=self.progress_summarizer,
            progress_summary_keepalive_seconds=self.progress_summary_keepalive_seconds,
            tool_output_limit=self.tool_output_limit,
            executor=self,
        )
        return h.finalize_observation(
            attempt_ctx,
            terminal_status=terminal_status,
            raw_result=raw_result,
            is_error=is_error,
            summary=summary,
            model_content_override=model_content_override,
        )

    # -- Approval matching delegate --
    def _matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> tuple[Any, str | None, str | None]:
        from hermit.kernel.execution.executor.approval_handler import ApprovalHandler

        h = ApprovalHandler(
            store=self.store,
            artifact_store=self.artifact_store,
            approval_service=self.approval_service,
            approval_copy=self.approval_copy,
            witness=self._witness,
            policy_engine=self.policy_engine,
        )
        return h.matching_approval(
            approval_record, action_request, policy, preview_artifact, attempt_ctx=attempt_ctx
        )

    # -- Drift handling delegate --
    def _supersede_attempt_for_drift(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        drift_reason: str,
    ) -> ToolExecutionResult:
        from hermit.kernel.execution.executor.drift_handler import DriftHandler

        h = DriftHandler(
            store=self.store,
            artifact_store=self.artifact_store,
            execution_contracts=self.execution_contracts,
            evidence_cases=self.evidence_cases,
            authorization_plans=self.authorization_plans,
        )
        return h.supersede_attempt_for_drift(
            attempt_ctx=attempt_ctx,
            tool_name=tool_name,
            tool_input=tool_input,
            drift_reason=drift_reason,
            execute_fn=self.execute,
        )

    # ================================================================
    # Extracted sub-flows (called from execute)
    # ================================================================

    def _handle_observation_submission(
        self,
        *,
        tool: Any,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        observation: ObservationTicket,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        capability_grant_id: str | None,
        workspace_lease_id: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        action_request: ActionRequest,
        action_request_ref: str | None,
        approval_packet_ref: str | None,
        environment_ref: str | None,
        approval_mode: str,
        rollback_plan: dict[str, Any],
    ) -> ToolExecutionResult:
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        self._state_persistence._store_pending_execution(
            attempt_ctx,
            {
                "tool_name": tool_name,
                "tool_input": dict(tool_input),
                "action_type": action_type,
                "policy": policy.to_dict(),
                "policy_ref": policy_ref,
                "decision_id": decision_id,
                "capability_grant_id": capability_grant_id,
                "workspace_lease_id": workspace_lease_id,
                "approval_ref": approval_ref,
                "action_request_ref": action_request_ref,
                "witness_ref": witness_ref,
                "idempotency_key": action_request.idempotency_key,
                "policy_result_ref": policy_ref,
                "approval_mode": approval_mode,
                "approval_packet_ref": approval_packet_ref,
                "environment_ref": environment_ref,
                "rollback_plan": rollback_plan,
            },
        )
        self._set_attempt_phase(attempt_ctx, "observing", reason="observation_submitted")
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="observing",
            waiting_reason=observation.topic_summary,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_ref,
            approval_packet_ref=approval_packet_ref,
            environment_ref=environment_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        self.store.append_event(
            event_type="tool.submitted",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor=getattr(attempt_ctx, "actor_principal_id", "principal_user"),
            payload={
                "tool_name": tool_name,
                "observer_kind": observation.observer_kind,
                "job_id": observation.job_id,
                "status_ref": observation.status_ref,
                "display_name": observation.display_name or tool_name,
                "topic_summary": observation.topic_summary,
                "poll_after_seconds": observation.poll_after_seconds,
                "ready_return": observation.ready_return,
            },
        )
        return ToolExecutionResult(
            model_content=observation.topic_summary,
            raw_result={"job_id": observation.job_id, "status_ref": observation.status_ref},
            blocked=True,
            suspended=True,
            waiting_kind="observing",
            observation=observation,
            approval_id=approval_ref,
            policy_decision=policy,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="observation_submitted",
            execution_status="observing",
            state_applied=True,
        )

    def _handle_dispatch_denied(
        self,
        *,
        tool: Any,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        capability_grant_id: str,
        workspace_lease_id: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        error: CapabilityGrantError,
        idempotency_key: str | None,
        action_request_ref: str | None,
        policy_result_ref: str | None,
        environment_ref: str | None,
    ) -> ToolExecutionResult:
        if workspace_lease_id is not None:
            self.workspace_lease_service.release(workspace_lease_id)
        if capability_grant_id is not None:
            self.capability_service.revoke(capability_grant_id)
        self.store.append_event(
            event_type="dispatch.denied",
            entity_type="capability_grant",
            entity_id=capability_grant_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor=getattr(attempt_ctx, "actor_principal_id", "principal_user"),
            payload={
                "capability_grant_ref": capability_grant_id,
                "decision_ref": decision_id,
                "error_code": error.code,
                "error": str(error),
                "tool_name": tool_name,
            },
        )
        now = time.time()
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="failed",
            waiting_reason=str(error),
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            environment_ref=environment_ref,
            finished_at=now,
        )
        self.store.update_step(attempt_ctx.step_id, status="failed", finished_at=now)
        self.store.update_task_status(attempt_ctx.task_id, "failed")
        receipt_id = None
        if policy.requires_receipt:
            receipt_id = self._receipt_handler.issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result={"error": str(error), "error_code": error.code},
                attempt_ctx=attempt_ctx,
                approval_ref=approval_ref,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_id,
                capability_grant_ref=capability_grant_id,
                workspace_lease_ref=workspace_lease_id,
                action_request_ref=action_request_ref,
                policy_result_ref=policy_result_ref,
                witness_ref=witness_ref,
                environment_ref=environment_ref,
                result_code="dispatch_denied",
                idempotency_key=idempotency_key,
                result_summary=str(error),
                output_kind="dispatch_error",
                contract_ref=self._contract_refs(attempt_ctx)[0],
                authorization_plan_ref=self._contract_refs(attempt_ctx)[2],
                observed_effect_summary=str(error),
                reconciliation_required=True,
            )
            at = tool.action_class or self.policy_engine.infer_action_class(tool)
            self._reconciliation_executor.record_reconciliation(
                attempt_ctx=attempt_ctx,
                receipt_id=receipt_id,
                action_type=at,
                tool_input=tool_input,
                observables={},
                witness_ref=witness_ref,
                result_code_hint="dispatch_denied",
                authorized_effect_summary=str(error),
            )
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="failed")
            self.store.update_step(attempt_ctx.step_id, status="failed")
            self.store.update_task_status(attempt_ctx.task_id, "failed")
        return ToolExecutionResult(
            model_content=f"[Capability Denied] {error}",
            raw_result={"error": str(error), "error_code": error.code},
            denied=True,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="dispatch_denied",
            execution_status="failed",
            state_applied=True,
        )

    def _handle_uncertain_outcome(
        self,
        *,
        tool: Any,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        capability_grant_id: str,
        workspace_lease_id: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        exc: Exception,
        idempotency_key: str | None,
        action_request: ActionRequest,
        action_request_ref: str | None,
        policy_result_ref: str | None,
        environment_ref: str | None,
    ) -> ToolExecutionResult:
        if workspace_lease_id is not None:
            self.workspace_lease_service.release(workspace_lease_id)
        if capability_grant_id is not None:
            self.capability_service.revoke(capability_grant_id)
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        outcome = self.reconcile_service.reconcile(
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=attempt_ctx.workspace_root,
            observables=dict(action_request.derived),
            witness=load_witness_payload(self.store, self.artifact_store, witness_ref),
        )
        result_code = (
            outcome.result_code if outcome.result_code != "still_unknown" else "unknown_outcome"
        )
        task_status = "needs_attention" if result_code == "unknown_outcome" else "reconciling"
        summary = f"{outcome.summary} Original error: {type(exc).__name__}: {exc}"
        self.store.append_event(
            event_type="outcome.uncertain",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor=getattr(attempt_ctx, "actor_principal_id", "principal_user"),
            payload={
                "tool_name": tool_name,
                "capability_grant_ref": capability_grant_id,
                "decision_ref": decision_id,
                "result_code": result_code,
                "error": str(exc),
            },
        )
        self._trace(
            "outcome.uncertain",
            attempt_ctx,
            phase="reconciling",
            grant_ref=capability_grant_id,
            payload={"error": str(exc)[:200]},
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="reconciling",
            waiting_reason=str(exc),
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            environment_ref=environment_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="reconciling")
        self.store.update_task_status(attempt_ctx.task_id, task_status)
        receipt_id = self._receipt_handler.issue_receipt(
            tool=tool,
            tool_name=tool_name,
            tool_input=tool_input,
            raw_result={"error": str(exc), "reconcile_summary": outcome.summary},
            attempt_ctx=attempt_ctx,
            approval_ref=approval_ref,
            policy=policy,
            policy_ref=policy_ref,
            decision_ref=decision_id,
            capability_grant_ref=capability_grant_id,
            workspace_lease_ref=workspace_lease_id,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            witness_ref=witness_ref,
            environment_ref=environment_ref,
            result_code=result_code,
            idempotency_key=idempotency_key,
            result_summary=summary,
            output_kind="tool_error",
            contract_ref=self._contract_refs(attempt_ctx)[0],
            authorization_plan_ref=self._contract_refs(attempt_ctx)[2],
            observed_effect_summary=outcome.summary,
            reconciliation_required=True,
        )
        reconciliation, _ = self._reconciliation_executor.record_reconciliation(
            attempt_ctx=attempt_ctx,
            receipt_id=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            observables=dict(action_request.derived),
            witness_ref=witness_ref,
            result_code_hint="unknown_outcome",
            authorized_effect_summary=ReconciliationExecutor.authorized_effect_summary(
                action_request=action_request, contract=self._load_contract_bundle(attempt_ctx)[0]
            ),
        )
        return ToolExecutionResult(
            model_content=f"[Execution Requires Attention] {summary}",
            raw_result={"error": str(exc)},
            denied=True,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code=result_code,
            execution_status=ReconciliationExecutor.reconciliation_execution_status(reconciliation),
            state_applied=True,
        )

    def _handle_admissibility_block(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        resolution: str,
        action_ref: str,
        policy_ref: str,
        preview_artifact: str | None,
        witness_ref: str | None,
        contract: Any,
        evidence_case: Any,
        authorization_plan: Any,
        action_type: str,
        policy: PolicyDecision,
    ) -> ToolExecutionResult:
        decision_id = self.decision_service.record(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_type="admission",
            verdict=resolution,
            reason=f"Contract is not admissible: {resolution}.",
            evidence_refs=[
                r
                for r in [
                    action_ref,
                    policy_ref,
                    preview_artifact,
                    witness_ref,
                    contract.contract_id,
                    evidence_case.evidence_case_id,
                    authorization_plan.authorization_plan_id,
                ]
                if r
            ],
            policy_ref=policy_ref,
            contract_ref=contract.contract_id,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
            evidence_case_ref=evidence_case.evidence_case_id,
            action_type=action_type,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="blocked",
            waiting_reason=resolution,
            decision_id=decision_id,
            state_witness_ref=witness_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        return ToolExecutionResult(
            model_content=f"[Contract Blocked] {resolution}",
            raw_result={"resolution": resolution},
            blocked=True,
            suspended=True,
            waiting_kind="awaiting_evidence",
            policy_decision=policy,
            decision_id=decision_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="contract_blocked",
            execution_status="blocked",
            state_applied=True,
        )

    def _handle_policy_deny(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        action_ref: str,
        policy_ref: str,
        preview_artifact: str | None,
        witness_ref: str | None,
        contract: Any,
        evidence_case: Any,
        authorization_plan: Any,
        action_type: str,
        policy: PolicyDecision,
    ) -> ToolExecutionResult:
        self._set_attempt_phase(attempt_ctx, "settling", reason="policy_denied")
        decision_id = self.decision_service.record(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_type="policy_gate",
            verdict="deny",
            reason=policy.reason or f"{tool_name} denied by policy.",
            evidence_refs=[
                r
                for r in [
                    action_ref,
                    policy_ref,
                    preview_artifact,
                    getattr(contract, "contract_id", None),
                    getattr(evidence_case, "evidence_case_id", None),
                    getattr(authorization_plan, "authorization_plan_id", None),
                ]
                if r
            ],
            policy_ref=policy_ref,
            contract_ref=getattr(contract, "contract_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            action_type=action_type,
        )
        message = f"[Policy Denied] {policy.reason or f'{tool_name} denied by policy.'}"
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="failed",
            waiting_reason=policy.reason,
            approval_id=None,
            decision_id=decision_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_ref,
            policy_result_ref=policy_ref,
            approval_packet_ref=preview_artifact,
        )
        self.store.update_step(attempt_ctx.step_id, status="failed")
        self.store.update_task_status(attempt_ctx.task_id, "failed")
        self.store.append_event(
            event_type="policy.denied",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor=getattr(attempt_ctx, "actor_principal_id", "principal_user"),
            payload={
                "tool_name": tool_name,
                "policy_ref": policy_ref,
                "decision_ref": decision_id,
                "policy": policy.to_dict(),
            },
        )
        self._trace(
            "policy.denied",
            attempt_ctx,
            phase="settling",
            decision_ref=decision_id,
            payload={"action_class": str(action_type)},
        )
        return ToolExecutionResult(
            model_content=message,
            raw_result=message,
            denied=True,
            policy_decision=policy,
            decision_id=decision_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="denied",
            execution_status="failed",
            state_applied=True,
        )

    def _handle_approval_required(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        action_request: ActionRequest,
        action_ref: str,
        policy: PolicyDecision,
        policy_ref: str,
        preview_artifact: str | None,
        witness_ref: str | None,
        contract: Any,
        evidence_case: Any,
        authorization_plan: Any,
        action_type: str,
    ) -> ToolExecutionResult:
        self._set_attempt_phase(attempt_ctx, "awaiting_approval", reason="approval_required")
        decision_id = self.decision_service.record(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_type="policy_gate",
            verdict="require_approval",
            reason=policy.reason or "Approval required before execution.",
            evidence_refs=[
                r
                for r in [
                    action_ref,
                    policy_ref,
                    preview_artifact,
                    witness_ref,
                    getattr(contract, "contract_id", None),
                    getattr(evidence_case, "evidence_case_id", None),
                    getattr(authorization_plan, "authorization_plan_id", None),
                ]
                if r
            ],
            policy_ref=policy_ref,
            contract_ref=getattr(contract, "contract_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            action_type=action_type,
        )
        requested_action = self._request_builder.requested_action_payload(
            action_request,
            policy,
            preview_artifact,
            decision_ref=decision_id,
            policy_ref=policy_ref,
            state_witness_ref=witness_ref,
            contract_ref=getattr(contract, "contract_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
        )
        requested_action["display_copy"] = self.approval_copy.build_canonical_copy(requested_action)
        approval_id = self.approval_service.request(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            approval_type=action_type,
            requested_action=requested_action,
            request_packet_ref=preview_artifact,
            requested_action_ref=action_ref,
            approval_packet_ref=preview_artifact,
            policy_result_ref=policy_ref,
            requested_contract_ref=getattr(contract, "contract_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            drift_expiry=getattr(contract, "expiry_at", None),
            fallback_contract_refs=getattr(contract, "fallback_contract_refs", []),
            decision_ref=decision_id,
            state_witness_ref=witness_ref,
        )
        self._trace(
            "approval.requested",
            attempt_ctx,
            phase="awaiting_approval",
            approval_ref=approval_id,
            decision_ref=decision_id,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="awaiting_approval",
            waiting_reason=policy.reason,
            approval_id=approval_id,
            decision_id=decision_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_ref,
            policy_result_ref=policy_ref,
            approval_packet_ref=preview_artifact,
            execution_contract_ref=getattr(contract, "contract_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        blocked_message = self.approval_copy.model_prompt(requested_action, approval_id)
        approval_message = self.approval_copy.blocked_message(requested_action, approval_id)
        return ToolExecutionResult(
            model_content=blocked_message,
            blocked=True,
            suspended=True,
            waiting_kind="awaiting_approval",
            approval_id=approval_id,
            approval_message=approval_message,
            policy_decision=policy,
            decision_id=decision_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="approval_required",
            execution_status="awaiting_approval",
            state_applied=True,
        )

    def _authorize_and_grant(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        action_request: ActionRequest,
        action_ref: str,
        policy: PolicyDecision,
        policy_ref: str,
        preview_artifact: str | None,
        witness_ref: str | None,
        contract: Any,
        evidence_case: Any,
        authorization_plan: Any,
        action_type: str,
        approval_mode: str,
        matched_approval: Any,
    ) -> ToolExecutionResult | dict[str, Any]:
        self._set_attempt_phase(attempt_ctx, "authorized_pre_exec", reason="execution_authorized")
        if authorization_plan is not None:
            self.store.update_authorization_plan(
                authorization_plan.authorization_plan_id, status="authorized"
            )
        if contract is not None:
            self.store.update_execution_contract(contract.contract_id, status="authorized")
        decision_id = self.decision_service.record(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_type="execution_authorization",
            verdict="allow",
            reason=self._auth_handler.authorization_reason(
                policy=policy, approval_mode=approval_mode
            ),
            evidence_refs=[
                r
                for r in [
                    action_ref,
                    policy_ref,
                    preview_artifact,
                    witness_ref,
                    getattr(contract, "contract_id", None),
                    getattr(evidence_case, "evidence_case_id", None),
                    getattr(authorization_plan, "authorization_plan_id", None),
                ]
                if r
            ],
            policy_ref=policy_ref,
            approval_ref=matched_approval.approval_id if matched_approval is not None else None,
            contract_ref=getattr(contract, "contract_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            action_type=action_type,
        )
        try:
            workspace_lease_id = self._auth_handler.ensure_workspace_lease(
                attempt_ctx=attempt_ctx, action_request=action_request, approval_mode=approval_mode
            )
        except WorkspaceLeaseQueued as wlq:
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="waiting",
                waiting_reason=f"Workspace busy, queued at position {wlq.position}",
            )
            return ToolExecutionResult(
                model_content=f"[Workspace Queued] {wlq}",
                raw_result={"queue_entry_id": wlq.queue_entry_id, "position": wlq.position},
                blocked=True,
                result_code="workspace_queued",
                execution_status="waiting",
                state_applied=True,
            )
        environment_ref = None
        if workspace_lease_id is not None:
            lease = self.store.get_workspace_lease(workspace_lease_id)
            if lease is not None:
                environment_ref = lease.environment_ref
        pa = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if pa is not None and self._policy_version_drifted(pa):
            return self._supersede_attempt_for_drift(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
                drift_reason="policy_version_drift",
            )
        capability_grant_id = self.capability_service.issue(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_ref=decision_id,
            approval_ref=matched_approval.approval_id if matched_approval is not None else None,
            policy_ref=policy_ref,
            issued_to_principal_id=attempt_ctx.actor_principal_id,
            issued_by_principal_id="kernel",
            workspace_lease_ref=workspace_lease_id,
            action_class=action_type,
            resource_scope=list(action_request.resource_scopes),
            idempotency_key=action_request.idempotency_key,
            constraints=self._auth_handler.capability_constraints(
                action_request, workspace_lease_id=workspace_lease_id
            ),
        )
        self._trace(
            "execution.authorized",
            attempt_ctx,
            phase="authorized_pre_exec",
            decision_ref=decision_id,
            grant_ref=capability_grant_id,
            lease_ref=workspace_lease_id,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="dispatching",
            waiting_reason=None,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_ref,
            policy_result_ref=policy_ref,
            approval_packet_ref=preview_artifact,
            execution_contract_ref=getattr(contract, "contract_id", None),
            evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
            authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            environment_ref=environment_ref,
            idempotency_key=action_request.idempotency_key,
            executor_mode="tool_executor",
            policy_version=POLICY_RULES_VERSION,
        )
        self.store.update_step(attempt_ctx.step_id, status="dispatching")
        return {
            "decision_id": decision_id,
            "capability_grant_id": capability_grant_id,
            "workspace_lease_id": workspace_lease_id,
            "environment_ref": environment_ref,
        }

    def _finalize_successful_execution(
        self,
        *,
        tool: Any,
        tool_name: str,
        tool_input: dict[str, Any],
        raw_result: Any,
        attempt_ctx: TaskExecutionContext,
        action_request: ActionRequest,
        action_ref: str,
        policy: PolicyDecision,
        policy_ref: str | None,
        preview_artifact: str | None,
        witness_ref: str | None,
        contract: Any,
        evidence_case: Any,
        authorization_plan: Any,
        decision_id: str | None,
        capability_grant_id: str | None,
        workspace_lease_id: str | None,
        environment_ref: str | None,
        approval_mode: str,
        matched_approval: Any,
        rollback_plan: dict[str, Any],
        governed: bool,
        deliberation_ref: str | None,
    ) -> ToolExecutionResult:
        model_content = _format_model_content(raw_result, self.tool_output_limit)
        receipt_id = None
        reconciliation = None
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        auth_summary = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=contract
        )
        if governed:
            self._set_attempt_phase(attempt_ctx, "settling", reason="receipt_pending")
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="receipt_pending",
                decision_id=decision_id,
                capability_grant_id=capability_grant_id,
                workspace_lease_id=workspace_lease_id,
                state_witness_ref=witness_ref,
                action_request_ref=action_ref,
                policy_result_ref=policy_ref,
                approval_packet_ref=preview_artifact,
                environment_ref=environment_ref,
                execution_contract_ref=getattr(contract, "contract_id", None),
                evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
            )
            self.store.update_step(attempt_ctx.step_id, status="receipt_pending")
        if policy.requires_receipt:
            if capability_grant_id is not None:
                self.capability_service.consume(capability_grant_id)
            receipt_id = self._receipt_handler.issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result=raw_result,
                attempt_ctx=attempt_ctx,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_id,
                capability_grant_ref=capability_grant_id,
                workspace_lease_ref=workspace_lease_id,
                action_request_ref=action_ref,
                policy_result_ref=policy_ref,
                witness_ref=witness_ref,
                environment_ref=environment_ref,
                result_code="succeeded",
                idempotency_key=action_request.idempotency_key,
                result_summary=self._auth_handler.successful_result_summary(
                    tool_name=tool_name, approval_mode=approval_mode
                ),
                rollback_supported=rollback_plan["supported"],
                rollback_strategy=rollback_plan["strategy"],
                rollback_artifact_refs=rollback_plan["artifact_refs"],
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                observed_effect_summary=auth_summary,
                reconciliation_required=governed,
            )
            self._log_trust_feedback(action_request, attempt_ctx, "succeeded")
            self._trace(
                "receipt.issued",
                attempt_ctx,
                phase="settling",
                receipt_ref=receipt_id,
                grant_ref=capability_grant_id,
                decision_ref=decision_id,
            )
        execution_status = "succeeded"
        if governed and receipt_id is not None:
            reconciliation, _ = self._reconciliation_executor.record_reconciliation(
                attempt_ctx=attempt_ctx,
                receipt_id=receipt_id,
                action_type=action_type,
                tool_input=tool_input,
                observables=dict(action_request.derived),
                witness_ref=witness_ref,
                result_code_hint="succeeded",
                authorized_effect_summary=auth_summary,
            )
            execution_status = ReconciliationExecutor.reconciliation_execution_status(
                reconciliation
            )
        return ToolExecutionResult(
            model_content=model_content,
            raw_result=raw_result,
            blocked=False,
            approval_id=matched_approval.approval_id if matched_approval else None,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            deliberation_ref=deliberation_ref,
            result_code="succeeded",
            execution_status=execution_status,
        )

    def _run_deliberation_gate(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        action_type: str,
        action_ref: str,
        policy: PolicyDecision,
        policy_ref: str,
        witness_ref: str | None,
    ) -> ToolExecutionResult | dict[str, Any]:
        deliberation_risk = policy.risk_level or "medium"
        # Use policy.action_class (which reflects action_class_override from
        # guard rules) instead of the raw tool action_class.  This ensures
        # that read-only shell commands classified as "execute_command_readonly"
        # by the guard rules correctly bypass deliberation.
        effective_action_class = policy.action_class or action_type
        if not DeliberationService.check_deliberation_needed(
            risk_level=deliberation_risk, action_class=effective_action_class
        ):
            return {"deliberation_ref": None}
        dd = self._deliberation.run_full_deliberation(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            risk_level=deliberation_risk,
            action_class=action_type,
            context={
                "tool_name": tool_name,
                "tool_input": tool_input,
                "risk_level": deliberation_risk,
                "action_class": action_type,
                "task_id": attempt_ctx.task_id,
                "step_id": attempt_ctx.step_id,
            },
        )
        deliberation_ref: str | None = None
        if dd.debate_id:
            deliberation_ref, _ = self.artifact_store.store_json(
                {
                    "artifact_type": "deliberation_gate_record",
                    "debate_id": dd.debate_id,
                    "task_id": attempt_ctx.task_id,
                    "step_id": attempt_ctx.step_id,
                    "tool_name": tool_name,
                    "action_class": action_type,
                    "selected_candidate_id": dd.selected_candidate_id,
                    "confidence": dd.confidence,
                    "escalation_required": dd.escalation_required,
                    "merge_notes": dd.merge_notes,
                }
            )
        confidence = dd.confidence
        if dd.escalation_required or confidence < 0.3:
            reason = (
                "deliberation_escalation"
                if dd.escalation_required
                else "deliberation_low_confidence"
            )
            did = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="deliberation_gate",
                verdict="deny",
                reason=f"Deliberation {reason}: confidence={confidence:.2f}",
                evidence_refs=[r for r in [action_ref, policy_ref, deliberation_ref] if r],
                policy_ref=policy_ref,
                action_type=action_type,
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="blocked",
                waiting_reason=reason,
                state_witness_ref=witness_ref,
                decision_id=did,
            )
            self.store.update_step(attempt_ctx.step_id, status="blocked")
            self.store.update_task_status(attempt_ctx.task_id, "blocked")
            return ToolExecutionResult(
                model_content=f"[Deliberation Blocked] {reason} — confidence={confidence:.2f}, human review required.",
                raw_result={"deliberation_decision": dd},
                blocked=True,
                suspended=True,
                waiting_kind="awaiting_deliberation",
                policy_decision=policy,
                decision_id=did,
                policy_ref=policy_ref,
                witness_ref=witness_ref,
                result_code="deliberation_blocked",
                execution_status="blocked",
                state_applied=True,
            )
        if confidence < 0.7:
            did = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="deliberation_gate",
                verdict="require_approval",
                reason=f"Deliberation passed with low confidence ({confidence:.2f}). Selected: {dd.selected_candidate_id}.",
                evidence_refs=[r for r in [action_ref, policy_ref, deliberation_ref] if r],
                policy_ref=policy_ref,
                action_type=action_type,
            )
            approval_msg = (
                f"[Deliberation Review] confidence={confidence:.2f}\n"
                f"Selected: {dd.selected_candidate_id}\nNotes: {dd.merge_notes[:200]}"
            )
            ra: dict[str, Any] = {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "action_class": action_type,
                "reason": approval_msg,
                "deliberation_ref": deliberation_ref,
                "confidence": confidence,
                "selected_candidate_id": dd.selected_candidate_id,
            }
            if self.approval_copy:
                ra["display_copy"] = self.approval_copy.build_canonical_copy(ra)
            aid = self.approval_service.request(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                approval_type="deliberation_review",
                requested_action=ra,
                request_packet_ref=deliberation_ref,
                requested_action_ref=action_ref,
                policy_result_ref=policy_ref,
                decision_ref=did,
                state_witness_ref=witness_ref,
            )
            self._trace(
                "approval.requested",
                attempt_ctx,
                phase="awaiting_approval",
                approval_ref=aid,
                decision_ref=did,
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="blocked",
                waiting_reason="deliberation_approval_required",
                approval_id=aid,
                decision_id=did,
                state_witness_ref=witness_ref,
            )
            self.store.update_step(attempt_ctx.step_id, status="blocked")
            self.store.update_task_status(attempt_ctx.task_id, "blocked")
            return ToolExecutionResult(
                model_content=approval_msg,
                raw_result={"deliberation_decision": dd},
                blocked=True,
                suspended=True,
                waiting_kind="awaiting_approval",
                approval_id=aid,
                approval_message=approval_msg,
                policy_decision=policy,
                decision_id=did,
                policy_ref=policy_ref,
                witness_ref=witness_ref,
                result_code="deliberation_approval_required",
                execution_status="blocked",
                state_applied=True,
            )
        did = self.decision_service.record(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            decision_type="deliberation_gate",
            verdict="allow",
            reason=f"Deliberation passed with confidence={confidence:.2f}. Selected: {dd.selected_candidate_id}.",
            evidence_refs=[r for r in [action_ref, policy_ref, deliberation_ref] if r],
            policy_ref=policy_ref,
            action_type=action_type,
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, decision_id=did)
        return {"deliberation_ref": deliberation_ref}

    # -- Legacy compat methods for ObservationHandler and other callers --
    def _store_pending_execution(
        self, attempt_ctx: TaskExecutionContext, payload: dict[str, Any]
    ) -> None:
        self._state_persistence._store_pending_execution(attempt_ctx, payload)

    def _load_pending_execution(self, step_attempt_id: str) -> dict[str, Any]:
        return self._state_persistence._load_pending_execution(step_attempt_id)

    def _clear_pending_execution(self, step_attempt_id: str) -> None:
        self._state_persistence._clear_pending_execution(step_attempt_id)

    def _runtime_snapshot_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot.create_envelope(payload)

    def _store_runtime_snapshot_artifact(
        self, *, attempt_ctx: TaskExecutionContext, envelope: dict[str, Any], suspend_kind: str
    ) -> str:
        return self._request_builder.store_json_artifact(
            payload=envelope,
            kind="runtime.snapshot",
            attempt_ctx=attempt_ctx,
            metadata={"suspend_kind": suspend_kind},
        )

    def _issue_receipt(self, **kwargs: Any) -> str:
        return self._receipt_handler.issue_receipt(**kwargs)

    def _record_reconciliation(self, **kwargs: Any) -> Any:
        return self._reconciliation_executor.record_reconciliation(**kwargs)

    def _successful_result_summary(self, **kwargs: Any) -> str:
        return self._auth_handler.successful_result_summary(**kwargs)

    def _authorized_effect_summary(self, **kwargs: Any) -> str:
        return ReconciliationExecutor.authorized_effect_summary(**kwargs)

    def _store_json_artifact(self, **kwargs: Any) -> str:
        return self._request_builder.store_json_artifact(**kwargs)

    def _load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        return load_witness_payload(self.store, self.artifact_store, witness_ref)

    # Aliases for tests that mock these attributes directly
    @property
    def _receipt(self) -> ReceiptHandler:
        return self._receipt_handler

    @_receipt.setter
    def _receipt(self, value: Any) -> None:
        self._receipt_handler = value

    @property
    def _authorization(self) -> AuthorizationHandler:
        return self._auth_handler

    @_authorization.setter
    def _authorization(self, value: Any) -> None:
        self._auth_handler = value

    @property
    def _reconciliation(self) -> ReconciliationExecutor:
        return self._reconciliation_executor

    @_reconciliation.setter
    def _reconciliation(self, value: Any) -> None:
        self._reconciliation_executor = value
