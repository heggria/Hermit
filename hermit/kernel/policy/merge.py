from __future__ import annotations

from hermit.kernel.policy.models import PolicyDecision, PolicyObligations, PolicyReason
from hermit.kernel.policy.rules import RuleOutcome

_PRIORITY = {
    "deny": 5,
    "approval_required": 4,
    "preview_required": 3,
    "allow_with_receipt": 2,
    "allow": 1,
}


def merge_outcomes(outcomes: list[RuleOutcome], *, action_class: str, default_risk: str) -> PolicyDecision:
    chosen = sorted(outcomes, key=lambda item: _PRIORITY.get(item.verdict, 0), reverse=True)[0]
    obligations = PolicyObligations()
    reasons: list[PolicyReason] = []
    normalized_constraints: dict[str, object] = {}
    approval_packet = chosen.approval_packet
    risk_level = chosen.risk_level or default_risk
    for outcome in outcomes:
        reasons.extend(outcome.reasons)
        obligations.require_receipt = obligations.require_receipt or outcome.obligations.require_receipt
        obligations.require_preview = obligations.require_preview or outcome.obligations.require_preview
        obligations.require_approval = obligations.require_approval or outcome.obligations.require_approval
        obligations.require_evidence = obligations.require_evidence or outcome.obligations.require_evidence
        obligations.approval_risk_level = obligations.approval_risk_level or outcome.obligations.approval_risk_level
        normalized_constraints.update(outcome.normalized_constraints)
        if approval_packet is None and outcome.approval_packet is not None:
            approval_packet = outcome.approval_packet
    return PolicyDecision(
        verdict=chosen.verdict,
        action_class=action_class,
        reasons=reasons,
        obligations=obligations,
        normalized_constraints=normalized_constraints,
        approval_packet=approval_packet,
        risk_level=risk_level,
    )
