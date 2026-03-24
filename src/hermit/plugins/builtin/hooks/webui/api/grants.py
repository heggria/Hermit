"""Capability Grants & Workspace Leases API router for WebUI."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog
from fastapi import APIRouter, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter(tags=["grants"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dict(record: Any) -> dict[str, Any]:
    """Convert a dataclass record to a JSON-safe dict."""
    if isinstance(record, dict):
        return record
    try:
        return asdict(record)
    except Exception:
        d = dict(record.__dict__)
        d.pop("_sa_instance_state", None)
        return d


# ---------------------------------------------------------------------------
# GET /grants
# ---------------------------------------------------------------------------


@router.get("/grants")
def list_grants(
    task_id: str | None = Query(None, description="Filter by task ID"),
    status: str | None = Query(None, description="Filter by grant status"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of grants to return"),
    offset: int = Query(0, ge=0, description="Number of grants to skip"),
) -> dict[str, Any]:
    """List capability grants with optional filters and pagination."""
    store = get_store()

    try:
        fetch_limit = limit + offset
        grants = store.list_capability_grants(task_id=task_id, limit=fetch_limit)
    except AttributeError:
        _log.warning("grants_store_method_missing", method="list_capability_grants")
        grants = []
    except Exception as exc:
        _log.error("grants_list_error", error=str(exc))
        grants = []

    # Apply status filter after fetch (store method only supports task_id filter)
    if status:
        grants = [g for g in grants if getattr(g, "status", None) == status]

    grants = grants[offset:]
    grant_dicts = [_to_dict(g) for g in grants]

    return {"grants": grant_dicts, "total": len(grant_dicts)}


# ---------------------------------------------------------------------------
# GET /workspace-leases
# ---------------------------------------------------------------------------


@router.get("/workspace-leases")
def list_workspace_leases(
    task_id: str | None = Query(None, description="Filter by task ID"),
    status: str | None = Query(None, description="Filter by lease status"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of leases to return"),
    offset: int = Query(0, ge=0, description="Number of leases to skip"),
) -> dict[str, Any]:
    """List workspace leases with optional filters and pagination."""
    store = get_store()

    try:
        fetch_limit = limit + offset
        leases = store.list_workspace_leases(task_id=task_id, status=status, limit=fetch_limit)
    except AttributeError:
        _log.warning("grants_store_method_missing", method="list_workspace_leases")
        leases = []
    except Exception as exc:
        _log.error("workspace_leases_list_error", error=str(exc))
        leases = []

    leases = leases[offset:]
    lease_dicts = [_to_dict(lease) for lease in leases]

    return {"leases": lease_dicts, "total": len(lease_dicts)}
