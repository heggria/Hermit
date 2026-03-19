"""Shared execution helpers for governed tool execution handlers.

These pure functions and thin wrappers were previously duplicated across
DispatchDeniedHandler, ReconciliationExecutor, ContractExecutor, RecoveryHandler,
and ToolExecutor.  Centralising them here eliminates the DRY violations and
provides a single authoritative implementation for each concern.

Public API
----------
_is_governed_action(tool, policy) -> bool
_set_attempt_phase(store, attempt_ctx, phase, *, reason=None) -> None
_contract_refs(store, attempt_ctx) -> tuple[str | None, str | None, str | None]
_load_witness_payload(store, artifact_store, witness_ref) -> dict[str, Any]
"""

from __future__ import annotations

import json
from typing import Any, cast

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision
from hermit.runtime.capability.registry.tools import ToolSpec

# ---------------------------------------------------------------------------
# _is_governed_action
# ---------------------------------------------------------------------------


def _is_governed_action(tool: ToolSpec, policy: PolicyDecision) -> bool:
    """Return True when the action must pass through the full governance path.

    Read-only tools that are explicitly allowed, cheap network reads that don't
    require a receipt, and ephemeral UI mutations are exempt from the governed
    execution lifecycle.

    This is the single canonical implementation; the copy that previously lived
    in ``phase_tracker`` is re-exported from there for backwards compatibility.
    """
    if tool.readonly and policy.verdict == "allow":
        return False
    if policy.action_class in {"read_local", "network_read"} and not policy.requires_receipt:
        return False
    return policy.action_class != "ephemeral_ui_mutation"


# ---------------------------------------------------------------------------
# _set_attempt_phase
# ---------------------------------------------------------------------------


def _set_attempt_phase(
    store: KernelStore,
    attempt_ctx: TaskExecutionContext,
    phase: str,
    *,
    reason: str | None = None,
) -> None:
    """Transition a step attempt to *phase* and emit a phase-changed event.

    The function is idempotent: if the attempt is already in the requested
    phase it returns without writing or emitting anything.  If the attempt
    record cannot be found it also returns silently so callers do not need to
    guard against missing attempts.

    Previously this exact body existed in:
    - ``ToolExecutor._set_attempt_phase`` (as a one-liner delegate to PhaseTracker)
    - ``PhaseTracker.set_attempt_phase`` (canonical)
    - ``ContractExecutor._set_attempt_phase`` (full inline copy)
    - ``ReconciliationExecutor._set_attempt_phase`` (full inline copy)
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


# ---------------------------------------------------------------------------
# _contract_refs
# ---------------------------------------------------------------------------


def _contract_refs(
    store: KernelStore,
    attempt_ctx: TaskExecutionContext,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(execution_contract_ref, evidence_case_ref, authorization_plan_ref)``.

    All three values are ``None`` when the step attempt record does not exist.

    Previously duplicated verbatim in:
    - ``ToolExecutor._contract_refs`` (one-liner delegate to ContractExecutor)
    - ``ContractExecutor.contract_refs`` (canonical)
    - ``DispatchDeniedHandler._contract_refs`` (full inline copy)
    - ``ReconciliationExecutor._contract_refs`` (full inline copy)
    """
    attempt = store.get_step_attempt(attempt_ctx.step_attempt_id)
    if attempt is None:
        return None, None, None
    return (
        attempt.execution_contract_ref,
        attempt.evidence_case_ref,
        attempt.authorization_plan_ref,
    )


# ---------------------------------------------------------------------------
# _load_witness_payload
# ---------------------------------------------------------------------------


def _load_witness_payload(
    store: KernelStore,
    artifact_store: ArtifactStore,
    witness_ref: str | None,
) -> dict[str, Any]:
    """Load a previously-captured witness payload from the artifact store.

    Returns an empty dict when *witness_ref* is falsy, the artifact is missing,
    or the stored bytes cannot be decoded as a JSON object.

    Previously duplicated verbatim in:
    - ``ToolExecutor._load_witness_payload`` (one-liner delegate to WitnessHandler)
    - ``WitnessHandler.load_witness_payload`` (canonical)
    - ``RecoveryHandler._load_witness_payload`` (full inline copy)
    - ``ReconciliationExecutor._load_witness_payload`` (full inline copy)
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
