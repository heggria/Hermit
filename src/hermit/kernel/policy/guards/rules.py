from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

POLICY_RULES_VERSION = "strict-task-first-v2"


@dataclass
class RuleOutcome:
    verdict: str
    reasons: list[PolicyReason] = field(default_factory=list[PolicyReason])
    obligations: PolicyObligations = field(default_factory=PolicyObligations)
    normalized_constraints: dict[str, Any] = field(default_factory=dict[str, Any])
    approval_packet: dict[str, Any] | None = None
    risk_level: str | None = None


def evaluate_rules(request: ActionRequest) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    profile = str(request.context.get("policy_profile", "default"))
    if profile == "readonly" and request.action_class != "read_local":
        outcomes.append(
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
        )
        return outcomes

    if request.action_class == "read_local":
        outcomes.append(
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("readonly_tool", "Readonly tool auto-allowed.")],
                obligations=PolicyObligations(require_receipt=request.requires_receipt),
                risk_level=request.risk_hint or "low",
            )
        )
        return outcomes

    if request.action_class == "network_read":
        outcomes.append(
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason("readonly_network", "Readonly network access is auto-allowed.")
                ],
                obligations=PolicyObligations(require_receipt=request.requires_receipt),
                risk_level=request.risk_hint or "low",
            )
        )
        return outcomes

    if request.action_class == "delegate_reasoning":
        outcomes.append(
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason(
                        "delegate_reasoning",
                        "Internal delegated reasoning is readonly context gathering.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint or "low",
            )
        )
        return outcomes

    if request.action_class == "approval_resolution":
        outcomes.append(
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
        )
        return outcomes

    if request.action_class == "scheduler_mutation":
        outcomes.append(
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
        )
        return outcomes

    if request.action_class == "attachment_ingest":
        actor_kind = str(request.actor.get("kind", "") or "")
        actor_id = str(request.actor.get("agent_id", "") or "")
        if actor_kind == "adapter" and actor_id == "feishu_adapter":
            outcomes.append(
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
            )
        else:
            outcomes.append(
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
            )
        return outcomes

    target_paths = list(request.derived.get("target_paths", []))
    sensitive_paths = list(request.derived.get("sensitive_paths", []))
    outside_workspace = bool(request.derived.get("outside_workspace"))
    planning_required = bool(request.context.get("planning_required", False))
    selected_plan_ref = str(request.context.get("selected_plan_ref", "") or "").strip()
    if (
        planning_required
        and not selected_plan_ref
        and request.action_class
        in {
            "write_local",
            "patch_file",
            "execute_command",
            "network_write",
            "credentialed_api_call",
            "publication",
            "vcs_mutation",
            "external_mutation",
        }
    ):
        outcomes.append(
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
                    approval_risk_level=request.risk_hint or "high",
                ),
                approval_packet={
                    "title": "Select an execution plan first",
                    "summary": "This action requires a confirmed plan before it can run.",
                    "risk_level": request.risk_hint or "high",
                },
                risk_level=request.risk_hint or "high",
            )
        )
        return outcomes
    if (
        request.action_class in {"write_local", "patch_file"}
        and sensitive_paths
        and outside_workspace
    ):
        outcomes.append(
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason(
                        "protected_path",
                        "Protected system or credential paths cannot receive mutable workspace approval.",
                        "error",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                normalized_constraints={"denied_paths": sensitive_paths},
                risk_level="critical",
            )
        )
        return outcomes

    if request.action_class in {"write_local", "patch_file"} and sensitive_paths:
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

    if request.action_class in {"write_local", "patch_file"} and outside_workspace:
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
                    "summary": "The requested file change targets a directory outside the current workspace.",
                    "risk_level": request.risk_hint or "high",
                },
                risk_level=request.risk_hint or "high",
            )
        )
        return outcomes

    if request.action_class in {"write_local", "patch_file"} and not sensitive_paths:
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

    if request.action_class == "execute_command":
        flags = dict(request.derived.get("command_flags", {}))
        if flags.get("sudo") or flags.get("curl_pipe_sh"):
            outcomes.append(
                RuleOutcome(
                    verdict="deny",
                    reasons=[
                        PolicyReason(
                            "dangerous_shell", "Dangerous shell pattern is denied.", "error"
                        )
                    ],
                    risk_level="critical",
                )
            )
            return outcomes
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
            outcomes.append(
                RuleOutcome(
                    verdict="allow_with_receipt",
                    reasons=[PolicyReason("readonly_shell", "Shell command appears read-only.")],
                    obligations=PolicyObligations(require_receipt=True),
                    normalized_constraints={"shell_mode": "readonly"},
                    risk_level="medium",
                )
            )

    if request.action_class in {
        "network_write",
        "credentialed_api_call",
        "publication",
        "vcs_mutation",
        "external_mutation",
    }:
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "external_mutation", "External mutation requires approval.", "warning"
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_preview=request.supports_preview,
                    require_approval=True,
                    approval_risk_level=request.risk_hint or "high",
                ),
                approval_packet={
                    "title": f"Approve external mutation via {request.tool_name}",
                    "summary": "This action mutates external state.",
                    "risk_level": request.risk_hint or "high",
                },
                risk_level=request.risk_hint or "high",
            )
        )

    if request.action_class in {"ephemeral_ui_mutation"}:
        outcomes.append(
            RuleOutcome(
                verdict="allow",
                reasons=[
                    PolicyReason(
                        "ephemeral_ui_mutation",
                        "Ephemeral UI feedback is allowed without approval.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint or "low",
            )
        )
        return outcomes

    if request.action_class == "rollback":
        outcomes.append(
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
        )
        return outcomes

    if request.action_class in {"memory_write"}:
        if request.actor.get("kind") == "kernel" and request.context.get("evidence_refs"):
            outcomes.append(
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
            )
            return outcomes
        outcomes.append(
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
        )

    if not outcomes:
        outcomes.append(
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
        )
    return outcomes
