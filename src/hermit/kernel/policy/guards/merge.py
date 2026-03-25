from __future__ import annotations

import structlog

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations, PolicyReason

_log = structlog.get_logger()

_PRIORITY = {
    "deny": 5,
    "approval_required": 4,
    "preview_required": 3,
    "allow_with_receipt": 2,
    "allow": 1,
}


def merge_outcomes(
    outcomes: list[RuleOutcome], *, action_class: str, default_risk: str
) -> PolicyDecision:
    if not outcomes:
        _log.warning(
            "guard.merge.empty_outcomes",
            action_class=action_class,
            default_risk=default_risk,
            msg="No rule outcomes to merge; falling back to default allow decision.",
        )
        return PolicyDecision(
            verdict="allow",
            action_class=action_class,
            reasons=[],
            obligations=PolicyObligations(),
            normalized_constraints={},
            approval_packet=None,
            risk_level=default_risk,
        )

    chosen = sorted(outcomes, key=lambda item: _PRIORITY.get(item.verdict, 0), reverse=True)[0]
    obligations = PolicyObligations()
    reasons: list[PolicyReason] = []
    normalized_constraints: dict[str, object] = {}
    approval_packet = chosen.approval_packet
    risk_level = chosen.risk_level or default_risk
    for outcome in outcomes:
        reasons.extend(outcome.reasons)
        obligations.require_receipt = (
            obligations.require_receipt or outcome.obligations.require_receipt
        )
        obligations.require_preview = (
            obligations.require_preview or outcome.obligations.require_preview
        )
        obligations.require_approval = (
            obligations.require_approval or outcome.obligations.require_approval
        )
        obligations.require_evidence = (
            obligations.require_evidence or outcome.obligations.require_evidence
        )
        obligations.approval_risk_level = (
            obligations.approval_risk_level or outcome.obligations.approval_risk_level
        )
        normalized_constraints.update(outcome.normalized_constraints)
        if approval_packet is None and outcome.approval_packet is not None:
            approval_packet = outcome.approval_packet
    effective_action_class = chosen.action_class_override or action_class
    _log.debug(
        "guard.merge",
        verdict=chosen.verdict,
        action_class=effective_action_class,
        risk_level=risk_level,
        outcome_count=len(outcomes),
    )
    return PolicyDecision(
        verdict=chosen.verdict,
        action_class=effective_action_class,
        reasons=reasons,
        obligations=obligations,
        normalized_constraints=normalized_constraints,
        approval_packet=approval_packet,
        risk_level=risk_level,
    )
