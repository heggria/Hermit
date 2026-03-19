from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
from hermit.kernel.authority.workspaces import WorkspaceLeaseService
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.coordination.observation import (
    ObservationPollResult,
    ObservationTicket,
    normalize_observation_ticket,
)
from hermit.kernel.execution.executor.approval_handler import ApprovalHandler
from hermit.kernel.execution.executor.authorization_handler import AuthorizationHandler
from hermit.kernel.execution.executor.contract_executor import ContractExecutor
from hermit.kernel.execution.executor.dispatch_handler import DispatchDeniedHandler
from hermit.kernel.execution.executor.drift_handler import DriftHandler
from hermit.kernel.execution.executor.formatting import (
    format_model_content as _format_model_content,
)
from hermit.kernel.execution.executor.observation_handler import ObservationHandler
from hermit.kernel.execution.executor.phase_tracker import (
    PhaseTracker,
    _is_governed_action,
    _needs_witness,
)
from hermit.kernel.execution.executor.receipt_handler import ReceiptHandler
from hermit.kernel.execution.executor.reconciliation_executor import ReconciliationExecutor
from hermit.kernel.execution.executor.request_builder import RequestBuilder
from hermit.kernel.execution.executor.snapshot import RuntimeSnapshotManager
from hermit.kernel.execution.executor.state_persistence import StatePersistence
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.execution.executor.witness_handler import WitnessHandler
from hermit.kernel.execution.recovery.reconcile import ReconcileService
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
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
from hermit.kernel.task.projections.progress_summary import (
    ProgressSummaryFormatter,
)
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


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
    result_code: str = "succeeded"
    execution_status: str = "succeeded"
    state_applied: bool = False


