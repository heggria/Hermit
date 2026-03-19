from __future__ import annotations

from typing import Any, cast

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

_PATTERN_HIGH_CONFIDENCE_THRESHOLD = 0.85
_PATTERN_MIN_INVOCATIONS = 3


def apply_policy_suggestion(
    request: ActionRequest, outcomes: list[RuleOutcome]
) -> list[RuleOutcome]:
    """Adjust outcomes based on template-confidence policy suggestion.

    Only applies to ``approval_required`` verdicts.  Critical risk actions
    never have approval skipped.
    """
    suggestion: Any = request.context.get("policy_suggestion")
    if not suggestion or not isinstance(suggestion, dict):
        return outcomes

    suggestion_dict: dict[str, Any] = dict(cast(dict[str, Any], suggestion))
    skip_eligible = bool(suggestion_dict.get("skip_approval_eligible", False))
    suggested_risk: str | None = (
        str(suggestion_dict["suggested_risk_level"])
        if suggestion_dict.get("suggested_risk_level")
        else None
    )
    confidence_basis = str(suggestion_dict.get("confidence_basis", ""))

    adjusted: list[RuleOutcome] = []
    for outcome in outcomes:
        if outcome.verdict != "approval_required":
            adjusted.append(outcome)
            continue

        # Never skip approval for critical risk
        if outcome.risk_level == "critical":
            adjusted.append(outcome)
            continue

        if skip_eligible:
            adjusted.append(
                RuleOutcome(
                    verdict="allow_with_receipt",
                    reasons=outcome.reasons
                    + [
                        PolicyReason(
                            "template_confidence_skip",
                            f"Approval skipped: {confidence_basis}",
                        )
                    ],
                    obligations=PolicyObligations(
                        require_receipt=True,
                        require_approval=False,
                    ),
                    normalized_constraints=outcome.normalized_constraints,
                    risk_level=str(suggested_risk or outcome.risk_level),
                )
            )
        elif suggested_risk and suggested_risk != outcome.risk_level:
            new_outcome = RuleOutcome(
                verdict=outcome.verdict,
                reasons=outcome.reasons
                + [
                    PolicyReason(
                        "template_confidence_downgrade",
                        f"Risk downgraded: {confidence_basis}",
                    )
                ],
                obligations=outcome.obligations,
                normalized_constraints=outcome.normalized_constraints,
                approval_packet=outcome.approval_packet,
                risk_level=suggested_risk,
            )
            adjusted.append(new_outcome)
        else:
            adjusted.append(outcome)

    return adjusted


def apply_task_pattern(request: ActionRequest, outcomes: list[RuleOutcome]) -> list[RuleOutcome]:
    """Annotate outcomes with task-pattern context when a known-good pattern matches.

    High-confidence patterns (>= 85% success, >= 3 invocations) add an
    informational reason and may downgrade risk by one level for
    ``approval_required`` verdicts.  Critical risk is never downgraded.
    """
    pattern: Any = request.context.get("task_pattern")
    if not pattern or not isinstance(pattern, dict):
        return outcomes

    pattern_dict: dict[str, Any] = dict(cast(dict[str, Any], pattern))
    invocation_count = int(pattern_dict.get("invocation_count", 0))
    success_rate = float(pattern_dict.get("success_rate", 0.0))

    if invocation_count < _PATTERN_MIN_INVOCATIONS:
        return outcomes
    if success_rate < _PATTERN_HIGH_CONFIDENCE_THRESHOLD:
        return outcomes

    basis = f"pattern: {invocation_count} tasks, {success_rate:.0%} success"

    adjusted: list[RuleOutcome] = []
    for outcome in outcomes:
        reason = PolicyReason(
            "task_pattern_match",
            f"Action matches known-good task pattern ({basis})",
        )
        if outcome.verdict == "approval_required" and outcome.risk_level != "critical":
            downgraded_risk = "medium" if outcome.risk_level == "high" else outcome.risk_level
            adjusted.append(
                RuleOutcome(
                    verdict=outcome.verdict,
                    reasons=outcome.reasons + [reason],
                    obligations=outcome.obligations,
                    normalized_constraints=outcome.normalized_constraints,
                    approval_packet=outcome.approval_packet,
                    risk_level=downgraded_risk,
                )
            )
        else:
            adjusted.append(
                RuleOutcome(
                    verdict=outcome.verdict,
                    reasons=outcome.reasons + [reason],
                    obligations=outcome.obligations,
                    normalized_constraints=outcome.normalized_constraints,
                    approval_packet=outcome.approval_packet,
                    risk_level=outcome.risk_level,
                )
            )

    return adjusted


def evaluate_autonomous(request: ActionRequest) -> list[RuleOutcome]:
    """Autonomous profile: receipts preserved, approvals skipped, dangerous ops denied."""
    if request.action_class == "read_local":
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("autonomous_read", "Autonomous read auto-allowed.")],
                obligations=PolicyObligations(require_receipt=False),
                risk_level="low",
            )
        ]

    if request.action_class in {"network_read", "delegate_reasoning", "ephemeral_ui_mutation"}:
        return [
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("autonomous_passthrough", "Autonomous safe action.")],
                obligations=PolicyObligations(require_receipt=False),
                risk_level="low",
            )
        ]

    if request.action_class == "execute_command":
        flags = dict(request.derived.get("command_flags", {}))
        if flags.get("sudo") or flags.get("curl_pipe_sh"):
            return [
                RuleOutcome(
                    verdict="deny",
                    reasons=[
                        PolicyReason(
                            "dangerous_shell",
                            "Dangerous shell pattern denied even in autonomous.",
                            "error",
                        )
                    ],
                    risk_level="critical",
                )
            ]

    sensitive_paths = list(request.derived.get("sensitive_paths", []))
    outside_workspace = bool(request.derived.get("outside_workspace"))
    if (
        request.action_class in {"write_local", "patch_file"}
        and sensitive_paths
        and outside_workspace
    ):
        return [
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason(
                        "protected_path",
                        "Protected paths denied even in autonomous mode.",
                        "error",
                    )
                ],
                risk_level="critical",
            )
        ]

    # Kernel self-modification guard (applies even in autonomous mode)
    kernel_paths = list(request.derived.get("kernel_paths", []))
    if request.action_class in {"write_local", "patch_file"} and kernel_paths:
        from pathlib import Path as _Path

        return [
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "kernel_self_modification",
                        "Kernel modification requires approval even in autonomous mode.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_approval=True,
                    require_evidence=True,
                    approval_risk_level="critical",
                ),
                normalized_constraints={"kernel_paths": kernel_paths},
                approval_packet={
                    "title": "Approve kernel self-modification (autonomous)",
                    "summary": (
                        f"Even in autonomous mode, kernel changes require approval: "
                        f"{', '.join(_Path(p).name for p in kernel_paths)}"
                    ),
                    "risk_level": "critical",
                },
                risk_level="critical",
            )
        ]

    return [
        RuleOutcome(
            verdict="allow_with_receipt",
            reasons=[
                PolicyReason(
                    "autonomous_auto_approve",
                    "Autonomous profile: action allowed with receipt, no approval required.",
                )
            ],
            obligations=PolicyObligations(require_receipt=True, require_approval=False),
            risk_level=request.risk_hint or "medium",
        )
    ]
