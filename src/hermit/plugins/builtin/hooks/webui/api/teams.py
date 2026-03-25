"""WebUI API router for team and milestone management."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermit.kernel.task.models.team import (
    MILESTONE_STATE_TRANSITIONS,
    TEAM_STATE_TRANSITIONS,
    MilestoneState,
    TeamState,
)
from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter(tags=["teams"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class RoleSlotInput(BaseModel):
    role: str
    count: int = 1
    config: dict[str, Any] | None = None


class TeamCreateRequest(BaseModel):
    program_id: str | None = None
    title: str
    role_assembly: dict[str, RoleSlotInput] | None = None
    workspace_id: str | None = None
    context_boundary: list[str] | None = None
    metadata: dict[str, Any] | None = None


class TeamUpdateRequest(BaseModel):
    title: str | None = None
    role_assembly: dict[str, RoleSlotInput] | None = None
    metadata: dict[str, Any] | None = None
    status: str | None = None


class MilestoneCreateRequest(BaseModel):
    title: str
    description: str = ""
    dependency_ids: list[str] | None = None
    acceptance_criteria: list[str] | None = None


class MilestoneStatusRequest(BaseModel):
    status: str


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _record_dict(record: Any) -> dict[str, Any]:
    """Convert a dataclass record to a JSON-safe dict."""
    if isinstance(record, dict):
        return record
    d = {k: v for k, v in record.__dict__.items() if not k.startswith("_")}
    return d


def _team_dict(team: Any) -> dict[str, Any]:
    """Convert a TeamRecord to a JSON-safe dict, serializing role_assembly."""
    d = _record_dict(team)
    # Convert RoleSlotSpec values to plain dicts
    assembly = d.get("role_assembly", {})
    serialized: dict[str, Any] = {}
    for key, val in assembly.items():
        if hasattr(val, "role"):
            serialized[key] = {"role": val.role, "count": val.count, "config": val.config}
        else:
            serialized[key] = val
    d["role_assembly"] = serialized
    return d


def _record_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of record objects to dicts."""
    return [_record_dict(r) for r in records]


def _role_input_to_store(
    assembly: dict[str, RoleSlotInput] | None,
) -> dict[str, Any] | None:
    """Convert Pydantic RoleSlotInput dict to store-compatible dict."""
    if assembly is None:
        return None
    return {
        key: {"role": val.role, "count": val.count, "config": val.config or {}}
        for key, val in assembly.items()
    }


# ------------------------------------------------------------------
# Team endpoints
# ------------------------------------------------------------------


@router.get("/teams")
def list_teams(
    program_id: str | None = Query(None, description="Filter by program ID"),
    limit: int = Query(50, ge=1, le=200, description="Max teams to return"),
) -> dict[str, Any]:
    """List teams, optionally filtered by program."""
    store = get_store()
    if program_id:
        teams = store.list_teams_by_program(program_id=program_id, limit=limit)
    else:
        # No global list_teams method — list from all programs
        programs = store.list_programs(limit=1000)
        teams: list[Any] = []
        for prog in programs:
            batch = store.list_teams_by_program(
                program_id=prog.program_id, limit=limit - len(teams)
            )
            teams.extend(batch)
            if len(teams) >= limit:
                break
        teams = teams[:limit]

    return {
        "teams": [_team_dict(t) for t in teams],
        "count": len(teams),
    }


@router.post("/teams")
def create_team(body: TeamCreateRequest) -> dict[str, Any]:
    """Create a new team under a program."""
    store = get_store()

    # Resolve program: use provided, or fall back to first available, or auto-create
    program_id = body.program_id
    if program_id:
        program = store.get_program(program_id)
        if program is None:
            raise HTTPException(status_code=404, detail="Program not found")
    else:
        programs = store.list_programs()
        if programs:
            program_id = programs[0].program_id
        else:
            program = store.create_program(
                title="Default",
                goal="Default program for standalone teams",
            )
            program_id = program.program_id

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title must not be empty")

    workspace_id = body.workspace_id or f"ws-{program_id}"
    role_assembly = _role_input_to_store(body.role_assembly)

    try:
        team = store.create_team(
            program_id=program_id,
            title=title,
            workspace_id=workspace_id,
            role_assembly=role_assembly,
            context_boundary=body.context_boundary,
            metadata=body.metadata,
        )
    except Exception as exc:
        _log.exception("webui_team_create_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to create team: {exc}") from exc

    return _team_dict(team)


