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
_PATTERN_DOWNGRADE_SAFE_CLASSES = frozenset(
    {
        "read_local",
        "network_read",
        "delegate_reasoning",
        "ephemeral_ui_mutation",
        "execute_command",
        "delegate_execution",
        "approval_resolution",
        "scheduler_mutation",
    }
)


@dataclass
class RuleOutcome:
    verdict: str
    reasons: list[PolicyReason] = field(default_factory=list[PolicyReason])
    obligations: PolicyObligations = field(default_factory=PolicyObligations)
    normalized_constraints: dict[str, Any] = field(default_factory=dict[str, Any])
    approval_packet: dict[str, Any] | None = None
    risk_level: str | None = None
    action_class_override: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "obligations": self.obligations.to_dict(),
            "normalized_constraints": dict(self.normalized_constraints),
            "risk_level": self.risk_level,
            "action_class_override": self.action_class_override,
        }


def evaluate_rules(request: ActionRequest) -> list[RuleOutcome]:
    # Lazy imports to avoid circular dependency -- modular guard files import
    # RuleOutcome from this module, so top-level imports would create a cycle.
    from hermit.kernel.policy.guards.rules_network import evaluate_network_rules
    from hermit.kernel.policy.guards.rules_planning import evaluate_planning_rules
    from hermit.kernel.policy.guards.rules_readonly import evaluate_readonly_rules

    outcomes: list[RuleOutcome] = []
    profile = str(request.context.get("policy_profile", "default"))

    # ------------------------------------------------------------------
    # Delegation scope enforcement
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

    # -- Readonly action classes -- delegated to rules_readonly module.
    readonly_result = evaluate_readonly_rules(request)
    if readonly_result is not None:
        return readonly_result

    # -- Governance action classes -- inline (rules_governance.py diverges).
    if request.action_class == "delegate_execution":
        return [
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
        ]

    if request.action_class == "approval_resolution":
        return [
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
        ]

    if request.action_class == "scheduler_mutation":
        return [
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
        ]

    # -- Attachment ingest -- inline (rules_attachment.py diverges).
    if request.action_class == "attachment_ingest":
        actor_kind = str(request.actor.get("kind", "") or "")
        actor_id = str(request.actor.get("agent_id", "") or "")
        if actor_kind == "adapter" and actor_id == "feishu_adapter":
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

    # -- Planning gate -- delegated to rules_planning module.
    planning_result = evaluate_planning_rules(request)
    if planning_result is not None:
        return planning_result

    # -- Filesystem rules -- inline (rules_filesystem.py has different
    #    control flow: some branches fall through to adjustment functions).
    target_paths = list(request.derived.get("target_paths", []))
    sensitive_paths = list(request.derived.get("sensitive_paths", []))
    outside_workspace = bool(request.derived.get("outside_workspace"))

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

    if request.action_class in {"write_local", "patch_file"} and sensitive_paths:
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "sensitive_path",
                        "Sensitive path mutation requires approval.",
                        "warning",
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
                    "summary": "The requested file change targets a directory "
                    "outside the current workspace.",
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

    # -- Shell rules -- inline (rules_shell.py diverges).
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
                    action_class_override="execute_command_readonly",
                )
            )

    # -- Network / external mutation -- delegated to rules_network module.
    network_result = evaluate_network_rules(request)
    if network_result is not None:
        outcomes.extend(network_result)

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

    # -- Rollback -- inline (rules_governance.py diverges).
    if request.action_class == "rollback":
        return [
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
        ]

    # -- Memory write -- inline (rules_governance.py diverges).
    if request.action_class in {"memory_write"}:
        if request.actor.get("kind") == "kernel" and request.context.get("evidence_refs"):
            return [
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
            ]
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

    # -- Adjustment functions remain inline (rules_adjustment.py diverges).
    outcomes = _apply_policy_suggestion(request, outcomes)
    outcomes = _apply_task_pattern(request, outcomes)
    _apply_signal_risk(request, outcomes)
    _apply_trust_adjustment(request, outcomes)

    return outcomes


