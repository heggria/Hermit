from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

# Action classes that are unconditionally auto-allowed as readonly.
# Each entry maps action_class -> (reason_code, description, use_request_receipt).
# When use_request_receipt is False the outcome never requests a receipt.
_READONLY_CLASSES: dict[str, tuple[str, str, bool]] = {
    "read_local": (
        "readonly_tool",
        "Readonly tool auto-allowed.",
        True,
    ),
    "network_read": (
        "readonly_network",
        "Readonly network access is auto-allowed.",
        True,
    ),
    "delegate_reasoning": (
        "delegate_reasoning",
        "Internal delegated reasoning is readonly context gathering.",
        False,
    ),
    "ephemeral_ui_mutation": (
        "ephemeral_ui_mutation",
        "Ephemeral UI feedback is allowed without approval.",
        False,
    ),
}


def _allow_outcome(
    request: ActionRequest,
    reason_code: str,
    description: str,
    use_request_receipt: bool,
) -> RuleOutcome:
    """Build a single auto-allow RuleOutcome for a readonly action class."""
    require_receipt = request.requires_receipt if use_request_receipt else False
    return RuleOutcome(
        verdict="allow",
        reasons=[PolicyReason(reason_code, description)],
        obligations=PolicyObligations(require_receipt=require_receipt),
        risk_level=request.risk_hint or "low",
    )


def evaluate_readonly_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate auto-allow rules for readonly action classes.

    Returns a list of RuleOutcome if the request matches a readonly pattern,
    or None if the request does not match (so the caller knows to continue
    evaluation with the full rule set).

    Raises:
        TypeError: If *request* is None, to surface caller bugs early rather
                   than raising an opaque AttributeError deep inside the guard.
    """
    if request is None:
        raise TypeError("evaluate_readonly_rules requires a non-None ActionRequest")

    entry = _READONLY_CLASSES.get(request.action_class)
    if entry is None:
        return None

    reason_code, description, use_request_receipt = entry
    return [_allow_outcome(request, reason_code, description, use_request_receipt)]