class ToolExecutor:
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
        git_worktree: GitWorktreeInspector | None = None,
        progress_summarizer: ProgressSummaryFormatter | None = None,
        progress_summary_keepalive_seconds: float = 15.0,
        tool_output_limit: int = 4000,
    ) -> None:
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
        self._pattern_learner = TaskPatternLearner(store)
        self.progress_summarizer = progress_summarizer
        self.progress_summary_keepalive_seconds = max(
            float(progress_summary_keepalive_seconds or 0.0), 0.0
        )
        self.tool_output_limit = tool_output_limit

        # --- Delegate handlers ---
        self._phase = PhaseTracker(store=store)
        self._contract = ContractExecutor(
            store=store,
            artifact_store=artifact_store,
            execution_contracts=self.execution_contracts,
            evidence_cases=self.evidence_cases,
            authorization_plans=self.authorization_plans,
        )
        self._reconciliation = ReconciliationExecutor(
            store=store,
            artifact_store=artifact_store,
            reconciliations=self.reconciliations,
            execution_contracts=self.execution_contracts,
            evidence_cases=self.evidence_cases,
            pattern_learner=self._pattern_learner,
        )
        self._request = RequestBuilder(
            store=store,
            artifact_store=artifact_store,
            policy_engine=policy_engine,
            registry=registry,
            tool_output_limit=tool_output_limit,
        )
        self._receipt = ReceiptHandler(
            store=store,
            artifact_store=artifact_store,
            receipt_service=self.receipt_service,
            registry=registry,
            policy_engine=policy_engine,
            workspace_lease_service=self.workspace_lease_service,
        )
        self._approval = ApprovalHandler(
            store=store,
            artifact_store=artifact_store,
            approval_service=self.approval_service,
            approval_copy=self.approval_copy,
            witness=self._witness,
            policy_engine=policy_engine,
        )
        self._authorization = AuthorizationHandler(
            store=store,
            artifact_store=artifact_store,
            capability_service=self.capability_service,
            workspace_lease_service=self.workspace_lease_service,
            authorization_plans=self.authorization_plans,
            registry=registry,
            policy_engine=policy_engine,
            git_worktree=self.git_worktree,
        )
        self._witness_handler = WitnessHandler(
            store=store,
            artifact_store=artifact_store,
            witness=self._witness,
        )
        self._drift = DriftHandler(
            store=store,
            artifact_store=artifact_store,
            execution_contracts=self.execution_contracts,
            evidence_cases=self.evidence_cases,
            authorization_plans=self.authorization_plans,
        )
        self._dispatch = DispatchDeniedHandler(
            store=store,
            policy_engine=policy_engine,
            receipt_handler=self._receipt,
            reconciliation_executor=self._reconciliation,
        )
        self._persistence = StatePersistence(
            store=store,
            artifact_store=artifact_store,
            _snapshot=self._snapshot,
            _store_json_artifact=self._request.store_json_artifact,
        )
        self._observation = ObservationHandler(
            store=store,
            registry=registry,
            policy_engine=policy_engine,
            receipt_service=self.receipt_service,
            decision_service=self.decision_service,
            capability_service=self.capability_service,
            reconciliations=self.reconciliations,
            _snapshot=self._snapshot,
            progress_summarizer=progress_summarizer,
            progress_summary_keepalive_seconds=progress_summary_keepalive_seconds,
            tool_output_limit=tool_output_limit,
            executor=self,
        )

    # ------------------------------------------------------------------
    # Phase tracking (delegates to PhaseTracker)
    # ------------------------------------------------------------------

    def _set_attempt_phase(
        self,
        attempt_ctx: TaskExecutionContext,
        phase: str,
        *,
        reason: str | None = None,
    ) -> None:
        self._phase.set_attempt_phase(attempt_ctx, phase, reason=reason)

    # ------------------------------------------------------------------
    # Contract helpers (delegates to ContractExecutor)
    # ------------------------------------------------------------------

    def _contract_refs(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[str | None, str | None, str | None]:
        return self._contract.contract_refs(attempt_ctx)

    def _load_contract_bundle(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[Any | None, Any | None, Any | None]:
        return self._contract.load_contract_bundle(attempt_ctx)

    @staticmethod
    def _contract_expired(contract: Any) -> bool:
        return ContractExecutor.contract_expired(contract)

    @staticmethod
    def _policy_version_drifted(attempt: Any) -> bool:
        return ContractExecutor.policy_version_drifted(attempt)

    def _synthesize_contract_loop(
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
        return self._contract.synthesize_contract_loop(
            attempt_ctx=attempt_ctx,
            tool=tool,
            action_request=action_request,
            policy=policy,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            preview_artifact=preview_artifact,
            witness_ref=witness_ref,
        )

    @staticmethod
    def _admissibility_resolution(evidence_case: Any, authorization_plan: Any) -> str | None:
        return ContractExecutor.admissibility_resolution(evidence_case, authorization_plan)

    # ------------------------------------------------------------------
    # Reconciliation (delegates to ReconciliationExecutor)
    # ------------------------------------------------------------------

    def _record_reconciliation(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        receipt_id: str,
        action_type: str,
        tool_input: dict[str, Any],
        observables: dict[str, Any] | None,
        witness_ref: str | None,
        result_code_hint: str,
        authorized_effect_summary: str,
        resume_execution: bool = False,
    ) -> tuple[Any, Any]:
        return self._reconciliation.record_reconciliation(
            attempt_ctx=attempt_ctx,
            receipt_id=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            observables=observables,
            witness_ref=witness_ref,
            result_code_hint=result_code_hint,
            authorized_effect_summary=authorized_effect_summary,
            resume_execution=resume_execution,
        )

    @staticmethod
    def _reconciliation_execution_status(reconciliation: Any | None) -> str:
        return ReconciliationExecutor.reconciliation_execution_status(reconciliation)

    @staticmethod
    def _authorized_effect_summary(
        *,
        action_request: ActionRequest,
        contract: Any | None,
    ) -> str:
        return ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request,
            contract=contract,
        )

    # ------------------------------------------------------------------
    # Request building (delegates to RequestBuilder)
    # ------------------------------------------------------------------

    def _apply_request_overrides(
        self,
        action_request: ActionRequest,
        request_overrides: dict[str, Any],
    ) -> ActionRequest:
        return self._request.apply_request_overrides(action_request, request_overrides)

    def _record_action_request(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._request.record_action_request(action_request, attempt_ctx)

    def _record_policy_evaluation(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._request.record_policy_evaluation(action_request, policy, attempt_ctx)

    def _store_json_artifact(
        self,
        *,
        payload: Any,
        kind: str,
        attempt_ctx: TaskExecutionContext,
        metadata: dict[str, Any],
        event_type: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload_summary: dict[str, Any] | None = None,
    ) -> str:
        return self._request.store_json_artifact(
            payload=payload,
            kind=kind,
            attempt_ctx=attempt_ctx,
            metadata=metadata,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_summary=payload_summary,
        )

    def _build_preview_artifact(
        self,
        tool: ToolSpec,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str | None:
        return self._request.build_preview_artifact(tool, tool_input, attempt_ctx)

    def _requested_action_payload(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        decision_ref: str | None,
        policy_ref: str | None,
        state_witness_ref: str | None,
        contract_ref: str | None = None,
        evidence_case_ref: str | None = None,
        authorization_plan_ref: str | None = None,
    ) -> dict[str, Any]:
        return self._request.requested_action_payload(
            action_request,
            policy,
            preview_artifact,
            decision_ref=decision_ref,
            policy_ref=policy_ref,
            state_witness_ref=state_witness_ref,
            contract_ref=contract_ref,
            evidence_case_ref=evidence_case_ref,
            authorization_plan_ref=authorization_plan_ref,
        )

    # ------------------------------------------------------------------
    # Approval matching (delegates to ApprovalHandler)
    # ------------------------------------------------------------------

    def _matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> tuple[Any, str | None, str | None]:
        return self._approval.matching_approval(
            approval_record,
            action_request,
            policy,
            preview_artifact,
            attempt_ctx=attempt_ctx,
        )

    # ------------------------------------------------------------------
    # Receipt issuance (delegates to ReceiptHandler)
    # ------------------------------------------------------------------

    def _issue_receipt(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        raw_result: Any,
        attempt_ctx: TaskExecutionContext,
        approval_ref: str | None,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_ref: str | None,
        capability_grant_ref: str | None,
        workspace_lease_ref: str | None,
        action_request_ref: str | None = None,
        policy_result_ref: str | None = None,
        witness_ref: str | None,
        environment_ref: str | None = None,
        result_code: str,
        idempotency_key: str | None,
        result_summary: str | None = None,
        output_kind: str = "tool_output",
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
        contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        observed_effect_summary: str | None = None,
        reconciliation_required: bool = False,
    ) -> str:
        return self._receipt.issue_receipt(
            tool=tool,
            tool_name=tool_name,
            tool_input=tool_input,
            raw_result=raw_result,
            attempt_ctx=attempt_ctx,
            approval_ref=approval_ref,
            policy=policy,
            policy_ref=policy_ref,
            decision_ref=decision_ref,
            capability_grant_ref=capability_grant_ref,
            workspace_lease_ref=workspace_lease_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            witness_ref=witness_ref,
            environment_ref=environment_ref,
            result_code=result_code,
            idempotency_key=idempotency_key,
            result_summary=result_summary,
            output_kind=output_kind,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_artifact_refs=rollback_artifact_refs,
            contract_ref=contract_ref,
            authorization_plan_ref=authorization_plan_ref,
            observed_effect_summary=observed_effect_summary,
            reconciliation_required=reconciliation_required,
        )

    # ------------------------------------------------------------------
    # Authorization / rollback / lease (delegates to AuthorizationHandler)
    # ------------------------------------------------------------------

    def _authorization_reason(
        self,
        *,
        policy: PolicyDecision,
        approval_mode: str,
    ) -> str:
        return self._authorization.authorization_reason(policy=policy, approval_mode=approval_mode)

    def _successful_result_summary(
        self,
        *,
        tool_name: str,
        approval_mode: str,
    ) -> str:
        return self._authorization.successful_result_summary(
            tool_name=tool_name, approval_mode=approval_mode
        )

    def _prepare_rollback_plan(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        return self._authorization.prepare_rollback_plan(
            action_type=action_type,
            tool_name=tool_name,
            tool_input=tool_input,
            attempt_ctx=attempt_ctx,
        )

    def _store_inline_json_artifact(
        self,
        *,
        task_id: str,
        step_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        return self._authorization.store_inline_json_artifact(
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            payload=payload,
            metadata=metadata,
        )

    def _ensure_workspace_lease(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        action_request: ActionRequest,
        approval_mode: str,
    ) -> str | None:
        return self._authorization.ensure_workspace_lease(
            attempt_ctx=attempt_ctx,
            action_request=action_request,
            approval_mode=approval_mode,
        )

    def _capability_constraints(
        self,
        action_request: ActionRequest,
        *,
        workspace_lease_id: str | None,
    ) -> dict[str, Any]:
        return self._authorization.capability_constraints(
            action_request, workspace_lease_id=workspace_lease_id
        )

    # ------------------------------------------------------------------
    # Witness (delegates to WitnessHandler)
    # ------------------------------------------------------------------

    def _capture_state_witness(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._witness_handler.capture_state_witness(
            action_request, attempt_ctx, store_artifact=self._store_json_artifact
        )

    def _validate_state_witness(
        self,
        witness_ref: str,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> bool:
        return self._witness_handler.validate_state_witness(
            witness_ref, action_request, attempt_ctx
        )

    def _load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        return self._witness_handler.load_witness_payload(witness_ref)

    # ------------------------------------------------------------------
    # Drift handling (delegates to DriftHandler)
    # ------------------------------------------------------------------

    def _supersede_attempt_for_drift(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        drift_reason: str,
    ) -> ToolExecutionResult:
        return self._drift.supersede_attempt_for_drift(
            attempt_ctx=attempt_ctx,
            tool_name=tool_name,
            tool_input=tool_input,
            drift_reason=drift_reason,
            execute_fn=self.execute,
        )

    # ------------------------------------------------------------------
    # Dispatch denied (delegates to DispatchDeniedHandler)
    # ------------------------------------------------------------------

    def _handle_dispatch_denied(
        self,
        *,
        tool: ToolSpec,
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
        return self._dispatch.handle_dispatch_denied(
            tool=tool,
            tool_name=tool_name,
            tool_input=tool_input,
            attempt_ctx=attempt_ctx,
            policy=policy,
            policy_ref=policy_ref,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            approval_ref=approval_ref,
            witness_ref=witness_ref,
            error=error,
            idempotency_key=idempotency_key,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref,
            environment_ref=environment_ref,
        )

    # ------------------------------------------------------------------
    # Uncertain outcome (delegates to reconcile + receipt)
    # ------------------------------------------------------------------

    def _handle_uncertain_outcome(
        self,
        *,
        tool: ToolSpec,
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
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        outcome = self.reconcile_service.reconcile(
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=attempt_ctx.workspace_root,
            observables=dict(action_request.derived),
            witness=self._load_witness_payload(witness_ref),
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
            actor="kernel",
            payload={
                "tool_name": tool_name,
                "capability_grant_ref": capability_grant_id,
                "decision_ref": decision_id,
                "result_code": result_code,
                "error": str(exc),
            },
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
        receipt_id = self._issue_receipt(
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
        reconciliation, _ = self._record_reconciliation(
            attempt_ctx=attempt_ctx,
            receipt_id=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            observables=dict(action_request.derived),
            witness_ref=witness_ref,
            result_code_hint="unknown_outcome",
            authorized_effect_summary=self._authorized_effect_summary(
                action_request=action_request,
                contract=self._load_contract_bundle(attempt_ctx)[0],
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
            execution_status=self._reconciliation_execution_status(reconciliation),
            state_applied=True,
        )

    # ------------------------------------------------------------------
    # Observation (delegates to ObservationHandler)
    # ------------------------------------------------------------------

    def _handle_observation_submission(
        self,
        *,
        tool: ToolSpec,
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
        return self._observation.handle_observation_submission(
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
            approval_ref=approval_ref,
            witness_ref=witness_ref,
            action_request=action_request,
            action_request_ref=action_request_ref,
            approval_packet_ref=approval_packet_ref,
            environment_ref=environment_ref,
            approval_mode=approval_mode,
            rollback_plan=rollback_plan,
        )

    def poll_observation(
        self, step_attempt_id: str, *, now: float | None = None
    ) -> ObservationPollResult | None:
        return self._observation.poll_observation(step_attempt_id, now=now)

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
        return self._observation.finalize_observation(
            attempt_ctx,
            terminal_status=terminal_status,
            raw_result=raw_result,
            is_error=is_error,
            summary=summary,
            model_content_override=model_content_override,
        )

    # ------------------------------------------------------------------
    # State persistence (delegates to StatePersistence)
    # ------------------------------------------------------------------

    def persist_suspended_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        suspend_kind: str,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
        note_cursor_event_seq: int = 0,
        observation: ObservationTicket | None = None,
    ) -> None:
        self._persistence.persist_suspended_state(
            attempt_ctx,
            suspend_kind=suspend_kind,
            pending_tool_blocks=pending_tool_blocks,
            tool_result_blocks=tool_result_blocks,
            messages=messages,
            next_turn=next_turn,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
            note_cursor_event_seq=note_cursor_event_seq,
            observation=observation,
        )

    def persist_blocked_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
    ) -> None:
        self._persistence.persist_blocked_state(
            attempt_ctx,
            pending_tool_blocks=pending_tool_blocks,
            tool_result_blocks=tool_result_blocks,
            messages=messages,
            next_turn=next_turn,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
        )

    def load_suspended_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self._persistence.load_suspended_state(step_attempt_id)

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self._persistence.load_blocked_state(step_attempt_id)

    def clear_suspended_state(self, step_attempt_id: str) -> None:
        self._persistence.clear_suspended_state(step_attempt_id)

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        self._persistence.clear_blocked_state(step_attempt_id)

    def current_note_cursor(self, step_attempt_id: str) -> int:
        return self._persistence.current_note_cursor(step_attempt_id)

    def consume_appended_notes(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[list[dict[str, Any]], int]:
        return self._persistence.consume_appended_notes(attempt_ctx)

    def _runtime_snapshot_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._persistence._runtime_snapshot_envelope(payload)

    def _store_runtime_snapshot_artifact(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        envelope: dict[str, Any],
        suspend_kind: str,
    ) -> str:
        return self._persistence._store_runtime_snapshot_artifact(
            attempt_ctx=attempt_ctx,
            envelope=envelope,
            suspend_kind=suspend_kind,
        )

    def _store_pending_execution(
        self, attempt_ctx: TaskExecutionContext, payload: dict[str, Any]
    ) -> None:
        self._persistence._store_pending_execution(attempt_ctx, payload)

    def _load_pending_execution(self, step_attempt_id: str) -> dict[str, Any]:
        return self._persistence._load_pending_execution(step_attempt_id)

    def _clear_pending_execution(self, step_attempt_id: str) -> None:
        self._persistence._clear_pending_execution(step_attempt_id)

    # ------------------------------------------------------------------
    # Main orchestration — execute()
    # ------------------------------------------------------------------

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
            action_request = self._apply_request_overrides(action_request, request_overrides)
        action_ref = self._record_action_request(action_request, attempt_ctx)
        action_request = self._evidence_enricher.enrich(action_request)
        self._set_attempt_phase(attempt_ctx, "policy_pending", reason="policy_evaluation_started")
        policy = self.policy_engine.evaluate(action_request)
        policy_ref = self._record_policy_evaluation(action_request, policy, attempt_ctx)
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            action_request_ref=action_ref,
            policy_result_ref=policy_ref,
            idempotency_key=action_request.idempotency_key,
            executor_mode="tool_executor",
            policy_version=POLICY_RULES_VERSION,
        )
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        governed = _is_governed_action(tool, policy)

        attempt_record = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        approval_record = None
        if attempt_record is not None and attempt_record.approval_id:
            approval_record = self.store.get_approval(attempt_record.approval_id)

        preview_artifact = None
        if policy.obligations.require_preview:
            preview_artifact = self._build_preview_artifact(tool, tool_input, attempt_ctx)
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                approval_packet_ref=preview_artifact,
            )

        matched_approval, witness_ref, drift_reason = self._matching_approval(
            approval_record,
            action_request,
            policy,
            preview_artifact,
            attempt_ctx=attempt_ctx,
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
            witness_ref = self._capture_state_witness(action_request, attempt_ctx)

        contract = None
        evidence_case = None
        authorization_plan = None
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
            attempt_record_for_policy = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
            if attempt_record_for_policy is not None and self._policy_version_drifted(
                attempt_record_for_policy
            ):
                return self._supersede_attempt_for_drift(
                    attempt_ctx=attempt_ctx,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    drift_reason="policy_version_drift",
                )
            resolution = self._admissibility_resolution(evidence_case, authorization_plan)
            if resolution is not None and policy.verdict != "deny":
                decision_id = self.decision_service.record(
                    task_id=attempt_ctx.task_id,
                    step_id=attempt_ctx.step_id,
                    step_attempt_id=attempt_ctx.step_attempt_id,
                    decision_type="admission",
                    verdict=resolution,
                    reason=f"Contract is not admissible: {resolution}.",
                    evidence_refs=[
                        ref
                        for ref in [
                            action_ref,
                            policy_ref,
                            preview_artifact,
                            witness_ref,
                            contract.contract_id,
                            evidence_case.evidence_case_id,
                            authorization_plan.authorization_plan_id,
                        ]
                        if ref
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

        if policy.verdict == "deny":
            self._set_attempt_phase(attempt_ctx, "settling", reason="policy_denied")
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="policy_gate",
                verdict="deny",
                reason=policy.reason or f"{tool_name} denied by policy.",
                evidence_refs=[
                    ref
                    for ref in [
                        action_ref,
                        policy_ref,
                        preview_artifact,
                        getattr(contract, "contract_id", None),
                        getattr(evidence_case, "evidence_case_id", None),
                        getattr(authorization_plan, "authorization_plan_id", None),
                    ]
                    if ref
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
                actor="kernel",
                payload={
                    "tool_name": tool_name,
                    "policy_ref": policy_ref,
                    "decision_ref": decision_id,
                    "policy": policy.to_dict(),
                },
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

        if policy.obligations.require_approval and matched_approval is None:
            self._set_attempt_phase(attempt_ctx, "awaiting_approval", reason="approval_required")
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="policy_gate",
                verdict="require_approval",
                reason=policy.reason or "Approval required before execution.",
                evidence_refs=[
                    ref
                    for ref in [
                        action_ref,
                        policy_ref,
                        preview_artifact,
                        witness_ref,
                        getattr(contract, "contract_id", None),
                        getattr(evidence_case, "evidence_case_id", None),
                        getattr(authorization_plan, "authorization_plan_id", None),
                    ]
                    if ref
                ],
                policy_ref=policy_ref,
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
                action_type=action_type,
            )
            requested_action = self._requested_action_payload(
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
            requested_action["display_copy"] = self.approval_copy.build_canonical_copy(
                requested_action
            )
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

        decision_id = None
        capability_grant_id = None
        workspace_lease_id = None
        environment_ref = None
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
            self._set_attempt_phase(
                attempt_ctx, "authorized_pre_exec", reason="execution_authorized"
            )
            if authorization_plan is not None:
                self.store.update_authorization_plan(
                    authorization_plan.authorization_plan_id,
                    status="authorized",
                )
            if contract is not None:
                self.store.update_execution_contract(contract.contract_id, status="authorized")
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="execution_authorization",
                verdict="allow",
                reason=self._authorization_reason(policy=policy, approval_mode=approval_mode),
                evidence_refs=[
                    ref
                    for ref in [
                        action_ref,
                        policy_ref,
                        preview_artifact,
                        witness_ref,
                        getattr(contract, "contract_id", None),
                        getattr(evidence_case, "evidence_case_id", None),
                        getattr(authorization_plan, "authorization_plan_id", None),
                    ]
                    if ref
                ],
                policy_ref=policy_ref,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                evidence_case_ref=getattr(evidence_case, "evidence_case_id", None),
                action_type=action_type,
            )
            workspace_lease_id = self._ensure_workspace_lease(
                attempt_ctx=attempt_ctx,
                action_request=action_request,
                approval_mode=approval_mode,
            )
            if workspace_lease_id is not None:
                lease = self.store.get_workspace_lease(workspace_lease_id)
                if lease is not None:
                    environment_ref = lease.environment_ref
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
                constraints=self._capability_constraints(
                    action_request,
                    workspace_lease_id=workspace_lease_id,
                ),
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

        if governed and capability_grant_id is not None:
            try:
                self.capability_service.enforce(
                    capability_grant_id,
                    action_class=action_type,
                    resource_scope=list(action_request.resource_scopes),
                    constraints=self._capability_constraints(
                        action_request,
                        workspace_lease_id=workspace_lease_id,
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

        rollback_plan = self._prepare_rollback_plan(
            action_type=action_type,
            tool_name=tool_name,
            tool_input=tool_input,
            attempt_ctx=attempt_ctx,
        )

        try:
            self._set_attempt_phase(attempt_ctx, "executing", reason="tool_handler_invoked")
            if contract is not None:
                self.store.update_execution_contract(contract.contract_id, status="executing")
            raw_result = tool.handler(tool_input)
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

        model_content = _format_model_content(raw_result, self.tool_output_limit)
        receipt_id = None
        reconciliation = None
        authorized_effect_summary = self._authorized_effect_summary(
            action_request=action_request,
            contract=contract,
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
            receipt_id = self._issue_receipt(
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
                result_summary=self._successful_result_summary(
                    tool_name=tool_name,
                    approval_mode=approval_mode,
                ),
                rollback_supported=rollback_plan["supported"],
                rollback_strategy=rollback_plan["strategy"],
                rollback_artifact_refs=rollback_plan["artifact_refs"],
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                observed_effect_summary=authorized_effect_summary,
                reconciliation_required=governed,
            )
        execution_status = "succeeded"
        if governed and receipt_id is not None:
            reconciliation, _outcome = self._record_reconciliation(
                attempt_ctx=attempt_ctx,
                receipt_id=receipt_id,
                action_type=action_type,
                tool_input=tool_input,
                observables=dict(action_request.derived),
                witness_ref=witness_ref,
                result_code_hint="succeeded",
                authorized_effect_summary=authorized_effect_summary,
            )
            execution_status = self._reconciliation_execution_status(reconciliation)
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
            result_code="succeeded",
            execution_status=execution_status,
        )
