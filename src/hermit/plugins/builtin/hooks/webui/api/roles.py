"""WebUI API router for custom role definition management."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter(tags=["roles"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class RoleCreateRequest(BaseModel):
    name: str
    description: str = ""
    mcp_servers: list[str] | None = None
    skills: list[str] | None = None
    config: dict[str, Any] | None = None


class RoleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    mcp_servers: list[str] | None = None
    skills: list[str] | None = None
    config: dict[str, Any] | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _role_dict(role: Any) -> dict[str, Any]:
    """Convert a RoleDefinition to a JSON-safe dict."""
    if isinstance(role, dict):
        return role
    return {k: v for k, v in role.__dict__.items() if not k.startswith("_")}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/roles")
def list_roles(
    include_builtin: bool = Query(True, description="Include builtin roles"),
    limit: int = Query(100, ge=1, le=500, description="Max roles to return"),
) -> dict[str, Any]:
    """List all role definitions."""
    store = get_store()
    roles = store.list_role_definitions(include_builtin=include_builtin, limit=limit)
    return {
        "roles": [_role_dict(r) for r in roles],
        "count": len(roles),
    }


@router.post("/roles")
def create_role(body: RoleCreateRequest) -> dict[str, Any]:
    """Create a custom role definition."""
    store = get_store()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")

    try:
        role = store.create_role_definition(
            name=name,
            description=body.description,
            mcp_servers=body.mcp_servers,
            skills=body.skills,
            config=body.config,
        )
    except Exception as exc:
        _log.exception("webui_role_create_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to create role: {exc}") from exc

    return _role_dict(role)


@router.get("/roles/{role_id}")
def get_role(role_id: str) -> dict[str, Any]:
    """Get role definition detail."""
    store = get_store()
    role = store.get_role_definition(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return _role_dict(role)


@router.patch("/roles/{role_id}")
def update_role(role_id: str, body: RoleUpdateRequest) -> dict[str, Any]:
    """Update a custom role definition."""
    store = get_store()
    role = store.get_role_definition(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    kwargs: dict[str, Any] = {}
    if body.name is not None:
        kwargs["name"] = body.name.strip()
    if body.description is not None:
        kwargs["description"] = body.description
    if body.mcp_servers is not None:
        kwargs["mcp_servers"] = body.mcp_servers
    if body.skills is not None:
        kwargs["skills"] = body.skills
    if body.config is not None:
        kwargs["config"] = body.config

    if not kwargs:
        return _role_dict(role)

    try:
        store.update_role_definition(role_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    updated = store.get_role_definition(role_id)
    return _role_dict(updated)


@router.delete("/roles/{role_id}")
def delete_role(role_id: str) -> dict[str, Any]:
    """Delete a custom role definition."""
    store = get_store()
    role = store.get_role_definition(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    try:
        store.delete_role_definition(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"role_id": role_id, "status": "deleted"}
