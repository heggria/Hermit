from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

_MONOTONICITY_SKIP_COORDINATION = frozenset({"readonly", "additive"})
_MONOTONICITY_REQUIRE_COORDINATION = frozenset({"compensatable_mutation", "irreversible_mutation"})
_VALID_MONOTONICITY_CLASSES = _MONOTONICITY_SKIP_COORDINATION | _MONOTONICITY_REQUIRE_COORDINATION
_DEFAULT_COMMUNICATION_BUDGET_RATIO = 0.3


def evaluate_monotonicity_guard(request: ActionRequest) -> RuleOutcome | None:
    """Skip coordination overhead for readonly/additive steps.

    Returns an allow-without-approval outcome for monotonic steps,
    a warning outcome for unrecognised monotonicity classes,
    or None to let the normal policy chain continue.
    """
    monotonicity_class = request.context.get("monotonicity_class", "")
    if not monotonicity_class:
        return None
    if monotonicity_class in _MONOTONICITY_SKIP_COORDINATION:
        return RuleOutcome(
            verdict="allow",
            reasons=[
                PolicyReason(
                    "monotonicity_skip",
                    f"Step classified as '{monotonicity_class}'"
                    " \u2014 coordination overhead skipped.",
                    "info",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=monotonicity_class != "readonly",
                require_approval=False,
            ),
            risk_level="low",
        )
    # Warn on unrecognised monotonicity classes rather than silently falling
    # through to the normal policy chain.  A typo (e.g. "irreversibe_mutation")
    # would otherwise be indistinguishable from an absent class, potentially
    # skipping required coordination without any observable signal.
    if monotonicity_class not in _VALID_MONOTONICITY_CLASSES:
        return RuleOutcome(
            verdict="allow",
            reasons=[
                PolicyReason(
                    "monotonicity_unknown",
                    f"Unrecognised monotonicity_class '{monotonicity_class}';"
                    f" expected one of {sorted(_VALID_MONOTONICITY_CLASSES)}."
                    " Falling back to full coordination.",
                    "warning",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_approval=True,
            ),
            risk_level="medium",
        )
    return None


def evaluate_communication_budget_guard(
    request: ActionRequest,
    *,
    budget_tokens_used: int = 0,
    budget_tokens_limit: int | None = None,
    communication_tokens: int = 0,
) -> RuleOutcome | None:
    """Evaluate whether the task's communication budget is within limits.

    Returns a deny outcome if budget is exceeded, a warning outcome if
    communication ratio exceeds the configured threshold, or None if
    everything is within bounds.
    """
    if budget_tokens_limit is None or budget_tokens_limit <= 0:
        return None
    if budget_tokens_used >= budget_tokens_limit:
        return RuleOutcome(
            verdict="deny",
            reasons=[
                PolicyReason(
                    "budget_exceeded",
                    f"Task budget exhausted: {budget_tokens_used}/{budget_tokens_limit}"
                    " tokens used.",
                    "error",
                )
            ],
            obligations=PolicyObligations(require_receipt=False),
            risk_level="high",
        )
    ratio_threshold = float(
        request.context.get("communication_budget_ratio", _DEFAULT_COMMUNICATION_BUDGET_RATIO)
    )
    # budget_tokens_limit > 0 is guaranteed by the early-return guard above;
    # the redundant re-check has been removed.
    if communication_tokens > 0:
        comm_ratio = communication_tokens / budget_tokens_limit
        if comm_ratio > ratio_threshold:
            return RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason(
                        "communication_budget_warning",
                        f"Communication cost ({communication_tokens} tokens) is "
                        f"{comm_ratio:.1%} of total budget, exceeding "
                        f"{ratio_threshold:.0%} threshold.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level="medium",
            )
    return None
