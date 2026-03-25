from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionContract:
    action_class: str
    default_risk_band: str
    decision_required: bool
    witness_required: bool
    receipt_required: bool
    reconcile_strategy: str
    rollback_strategy: str


_DEFAULT_CONTRACT = ActionContract(
    action_class="unknown",
    default_risk_band="high",
    decision_required=True,
    witness_required=True,
    receipt_required=True,
    reconcile_strategy="observe_then_reconcile",
    rollback_strategy="manual_only",
)

ACTION_CONTRACTS: dict[str, ActionContract] = {
    "delegate_reasoning": ActionContract(
        action_class="delegate_reasoning",
        default_risk_band="low",
        decision_required=False,
        witness_required=False,
        receipt_required=False,
        reconcile_strategy="none",
        rollback_strategy="not_needed",
    ),
    "delegate_execution": ActionContract(
        action_class="delegate_execution",
        default_risk_band="medium",
        decision_required=True,
        witness_required=False,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="manual_or_followup",
    ),
    "read_local": ActionContract(
        action_class="read_local",
        default_risk_band="low",
        decision_required=False,
        witness_required=False,
        receipt_required=False,
        reconcile_strategy="none",
        rollback_strategy="not_needed",
    ),
    "network_read": ActionContract(
        action_class="network_read",
        default_risk_band="low",
        decision_required=False,
        witness_required=False,
        receipt_required=False,
        reconcile_strategy="none",
        rollback_strategy="not_needed",
    ),
    "write_local": ActionContract(
        action_class="write_local",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="observe_then_reconcile",
        rollback_strategy="manual_only",
    ),
    "network_write": ActionContract(
        action_class="network_write",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="observe_then_reconcile",
        rollback_strategy="manual_only",
    ),
    "process_exec": ActionContract(
        action_class="process_exec",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="observe_then_reconcile",
        rollback_strategy="manual_only",
    ),
    "ui_read": ActionContract(
        action_class="ui_read",
        default_risk_band="low",
        decision_required=False,
        witness_required=False,
        receipt_required=False,
        reconcile_strategy="none",
        rollback_strategy="not_needed",
    ),
    "ui_mutation": ActionContract(
        action_class="ui_mutation",
        default_risk_band="medium",
        decision_required=True,
        witness_required=False,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="manual_or_followup",
    ),
    "scheduler_mutation": ActionContract(
        action_class="scheduler_mutation",
        default_risk_band="medium",
        decision_required=True,
        witness_required=False,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="manual_or_followup",
    ),
    "attachment_ingest": ActionContract(
        action_class="attachment_ingest",
        default_risk_band="high",
        decision_required=True,
        witness_required=False,
        receipt_required=True,
        reconcile_strategy="artifact_observation",
        rollback_strategy="manual_only",
    ),
    "ephemeral_ui_mutation": ActionContract(
        action_class="ephemeral_ui_mutation",
        default_risk_band="low",
        decision_required=False,
        witness_required=False,
        receipt_required=False,
        reconcile_strategy="none",
        rollback_strategy="not_needed",
    ),
    "memory_write": ActionContract(
        action_class="memory_write",
        default_risk_band="medium",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="supersede_or_invalidate",
    ),
    "rollback": ActionContract(
        action_class="rollback",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="manual_or_followup",
    ),
    "approval_resolution": ActionContract(
        action_class="approval_resolution",
        default_risk_band="medium",
        decision_required=True,
        witness_required=False,
        receipt_required=True,
        reconcile_strategy="store_observation",
        rollback_strategy="not_needed",
    ),
}


def contract_for(action_class: str) -> ActionContract:
    """Return the contract for *action_class*, falling back to the high-risk
    default contract when the class is not explicitly registered.

    Callers that need to distinguish a genuine registration miss from an
    intentional fall-through should use :func:`contract_for_strict` instead.
    """
    return ACTION_CONTRACTS.get(action_class, _DEFAULT_CONTRACT)


def contract_for_strict(action_class: str) -> ActionContract:
    """Return the contract for *action_class*, raising ``KeyError`` if it is
    not registered.

    Use this during configuration-time validation or in tests where an
    unrecognised action class should be treated as a programming error rather
    than silently defaulting to the high-risk fallback.

    Raises:
        KeyError: If *action_class* has no entry in ``ACTION_CONTRACTS``.
    """
    try:
        return ACTION_CONTRACTS[action_class]
    except KeyError:
        registered = ", ".join(sorted(ACTION_CONTRACTS))
        raise KeyError(
            f"No ActionContract registered for {action_class!r}. "
            f"Registered action classes: {registered}"
        ) from None


def known_action_classes() -> set[str]:
    return set(ACTION_CONTRACTS)
