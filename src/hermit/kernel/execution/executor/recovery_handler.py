from __future__ import annotations

import json
import time
from typing import Any, cast

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import ActionRequest, PolicyDecision, PolicyEngine
from hermit.kernel.policy.approvals.decisions import DecisionService
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


class RecoveryHandler:
    """Handles uncertain and failed execution outcomes for governed tool execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        reconciliations: ReconciliationService,
        receipt_service: ReceiptService,
        decision_service: DecisionService,
        capability_service: CapabilityGrantService,
        policy_engine: PolicyEngine,
        registry: ToolRegistry,
        _witness: WitnessCapture,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.reconciliations = reconciliations
        self.receipt_service = receipt_service
        self.decision_service = decision_service
        self.capability_service = capability_service
        self.policy_engine = policy_engine
        self.registry = registry
        self._witness = _witness

    def handle_uncertain_outcome(
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
        issue_receipt: Any,
        contract_refs_fn: Any,
        load_contract_bundle_fn: Any,
        record_reconciliation_fn: Any,
        reconciliation_execution_status_fn: Any,
        authorized_effect_summary_fn: Any,
    ) -> dict[str, Any]:
        """Handle an uncertain execution outcome after a tool handler raises.

        Returns a dict with all the fields needed to construct a ToolExecutionResult.
        The caller is responsible for building the actual result object.
        """
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        outcome = self.reconciliations._reconcile_service.reconcile(
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
        receipt_id = issue_receipt(
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
            contract_ref=contract_refs_fn(attempt_ctx)[0],
            authorization_plan_ref=contract_refs_fn(attempt_ctx)[2],
            observed_effect_summary=outcome.summary,
            reconciliation_required=True,
        )
        reconciliation, _ = record_reconciliation_fn(
            attempt_ctx=attempt_ctx,
            receipt_id=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            observables=dict(action_request.derived),
            witness_ref=witness_ref,
            result_code_hint="unknown_outcome",
            authorized_effect_summary=authorized_effect_summary_fn(
                action_request=action_request,
                contract=load_contract_bundle_fn(attempt_ctx)[0],
            ),
        )
        return {
            "model_content": f"[Execution Requires Attention] {summary}",
            "raw_result": {"error": str(exc)},
            "denied": True,
            "policy_decision": policy,
            "receipt_id": receipt_id,
            "decision_id": decision_id,
            "capability_grant_id": capability_grant_id,
            "workspace_lease_id": workspace_lease_id,
            "policy_ref": policy_ref,
            "witness_ref": witness_ref,
            "result_code": result_code,
            "execution_status": reconciliation_execution_status_fn(reconciliation),
            "state_applied": True,
        }

    def handle_dispatch_denied(
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
        issue_receipt: Any,
        contract_refs_fn: Any,
        record_reconciliation_fn: Any,
    ) -> dict[str, Any]:
        """Handle a dispatch-denied outcome when capability enforcement fails.

        Returns a dict with all the fields needed to construct a ToolExecutionResult.
        The caller is responsible for building the actual result object.
        """
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
            receipt_id = issue_receipt(
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
                contract_ref=contract_refs_fn(attempt_ctx)[0],
                authorization_plan_ref=contract_refs_fn(attempt_ctx)[2],
                observed_effect_summary=str(error),
                reconciliation_required=True,
            )
            record_reconciliation_fn(
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

        return {
            "model_content": f"[Capability Denied] {error}",
            "raw_result": {"error": str(error), "error_code": error.code},
            "denied": True,
            "policy_decision": policy,
            "receipt_id": receipt_id,
            "decision_id": decision_id,
            "capability_grant_id": capability_grant_id,
            "workspace_lease_id": workspace_lease_id,
            "policy_ref": policy_ref,
            "witness_ref": witness_ref,
            "result_code": "dispatch_denied",
            "execution_status": "failed",
            "state_applied": True,
        }

    def _load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        """Load a previously-captured witness payload from the artifact store."""
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
