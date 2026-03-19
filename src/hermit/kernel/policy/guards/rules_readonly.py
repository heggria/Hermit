from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason


def evaluate_readonly_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate auto-allow rules for readonly action classes.

    Returns a list of RuleOutcome if the request matches a readonly pattern,
    or None if the request doesn't match (so the caller knows to continue
    evaluation with the full rule set).
    """
    if request.action_class == "read_local":
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("readonly_tool", "Readonly tool auto-allowed.")],
                obligations=PolicyObligations(require_receipt=request.requires_receipt),
                risk_level=request.risk_hint or "low",
            )
        ]

    if request.action_class == "network_read":
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason("readonly_network", "Readonly network access is auto-allowed.")
                ],
                obligations=PolicyObligations(require_receipt=request.requires_receipt),
                risk_level=request.risk_hint or "low",
            )
        ]

    if request.action_class == "delegate_reasoning":
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason(
                        "delegate_reasoning",
                        "Internal delegated reasoning is readonly context gathering.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint or "low",
            )
        ]

    if request.action_class == "ephemeral_ui_mutation":
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason(
                        "ephemeral_ui_mutation",
                        "Ephemeral UI feedback is allowed without approval.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint or "low",
            )
        ]

    return None
