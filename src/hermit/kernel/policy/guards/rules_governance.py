from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

_GOVERNANCE_ACTION_CLASSES = frozenset(
    {
        "delegate_execution",
        "approval_resolution",
        "scheduler_mutation",
        "rollback",
        "memory_write",
        "patrol_execution",
    }
)


def evaluate_governance_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate governance rules for actions requiring decisions/receipts.

    Returns a list of RuleOutcome if the request matches a governance pattern,
    or None if the action class is not a governance action.
    """
    if request.action_class not in _GOVERNANCE_ACTION_CLASSES:
        return None

    if request.action_class == "delegate_execution":
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "delegate_execution",
                        "Governed subagent delegation requires a decision and receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
            )
        ]

    if request.action_class == "approval_resolution":
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "approval_resolution",
                        "Approval resolution is a governed kernel action and must emit a receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
            )
        ]

    if request.action_class == "scheduler_mutation":
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "scheduler_mutation",
                        "Scheduler mutations are allowed with a durable receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
            )
        ]

    if request.action_class == "rollback":
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "rollback",
                        "Rollback execution is a governed kernel action and must emit a receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "high",
            )
        ]

    if request.action_class == "patrol_execution":
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "patrol_execution",
                        "Patrol execution is a governed kernel action and must emit a receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
            )
        ]

    if request.action_class == "memory_write":
        if request.actor.get("kind") == "kernel" and request.context.get("evidence_refs"):
            return [
                RuleOutcome(
                    verdict="allow_with_receipt",
                    reasons=[
                        PolicyReason(
                            "memory_write_evidence_bound",
                            "Evidence-bound kernel memory write allowed with receipt.",
                        )
                    ],
                    obligations=PolicyObligations(
                        require_receipt=True,
                        require_evidence=True,
                    ),
                    risk_level=request.risk_hint or "medium",
                )
            ]
        return [
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "memory_write",
                        "Durable memory writes require evidence and approval.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_approval=True,
                    require_evidence=True,
                    approval_risk_level=request.risk_hint or "high",
                ),
                approval_packet={
                    "title": f"Approve memory write via {request.tool_name}",
                    "summary": "This action writes durable memory and requires evidence.",
                    "risk_level": request.risk_hint or "high",
                },
                risk_level=request.risk_hint or "high",
            )
        ]

    return None
