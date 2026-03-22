from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

POLICY_RULES_VERSION = "strict-task-first-v2"

# ---------------------------------------------------------------------------
# Policy strictness ordering
# Higher ordinal = more restrictive. Child tasks must not exceed parent's
# ordinal (i.e. must be equally or more restrictive, never more permissive).
# ---------------------------------------------------------------------------
POLICY_STRICTNESS: dict[str, int] = {
    "readonly": 3,
    "supervised": 2,
    "default": 1,
    "autonomous": 0,
}

# Verdict restrictiveness: higher = more restrictive.
_VERDICT_PRIORITY: dict[str, int] = {
    "allow": 0,
    "allow_with_receipt": 1,
    "preview_required": 2,
    "approval_required": 3,
    "deny": 4,
}


@dataclass
class RuleOutcome:
    verdict: str
    reasons: list[PolicyReason] = field(default_factory=list[PolicyReason])
    obligations: PolicyObligations = field(default_factory=PolicyObligations)
    normalized_constraints: dict[str, Any] = field(default_factory=dict[str, Any])
    approval_packet: dict[str, Any] | None = None
    risk_level: str | None = None
    action_class_override: str | None = None


def evaluate_rules(request: ActionRequest) -> list[RuleOutcome]:
    """Evaluate policy rules for an action request via dispatch chain."""
    profile = str(request.context.get("policy_profile", "default"))

    # ------------------------------------------------------------------
    # Delegation scope enforcement: if a delegation_scope is attached to
    # this request's context (injected by TaskDelegationService), deny
    # any action whose action_class is not in allowed_action_classes.
    # An empty allowed_action_classes list means "no restriction".
    # ------------------------------------------------------------------
    delegation_scope = request.context.get("delegation_scope")
    if delegation_scope is not None:
        allowed = delegation_scope.get("allowed_action_classes", [])
        if allowed and request.action_class not in allowed:
            return [
                RuleOutcome(
                    verdict="deny",
                    reasons=[
                        PolicyReason(
                            "delegation_scope_violation",
                            f"Action class '{request.action_class}' is not permitted by "
                            f"delegation scope. Allowed: {allowed}.",
                            "error",
                        )
                    ],
                    obligations=PolicyObligations(require_receipt=False),
                    risk_level=request.risk_hint,
                )
            ]

    if profile == "readonly" and request.action_class != "read_local":
        return [
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason(
                        "readonly_profile", "Readonly policy profile forbids side effects.", "error"
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint,
            )
        ]

    if profile == "autonomous":
        return _evaluate_autonomous(request)

    from hermit.kernel.policy.guards.rules_attachment import evaluate_attachment_rules
    from hermit.kernel.policy.guards.rules_filesystem import evaluate_filesystem_rules
    from hermit.kernel.policy.guards.rules_governance import evaluate_governance_rules
    from hermit.kernel.policy.guards.rules_network import evaluate_network_rules
    from hermit.kernel.policy.guards.rules_planning import evaluate_planning_rules
    from hermit.kernel.policy.guards.rules_readonly import evaluate_readonly_rules
    from hermit.kernel.policy.guards.rules_shell import evaluate_shell_rules

    evaluators = [
        evaluate_planning_rules,
        evaluate_readonly_rules,
        evaluate_filesystem_rules,
        evaluate_shell_rules,
        evaluate_network_rules,
        evaluate_attachment_rules,
        evaluate_governance_rules,
    ]

    # Collect results from ALL evaluators so a later deny is never skipped.
    all_outcomes: list[RuleOutcome] = []
    for evaluator in evaluators:
        result = evaluator(request)
        if result is not None:
            result = _apply_policy_suggestion(request, result)
            result = _apply_task_pattern(request, result)
            all_outcomes.extend(result)

    if all_outcomes:
        # Return the single most restrictive outcome.
        all_outcomes.sort(
            key=lambda o: _VERDICT_PRIORITY.get(o.verdict, 2),
            reverse=True,
        )
        return [all_outcomes[0]]

    # Unclassified mutable action: default to approval
    return [
        RuleOutcome(
            verdict="approval_required",
            reasons=[
                PolicyReason(
                    "unknown_mutation",
                    "Unclassified mutable action defaulted to approval.",
                    "warning",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_approval=True,
                approval_risk_level=request.risk_hint or "high",
            ),
            approval_packet={
                "title": f"Approve unknown action via {request.tool_name}",
                "summary": "The action is writable but not classified.",
                "risk_level": request.risk_hint or "high",
            },
            risk_level=request.risk_hint or "high",
        )
    ]


# ---------------------------------------------------------------------------
# Post-evaluation adjustment helpers (delegated to rules_adjustment module)
# ---------------------------------------------------------------------------


def _apply_policy_suggestion(
    request: ActionRequest, outcomes: list[RuleOutcome]
) -> list[RuleOutcome]:
    from hermit.kernel.policy.guards.rules_adjustment import apply_policy_suggestion

    return apply_policy_suggestion(request, outcomes)


def _apply_task_pattern(request: ActionRequest, outcomes: list[RuleOutcome]) -> list[RuleOutcome]:
    from hermit.kernel.policy.guards.rules_adjustment import apply_task_pattern

    return apply_task_pattern(request, outcomes)


def _evaluate_autonomous(request: ActionRequest) -> list[RuleOutcome]:
    from hermit.kernel.policy.guards.rules_adjustment import evaluate_autonomous

    return evaluate_autonomous(request)
