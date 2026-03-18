from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
from hermit.kernel.authority.workspaces import WorkspaceLeaseService, capture_execution_environment
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import ContractError
from hermit.kernel.execution.controller.contracts import contract_for
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.coordination.observation import (
    ObservationPollResult,
    ObservationProgress,
    ObservationTicket,
    normalize_observation_progress,
    normalize_observation_ticket,
)
from hermit.kernel.execution.executor.snapshot import RuntimeSnapshotManager
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome, ReconcileService
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import (
    POLICY_RULES_VERSION,
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    build_action_fingerprint,
)
from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.policy.approvals.decisions import DecisionService
from hermit.kernel.policy.evaluators.enrichment import PolicyEvidenceEnricher
from hermit.kernel.policy.permits.authorization_plans import AuthorizationPlanService
from hermit.kernel.task.models.records import ReconciliationRecord
from hermit.kernel.task.projections.progress_summary import (
    ProgressSummaryFormatter,
    normalize_progress_summary,
)
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec, serialize_tool_result

_BLOCK_TYPES = {"text", "image"}
_RUNTIME_SNAPSHOT_KEY = "runtime_snapshot"
_PENDING_EXECUTION_KEY = "pending_observation_execution"
_PENDING_EXECUTION_KIND = "runtime.pending_execution"
_RUNTIME_SNAPSHOT_SCHEMA_VERSION = 2
_RUNTIME_SNAPSHOT_TTL_SECONDS = 24 * 60 * 60
_RUNTIME_SNAPSHOT_MAX_BYTES = 256 * 1024
_RUNTIME_SNAPSHOT_V1_ALLOWED_KEYS = {
    "messages",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
}
_RUNTIME_SNAPSHOT_V2_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}
_RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}
_WITNESS_REQUIRED_ACTIONS = {
    "write_local",
    "patch_file",
    "execute_command",
    "network_write",
    "credentialed_api_call",
    "vcs_mutation",
    "publication",
    "memory_write",
}


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def _truncate_middle(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    head = max(1, limit // 2 - 8)
    tail = max(1, limit - head - len("\n...\n"))
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _format_model_content(value: Any, limit: int) -> Any:
    serialized: Any = serialize_tool_result(value)
    if isinstance(serialized, str):
        return _truncate_middle(serialized, limit)
    if (
        isinstance(serialized, dict)
        and cast(dict[str, Any], serialized).get("type") in _BLOCK_TYPES
    ):
        return cast(list[Any], [serialized])
    if isinstance(serialized, list) and all(
        isinstance(item, dict) and cast(dict[str, Any], item).get("type") in _BLOCK_TYPES
        for item in cast(list[Any], serialized)
    ):
        return cast(list[Any], serialized)
    text = json.dumps(serialized, ensure_ascii=True, indent=2, sort_keys=True)
    return _truncate_middle(text, limit)


def _progress_signature(
    value: dict[str, Any] | None,
) -> tuple[str, str, str | None, int | None, bool] | None:
    progress = normalize_observation_progress(value)
    if progress is None:
        return None
    return progress.signature()


def _progress_summary_signature(
    value: dict[str, Any] | None,
) -> tuple[str, str | None, str | None, int | None] | None:
    summary = normalize_progress_summary(value)
    if summary is None:
        return None
    return summary.signature()


def _compact_progress_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_governed_action(tool: ToolSpec, policy: PolicyDecision) -> bool:
    if tool.readonly and policy.verdict == "allow":
        return False
    if policy.action_class in {"read_local", "network_read"} and not policy.requires_receipt:
        return False
    return policy.action_class != "ephemeral_ui_mutation"


def _needs_witness(action_class: str) -> bool:
    return action_class in _WITNESS_REQUIRED_ACTIONS


def _execution_status_from_result_code(result_code: str) -> str:  # pyright: ignore[reportUnusedFunction]
    if result_code in {"approval_required"}:
        return "awaiting_approval"
    if result_code in {"contract_blocked"}:
        return "blocked"
    if result_code in {"observation_submitted"}:
        return "observing"
    if result_code in {"denied"}:
        return "failed"
    if result_code in {"failed", "timeout", "cancelled"}:
        return "failed"
    if result_code in {"reconciled_applied", "reconciled_not_applied", "reconciled_observed"}:
        return "reconciling"
    if result_code == "unknown_outcome":
        return "needs_attention"
    return "succeeded"


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

    def _set_attempt_phase(
        self,
        attempt_ctx: TaskExecutionContext,
        phase: str,
        *,
        reason: str | None = None,
    ) -> None:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context or {})
        previous = str(context.get("phase", "") or "")
        if previous == phase:
            return
        context["phase"] = phase
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, context=context)
        self.store.append_event(
            event_type="step_attempt.phase_changed",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "step_attempt_id": attempt_ctx.step_attempt_id,
                "previous_phase": previous,
                "phase": phase,
                "reason": reason,
            },
        )

    def _contract_refs(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[str | None, str | None, str | None]:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if attempt is None:
            return None, None, None
        return (
            attempt.execution_contract_ref,
            attempt.evidence_case_ref,
            attempt.authorization_plan_ref,
        )

    def _load_contract_bundle(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[Any | None, Any | None, Any | None]:
        contract_ref, evidence_case_ref, authorization_plan_ref = self._contract_refs(attempt_ctx)
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
        recorded_version = str(getattr(attempt, "policy_version", "") or "").strip()
        if not recorded_version:
            return False
        return recorded_version != POLICY_RULES_VERSION

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
        self._set_attempt_phase(attempt_ctx, "contracting", reason="contract_synthesis_started")
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
        self._set_attempt_phase(
            attempt_ctx, "preflighting", reason="authorization_preflight_started"
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
    def _admissibility_resolution(evidence_case: Any, authorization_plan: Any) -> str | None:
        if str(evidence_case.status or "") != "sufficient":
            return "gather_more_evidence"
        if str(authorization_plan.status or "") == "blocked":
            return "request_authority"
        return None

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
    ) -> tuple[ReconciliationRecord | None, ReconcileOutcome | None]:
        contract_ref, _evidence_case_ref, _authorization_plan_ref = self._contract_refs(attempt_ctx)
        if contract_ref is None:
            return None, None
        self._set_attempt_phase(attempt_ctx, "reconciling", reason="receipt_reconciliation_started")
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="reconciling")
        reconciliation, outcome, _artifact_ref = self.reconciliations.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract_ref,
            receipt_ref=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=attempt_ctx.workspace_root,
            observables=observables,
            witness=self._load_witness_payload(witness_ref),
            result_code_hint=result_code_hint,
            authorized_effect_summary=authorized_effect_summary,
        )
        contract_status = {
            "satisfied": "satisfied",
            "partial": "partially_satisfied",
            "satisfied_with_downgrade": "partially_satisfied",
            "violated": "violated",
            "unauthorized": "violated",
            "ambiguous": "abandoned",
        }.get(reconciliation.result_class, "abandoned")
        self.store.update_execution_contract(
            contract_ref,
            status=contract_status,
            operator_summary=reconciliation.operator_summary,
        )
        result_class = str(reconciliation.result_class or "")
        if result_class == "satisfied":
            self._learn_contract_template(reconciliation, contract_ref)
        if result_class == "violated":
            self._invalidate_memories_for_reconciliation(reconciliation)
            self._degrade_templates_for_violation(reconciliation)
        self._record_template_outcome(attempt_ctx, result_class)
        if resume_execution:
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="running")
            self._set_attempt_phase(attempt_ctx, "executing", reason="reconciliation_complete")
            return reconciliation, outcome
        if result_class == "satisfied":
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="succeeded")
            self.store.update_step(attempt_ctx.step_id, status="succeeded")
            self.store.update_task_status(attempt_ctx.task_id, "completed")
            self._learn_task_pattern(attempt_ctx.task_id)
            self._set_attempt_phase(attempt_ctx, "reconciled", reason="reconciliation_satisfied")
            return reconciliation, outcome
        if result_class in {"partial", "satisfied_with_downgrade"}:
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="reconciling")
            self.store.update_step(attempt_ctx.step_id, status="reconciling")
            self.store.update_task_status(attempt_ctx.task_id, "reconciling")
            return reconciliation, outcome
        failure_status = (
            "needs_attention" if result_class in {"ambiguous", "unauthorized"} else "failed"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status=failure_status)
        self.store.update_step(attempt_ctx.step_id, status=failure_status)
        self.store.update_task_status(
            attempt_ctx.task_id,
            "needs_attention" if failure_status == "needs_attention" else "failed",
        )
        return reconciliation, outcome

    def _learn_contract_template(
        self, reconciliation: ReconciliationRecord, contract_ref: str
    ) -> None:
        """Extract a learned template from a satisfied reconciliation."""
        contract = (
            self.store.get_execution_contract(contract_ref)
            if hasattr(self.store, "get_execution_contract")
            else None
        )
        if contract is None:
            return
        self.execution_contracts.template_learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
        )

    def _learn_task_pattern(self, task_id: str) -> None:
        """Extract a task-level execution pattern from a completed task."""
        self._pattern_learner.learn_from_completed_task(task_id)

    def _record_template_outcome(
        self, attempt_ctx: TaskExecutionContext, result_class: str
    ) -> None:
        """Record outcome for template-conditioned contracts."""
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if attempt is None:
            return
        template_ref = str(getattr(attempt, "selected_contract_template_ref", "") or "").strip()
        if not template_ref:
            return
        self.execution_contracts.template_learner.record_template_outcome(
            template_ref=template_ref,
            result_class=result_class,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
        )

    def _degrade_templates_for_violation(self, reconciliation: Any) -> None:
        """Degrade contract templates that were learned from a now-violated reconciliation."""
        reconciliation_ref = str(getattr(reconciliation, "reconciliation_id", "") or "").strip()
        if not reconciliation_ref:
            return
        self.execution_contracts.template_learner.degrade_templates_for_violation(
            reconciliation_ref
        )

    def _invalidate_memories_for_reconciliation(self, reconciliation: Any) -> None:
        reconciliation_ref = str(getattr(reconciliation, "reconciliation_id", "") or "").strip()
        if not reconciliation_ref or not hasattr(self.store, "list_memory_records"):
            return
        for record in self.store.list_memory_records(status="active", limit=5000):
            learned_ref = str(getattr(record, "learned_from_reconciliation_ref", "") or "").strip()
            if learned_ref == reconciliation_ref:
                self.store.update_memory_record(
                    record.memory_id,
                    status="invalidated",
                    invalidation_reason=f"reconciliation_violated:{reconciliation_ref}",
                    invalidated_at=time.time(),
                )

    @staticmethod
    def _reconciliation_execution_status(reconciliation: Any | None) -> str:
        result_class = str(getattr(reconciliation, "result_class", "") or "")
        if result_class == "satisfied":
            return "succeeded"
        if result_class in {"partial", "satisfied_with_downgrade"}:
            return "reconciling"
        if result_class in {"ambiguous", "unauthorized"}:
            return "needs_attention"
        if result_class == "violated":
            return "failed"
        return "reconciling"

    @staticmethod
    def _authorized_effect_summary(
        *,
        action_request: ActionRequest,
        contract: Any | None,
    ) -> str:
        if contract is not None and str(getattr(contract, "operator_summary", "") or "").strip():
            return str(contract.operator_summary)
        target_paths = [
            str(path) for path in action_request.derived.get("target_paths", []) if path
        ]
        network_hosts = [
            str(host) for host in action_request.derived.get("network_hosts", []) if host
        ]
        command_preview = str(action_request.derived.get("command_preview", "") or "").strip()
        if target_paths:
            return f"Expected side effects on {len(target_paths)} path(s)."
        if network_hosts:
            return f"Expected network mutation against {', '.join(network_hosts[:3])}."
        if command_preview:
            return f"Expected command execution: {command_preview}"
        return f"Expected {action_request.action_class} side effects."

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
        resume_messages_ref = self._store_resume_messages(messages, attempt_ctx=attempt_ctx)
        payload = {
            "suspend_kind": suspend_kind,
            "resume_messages_ref": resume_messages_ref,
            "pending_tool_blocks": pending_tool_blocks,
            "tool_result_blocks": tool_result_blocks,
            "next_turn": next_turn,
            "disable_tools": disable_tools,
            "readonly_only": readonly_only,
            "note_cursor_event_seq": note_cursor_event_seq,
            "observation": observation.to_dict() if observation is not None else None,
        }
        envelope = self._runtime_snapshot_envelope(payload)
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        if attempt_ctx.workspace_root:
            context["workspace_root"] = attempt_ctx.workspace_root
        context["note_cursor_event_seq"] = note_cursor_event_seq
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        context["phase"] = suspend_kind
        resume_from_ref = self._store_runtime_snapshot_artifact(
            attempt_ctx=attempt_ctx,
            envelope=envelope,
            suspend_kind=suspend_kind,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status=suspend_kind,
            context=context,
            resume_from_ref=resume_from_ref,
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
        self.persist_suspended_state(
            attempt_ctx,
            suspend_kind="awaiting_approval",
            pending_tool_blocks=pending_tool_blocks,
            tool_result_blocks=tool_result_blocks,
            messages=messages,
            next_turn=next_turn,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
        )

    def load_suspended_state(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                _t(
                    "kernel.executor.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        envelope = self._load_runtime_snapshot_envelope(attempt)
        if not envelope:
            return {}
        payload = self._runtime_snapshot_payload(envelope)
        if "messages" not in payload:
            resume_messages_ref = str(payload.get("resume_messages_ref", "") or "").strip()
            payload["messages"] = self._load_resume_messages(resume_messages_ref)
        return payload

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self.load_suspended_state(step_attempt_id)

    def clear_suspended_state(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_RUNTIME_SNAPSHOT_KEY, None)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            waiting_reason=None,
            resume_from_ref=None,
        )

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        self.clear_suspended_state(step_attempt_id)

    def _runtime_snapshot_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot.create_envelope(payload)

    def _runtime_snapshot_payload(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot.extract_payload(envelope)

    def _store_resume_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._store_json_artifact(
            payload=messages,
            kind="runtime.resume_messages",
            attempt_ctx=attempt_ctx,
            metadata={"message_count": len(messages)},
        )

    def _load_resume_messages(self, resume_messages_ref: str) -> list[dict[str, Any]]:
        return self._snapshot.load_resume_messages(resume_messages_ref)

    def _store_runtime_snapshot_artifact(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        envelope: dict[str, Any],
        suspend_kind: str,
    ) -> str:
        return self._store_json_artifact(
            payload=envelope,
            kind="runtime.snapshot",
            attempt_ctx=attempt_ctx,
            metadata={"suspend_kind": suspend_kind},
        )

    def _store_pending_execution_artifact(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        return self._store_json_artifact(
            payload={
                "schema": "runtime.pending_execution/v1",
                "payload": payload,
            },
            kind=_PENDING_EXECUTION_KIND,
            attempt_ctx=attempt_ctx,
            metadata={
                "status": "observing",
                "tool_name": str(payload.get("tool_name", "") or ""),
            },
        )

    def _load_runtime_snapshot_envelope(self, attempt: Any) -> dict[str, Any]:
        resume_from_ref = str(getattr(attempt, "resume_from_ref", "") or "").strip()
        if resume_from_ref:
            artifact = self.store.get_artifact(resume_from_ref)
            if artifact is not None:
                try:
                    payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    return cast(dict[str, Any], payload)
        context_val: Any = getattr(attempt, "context", {}) or {}
        snapshot_val: Any = cast(dict[str, Any], context_val).get(_RUNTIME_SNAPSHOT_KEY) or {}
        return cast(dict[str, Any], snapshot_val) if isinstance(snapshot_val, dict) else {}

    def _load_json_artifact_payload(self, artifact_ref: str) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_ref)
        if artifact is None:
            return {}
        try:
            payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        payload_dict = cast(dict[str, Any], payload)
        if payload_dict.get("schema") == "runtime.pending_execution/v1":
            nested: Any = payload_dict.get("payload")
            return cast(dict[str, Any], nested) if isinstance(nested, dict) else {}
        return payload_dict

    def current_note_cursor(self, step_attempt_id: str) -> int:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return 0
        return int(attempt.context.get("note_cursor_event_seq", 0) or 0)

    def consume_appended_notes(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[list[dict[str, Any]], int]:
        cursor = self.current_note_cursor(attempt_ctx.step_attempt_id)
        events = self.store.list_events(
            task_id=attempt_ctx.task_id,
            after_event_seq=cursor,
            limit=200,
        )
        note_events = [event for event in events if event["event_type"] == "task.note.appended"]
        if not note_events:
            return cast(list[dict[str, Any]], []), cursor
        latest = int(note_events[-1]["event_seq"])
        messages: list[dict[str, Any]] = []
        for event in note_events:
            payload = cast(dict[str, Any], event.get("payload") or {})
            prompt = str(payload.get("prompt", "") or payload.get("raw_text", "")).strip()
            if not prompt:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": (f"[Task Note Appended]\n{prompt}"),
                }
            )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context["note_cursor_event_seq"] = latest
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, context=context)
        return messages, latest

    def _store_pending_execution(
        self, attempt_ctx: TaskExecutionContext, payload: dict[str, Any]
    ) -> None:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context[_PENDING_EXECUTION_KEY] = payload
        pending_execution_ref = self._store_pending_execution_artifact(
            attempt_ctx=attempt_ctx,
            payload=payload,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            context=context,
            decision_id=str(payload.get("decision_id", "") or "") or None,
            capability_grant_id=str(payload.get("capability_grant_id", "") or "") or None,
            workspace_lease_id=str(payload.get("workspace_lease_id", "") or "") or None,
            state_witness_ref=str(payload.get("witness_ref", "") or "") or None,
            action_request_ref=str(payload.get("action_request_ref", "") or "") or None,
            policy_result_ref=str(payload.get("policy_result_ref", "") or "") or None,
            approval_packet_ref=str(payload.get("approval_packet_ref", "") or "") or None,
            pending_execution_ref=pending_execution_ref,
            idempotency_key=str(payload.get("idempotency_key", "") or "") or None,
            environment_ref=str(payload.get("environment_ref", "") or "") or None,
        )

    def _load_pending_execution(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return {}
        pending_execution_ref = str(getattr(attempt, "pending_execution_ref", "") or "").strip()
        if pending_execution_ref:
            payload = self._load_json_artifact_payload(pending_execution_ref)
            if payload:
                return payload
        payload_raw: Any = attempt.context.get(_PENDING_EXECUTION_KEY) or {}
        return cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}

    def _clear_pending_execution(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            pending_execution_ref=None,
        )

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
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        self._store_pending_execution(
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
            actor="kernel",
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

    def _poll_tool_call_observation(self, ticket: ObservationTicket) -> dict[str, Any]:
        tool_name = ticket.status_tool_name or ticket.tool_name
        if not tool_name:
            return {
                "status": "failed",
                "topic_summary": "Observation ticket is missing a status tool name.",
                "result": {"error": "missing status tool"},
                "is_error": True,
            }
        tool = self.registry.get(tool_name)
        payload = dict(ticket.status_tool_input or {})
        payload.setdefault("job_id", ticket.job_id)
        payload.setdefault("status_ref", ticket.status_ref)
        result = tool.handler(payload)
        nested = normalize_observation_ticket(result)
        if nested is not None:
            return {
                "status": "observing",
                "topic_summary": nested.topic_summary,
                "poll_after_seconds": nested.poll_after_seconds,
                "progress": nested.progress,
            }
        return result

    def _poll_ticket(self, ticket: ObservationTicket) -> dict[str, Any]:
        if ticket.observer_kind == "local_process":
            sandbox = getattr(getattr(self.registry, "_tools", {}).get("bash"), "handler", None)
            sandbox_self = getattr(sandbox, "_sandbox", None) or getattr(sandbox, "__self__", None)
            if sandbox_self is None or not hasattr(sandbox_self, "poll"):
                return {
                    "status": "failed",
                    "topic_summary": f"Observation handler unavailable for job {ticket.job_id}.",
                    "result": {"error": "local process observer unavailable"},
                    "is_error": True,
                }
            return sandbox_self.poll(ticket.job_id)
        if ticket.observer_kind == "tool_call":
            return self._poll_tool_call_observation(ticket)
        return {
            "status": "failed",
            "topic_summary": f"Unsupported observer kind: {ticket.observer_kind}",
            "result": {"error": f"unsupported observer kind: {ticket.observer_kind}"},
            "is_error": True,
        }

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
        pending = self._load_pending_execution(attempt_ctx.step_attempt_id)
        if not pending:
            model_content = (
                model_content_override
                if model_content_override is not None
                else _format_model_content(raw_result, self.tool_output_limit)
            )
            return {
                "raw_result": raw_result,
                "model_content": model_content,
                "is_error": is_error,
                "result_code": terminal_status,
            }
        tool_name = str(pending.get("tool_name", ""))
        tool_input = dict(pending.get("tool_input", {}) or {})
        tool = self.registry.get(tool_name)
        policy = PolicyDecision.from_dict(dict(pending.get("policy", {}) or {}))
        policy_ref = str(pending.get("policy_ref", "") or "") or None
        decision_ref = str(pending.get("decision_id", "") or "") or None
        capability_grant_ref = str(pending.get("capability_grant_id", "") or "") or None
        workspace_lease_ref = str(pending.get("workspace_lease_id", "") or "") or None
        approval_ref = str(pending.get("approval_ref", "") or "") or None
        witness_ref = str(pending.get("witness_ref", "") or "") or None
        action_request_ref = str(pending.get("action_request_ref", "") or "") or None
        policy_result_ref = str(pending.get("policy_result_ref", "") or "") or None
        environment_ref = str(pending.get("environment_ref", "") or "") or None
        approval_mode = str(pending.get("approval_mode", "") or "")
        rollback_plan = dict(pending.get("rollback_plan", {}) or {})

        result_code = (
            terminal_status
            if terminal_status in {"failed", "timeout", "cancelled"}
            else "succeeded"
        )
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        governed = _is_governed_action(tool, policy)
        model_content = (
            model_content_override
            if model_content_override is not None
            else _format_model_content(raw_result, self.tool_output_limit)
        )
        self._set_attempt_phase(attempt_ctx, "settling", reason="observation_finalized")
        receipt_id = None
        if policy.requires_receipt:
            if capability_grant_ref and result_code == "succeeded":
                self.capability_service.consume(capability_grant_ref)
            contract, _evidence_case, authorization_plan = self._load_contract_bundle(attempt_ctx)
            receipt_id = self._issue_receipt(
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
                idempotency_key=str(pending.get("idempotency_key", "") or "") or None,
                result_summary=summary
                if result_code != "succeeded"
                else self._successful_result_summary(
                    tool_name=tool_name,
                    approval_mode=approval_mode,
                ),
                output_kind="tool_error" if is_error else "tool_output",
                rollback_supported=bool(rollback_plan.get("supported", False)),
                rollback_strategy=str(rollback_plan.get("strategy", "") or "") or None,
                rollback_artifact_refs=list(rollback_plan.get("artifact_refs", []) or []),
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                observed_effect_summary=summary,
                reconciliation_required=governed,
            )
            if governed:
                action_request = self.policy_engine.build_action_request(
                    tool, tool_input, attempt_ctx=attempt_ctx
                )
                self._record_reconciliation(
                    attempt_ctx=attempt_ctx,
                    receipt_id=receipt_id,
                    action_type=action_type,
                    tool_input=tool_input,
                    observables=dict(action_request.derived),
                    witness_ref=witness_ref,
                    result_code_hint=result_code,
                    authorized_effect_summary=self._authorized_effect_summary(
                        action_request=action_request,
                        contract=contract,
                    ),
                )
        self._clear_pending_execution(attempt_ctx.step_attempt_id)
        return {
            "raw_result": raw_result,
            "model_content": model_content if not is_error else f"Error: {summary}",
            "is_error": is_error,
            "result_code": result_code,
        }

    def _progress_summary_facts(
        self,
        *,
        task_id: str,
        step_attempt_id: str,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        events = self.store.list_events(task_id=task_id, limit=500)[-80:]
        relevant_types = {
            "task.created",
            "task.note.appended",
            "tool.submitted",
            "tool.progressed",
            "tool.status.changed",
            "approval.requested",
            "approval.granted",
            "approval.denied",
            "approval.consumed",
        }
        recent_events: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] not in relevant_types:
                continue
            payload = dict(event.get("payload", {}) or {})
            text = ""
            if event["event_type"] == "task.note.appended":
                text = str(payload.get("raw_text", "") or payload.get("prompt", "")).strip()
            else:
                text = (
                    str(payload.get("summary", "") or "")
                    or str(payload.get("topic_summary", "") or "")
                    or str(payload.get("detail", "") or "")
                    or str(payload.get("status", "") or "")
                ).strip()
            phase = str(payload.get("phase", "") or "").strip()
            recent_events.append(
                {
                    "event_type": event["event_type"],
                    "text": _compact_progress_text(text, limit=180),
                    "phase": phase or None,
                    "progress_percent": payload.get("progress_percent"),
                }
            )
        latest_progress = progress or normalize_observation_progress(ticket.progress)
        return {
            "task": {
                "title": _compact_progress_text(getattr(task, "title", ""), limit=120),
                "goal": _compact_progress_text(getattr(task, "goal", ""), limit=600),
                "status": status,
                "source_channel": getattr(task, "source_channel", "") if task is not None else "",
            },
            "attempt": {
                "step_attempt_id": step_attempt_id,
                "tool_name": ticket.tool_name,
                "display_name": ticket.display_name or ticket.tool_name or "observed task",
                "topic_summary": ticket.topic_summary,
                "observer_kind": ticket.observer_kind,
            },
            "progress": latest_progress.to_dict() if latest_progress is not None else None,
            "recent_events": recent_events[-8:],
        }

    def _maybe_emit_progress_summary(
        self,
        *,
        step_attempt_id: str,
        task_id: str | None,
        step_id: str | None,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
        progress_changed: bool,
        now: float,
    ) -> None:
        if self.progress_summarizer is None or not task_id:
            return
        keepalive_due = (
            status == "observing"
            and self.progress_summary_keepalive_seconds > 0
            and ticket.last_progress_summary_at is not None
            and (now - ticket.last_progress_summary_at) >= self.progress_summary_keepalive_seconds
        )
        if not progress_changed and not keepalive_due:
            return
        try:
            summary = self.progress_summarizer.summarize(
                facts=self._progress_summary_facts(
                    task_id=task_id,
                    step_attempt_id=step_attempt_id,
                    ticket=ticket,
                    status=status,
                    progress=progress,
                )
            )
        except Exception:
            return
        if summary is None or not summary.summary.strip():
            return
        if not summary.phase:
            if progress is not None and progress.phase:
                summary.phase = progress.phase
            elif status:
                summary.phase = status
        if summary.progress_percent is None and progress is not None:
            summary.progress_percent = progress.progress_percent
        previous_signature = _progress_summary_signature(ticket.progress_summary)
        current_signature = summary.signature()
        ticket.last_progress_summary_at = now
        if current_signature == previous_signature:
            return
        ticket.progress_summary = summary.to_dict()
        self.store.append_event(
            event_type="task.progress.summarized",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={
                **summary.to_dict(),
                "job_id": ticket.job_id,
                "status": status,
            },
        )

    def poll_observation(
        self, step_attempt_id: str, *, now: float | None = None
    ) -> ObservationPollResult | None:
        payload = self.load_suspended_state(step_attempt_id)
        if str(payload.get("suspend_kind", "")) != "observing":
            return None
        observation_data: Any = payload.get("observation")
        if not isinstance(observation_data, dict):
            return None
        ticket = ObservationTicket.from_dict(cast(dict[str, Any], observation_data))
        current = time.time() if now is None else now
        if ticket.next_poll_at and current < ticket.next_poll_at:
            return ObservationPollResult(ticket=ticket, should_resume=False)

        status_payload = self._poll_ticket(ticket)
        status = str(status_payload.get("status", "observing") or "observing")
        progress = normalize_observation_progress(status_payload.get("progress"))
        summary = str(
            status_payload.get("topic_summary", ticket.topic_summary) or ticket.topic_summary
        )
        if progress is not None and progress.summary:
            summary = progress.summary
        task_attempt = self.store.get_step_attempt(step_attempt_id)
        task_id = task_attempt.task_id if task_attempt else None
        step_id = task_attempt.step_id if task_attempt else None

        previous_progress_sig = _progress_signature(ticket.progress)
        current_progress_sig = progress.signature() if progress is not None else None
        if progress is not None:
            ticket.progress = progress.to_dict()
            ticket.topic_summary = progress.summary or summary
            if current_progress_sig != previous_progress_sig:
                self.store.append_event(
                    event_type="tool.progressed",
                    entity_type="step_attempt",
                    entity_id=step_attempt_id,
                    task_id=task_id,
                    step_id=step_id,
                    actor="kernel",
                    payload={
                        "job_id": ticket.job_id,
                        "phase": progress.phase,
                        "summary": progress.summary,
                        "detail": progress.detail,
                        "progress_percent": progress.progress_percent,
                        "ready": bool(progress.ready),
                    },
                )
        else:
            ticket.topic_summary = summary
            progress = normalize_observation_progress(ticket.progress)

        if status != ticket.last_status or summary != ticket.last_status_summary:
            self.store.append_event(
                event_type="tool.status.changed",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "job_id": ticket.job_id,
                    "status": status,
                    "topic_summary": summary,
                },
            )
        ticket.last_status = status
        ticket.last_status_summary = summary
        self._maybe_emit_progress_summary(
            step_attempt_id=step_attempt_id,
            task_id=task_id,
            step_id=step_id,
            ticket=ticket,
            status=status,
            progress=progress,
            progress_changed=current_progress_sig != previous_progress_sig,
            now=current,
        )

        if (
            status == "observing"
            and ticket.ready_return
            and progress is not None
            and progress.ready
        ):
            attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
            ready_result = status_payload.get("result")
            if ready_result is None:
                ready_result = {
                    "job_id": ticket.job_id,
                    "status_ref": ticket.status_ref,
                    "ready": True,
                }
            final = self.finalize_observation(
                attempt_ctx,
                terminal_status="completed",
                raw_result=ready_result,
                is_error=False,
                summary=ticket.topic_summary,
                model_content_override=ticket.topic_summary,
            )
            ticket.terminal_status = "completed"
            ticket.final_result = final["raw_result"]
            ticket.final_model_content = final["model_content"]
            ticket.final_is_error = bool(final["is_error"])
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=True)

        if status == "observing":
            ticket.poll_after_seconds = float(
                status_payload.get("poll_after_seconds", ticket.poll_after_seconds)
                or ticket.poll_after_seconds
            )
            ticket.schedule_next_poll(now=current)
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=False)

        attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
        final = self.finalize_observation(
            attempt_ctx,
            terminal_status=status,
            raw_result=status_payload.get("result"),
            is_error=bool(status_payload.get("is_error", False) or status != "completed"),
            summary=summary,
        )
        ticket.terminal_status = status
        ticket.final_result = final["raw_result"]
        ticket.final_model_content = final["model_content"]
        ticket.final_is_error = bool(final["is_error"])
        payload["observation"] = ticket.to_dict()
        self._update_runtime_snapshot(step_attempt_id, payload)
        return ObservationPollResult(ticket=ticket, should_resume=True)

    def _attempt_context_from_snapshot(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown step attempt: {step_attempt_id}")
        task = self.store.get_task(attempt.task_id)
        if task is None:
            raise KeyError(f"Unknown task for step attempt: {step_attempt_id}")
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=str(attempt.context.get("workspace_root", "") or ""),
        )

    def _update_runtime_snapshot(self, step_attempt_id: str, payload: dict[str, Any]) -> None:
        snapshot_payload = dict(payload)
        snapshot_payload.pop("messages", None)
        envelope = self._runtime_snapshot_envelope(snapshot_payload)
        attempt = self.store.get_step_attempt(step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        if "note_cursor_event_seq" in snapshot_payload:
            context["note_cursor_event_seq"] = int(
                snapshot_payload.get("note_cursor_event_seq", 0) or 0
            )
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        if attempt is None:
            return
        resume_from_ref = self._store_runtime_snapshot_artifact(
            attempt_ctx=self._attempt_context_from_snapshot(step_attempt_id),
            envelope=envelope,
            suspend_kind=str(
                snapshot_payload.get("suspend_kind", attempt.status or "suspended") or "suspended"
            ),
        )
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            resume_from_ref=resume_from_ref,
        )

    def _apply_request_overrides(
        self,
        action_request: ActionRequest,
        request_overrides: dict[str, Any],
    ) -> ActionRequest:
        if "actor" in request_overrides:
            actor: Any = request_overrides["actor"]
            if not isinstance(actor, dict):
                raise ContractError(
                    "invalid_override",
                    _t(
                        "kernel.executor.error.request_overrides_actor_dict",
                        default="request_overrides.actor must be a dict",
                    ),
                )
            action_request.actor = dict(cast(dict[str, Any], actor))
        if "context" in request_overrides:
            context: Any = request_overrides["context"]
            if not isinstance(context, dict):
                raise ContractError(
                    "invalid_override",
                    _t(
                        "kernel.executor.error.request_overrides_context_dict",
                        default="request_overrides.context must be a dict",
                    ),
                )
            merged_context = dict(action_request.context)
            merged_context.update(cast(dict[str, Any], context))
            action_request.context = merged_context
        if "idempotency_key" in request_overrides:
            action_request.idempotency_key = str(request_overrides["idempotency_key"])
        return action_request

    def _record_action_request(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._store_json_artifact(
            payload=action_request.to_dict(),
            kind="action_request",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="action.requested",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "risk_hint": action_request.risk_hint,
                "resource_scopes": list(action_request.resource_scopes),
                "idempotency_key": action_request.idempotency_key,
            },
        )

    def _record_policy_evaluation(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        payload = {
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "risk_band": policy.risk_level,
            "verdict": policy.verdict,
            "reason": policy.reason,
            "reasons": [reason.to_dict() for reason in policy.reasons],
            "obligations": policy.obligations.to_dict(),
            "normalized_constraints": dict(policy.normalized_constraints),
            "policy_profile": action_request.context.get("policy_profile", "default"),
            "policy_rules_version": POLICY_RULES_VERSION,
        }
        return self._store_json_artifact(
            payload=payload,
            kind="policy_evaluation",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="policy.evaluated",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary=payload,
        )

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
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata=metadata,
        )
        if event_type and entity_type and entity_id:
            self.store.append_event(
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                actor="kernel",
                payload={"artifact_ref": artifact.artifact_id, **(payload_summary or {})},
            )
        return artifact.artifact_id

    def _build_preview_artifact(
        self,
        tool: ToolSpec,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str | None:
        preview_text = self._preview_text(tool, tool_input)
        if not preview_text:
            return None
        uri, content_hash = self.artifact_store.store_text(preview_text, extension="md")
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="approval_packet",
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool.name},
        )
        return artifact.artifact_id

    def _preview_text(self, tool: ToolSpec, tool_input: dict[str, Any]) -> str:
        if tool.name in {"write_file", "write_hermit_file"}:
            path = str(tool_input.get("path", ""))
            new_content = str(tool_input.get("content", ""))
            scope_hint = tool.resource_scope_hint
            root_hint = str(scope_hint[0] if isinstance(scope_hint, list) else scope_hint or "")
            old_content = ""
            if root_hint:
                try:
                    file_path = (Path(root_hint) / path).resolve()
                    if file_path.exists():
                        old_content = file_path.read_text(encoding="utf-8")
                except OSError:
                    old_content = ""
            diff = "\n".join(
                difflib.unified_diff(
                    old_content.splitlines(),
                    new_content.splitlines(),
                    fromfile=f"{path} (current)",
                    tofile=f"{path} (proposed)",
                    lineterm="",
                )
            )
            return _t(
                "kernel.executor.preview.write",
                default=f"# Write Preview\n\nPath: `{path}`\n\n```diff\n{diff or '(new file or no textual diff)'}\n```",
                path=path,
                diff=diff
                or _t(
                    "kernel.executor.preview.write.empty_diff",
                    default="(new file or no textual diff)",
                ),
            )
        if tool.name == "bash":
            command = str(tool_input.get("command", ""))
            return _t(
                "kernel.executor.preview.command",
                default=f"# Command Preview\n\n```bash\n{command}\n```",
                command=command,
            )
        return json.dumps({"tool": tool.name, "input": tool_input}, ensure_ascii=False, indent=2)

    def _capture_state_witness(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._witness.capture(
            action_request, attempt_ctx, store_artifact=self._store_json_artifact
        )

    def _state_witness_payload(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        return self._witness.payload(action_request, attempt_ctx)

    def _path_witness(self, path: str, *, workspace_root: Path) -> dict[str, Any]:
        return self._witness.path_witness(path, workspace_root=workspace_root)

    def _git_witness(self, workspace_root: Path) -> dict[str, Any]:
        return self._witness.git_witness(workspace_root)

    def _validate_state_witness(
        self,
        witness_ref: str,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> bool:
        return self._witness.validate(witness_ref, action_request, attempt_ctx)

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
        packet = dict(policy.approval_packet or {})
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
        fingerprint_payload = {
            "task_id": action_request.task_id,
            "step_attempt_id": action_request.step_attempt_id,
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "target_paths": action_request.derived.get("target_paths", []),
            "network_hosts": action_request.derived.get("network_hosts", []),
            "command_preview": action_request.derived.get("command_preview"),
        }
        fingerprint = build_action_fingerprint(fingerprint_payload)
        packet.setdefault("fingerprint", fingerprint)
        if preview_artifact is not None:
            packet["artifact_ids"] = list(
                dict.fromkeys(list(packet.get("artifact_ids", [])) + [preview_artifact])
            )
        contract_packet = None
        if contract is not None:
            contract_packet = {
                "contract_ref": contract.contract_id,
                "objective": contract.objective,
                "expected_effects": list(contract.expected_effects),
                "evidence_case_ref": evidence_case_ref,
                "evidence_sufficiency": {
                    "status": getattr(evidence_case, "status", None),
                    "score": getattr(evidence_case, "sufficiency_score", None),
                    "unresolved_gaps": list(getattr(evidence_case, "unresolved_gaps", []) or []),
                },
                "authorization_plan_ref": authorization_plan_ref,
                "authority_scope": dict(
                    getattr(authorization_plan, "proposed_grant_shape", {}) or {}
                ),
                "approval_route": getattr(authorization_plan, "approval_route", None),
                "current_gaps": list(getattr(authorization_plan, "current_gaps", []) or []),
                "drift_expiry": contract.expiry_at,
                "rollback_expectation": contract.rollback_expectation,
                "operator_summary": contract.operator_summary,
            }
            packet.setdefault(
                "title",
                f"Confirm {action_request.action_class.replace('_', ' ').title()} Contract",
            )
            packet.setdefault(
                "summary",
                contract.operator_summary
                or policy.reason
                or f"{action_request.tool_name} requires explicit approval.",
            )
            packet["contract_packet"] = contract_packet
        return {
            "tool_name": action_request.tool_name,
            "tool_input": action_request.tool_input,
            "risk_level": policy.risk_level,
            "reason": policy.reason,
            "fingerprint": fingerprint,
            "resource_scopes": list(action_request.resource_scopes),
            "target_paths": list(action_request.derived.get("target_paths", [])),
            "network_hosts": list(action_request.derived.get("network_hosts", [])),
            "command_preview": action_request.derived.get("command_preview"),
            "workspace_root": action_request.context.get("workspace_root", ""),
            "outside_workspace": bool(action_request.derived.get("outside_workspace")),
            "grant_scope_dir": action_request.derived.get("grant_candidate_prefix"),
            "approval_packet": packet,
            "contract_packet": contract_packet,
            "contract_ref": contract_ref,
            "evidence_case_ref": evidence_case_ref,
            "authorization_plan_ref": authorization_plan_ref,
            "decision_ref": decision_ref,
            "policy_ref": policy_ref,
            "state_witness_ref": state_witness_ref,
            "idempotency_key": action_request.idempotency_key,
        }

    def _matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> tuple[Any, str | None, str | None]:
        if approval_record is None or approval_record.status != "granted":
            return None, None, None
        witness_ref = approval_record.state_witness_ref
        if approval_record.drift_expiry and float(approval_record.drift_expiry) < time.time():
            self.store.append_event(
                event_type="approval.expired",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approval_id": approval_record.approval_id,
                    "drift_expiry": approval_record.drift_expiry,
                    "tool_name": action_request.tool_name,
                },
            )
            return None, witness_ref, "approval_drift"
        requested_action = dict(approval_record.requested_action or {})
        fingerprint_payload = {
            "task_id": action_request.task_id,
            "step_attempt_id": action_request.step_attempt_id,
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "target_paths": action_request.derived.get("target_paths", []),
            "network_hosts": action_request.derived.get("network_hosts", []),
            "command_preview": action_request.derived.get("command_preview"),
        }
        current_fingerprint = build_action_fingerprint(fingerprint_payload)
        approved_fingerprint = str(requested_action.get("fingerprint", "")).strip()
        if approved_fingerprint != current_fingerprint:
            self.store.append_event(
                event_type="approval.mismatch",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approved_fingerprint": approved_fingerprint,
                    "current_fingerprint": current_fingerprint,
                    "tool_name": action_request.tool_name,
                    "preview_artifact": preview_artifact,
                    "policy": policy.to_dict(),
                },
            )
            self.store.append_event(
                event_type="approval.drifted",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approval_id": approval_record.approval_id,
                    "drift_kind": "fingerprint_mismatch",
                    "approved_fingerprint": approved_fingerprint,
                    "current_fingerprint": current_fingerprint,
                },
            )
            return None, witness_ref, "approval_drift"
        if approval_record.evidence_case_ref:
            evidence_case = self.store.get_evidence_case(approval_record.evidence_case_ref)
            if evidence_case is None or str(evidence_case.status or "") != "sufficient":
                return None, witness_ref, "evidence_drift"
        if approval_record.authorization_plan_ref:
            authorization_plan = self.store.get_authorization_plan(
                approval_record.authorization_plan_ref
            )
            if authorization_plan is None:
                return None, witness_ref, "approval_drift"
            plan_status = str(authorization_plan.status or "")
            if plan_status in {"invalidated", "blocked", "expired"}:
                return None, witness_ref, "approval_drift"
            if plan_status not in {"awaiting_approval", "preflighted", "authorized"}:
                return None, witness_ref, "approval_drift"
        if (
            witness_ref
            and _needs_witness(action_request.action_class)
            and not self._validate_state_witness(witness_ref, action_request, attempt_ctx)
        ):
            return None, witness_ref, "witness_drift"
        return approval_record, witness_ref, None

    def _authorization_reason(
        self,
        *,
        policy: PolicyDecision,
        approval_mode: str,
    ) -> str:
        if approval_mode == "mutable_workspace":
            return _t(
                "kernel.executor.authorization.policy_allowed",
                default="Allowed after mutable workspace approval.",
            )
        if approval_mode == "once":
            return _t("kernel.executor.authorization.once")
        return policy.reason or _t("kernel.executor.authorization.policy_allowed")

    def _successful_result_summary(
        self,
        *,
        tool_name: str,
        approval_mode: str,
    ) -> str:
        if approval_mode == "mutable_workspace":
            return _t(
                "kernel.executor.result.success",
                default="{tool_name} completed under mutable workspace lease.",
                tool_name=tool_name,
            )
        if approval_mode == "once":
            return _t("kernel.executor.result.once", tool_name=tool_name)
        return _t("kernel.executor.result.success", tool_name=tool_name)

    def _prepare_rollback_plan(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        contract = contract_for(action_type)
        strategy = contract.rollback_strategy
        artifact_refs: list[str] = []
        supported = False
        if action_type in {"write_local", "patch_file"}:
            raw_path = str(tool_input.get("path", "") or "").strip()
            if raw_path:
                target = Path(raw_path)
                if not target.is_absolute():
                    target = Path(attempt_ctx.workspace_root or ".") / raw_path
                payload = {
                    "tool_name": tool_name,
                    "path": str(target),
                    "existed": target.exists(),
                    "content": target.read_text(encoding="utf-8") if target.exists() else "",
                }
                artifact_refs.append(
                    self._store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload=payload,
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = True
        elif action_type == "vcs_mutation":
            repo = Path(attempt_ctx.workspace_root or ".")
            prestate = self.git_worktree.snapshot(repo).to_prestate()
            if prestate is not None:
                artifact_refs.append(
                    self._store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload=prestate,
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = not bool(prestate.get("dirty"))
            else:
                supported = False
        elif action_type == "memory_write":
            strategy = "supersede_or_invalidate"
            supported = True
        return {"supported": supported, "strategy": strategy, "artifact_refs": artifact_refs}

    def _store_inline_json_artifact(
        self,
        *,
        task_id: str,
        step_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata=metadata,
        )
        return artifact.artifact_id

    def _lease_root_path(
        self,
        action_request: ActionRequest,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        target_paths = [
            str(path) for path in action_request.derived.get("target_paths", []) if path
        ]
        if target_paths:
            try:
                return str(Path(target_paths[0]).expanduser().resolve().parent)
            except OSError:
                return str(Path(target_paths[0]).expanduser())
        workspace_root = str(attempt_ctx.workspace_root or "").strip()
        return workspace_root

    def _ensure_workspace_lease(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        action_request: ActionRequest,
        approval_mode: str,
    ) -> str | None:
        lease_root = self._lease_root_path(action_request, attempt_ctx=attempt_ctx)
        if not lease_root:
            return None
        existing = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if existing is not None and existing.workspace_lease_id:
            lease = self.workspace_lease_service.validate_active(existing.workspace_lease_id)
            return lease.lease_id
        lease_mode = "mutable" if approval_mode == "mutable_workspace" else "scoped"
        lease = self.workspace_lease_service.acquire(
            task_id=attempt_ctx.task_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            workspace_id=f"{attempt_ctx.task_id}:{attempt_ctx.step_id}",
            root_path=lease_root,
            holder_principal_id=attempt_ctx.actor_principal_id,
            mode=lease_mode,
            resource_scope=list(action_request.resource_scopes),
        )
        return lease.lease_id

    def _capability_constraints(
        self,
        action_request: ActionRequest,
        *,
        workspace_lease_id: str | None,
    ) -> dict[str, Any]:
        constraints = dict(action_request.derived.get("constraints", {}))
        constraints.update(
            {
                "target_paths": list(action_request.derived.get("target_paths", [])),
                "network_hosts": list(action_request.derived.get("network_hosts", [])),
                "command_preview": action_request.derived.get("command_preview"),
            }
        )
        if workspace_lease_id:
            lease = self.store.get_workspace_lease(workspace_lease_id)
            if lease is not None:
                constraints["lease_root_path"] = lease.root_path
        return {key: value for key, value in constraints.items() if value not in (None, [], {}, "")}

    def _supersede_attempt_for_drift(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        drift_reason: str,
    ) -> ToolExecutionResult:
        current = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if current is None:
            raise KeyError(f"Unknown step attempt: {attempt_ctx.step_attempt_id}")
        now = time.time()
        reason_key = {
            "witness_drift": "witness_drift_reenter_policy",
            "approval_drift": "approval_drift_reenter_policy",
            "evidence_drift": "evidence_drift_reenter_policy",
            "contract_expiry": "contract_expiry_reenter_policy",
            "policy_version_drift": "policy_version_drift_reenter_policy",
        }.get(drift_reason, "contract_drift_reenter_policy")
        reentry_boundary = {
            "witness_drift": "policy_recompile",
            "approval_drift": "approval_revalidation",
            "evidence_drift": "admission_recompile",
            "contract_expiry": "policy_recompile",
            "policy_version_drift": "policy_recompile",
        }.get(drift_reason, "policy_recompile")
        evidence_summary = {
            "witness_drift": "Evidence invalidated because the state witness drifted before execution.",
            "approval_drift": "Evidence invalidated because the approval context drifted before execution.",
            "evidence_drift": "Evidence invalidated because the prior evidence case is no longer admissible.",
            "contract_expiry": "Contract expired before execution could proceed.",
            "policy_version_drift": "Policy version changed since contract was issued.",
        }.get(drift_reason, "Evidence invalidated because the contract loop drifted.")
        authorization_summary = {
            "witness_drift": "Authorization plan invalidated because the state witness drifted.",
            "approval_drift": "Authorization plan invalidated because approval must be revalidated.",
            "evidence_drift": "Authorization plan invalidated because the supporting evidence drifted.",
            "contract_expiry": "Contract expired before execution could proceed.",
            "policy_version_drift": "Policy version changed since contract was issued.",
        }.get(drift_reason, "Authorization plan invalidated because the contract loop drifted.")
        successor_contract_id = (
            self.store.generate_id("contract") if hasattr(self.store, "generate_id") else None
        )
        if current.execution_contract_ref and successor_contract_id:
            if drift_reason == "contract_expiry":
                self.store.update_execution_contract(
                    current.execution_contract_ref,
                    status="expired",
                )
            self.execution_contracts.supersede(
                current.execution_contract_ref,
                superseded_by_contract_id=successor_contract_id,
                attempt_ctx=attempt_ctx,
                reason=reason_key,
            )
        if current.evidence_case_ref:
            if drift_reason == "contract_expiry":
                self.evidence_cases.mark_expired(
                    current.evidence_case_ref,
                    summary=evidence_summary,
                )
            elif drift_reason == "policy_version_drift":
                self.evidence_cases.mark_stale(
                    current.evidence_case_ref,
                    summary=evidence_summary,
                )
            else:
                self.evidence_cases.invalidate(
                    current.evidence_case_ref,
                    contradictions=[drift_reason],
                    summary=evidence_summary,
                )
        if current.authorization_plan_ref:
            auth_status = {
                "contract_expiry": "expired",
                "policy_version_drift": "superseded",
            }.get(drift_reason, "invalidated")
            self.authorization_plans.invalidate(
                current.authorization_plan_ref,
                gaps=[drift_reason],
                summary=authorization_summary,
                status=auth_status,
            )
        if current.approval_id:
            self.store.append_event(
                event_type="approval.drifted",
                entity_type="approval",
                entity_id=current.approval_id,
                task_id=current.task_id,
                step_id=current.step_id,
                actor="kernel",
                payload={
                    "approval_id": current.approval_id,
                    "drift_kind": drift_reason,
                    "step_attempt_id": current.step_attempt_id,
                },
            )
        self.store.update_step_attempt(
            current.step_attempt_id,
            status="superseded",
            superseded_by_step_attempt_id=None,
            finished_at=now,
        )
        self.store.update_step(attempt_ctx.step_id, status="awaiting_approval")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        successor = self.store.create_step_attempt(
            task_id=current.task_id,
            step_id=current.step_id,
            attempt=current.attempt + 1,
            status="running",
            context={
                **dict(current.context),
                "phase": "policy_pending",
                "reentered_via": drift_reason,
                "recompile_required": True,
                "reentry_required": True,
                "reentry_boundary": reentry_boundary,
                "reentry_reason": drift_reason,
                "reentry_requested_at": now,
                "supersedes_step_attempt_id": current.step_attempt_id,
            },
            queue_priority=current.queue_priority,
            contract_version=int(current.contract_version or 0) + 1,
            reentry_boundary=reentry_boundary,
            reentry_reason=drift_reason,
        )
        self.store.update_step_attempt(
            current.step_attempt_id,
            superseded_by_step_attempt_id=successor.step_attempt_id,
        )
        self.store.append_event(
            event_type="step_attempt.superseded",
            entity_type="step_attempt",
            entity_id=current.step_attempt_id,
            task_id=current.task_id,
            step_id=current.step_id,
            actor="kernel",
            payload={
                "step_attempt_id": current.step_attempt_id,
                "superseded_by_step_attempt_id": successor.step_attempt_id,
                "reason": reason_key,
            },
        )
        successor_ctx = replace(
            attempt_ctx, step_attempt_id=successor.step_attempt_id, created_at=time.time()
        )
        return self.execute(successor_ctx, tool_name, tool_input)

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

    def _load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        if not witness_ref:
            return {}
        artifact = self.store.get_artifact(witness_ref)
        if artifact is None:
            return {}
        try:
            payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return {}
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

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
        self.store.append_event(
            event_type="dispatch.denied",
            entity_type="capability_grant",
            entity_id=capability_grant_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
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
            receipt_id = self._issue_receipt(
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
            self._record_reconciliation(
                attempt_ctx=attempt_ctx,
                receipt_id=receipt_id,
                action_type=tool.action_class or self.policy_engine.infer_action_class(tool),
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
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        effective_contract_ref = contract_ref or getattr(attempt, "execution_contract_ref", None)
        effective_authorization_plan_ref = authorization_plan_ref or getattr(
            attempt, "authorization_plan_ref", None
        )
        input_uri, input_hash = self.artifact_store.store_json(
            {"tool": tool_name, "input": tool_input}
        )
        input_artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="tool_input",
            uri=input_uri,
            content_hash=input_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool_name},
        )
        output_uri, output_hash = self.artifact_store.store_json(serialize_tool_result(raw_result))
        output_artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=output_kind,
            uri=output_uri,
            content_hash=output_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool_name},
        )
        effective_environment_ref = environment_ref
        if effective_environment_ref is None and workspace_lease_ref:
            lease = self.store.get_workspace_lease(workspace_lease_ref)
            if lease is not None and lease.environment_ref:
                effective_environment_ref = lease.environment_ref
        if effective_environment_ref is None:
            env_uri, env_hash = self.artifact_store.store_json(
                capture_execution_environment(cwd=Path(attempt_ctx.workspace_root or "."))
            )
            env_artifact = self.store.create_artifact(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                kind="environment.snapshot",
                uri=env_uri,
                content_hash=env_hash,
                producer="tool_executor",
                retention_class="audit",
                trust_tier="observed",
                metadata={"tool_name": tool_name},
            )
            effective_environment_ref = env_artifact.artifact_id
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            environment_ref=effective_environment_ref,
        )
        return self.receipt_service.issue(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            action_type=tool.action_class or self.policy_engine.infer_action_class(tool),
            receipt_class=tool.action_class or self.policy_engine.infer_action_class(tool),
            input_refs=[input_artifact.artifact_id],
            environment_ref=effective_environment_ref,
            policy_result=policy.to_dict(),
            approval_ref=approval_ref,
            output_refs=[output_artifact.artifact_id],
            result_summary=result_summary or f"{tool_name} executed successfully",
            result_code=result_code,
            decision_ref=decision_ref,
            capability_grant_ref=capability_grant_ref,
            workspace_lease_ref=workspace_lease_ref,
            policy_ref=policy_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref or policy_ref,
            contract_ref=effective_contract_ref,
            authorization_plan_ref=effective_authorization_plan_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            verifiability="baseline_verifiable" if policy.requires_receipt else "hash_linked_only",
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_artifact_refs=rollback_artifact_refs,
            observed_effect_summary=observed_effect_summary,
            reconciliation_required=reconciliation_required,
        )
