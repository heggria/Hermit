from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import structlog

from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

POLICY_RULES_VERSION = "strict-task-first-v2"

_log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Policy strictness ordering
# ---------------------------------------------------------------------------
POLICY_STRICTNESS: dict[str, int] = {
    "readonly": 3,
    "supervised": 2,
    "default": 1,
    "autonomous": 0,
}

_VERDICT_PRIORITY: dict[str, int] = {
    "allow": 0,
    "allow_with_receipt": 1,
    "preview_required": 2,
    "approval_required": 3,
    "deny": 4,
}

# Action classes for which pattern-based risk downgrade is considered safe.
# Only these classes may have their risk level reduced by task-pattern or
# template evidence. More dangerous classes (write_local, patch_file,
# network_write, etc.) keep their original risk even when a known-good
# pattern matches.
_PATTERN_DOWNGRADE_SAFE_CLASSES = frozenset({
    "read_local",
    "network_read",
    "delegate_reasoning",
    "ephemeral_ui_mutation",
    "execute_command",
    "delegate_execution",
    "approval_resolution",
    "scheduler_mutation",
})


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
            _log.warning(
                "guard.deny",
                rule="delegation_scope_violation",
                tool=request.tool_name,
                action_class=request.action_class,
                allowed_classes=allowed,
            )
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
        _log.warning(
            "guard.deny",
            rule="readonly_profile",
            tool=request.tool_name,
            action_class=request.action_class,
        )
        return [
            RuleOutcome(
                verdict="deny",
                reasons=[
                    PolicyReason(
                        "readonly_profile",
                        "Readonly policy profile forbids side effects.",
                        "error",
                    )
                ],
                obligations=PolicyObligations(require_receipt=False),
                risk_level=request.risk_hint,
            )
        ]

    if profile == "autonomous":
        return _evaluate_autonomous(request)

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

    if request.action_class == "delegate_execution":
        outcomes.append(
            RuleOutcome(
                verdict="allow_with_receipt",
                reasons=[
                    PolicyReason(
                        "delegate_execution",
                        "Governed subagent delegation requires a decision and receipt.",
                    )
                ],
                obligations=PolicyObligations(require_receipt=True),
                risk_level=request.risk_hint or "medium",
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

    # Kernel self-modification guard
    kernel_paths = list(request.derived.get("kernel_paths", []))
    if request.action_class in {"write_local", "patch_file"} and kernel_paths:
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

    # -- Template-confidence policy suggestion adjustment ------------------
    outcomes = _apply_policy_suggestion(request, outcomes)

    # -- Task-pattern context annotation ------------------------------------
    outcomes = _apply_task_pattern(request, outcomes)

    return outcomes


def _apply_policy_suggestion(
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


_PATTERN_HIGH_CONFIDENCE_THRESHOLD = 0.85
_PATTERN_MIN_INVOCATIONS = 3


def _apply_task_pattern(request: ActionRequest, outcomes: list[RuleOutcome]) -> list[RuleOutcome]:
    """Annotate outcomes with task-pattern context when a known-good pattern matches.

    High-confidence patterns (>= 85% success, >= 3 invocations) add an
    informational reason and may downgrade risk by one level for
    ``approval_required`` verdicts.  Critical risk is never downgraded.
    Risk downgrade is only applied when the action class is in
    ``_PATTERN_DOWNGRADE_SAFE_CLASSES``; for dangerous action classes the
    pattern match is recorded but the risk level is preserved.
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
            # Only downgrade risk for safe action classes
            if request.action_class in _PATTERN_DOWNGRADE_SAFE_CLASSES:
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
                _log.info(
                    "policy_rules.pattern_downgrade_blocked",
                    action_class=request.action_class,
                    pattern_basis=basis,
                    reason="action_class not in safe classes for risk downgrade",
                )
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


def _evaluate_autonomous(request: ActionRequest) -> list[RuleOutcome]:
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