@router.get("/teams/{team_id}")
def get_team(team_id: str) -> dict[str, Any]:
    """Get team detail with milestones."""
    store = get_store()
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    milestones = store.list_milestones_by_team(team_id=team_id)
    return {
        "team": _team_dict(team),
        "milestones": _record_dicts(milestones),
    }


@router.patch("/teams/{team_id}")
def update_team(team_id: str, body: TeamUpdateRequest) -> dict[str, Any]:
    """Update team fields."""
    store = get_store()
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    # Handle status update — validate against TeamState transitions
    if body.status is not None:
        if body.status not in TeamState.__members__.values():
            valid = ", ".join(s.value for s in TeamState)
            raise HTTPException(
                status_code=422, detail=f"Invalid status: {body.status!r}. Valid: {valid}"
            )
        allowed = TEAM_STATE_TRANSITIONS.get(team.status, frozenset())
        if body.status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot transition from {team.status!r} to {body.status!r}",
            )
        try:
            store.update_team_status(team_id, body.status)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Handle role_assembly and metadata via direct SQL update
    import time

    from hermit.kernel.ledger.journal.store_support import canonical_json

    updates: list[str] = []
    params: list[Any] = []

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Title must not be empty")
        updates.append("title = ?")
        params.append(title)

    if body.role_assembly is not None:
        role_data = _role_input_to_store(body.role_assembly) or {}
        updates.append("role_assembly_json = ?")
        params.append(canonical_json(role_data))

    if body.metadata is not None:
        merged = {**team.metadata, **body.metadata}
        updates.append("metadata_json = ?")
        params.append(canonical_json(merged))

    if updates:
        now = time.time()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(team_id)
        set_clause = ", ".join(updates)
        conn = store._get_conn()
        with conn:
            conn.execute(
                f"UPDATE teams SET {set_clause} WHERE team_id = ?",
                params,
            )

    updated = store.get_team(team_id)
    return _team_dict(updated)


@router.delete("/teams/{team_id}")
def archive_team(team_id: str) -> dict[str, Any]:
    """Archive a team."""
    store = get_store()
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.status == "archived":
        raise HTTPException(status_code=409, detail="Team already archived")

    allowed = TEAM_STATE_TRANSITIONS.get(team.status, frozenset())
    if "archived" not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot archive team in {team.status!r} state",
        )

    try:
        store.update_team_status(team_id, "archived")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"team_id": team_id, "status": "archived"}


# ------------------------------------------------------------------
# Milestone endpoints
# ------------------------------------------------------------------


@router.post("/teams/{team_id}/milestones")
def create_milestone(team_id: str, body: MilestoneCreateRequest) -> dict[str, Any]:
    """Create a milestone under a team."""
    store = get_store()
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title must not be empty")

    try:
        milestone = store.create_milestone(
            team_id=team_id,
            title=title,
            description=body.description,
            dependency_ids=body.dependency_ids,
            acceptance_criteria=body.acceptance_criteria,
        )
    except Exception as exc:
        _log.exception("webui_milestone_create_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to create milestone: {exc}") from exc

    return _record_dict(milestone)


@router.patch("/milestones/{milestone_id}")
def update_milestone_status(milestone_id: str, body: MilestoneStatusRequest) -> dict[str, Any]:
    """Update milestone status."""
    store = get_store()
    milestone = store.get_milestone(milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")

    if body.status not in MilestoneState.__members__.values():
        valid = ", ".join(s.value for s in MilestoneState)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid milestone status: {body.status!r}. Valid: {valid}",
        )
    allowed = MILESTONE_STATE_TRANSITIONS.get(milestone.status, frozenset())
    if body.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition milestone from {milestone.status!r} to {body.status!r}",
        )

    try:
        store.update_milestone_status(milestone_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    updated = store.get_milestone(milestone_id)
    return _record_dict(updated)
