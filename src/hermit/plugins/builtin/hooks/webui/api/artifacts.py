"""Artifacts API router for WebUI — artifact listing and detail endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _artifact_dict(artifact: Any) -> dict[str, Any]:
    """Convert an ArtifactRecord to a JSON-safe dict."""
    if isinstance(artifact, dict):
        return artifact
    d = dict(artifact.__dict__)
    d.pop("_sa_instance_state", None)
    return d


# ---------------------------------------------------------------------------
# GET /artifacts
# ---------------------------------------------------------------------------


@router.get("/artifacts")
def list_artifacts(
    task_id: str | None = Query(None, description="Filter artifacts by task ID"),
    kind: str | None = Query(None, description="Filter artifacts by kind"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of artifacts to return"),
    offset: int = Query(0, ge=0, description="Number of artifacts to skip"),
) -> dict[str, Any]:
    """List artifacts with optional task_id / kind filters and pagination."""
    store = get_store()
    try:
        if kind:
            artifacts = store.list_artifacts_by_kind(kind, task_id=task_id)
        else:
            artifacts = store.list_artifacts(task_id=task_id, limit=limit + offset)
    except Exception as exc:
        _log.warning("webui_list_artifacts_error", error=str(exc))  # type: ignore[call-arg]
        artifacts = []

    artifacts = artifacts[offset : offset + limit]

    # Collect distinct kinds for facet info
    kinds: list[str] = []
    seen_kinds: set[str] = set()
    for a in artifacts:
        k = getattr(a, "kind", None) or (a.get("kind") if isinstance(a, dict) else None)
        if k and k not in seen_kinds:
            seen_kinds.add(k)
            kinds.append(k)

    return {
        "artifacts": [_artifact_dict(a) for a in artifacts],
        "limit": limit,
        "offset": offset,
        "count": len(artifacts),
        "kinds": kinds,
    }


# ---------------------------------------------------------------------------
# GET /artifacts/{artifact_id}
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    """Get a single artifact's detail plus any available lineage information."""
    store = get_store()

    try:
        artifact = store.get_artifact(artifact_id)
    except Exception as exc:
        _log.warning("webui_get_artifact_error", artifact_id=artifact_id, error=str(exc))  # type: ignore[call-arg]
        artifact = None

    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_data = _artifact_dict(artifact)

    # Attempt to fetch sibling artifacts (lineage context) if task_id is present
    lineage: dict[str, Any] = {}
    task_id = artifact_data.get("task_id")
    if task_id:
        try:
            manifest = store.get_artifact_manifest(task_id)
            lineage = {
                "task_id": task_id,
                "total_in_task": manifest.get("total", 0),
                "counts_by_kind": manifest.get("counts_by_kind", {}),
                "sibling_ids": [
                    aid for aid in manifest.get("artifact_ids", []) if aid != artifact_id
                ],
            }
        except Exception as exc:
            _log.warning("webui_artifact_lineage_error", artifact_id=artifact_id, error=str(exc))  # type: ignore[call-arg]

    return {
        "artifact": artifact_data,
        "lineage": lineage,
    }
