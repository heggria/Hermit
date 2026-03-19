from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason


def evaluate_shell_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate policy rules for shell command execution.

    Returns a list of RuleOutcome if the request is an ``execute_command``,
    or ``None`` if the request is not a shell command.
    """
    if request.action_class != "execute_command":
        return None

    outcomes: list[RuleOutcome] = []
    flags = dict(request.derived.get("command_flags", {}))

    # Dangerous patterns are always denied
    if flags.get("sudo") or flags.get("curl_pipe_sh"):
        outcomes.append(
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason("dangerous_shell", "Dangerous shell pattern is denied.", "error")
                ],
                risk_level="critical",
            )
        )
        return outcomes

    # Git push requires explicit approval
    if flags.get("git_push"):
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason("git_push", "Git push requires explicit approval.", "warning")
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=True,
                    require_approval=True,
                    approval_risk_level="critical",
                ),
                approval_packet={
                    "title": "Approve git push",
                    "summary": "This shell command would push repository state remotely.",
                    "risk_level": "critical",
                },
                risk_level="critical",
            )
        )
    elif any(flags.get(name) for name in ("writes_disk", "deletes_files", "network_access")):
        # Mutable shell commands with side effects
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "mutable_shell",
                        "Shell command has side effects and requires approval.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=True,
                    require_approval=True,
                    approval_risk_level=request.risk_hint or "critical",
                ),
                approval_packet={
                    "title": f"Approve shell command via {request.tool_name}",
                    "summary": "The command has write, delete, or network side effects.",
                    "risk_level": request.risk_hint or "critical",
                },
                risk_level=request.risk_hint or "critical",
            )
        )
    else:
        # Read-only shell commands
        outcomes.append(
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[PolicyReason("readonly_shell", "Shell command appears read-only.")],
                obligations=PolicyObligations(require_receipt=True),
                normalized_constraints={"shell_mode": "readonly"},
                risk_level="medium",
                action_class_override="execute_command_readonly",
            )
        )

    return outcomes
