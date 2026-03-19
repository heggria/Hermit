from __future__ import annotations

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.execution_helpers import (
    _is_governed_action,
    _set_attempt_phase,
)
from hermit.kernel.ledger.journal.store import KernelStore

# Re-export for callers that import _is_governed_action from this module.
# The canonical implementation now lives in execution_helpers.
__all__ = [
    "PhaseTracker",
    "_execution_status_from_result_code",
    "_is_governed_action",
    "_needs_witness",
]

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
        _set_attempt_phase(self.store, attempt_ctx, phase, reason=reason)
