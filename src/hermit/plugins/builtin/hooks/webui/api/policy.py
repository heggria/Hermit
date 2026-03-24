"""WebUI API router for policy configuration."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner, get_store

_log = structlog.get_logger()

router = APIRouter(tags=["policy"])


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/policy/profiles")
async def list_profiles() -> dict[str, Any]:
    """List available policy profiles."""
    runner = get_runner()
    profiles: list[dict[str, Any]] = []

    settings = getattr(runner, "_settings", None)
    if settings is None:
        settings = getattr(runner, "settings", None)

    catalog = getattr(settings, "profile_catalog", None) or {}
    for name, profile in catalog.items():
        profiles.append(
            {
                "name": name,
                "description": getattr(profile, "description", ""),
                "policy_mode": getattr(profile, "policy_mode", "default"),
            }
        )

    # Fallback when no catalog is available
    if not profiles:
        profiles = [
            {
                "name": "autonomous",
                "description": "Auto-approve low-risk actions",
                "policy_mode": "autonomous",
            },
            {
                "name": "supervised",
                "description": "Require approval for all mutations",
                "policy_mode": "supervised",
            },
            {
                "name": "default",
                "description": "Default policy profile",
                "policy_mode": "default",
            },
        ]

    return {"profiles": profiles}


@router.get("/policy/guards")
async def list_guards() -> dict[str, Any]:
    """List policy guard configuration."""
    guards = [
        {"name": "readonly", "description": "Enforces read-only mode", "order": 1},
        {"name": "filesystem", "description": "File access restrictions", "order": 2},
        {"name": "shell", "description": "Shell command restrictions", "order": 3},
        {"name": "network", "description": "Network access restrictions", "order": 4},
        {"name": "attachment", "description": "Attachment handling", "order": 5},
        {"name": "planning", "description": "Planning mode restrictions", "order": 6},
        {"name": "governance", "description": "Approval/receipt requirements", "order": 7},
    ]
    return {"guards": guards}


@router.get("/policy/action-classes")
async def list_action_classes() -> dict[str, Any]:
    """List all action classifications and their default verdicts."""
    classes = [
        {"name": "READ_LOCAL", "risk": "low", "default_verdict": "allow"},
        {"name": "NETWORK_READ", "risk": "low", "default_verdict": "allow"},
        {
            "name": "EXECUTE_COMMAND_READONLY",
            "risk": "low",
            "default_verdict": "allow",
        },
        {
            "name": "WRITE_LOCAL",
            "risk": "medium",
            "default_verdict": "allow_with_receipt",
        },
        {
            "name": "PATCH_FILE",
            "risk": "medium",
            "default_verdict": "allow_with_receipt",
        },
        {"name": "MEMORY_WRITE", "risk": "low", "default_verdict": "allow"},
        {
            "name": "EXECUTE_COMMAND",
            "risk": "high",
            "default_verdict": "approval_required",
        },
        {
            "name": "NETWORK_WRITE",
            "risk": "high",
            "default_verdict": "approval_required",
        },
        {
            "name": "VCS_MUTATION",
            "risk": "high",
            "default_verdict": "approval_required",
        },
        {
            "name": "PUBLICATION",
            "risk": "high",
            "default_verdict": "approval_required",
        },
        {
            "name": "DELEGATE_EXECUTION",
            "risk": "medium",
            "default_verdict": "allow_with_receipt",
        },
        {
            "name": "SCHEDULER_MUTATION",
            "risk": "medium",
            "default_verdict": "approval_required",
        },
    ]
    return {"action_classes": classes}


@router.post("/policy/approve-all")
async def approve_all_pending() -> dict[str, Any]:
    """Batch approve all pending approvals."""
    runner = get_runner()
    store = get_store()
    approvals = store.list_approvals(status="pending", limit=200)
    results: list[dict[str, Any]] = []

    for approval in approvals:
        try:
            task = store.get_task(approval.task_id)
            if task is None:
                continue
            runner._resolve_approval(  # type: ignore[attr-defined]
                task.conversation_id,
                action="approve",
                approval_id=approval.approval_id,
            )
            results.append({"approval_id": approval.approval_id, "status": "approved"})
        except Exception as e:
            _log.warning(  # type: ignore[call-arg]
                "approve_all_error",
                approval_id=approval.approval_id,
                error=str(e),
            )
            results.append(
                {
                    "approval_id": approval.approval_id,
                    "status": "error",
                    "error": str(e),
                }
            )

    approved_count = len([r for r in results if r["status"] == "approved"])
    return {
        "approved": approved_count,
        "total": len(approvals),
        "results": results,
    }
