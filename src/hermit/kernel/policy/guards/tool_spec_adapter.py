from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.contracts import contract_for
from hermit.kernel.policy.models.models import ActionRequest
from hermit.runtime.capability.registry.tools import ToolSpec


def infer_action_class(tool: ToolSpec) -> str:
    if tool.action_class:
        return tool.action_class
    if tool.readonly:
        return "read_local"
    return "unknown"


# Canonical symbolic scope tokens that are passed through without path resolution.
_SYMBOLIC_SCOPES = frozenset(
    {
        "task_workspace",
        "repo",
        "home",
        "system",
        "network",
        "remote_service",
        "memory_store",
        "unknown",
    }
)

# Filesystem path prefixes that classify as "system" scope.
_SYSTEM_PATH_PREFIXES = ("/etc", "/usr", "/Library", "/System")


def normalize_scope_hints(
    scope_hint: str | list[str] | None, *, workspace_root: str = ""
) -> list[str]:
    """Normalize raw scope hints into canonical scope tokens.

    Each hint is either a symbolic token (passed through as-is) or an
    absolute/relative path that is resolved and classified into one of:
    ``task_workspace``, ``home``, ``system``, or ``unknown``.

    The previous implementation fell through to ``"repo"`` for any path that
    did not match the workspace, home, or known system prefixes.  Paths such
    as ``/tmp``, ``/var``, ``/Volumes/...``, or a mounted network drive share
    have nothing to do with a source repository, so mapping them to ``"repo"``
    produced silently incorrect policy decisions.  They are now classified as
    ``"unknown"``, which causes the policy engine to apply the most
    conservative risk band and require explicit approval.
    """
    hints: list[str] = (
        scope_hint if isinstance(scope_hint, list) else ([scope_hint] if scope_hint else [])
    )
    scopes: list[str] = []
    workspace = Path(workspace_root).resolve() if workspace_root else None

    for hint in hints:
        if not hint:
            continue

        # Pass symbolic tokens through without path resolution.
        if hint in _SYMBOLIC_SCOPES:
            scopes.append(hint)
            continue

        try:
            path = Path(hint).expanduser().resolve()
        except OSError:
            scopes.append("unknown")
            continue

        if workspace and (path == workspace or workspace in path.parents):
            scopes.append("task_workspace")
        elif str(path).startswith(str(Path.home())):
            scopes.append("home")
        elif str(path).startswith(_SYSTEM_PATH_PREFIXES):
            scopes.append("system")
        else:
            # The path does not match the workspace, home directory, or any
            # known system prefix.  Fall back to "unknown" rather than "repo"
            # to avoid misclassifying paths like /tmp or /Volumes/... as source-
            # repository scope, which would grant them undeserved trust.
            scopes.append("unknown")

    return list(dict.fromkeys(scopes or ["unknown"]))


def build_action_request(
    tool: ToolSpec,
    tool_input: dict[str, Any],
    *,
    attempt_ctx: TaskExecutionContext | None = None,
) -> ActionRequest:
    action_class = infer_action_class(tool)
    workspace_root = attempt_ctx.workspace_root if attempt_ctx else ""
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    contract = contract_for(action_class)
    ingress = dict(attempt_ctx.ingress_metadata or {}) if attempt_ctx else {}
    actor_principal_id = attempt_ctx.actor_principal_id if attempt_ctx else None
    actor = (
        {"kind": "principal", "principal_id": actor_principal_id, "agent_id": "hermit"}
        if actor_principal_id
        else {"kind": "agent", "agent_id": "hermit"}
    )
    return ActionRequest(
        request_id=request_id,
        idempotency_key=request_id,
        task_id=attempt_ctx.task_id if attempt_ctx else "",
        step_id=attempt_ctx.step_id if attempt_ctx else "",
        step_attempt_id=attempt_ctx.step_attempt_id if attempt_ctx else "",
        conversation_id=attempt_ctx.conversation_id if attempt_ctx else None,
        tool_name=tool.name,
        tool_input=tool_input,
        action_class=action_class,
        resource_scopes=normalize_scope_hints(
            tool.resource_scope_hint, workspace_root=workspace_root
        ),
        risk_hint=tool.risk_hint or contract.default_risk_band,
        idempotent=bool(tool.idempotent),
        requires_receipt=bool(tool.requires_receipt)
        if tool.requires_receipt is not None
        else contract.receipt_required,
        supports_preview=bool(tool.supports_preview),
        actor=actor,
        context={
            "cwd": workspace_root,
            "repo_root": workspace_root,
            "source_ingress": attempt_ctx.source_channel if attempt_ctx else "unknown",
            "policy_profile": attempt_ctx.policy_profile if attempt_ctx else "default",
            "workspace_root": workspace_root,
            "selected_plan_ref": str(ingress.get("selected_plan_ref", "") or ""),
            "plan_status": str(ingress.get("plan_status", "") or ""),
            "planning_required": bool(ingress.get("planning_required", False)),
        },
    )
