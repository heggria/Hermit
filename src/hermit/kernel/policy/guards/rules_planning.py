from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

_PLANNING_GATED_ACTION_CLASSES = frozenset(
    {
        "write_local",
        "patch_file",
        "execute_command",
        "network_write",
        "credentialed_api_call",
        "publication",
        "vcs_mutation",
        "external_mutation",
    }
)


def evaluate_planning_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Check whether a planning gate applies to the request.

    Returns a list containing a single ``approval_required`` outcome when
    ``planning_required`` is set in the request context, no plan has been
    selected, and the action class is side-effecting.

    Returns ``None`` when the planning gate does not apply, allowing the
    caller to fall through to subsequent rule evaluation.
    """
    ctx = request.context

    planning_required = bool(ctx.get("planning_required", False))
    if not planning_required:
        return None

    selected_plan_ref = str(ctx.get("selected_plan_ref", "") or "").strip()
    if selected_plan_ref:
        return None

    if request.action_class not in _PLANNING_GATED_ACTION_CLASSES:
        return None

    # Compute the effective risk level once to avoid repeating the same
    # ``request.risk_hint or "high"`` expression across multiple fields,
    # which would create an inconsistency risk if only one site is updated.
    risk_level = request.risk_hint or "high"

    return [
        RuleOutcome(
            verdict="approval_required",
            reasons=[
                PolicyReason(
                    "plan_required",
                    "Selected execution plan is required before high-risk execution.",
                    "warning",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_preview=False,
                require_approval=True,
                approval_risk_level=risk_level,
            ),
            approval_packet={
                "title": "Select an execution plan first",
                "summary": "This action requires a confirmed plan before it can run.",
                "risk_level": risk_level,
            },
            risk_level=risk_level,
        )
    ]
