from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from hermit.kernel.authority.grants import CapabilityGrantError
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.execution_helpers import contract_refs
from hermit.kernel.execution.executor.receipt_handler import ReceiptHandler
from hermit.kernel.execution.executor.reconciliation_executor import ReconciliationExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision, PolicyEngine
from hermit.runtime.capability.registry.tools import ToolSpec

if TYPE_CHECKING:
    from hermit.kernel.execution.executor.executor import ToolExecutionResult


class DispatchDeniedHandler:
    """Handles the case when a tool execution dispatch is denied by policy."""

    def __init__(
        self,
        *,
        store: KernelStore,
        policy_engine: PolicyEngine,
        receipt_handler: ReceiptHandler,
        reconciliation_executor: ReconciliationExecutor,
    ) -> None:
        self.store = store
        self.policy_engine = policy_engine
        self.receipt_handler = receipt_handler
        self.reconciliation_executor = reconciliation_executor

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
    ) -> ToolExecutionResult:
        from hermit.kernel.execution.executor.executor import ToolExecutionResult

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
            status_reason=str(error),
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
            receipt_id = self.receipt_handler.issue_receipt(
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
                contract_ref=contract_refs(self.store, attempt_ctx)[0],
                authorization_plan_ref=contract_refs(self.store, attempt_ctx)[2],
                observed_effect_summary=str(error),
                reconciliation_required=True,
            )
            self.reconciliation_executor.record_reconciliation(
                attempt_ctx=attempt_ctx,
                receipt_id=receipt_id,
                action_type=tool.action_class or self.policy_engine.infer_action_class(tool),
                tool_input=tool_input,
                observables={},
                witness_ref=witness_ref,
                result_code_hint="dispatch_denied",
                authorized_effect_summary=str(error),
            )

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
