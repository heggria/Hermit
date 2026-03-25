"""Iterations API router for WebUI — self-iteration monitor endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = logging.getLogger(__name__)

router = APIRouter()

_PHASES = ("spec", "parse", "branch", "execute", "proof", "pr")

# Status values that map to a terminal state.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "accepted", "rejected"})

# Status values considered "in_progress".
_ACTIVE_STATUSES = frozenset(
    {"pending", "researching", "running", "executing", "branching", "proving"}
)


def _parse_meta(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _derive_phase(status: str, meta: dict[str, Any]) -> str:
    """Infer the current pipeline phase from status and metadata."""
    explicit = meta.get("phase")
    if explicit and explicit in _PHASES:
        return explicit
    mapping = {
        "pending": "spec",
        "researching": "spec",
        "branching": "branch",
        "executing": "execute",
        "proving": "proof",
        "completed": "pr",
        "accepted": "pr",
    }
    return mapping.get(status, "parse")


def _derive_proof_status(meta: dict[str, Any]) -> str | None:
    proof = meta.get("proof_status") or meta.get("proof", {})
    if isinstance(proof, dict):
        return proof.get("status")
    if isinstance(proof, str):
        return proof
    return None


def _format_iteration(row: dict[str, Any]) -> dict[str, Any]:
    meta = _parse_meta(row.get("metadata"))
    status = row.get("status", "pending")
    phase = _derive_phase(status, meta)

    return {
        "iteration_id": row.get("spec_id"),
        "spec_file": meta.get("spec_file") or row.get("goal", ""),
        "phase": phase,
        "status": status,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "branch_name": meta.get("branch_name"),
        "pr_url": meta.get("pr_url"),
        "proof_status": _derive_proof_status(meta),
        "priority": row.get("priority", "normal"),
        "source": row.get("source", "human"),
        "attempt": row.get("attempt", 1),
        "error": row.get("error"),
    }


def _status_group(status: str) -> str:
    if status in _TERMINAL_STATUSES:
        return "completed" if status in {"completed", "accepted"} else "failed"
    return "in_progress"


@router.get("/iterations")
def list_iterations(
    status: str | None = Query(
        None, description="Filter by status group: in_progress, completed, failed"
    ),
    limit: int = Query(50, ge=1, le=200, description="Maximum records to return"),
    offset: int = Query(0, ge=0, description="Records to skip"),
) -> dict[str, Any]:
    """List self-iteration records with optional status filter and pagination."""
    store = get_store()

    try:
        rows: list[dict[str, Any]] = store.list_spec_backlog(limit=limit + offset)
    except Exception as exc:
        _log.warning("list_iterations: store error (returning empty): %s", exc)
        rows = []

    iterations = [_format_iteration(r) for r in rows]

    # Apply status-group filter after formatting
    if status:
        iterations = [it for it in iterations if _status_group(it["status"]) == status]

    # Apply offset
    iterations = iterations[offset:]

    counts: dict[str, int] = {"in_progress": 0, "completed": 0, "failed": 0}
    for it in iterations:
        group = _status_group(it["status"])
        counts[group] = counts.get(group, 0) + 1

    return {
        "iterations": iterations,
        "total": len(iterations),
        "limit": limit,
        "offset": offset,
        "counts": counts,
    }
