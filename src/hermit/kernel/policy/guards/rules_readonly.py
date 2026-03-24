from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

# ---------------------------------------------------------------------------
# Dispatch table for readonly action classes.
#
# Each entry maps an action_class string to a tuple of:
#   (rule_key, description, require_receipt_override)
#
# ``require_receipt_override`` is:
#   - True  → always require a receipt
#   - False → never require a receipt (override the request flag)
#   - None  → defer to request.requires_receipt
# ---------------------------------------------------------------------------
_READONLY_RULES: dict[str, tuple[str, str, bool | None]] = {
    "read_local": (
        "readonly_tool",
        "Readonly tool auto-allowed.",
        None,
    ),
    "network_read": (
        "readonly_network",
        "Readonly network access is auto-allowed.",
        None,
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


def evaluate_readonly_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate auto-allow rules for readonly action classes.

    Returns a list of RuleOutcome if the request matches a readonly pattern,
    or None if the request doesn't match (so the caller knows to continue
    evaluation with the full rule set).
    """
    entry = _READONLY_RULES.get(request.action_class)
    if entry is None:
        return None

    rule_key, description, require_receipt_override = entry
    require_receipt = (
        request.requires_receipt if require_receipt_override is None else require_receipt_override
    )

    return [
        RuleOutcome(
            verdict="allow",
            reasons=[PolicyReason(rule_key, description)],
            obligations=PolicyObligations(require_receipt=require_receipt),
            risk_level=request.risk_hint or "low",
        )
    ]
