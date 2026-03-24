from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

# Adapters that are permitted to ingest attachments on behalf of users.
# Add new adapter IDs here when onboarding additional platform adapters.
_ALLOWED_INGEST_ADAPTERS: frozenset[str] = frozenset(
    {
        "feishu_adapter",
        "slack_adapter",
        "telegram_adapter",
    }
)


def evaluate_attachment_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate attachment ingest rules based on actor identity.

    Returns a list of RuleOutcome if the request is ``attachment_ingest``,
    or ``None`` if the action class does not match.
    """
    if request.action_class != "attachment_ingest":
        return None

    actor_kind = str(request.actor.get("kind", "") or "")
    actor_id = str(request.actor.get("agent_id", "") or "")

    if actor_kind == "adapter" and actor_id in _ALLOWED_INGEST_ADAPTERS:
        return [
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "attachment_ingest_adapter",
                        "Adapter-owned attachment ingestion is allowed with receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
            )
        ]

    return [
        RuleOutcome(
            verdict="deny",
            reasons=[
                PolicyReason(
                    "attachment_ingest_denied",
                    "Attachment ingestion is reserved for adapter-owned ingress.",
                    "error",
                )
            ],
            obligations=PolicyObligations(require_receipt=False),
            risk_level=request.risk_hint or "high",
        )
    ]
