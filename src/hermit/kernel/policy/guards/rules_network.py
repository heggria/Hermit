from __future__ import annotations

import structlog

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

_log = structlog.get_logger()

_NETWORK_MUTATION_CLASSES = frozenset(
    {
        "network_write",
        "credentialed_api_call",
        "publication",
        "vcs_mutation",
        "external_mutation",
    }
)


def evaluate_network_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate network/external mutation rules.

    Returns a list of RuleOutcome if the request is a network mutation,
    or None if the action class is not matched.
    """
    if request.action_class not in _NETWORK_MUTATION_CLASSES:
        return None

    _log.info(
        "guard.network.approval_required",
        rule="external_mutation",
        tool=request.tool_name,
        action_class=request.action_class,
        risk_level=request.risk_hint or "high",
    )
    return [
        RuleOutcome(
            verdict="approval_required",
            reasons=[
                PolicyReason("external_mutation", "External mutation requires approval.", "warning")
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_preview=request.supports_preview,
                require_approval=True,
                approval_risk_level=request.risk_hint or "high",
            ),
            approval_packet={
                "title": f"Approve external mutation via {request.tool_name}",
                "summary": "This action mutates external state.",
                "risk_level": request.risk_hint or "high",
            },
            risk_level=request.risk_hint or "high",
        )
    ]
