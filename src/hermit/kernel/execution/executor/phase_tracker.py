from __future__ import annotations

import logging

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.execution_helpers import (
    is_governed_action,
)
from hermit.kernel.execution.executor.execution_helpers import (
    set_attempt_phase as _set_attempt_phase,
)
from hermit.kernel.ledger.journal.store import KernelStore

logger = logging.getLogger(__name__)

# Re-export for callers that import _is_governed_action from this module.
# The canonical implementation now lives in execution_helpers.
__all__ = [
    "PhaseTracker",
    "_execution_status_from_result_code",
    "_is_governed_action",
    "is_governed_action",
    "needs_witness",
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


def needs_witness(action_class: str) -> bool:
    return action_class in _WITNESS_REQUIRED_ACTIONS


def _execution_status_from_result_code(result_code: str) -> str:
    """Map a raw result code to a normalised execution status string.

    Using plain ``==`` comparisons instead of single-element ``in {…}`` set
    lookups makes the intent explicit and avoids the false impression that
    multiple values are being matched.

    An explicit ``else`` branch is added so that unrecognised result codes
    are logged as a warning rather than silently returning ``"succeeded"``,
    which would be misleading for any future result code that does not map
    to a success state.
    """
    if result_code == "approval_required":
        return "awaiting_approval"
    if result_code == "contract_blocked":
        return "blocked"
    if result_code == "observation_submitted":
        return "observing"
    if result_code == "denied":
        return "failed"
    if result_code in {"failed", "timeout", "cancelled"}:
        return "failed"
    if result_code in {
        "reconciled_applied",
        "reconciled_not_applied",
        "reconciled_observed",
    }:
        return "reconciling"
    if result_code == "unknown_outcome":
        return "needs_attention"

    # Guard: surface unrecognised codes instead of silently treating them as
    # successes.  Callers should be updated to produce a known result code.
    logger.warning(
        "Unrecognised result_code %r — defaulting execution status to 'succeeded'. "
        "Add an explicit mapping in _execution_status_from_result_code.",
        result_code,
    )
    return "succeeded"


_is_governed_action = is_governed_action


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
