"""
Shared low-level helpers for attempt-lifecycle operations.

These are pure functions (no class state) used by multiple executor sub-
components that must stay self-contained (ReconciliationExecutor,
ContractExecutor, DispatchDeniedHandler, RecoveryHandler).  Each function
receives only the specific services it needs so callers do not have to take
on additional constructor dependencies.

Higher-level service classes (PhaseTracker, ContractExecutor,
WitnessHandler) wrap the same logic; prefer those when a class already
holds a reference to one of them.
"""

from __future__ import annotations

import json
from typing import Any, cast

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore


def set_attempt_phase(
    store: KernelStore,
    attempt_ctx: TaskExecutionContext,
    phase: str,
    *,
    reason: str | None = None,
) -> None:
    """Idempotently transition a step-attempt's phase and emit an event.

    No-ops silently when *attempt_ctx.step_attempt_id* is not found or the
    attempt is already in *phase*.
    """
    attempt = store.get_step_attempt(attempt_ctx.step_attempt_id)
    if attempt is None:
        return
    context = dict(attempt.context or {})
    previous = str(context.get("phase", "") or "")
    if previous == phase:
        return
    context["phase"] = phase
    store.update_step_attempt(attempt_ctx.step_attempt_id, context=context)
    store.append_event(
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


def contract_refs(
    store: KernelStore,
    attempt_ctx: TaskExecutionContext,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(execution_contract_ref, evidence_case_ref, authorization_plan_ref)``
    for the given step-attempt, or ``(None, None, None)`` if not found.
    """
    attempt = store.get_step_attempt(attempt_ctx.step_attempt_id)
    if attempt is None:
        return None, None, None
    return (
        attempt.execution_contract_ref,
        attempt.evidence_case_ref,
        attempt.authorization_plan_ref,
    )


def load_witness_payload(
    store: KernelStore,
    artifact_store: ArtifactStore,
    witness_ref: str | None,
) -> dict[str, Any]:
    """Load a previously-captured witness payload from the artifact store.

    Returns an empty dict when *witness_ref* is ``None``, the artifact is
    missing, or the stored content is not a valid JSON object.
    """
    if not witness_ref:
        return {}
    artifact = store.get_artifact(witness_ref)
    if artifact is None:
        return {}
    try:
        payload: Any = json.loads(artifact_store.read_text(artifact.uri))
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