def _apply_policy_suggestion(
    request: ActionRequest, outcomes: list[RuleOutcome]
) -> list[RuleOutcome]:
    """Adjust outcomes based on template-confidence policy suggestion."""
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
            adjusted.append(
                RuleOutcome(
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
            )
        else:
            adjusted.append(outcome)

    return adjusted


_PATTERN_HIGH_CONFIDENCE_THRESHOLD = 0.85
_PATTERN_MIN_INVOCATIONS = 3


def _apply_task_pattern(request: ActionRequest, outcomes: list[RuleOutcome]) -> list[RuleOutcome]:
    """Annotate outcomes with task-pattern context when a known-good pattern matches."""
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


def _apply_signal_risk(request: ActionRequest, outcomes: list[RuleOutcome]) -> None:
    """Escalate to require_approval if critical signals present."""
    indicators = request.context.get("signal_risk_indicators", [])
    if not isinstance(indicators, list):
        return
    critical_signals = [
        s for s in indicators if isinstance(s, dict) and s.get("risk_level") == "critical"
    ]
    if critical_signals:
        summary = str(critical_signals[0].get("summary", "unknown"))
        outcomes.append(
            RuleOutcome(
                verdict="approval_required",
                reasons=[
                    PolicyReason(
                        "signal_critical_escalation",
                        f"Critical signal detected: {summary}",
                        "warning",
                    )
                ],
                obligations=PolicyObligations(
                    require_receipt=True,
                    require_approval=True,
                    approval_risk_level="critical",
                ),
                approval_packet={
                    "title": f"Critical signal escalation for {request.tool_name}",
                    "summary": f"Critical signal: {summary}",
                    "risk_level": "critical",
                },
                risk_level="critical",
            )
        )


def _apply_trust_adjustment(request: ActionRequest, outcomes: list[RuleOutcome]) -> None:
    """Downgrade risk for trusted action classes based on trust-score evidence."""
    adjustment = request.context.get("trust_risk_adjustment")
    if not adjustment or not isinstance(adjustment, dict):
        return
    suggested = str(adjustment.get("suggested_risk_band", ""))
    current = str(adjustment.get("current_risk_band", ""))
    if not suggested or not current or suggested == current:
        return
    if request.action_class not in _PATTERN_DOWNGRADE_SAFE_CLASSES:
        return

    reason_text = f"Trust adjustment: {current} \u2192 {suggested}"
    adjusted: list[RuleOutcome] = []
    changed = False
    for outcome in outcomes:
        if outcome.verdict == "approval_required" and outcome.risk_level != "critical":
            adjusted.append(
                RuleOutcome(
                    verdict="allow_with_receipt",
                    reasons=outcome.reasons + [PolicyReason("trust_risk_downgrade", reason_text)],
                    obligations=PolicyObligations(
                        require_receipt=True,
                        require_approval=False,
                    ),
                    normalized_constraints=outcome.normalized_constraints,
                    risk_level=suggested,
                )
            )
            changed = True
        else:
            adjusted.append(outcome)
    if changed:
        outcomes.clear()
        outcomes.extend(adjusted)


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
        # Readonly shell commands: override action_class so deliberation is
        # bypassed.  The autonomous fallback at the end of this function
        # would otherwise leave action_class as "execute_command", which
        # the deliberation gate treats as a mutation action.
        if not any(flags.get(name) for name in ("writes_disk", "deletes_files", "network_access")):
            return [
                RuleOutcome(
                    verdict="allow_with_receipt",
                    reasons=[
                        PolicyReason(
                            "autonomous_readonly_shell",
                            "Autonomous readonly shell command auto-allowed with receipt.",
                        )
                    ],
                    obligations=PolicyObligations(require_receipt=True),
                    normalized_constraints={"shell_mode": "readonly"},
                    risk_level="low",
                    action_class_override="execute_command_readonly",
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

    # Kernel self-modification guard (applies even in autonomous mode,
    # but skipped when policy_profile is "autonomous" for throughput scenarios)
    kernel_paths = list(request.derived.get("kernel_paths", []))
    if (
        request.action_class in {"write_local", "patch_file"}
        and kernel_paths
        and request.policy_profile != "autonomous"
    ):
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
