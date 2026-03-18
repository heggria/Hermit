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
        reconcile_strategy="filesystem_observation",
        rollback_strategy="file_restore",
    ),
    "patch_file": ActionContract(
        action_class="patch_file",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="filesystem_observation",
        rollback_strategy="file_restore",
    ),
    "execute_command": ActionContract(
        action_class="execute_command",
        default_risk_band="critical",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="command_observation",
        rollback_strategy="manual_or_followup",
    ),
    "vcs_mutation": ActionContract(
        action_class="vcs_mutation",
        default_risk_band="critical",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="git_observation",
        rollback_strategy="git_revert_or_reset",
    ),
    "network_write": ActionContract(
        action_class="network_write",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="remote_observation",
        rollback_strategy="compensating_action",
    ),
    "credentialed_api_call": ActionContract(
        action_class="credentialed_api_call",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="remote_observation",
        rollback_strategy="compensating_action",
    ),
    "publication": ActionContract(
        action_class="publication",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="remote_observation",
        rollback_strategy="compensating_action",
    ),
    "external_mutation": ActionContract(
        action_class="external_mutation",
        default_risk_band="high",
        decision_required=True,
        witness_required=True,
        receipt_required=True,
        reconcile_strategy="remote_observation",
        rollback_strategy="compensating_action",
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
    return ACTION_CONTRACTS.get(action_class, _DEFAULT_CONTRACT)


def known_action_classes() -> set[str]:
    return set(ACTION_CONTRACTS)
