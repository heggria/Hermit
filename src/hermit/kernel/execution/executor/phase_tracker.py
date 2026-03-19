from __future__ import annotations

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision
from hermit.runtime.capability.registry.tools import ToolSpec

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


def _is_governed_action(tool: ToolSpec, policy: PolicyDecision) -> bool:
    if tool.readonly and policy.verdict == "allow":
        return False
    if policy.action_class in {"read_local", "network_read"} and not policy.requires_receipt:
        return False
    return policy.action_class != "ephemeral_ui_mutation"


def _needs_witness(action_class: str) -> bool:
    return action_class in _WITNESS_REQUIRED_ACTIONS


def _execution_status_from_result_code(result_code: str) -> str:
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


class PhaseTracker:
    """Tracks phase transitions for step attempts during governed execution."""

    def __init__(self, *, store: KernelStore) -> None:
        self.store = store

    def set_attempt_phase(
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
