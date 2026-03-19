from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason


def evaluate_filesystem_rules(request: ActionRequest) -> list[RuleOutcome] | None:
    """Evaluate filesystem path guard rules for write_local / patch_file actions.

    Returns a list of RuleOutcome if the request is a filesystem write,
    or None if the request is not a filesystem action.
    """
    if request.action_class not in {"write_local", "patch_file"}:
        return None

    outcomes: list[RuleOutcome] = []

    target_paths = list(request.derived.get("target_paths", []))
    sensitive_paths = list(request.derived.get("sensitive_paths", []))
    outside_workspace = bool(request.derived.get("outside_workspace"))

    # -- Protected paths: sensitive + outside workspace → hard deny -----------
    if sensitive_paths and outside_workspace:
        outcomes.append(
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason(
                        "protected_path",
                        "Protected system or credential paths cannot receive "
                        "mutable workspace approval.",
                        "error",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                normalized_constraints={"denied_paths": sensitive_paths},
                risk_level="critical",
            )
        )
        return outcomes

    # -- Sensitive paths: require approval ------------------------------------
    if sensitive_paths:
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "sensitive_path", "Sensitive path mutation requires approval.", "warning"
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=True,
                    require_approval=True,
                    approval_risk_level="critical",
                ),
                normalized_constraints={"denied_paths": sensitive_paths},
                approval_packet={
                    "title": f"Approve sensitive path mutation via {request.tool_name}",
                    "summary": "The requested write targets a sensitive path.",
                    "risk_level": "critical",
                },
                risk_level="critical",
            )
        )

    # -- Kernel self-modification guard ---------------------------------------
    kernel_paths = list(request.derived.get("kernel_paths", []))
    if kernel_paths:
        from pathlib import Path as _Path

        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "kernel_self_modification",
                        "Modifying kernel source requires elevated approval. "
                        "This action targets governed execution internals.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=True,
                    require_approval=True,
                    require_evidence=True,
                    approval_risk_level="critical",
                ),
                normalized_constraints={"kernel_paths": kernel_paths},
                approval_packet={
                    "title": "Approve kernel self-modification",
                    "summary": (
                        f"Agent requests to modify kernel source: "
                        f"{', '.join(_Path(p).name for p in kernel_paths)}. "
                        f"This changes governed execution internals."
                    ),
                    "risk_level": "critical",
                },
                risk_level="critical",
            )
        )
        return outcomes

    # -- Outside workspace: require approval ----------------------------------
    if outside_workspace:
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "outside_workspace_write",
                        "Writing outside the task workspace requires explicit approval.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=request.supports_preview,
                    require_approval=True,
                    approval_risk_level=request.risk_hint or "high",
                ),
                normalized_constraints={"allowed_paths": target_paths},
                approval_packet={
                    "title": f"Approve out-of-workspace write via {request.tool_name}",
                    "summary": "The requested file change targets a directory "
                    "outside the current workspace.",
                    "risk_level": request.risk_hint or "high",
                },
                risk_level=request.risk_hint or "high",
            )
        )
        return outcomes

    # -- Non-sensitive workspace mutation: preview or approval ----------------
    if not sensitive_paths:
        verdict = "preview_required" if request.supports_preview else "approval_required"
        outcomes.append(
            RuleOutcome(
                verdict=verdict,
                reasons=[
                    PolicyReason(
                        "workspace_mutation",
                        "Workspace mutation requires preview before execution.",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=request.supports_preview,
                    require_approval=not request.supports_preview,
                    approval_risk_level=(request.risk_hint or "high")
                    if not request.supports_preview
                    else None,
                ),
                normalized_constraints={"allowed_paths": target_paths},
                approval_packet=(
                    {
                        "title": f"Approve file mutation via {request.tool_name}",
                        "summary": "The requested file change cannot be safely previewed.",
                        "risk_level": request.risk_hint or "high",
                    }
                    if not request.supports_preview
                    else None
                ),
                risk_level=request.risk_hint or "high",
            )
        )

    return outcomes
