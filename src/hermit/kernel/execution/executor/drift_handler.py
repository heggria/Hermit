from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.permits.authorization_plans import AuthorizationPlanService

if TYPE_CHECKING:
    from hermit.kernel.execution.executor.executor import ToolExecutionResult

log = structlog.get_logger()

_MAX_DRIFT_REENTRIES = 3


class ExecuteFn(Protocol):
    """Callback protocol for re-entering the governed execution path."""

    def __call__(
        self,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionResult: ...


_REASON_KEYS: dict[str, str] = {
    "witness_drift": "witness_drift_reenter_policy",
    "approval_drift": "approval_drift_reenter_policy",
    "evidence_drift": "evidence_drift_reenter_policy",
    "contract_expiry": "contract_expiry_reenter_policy",
    "policy_version_drift": "policy_version_drift_reenter_policy",
}

_REENTRY_BOUNDARIES: dict[str, str] = {
    "witness_drift": "policy_recompile",
    "approval_drift": "approval_revalidation",
    "evidence_drift": "admission_recompile",
    "contract_expiry": "policy_recompile",
    "policy_version_drift": "policy_recompile",
}

_EVIDENCE_SUMMARIES: dict[str, str] = {
    "witness_drift": "Evidence invalidated because the state witness drifted before execution.",
    "approval_drift": (
        "Evidence invalidated because the approval context drifted before execution."
    ),
    "evidence_drift": (
        "Evidence invalidated because the prior evidence case is no longer admissible."
    ),
    "contract_expiry": "Contract expired before execution could proceed.",
    "policy_version_drift": "Policy version changed since contract was issued.",
}

_AUTHORIZATION_SUMMARIES: dict[str, str] = {
    "witness_drift": "Authorization plan invalidated because the state witness drifted.",
    "approval_drift": ("Authorization plan invalidated because approval must be revalidated."),
    "evidence_drift": ("Authorization plan invalidated because the supporting evidence drifted."),
    "contract_expiry": "Contract expired before execution could proceed.",
    "policy_version_drift": "Policy version changed since contract was issued.",
}

_AUTH_STATUS_OVERRIDES: dict[str, str] = {
    "contract_expiry": "expired",
    "policy_version_drift": "superseded",
}


class DriftHandler:
    """Supersede a step attempt when policy, contract, or witness drift is detected."""

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

    def supersede_attempt_for_drift(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        drift_reason: str,
        execute_fn: ExecuteFn,
    ) -> ToolExecutionResult:
        """Supersede the current step attempt and re-enter the governed execution path.

        Handles policy drift, contract expiry, witness drift, approval drift,
        evidence drift, and policy version drift by:
        1. Invalidating / expiring the current contract, evidence case, and
           authorization plan as appropriate.
        2. Creating a successor step attempt with reentry metadata.
        3. Delegating execution of the successor to *execute_fn*.

        If the same drift reason has triggered more than ``_MAX_DRIFT_REENTRIES``
        consecutive reentries, the attempt is failed instead of looping forever.
        """
        current = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if current is None:
            raise KeyError(f"Unknown step attempt: {attempt_ctx.step_attempt_id}")

        # --- loop detection: count consecutive reentries for same drift reason ---
        ctx = dict(current.context or {})
        consecutive = ctx.get("drift_reentry_count", 0)
        if ctx.get("reentered_via") == drift_reason:
            consecutive += 1
        else:
            consecutive = 1

        if consecutive > _MAX_DRIFT_REENTRIES:
            log.warning(
                "drift_reentry_limit_exceeded",
                step_attempt_id=current.step_attempt_id,
                drift_reason=drift_reason,
                consecutive=consecutive,
            )
            now = time.time()
            self.store.update_step_attempt(
                current.step_attempt_id,
                status="failed",
                finished_at=now,
                context={
                    **ctx,
                    "failure_reason": f"drift_reentry_limit_exceeded:{drift_reason}",
                    "drift_reentry_count": consecutive,
                },
                status_reason=f"drift_reentry_limit_exceeded_{drift_reason}",
            )
            self.store.update_step(attempt_ctx.step_id, status="failed", finished_at=now)
            self.store.update_task_status(
                attempt_ctx.task_id,
                "failed",
                payload={
                    "result_preview": f"drift_reentry_limit_exceeded:{drift_reason}",
                    "result_text": (
                        f"Step failed after {consecutive} consecutive {drift_reason} "
                        f"reentries (limit: {_MAX_DRIFT_REENTRIES})."
                    ),
                },
            )
            from hermit.kernel.execution.executor.executor import ToolExecutionResult

            return ToolExecutionResult(
                model_content=f"Step failed: exceeded {_MAX_DRIFT_REENTRIES} consecutive "
                f"{drift_reason} reentries.",
                result_code="failed",
                execution_status="failed",
            )

        now = time.time()
        reason_key = _REASON_KEYS.get(drift_reason, "contract_drift_reenter_policy")
        reentry_boundary = _REENTRY_BOUNDARIES.get(drift_reason, "policy_recompile")
        evidence_summary = _EVIDENCE_SUMMARIES.get(
            drift_reason,
            "Evidence invalidated because the contract loop drifted.",
        )
        authorization_summary = _AUTHORIZATION_SUMMARIES.get(
            drift_reason,
            "Authorization plan invalidated because the contract loop drifted.",
        )

        # --- contract supersession ---
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

        # --- evidence case handling ---
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

        # --- authorization plan invalidation ---
        if current.authorization_plan_ref:
            auth_status = _AUTH_STATUS_OVERRIDES.get(drift_reason, "invalidated")
            self.authorization_plans.invalidate(
                current.authorization_plan_ref,
                gaps=[drift_reason],
                summary=authorization_summary,
                status=auth_status,
            )

        # --- approval drift event ---
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

        # --- supersede current attempt and create successor (compare-and-swap) ---
        # try_supersede_step_attempt performs an atomic UPDATE ... WHERE status = 'running'.
        # If another thread already superseded this attempt, rowcount == 0 and we bail out
        # early to prevent duplicate successor creation.
        claimed = self.store.try_supersede_step_attempt(
            current.step_attempt_id,
            finished_at=now,
        )
        if not claimed:
            log.warning(
                "drift_supersession_race_lost",
                step_attempt_id=current.step_attempt_id,
                drift_reason=drift_reason,
            )
            from hermit.kernel.execution.executor.executor import ToolExecutionResult

            return ToolExecutionResult(
                model_content=(
                    f"Drift supersession skipped: attempt {current.step_attempt_id!r} "
                    f"was already superseded by a concurrent caller."
                ),
                result_code="skipped",
                execution_status="superseded",
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
                "drift_reentry_count": consecutive,
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
            attempt_ctx,
            step_attempt_id=successor.step_attempt_id,
            created_at=time.time(),
        )
        return execute_fn(successor_ctx, tool_name, tool_input)
