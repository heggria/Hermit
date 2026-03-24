"""WebUI API router for approval management."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner, get_store

_log = structlog.get_logger()

router = APIRouter(tags=["approvals"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class DenyRequest(BaseModel):
    reason: str = ""


class BatchRequest(BaseModel):
    action: str  # "approve" | "deny"
    ids: list[str]
    reason: str | None = None


class BatchResultItem(BaseModel):
    approval_id: str
    status: str
    text: str | None = None
    error: str | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _approval_to_dict(approval: Any) -> dict[str, Any]:
    """Serialize an ApprovalRecord to a JSON-safe dict."""
    return {k: v for k, v in approval.__dict__.items() if not k.startswith("_")}


def _resolve(approval_id: str, *, action: str, reason: str = "") -> dict[str, Any]:
    """Resolve a single approval (approve or deny).

    Follows the exact pattern from the webhook server.
    """
    runner = get_runner()
    store = get_store()

    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    task = store.get_task(approval.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    kwargs: dict[str, Any] = {
        "action": action,
        "approval_id": approval_id,
    }
    if action == "deny" and reason:
        kwargs["reason"] = reason

    result = runner._resolve_approval(  # type: ignore[attr-defined]
        task.conversation_id,
        **kwargs,
    )
    return {
        "status": "approved" if action == "approve" else "denied",
        "approval_id": approval_id,
        "text": result.text,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/approvals")
async def list_approvals(
    status: str | None = Query(None, description="Filter by status (e.g. 'pending')"),
    limit: int = Query(50, ge=1, le=200, description="Max number of approvals to return"),
) -> dict[str, Any]:
    """List approvals, optionally filtered by status."""
    store = get_store()
    approvals = store.list_approvals(status=status, limit=limit)
    return {"approvals": [_approval_to_dict(a) for a in approvals]}


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str) -> dict[str, Any]:
    """Get a single approval by ID."""
    store = get_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval": _approval_to_dict(approval)}


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str) -> dict[str, Any]:
    """Approve a pending approval."""
    return _resolve(approval_id, action="approve")


@router.post("/approvals/{approval_id}/deny")
async def deny(approval_id: str, body: DenyRequest | None = None) -> dict[str, Any]:
    """Deny a pending approval with an optional reason."""
    reason = body.reason if body is not None else ""
    return _resolve(approval_id, action="deny", reason=reason)


@router.post("/approvals/batch")
async def batch_resolve(body: BatchRequest) -> dict[str, Any]:
    """Batch approve or deny multiple approvals."""
    if body.action not in ("approve", "deny"):
        raise HTTPException(
            status_code=422,
            detail="action must be 'approve' or 'deny'",
        )

    results: list[dict[str, Any]] = []
    for aid in body.ids:
        try:
            reason = body.reason or "" if body.action == "deny" else ""
            outcome = _resolve(aid, action=body.action, reason=reason)
            results.append(
                BatchResultItem(
                    approval_id=aid,
                    status=outcome["status"],
                    text=outcome.get("text"),
                ).model_dump()
            )
        except HTTPException as exc:
            _log.warning(  # type: ignore[call-arg]
                "batch_approval_error",
                approval_id=aid,
                detail=exc.detail,
            )
            results.append(
                BatchResultItem(
                    approval_id=aid,
                    status="error",
                    error=str(exc.detail),
                ).model_dump()
            )

    return {"results": results}
