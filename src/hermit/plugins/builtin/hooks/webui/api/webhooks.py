"""WebUI API — webhook routes and trigger log endpoints."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

_WEBHOOKS_PATH = Path.home() / ".hermit" / "webhooks.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_raw() -> dict:
    if not _WEBHOOKS_PATH.exists():
        return {"routes": {}}
    try:
        return json.loads(_WEBHOOKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"routes": {}}


def _save_raw(data: dict) -> None:
    _WEBHOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _WEBHOOKS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_WEBHOOKS_PATH)


def _routes_as_list(raw: dict) -> list[dict]:
    """Normalise routes dict → list with an injected 'id' key."""
    routes_raw = raw.get("routes", {})
    if isinstance(routes_raw, list):
        # already list format
        result = []
        for i, r in enumerate(routes_raw):
            entry = dict(r)
            if "id" not in entry:
                entry["id"] = str(i)
            result.append(entry)
        return result
    # dict format: key is the route name
    result = []
    for name, r in routes_raw.items():
        entry = dict(r)
        entry.setdefault("id", name)
        entry.setdefault("name", name)
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class WebhookRouteCreate(BaseModel):
    path: str
    goal_template: str
    policy_profile: str = "default"
    name: str | None = None
    secret: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/webhooks")
async def list_webhooks(limit: int = Query(100, ge=1)) -> dict:
    """List all webhook routes from ~/.hermit/webhooks.json."""
    raw = _load_raw()
    routes = _routes_as_list(raw)[:limit]
    return {"routes": routes, "total": len(routes)}


@router.get("/webhooks/logs")
async def list_webhook_logs(limit: int = Query(50, ge=1)) -> dict:
    """List recent webhook trigger events from the kernel store."""
    from hermit.plugins.builtin.hooks.webui.api.deps import get_store

    try:
        store = get_store()
    except HTTPException:
        return {"logs": [], "total": 0}

    logs: list[Any] = []

    # Try generic record listing first
    for method_name in ("list_records", "list_generic_records"):
        fn = getattr(store, method_name, None)
        if fn is None:
            continue
        try:
            records = fn(kind="webhook_trigger", limit=limit)
            logs = [r.__dict__ if hasattr(r, "__dict__") else r for r in records]
            break
        except Exception:
            continue

    return {"logs": logs, "total": len(logs)}


@router.post("/webhooks")
async def create_webhook(body: WebhookRouteCreate) -> dict:
    """Add a new webhook route to ~/.hermit/webhooks.json."""
    raw = _load_raw()
    routes_raw = raw.get("routes", {})

    route_id = body.name or str(uuid.uuid4())[:8]
    name = body.name or route_id

    new_route: dict = {
        "path": body.path,
        "prompt_template": body.goal_template,
        "policy_profile": body.policy_profile,
    }
    if body.secret:
        new_route["secret"] = body.secret

    if isinstance(routes_raw, dict):
        if name in routes_raw:
            raise HTTPException(status_code=409, detail=f"Route '{name}' already exists")
        routes_raw[name] = new_route
        raw["routes"] = routes_raw
    else:
        # list format
        paths = [r.get("path") for r in routes_raw]
        if body.path in paths:
            raise HTTPException(status_code=409, detail=f"Path '{body.path}' already exists")
        new_route["id"] = route_id
        new_route["name"] = name
        routes_raw.append(new_route)
        raw["routes"] = routes_raw

    _save_raw(raw)
    return {"id": route_id, "name": name, **new_route}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str) -> dict:
    """Remove a webhook route by name/id from ~/.hermit/webhooks.json."""
    raw = _load_raw()
    routes_raw = raw.get("routes", {})

    if isinstance(routes_raw, dict):
        if webhook_id not in routes_raw:
            raise HTTPException(status_code=404, detail="Webhook route not found")
        del routes_raw[webhook_id]
        raw["routes"] = routes_raw
    else:
        before = len(routes_raw)
        routes_raw = [
            r for r in routes_raw if r.get("id") != webhook_id and r.get("name") != webhook_id
        ]
        if len(routes_raw) == before:
            raise HTTPException(status_code=404, detail="Webhook route not found")
        raw["routes"] = routes_raw

    _save_raw(raw)
    return {"deleted": webhook_id}
