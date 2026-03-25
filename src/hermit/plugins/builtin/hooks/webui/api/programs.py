"""WebUI API router for program management."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner, get_store

_log = structlog.get_logger()

router = APIRouter(tags=["programs"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ProgramCreateRequest(BaseModel):
    title: str
    goal: str
    description: str = ""
    priority: str = "normal"


class ProgramUpdateRequest(BaseModel):
    title: str | None = None
    goal: str | None = None
    description: str | None = None
    priority: str | None = None
    metadata: dict[str, Any] | None = None


class ProgramStatusRequest(BaseModel):
    status: str


class ProgramTaskSubmitRequest(BaseModel):
    description: str
    policy_profile: str | None = None
    team_id: str | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _record_dict(record: Any) -> dict[str, Any]:
    """Convert a dataclass record to a JSON-safe dict."""
    if isinstance(record, dict):
        return record
    d = {k: v for k, v in record.__dict__.items() if not k.startswith("_")}
    return d


def _record_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of record objects to dicts."""
    return [_record_dict(r) for r in records]


def _role_assembly_to_dict(assembly: dict[str, Any]) -> dict[str, Any]:
    """Convert role_assembly (with RoleSlotSpec values) to plain dicts."""
    result: dict[str, Any] = {}
    for key, val in assembly.items():
        if hasattr(val, "role"):
            result[key] = {"role": val.role, "count": val.count, "config": val.config}
        else:
            result[key] = val
    return result


def _get_program_or_404(program_id: str) -> Any:
    """Fetch a program by ID or raise 404."""
    store = get_store()
    program = store.get_program(program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


def _get_program_task_ids(program_id: str) -> list[str]:
    """Get all task IDs belonging to a program."""
    store = get_store()
    tasks = store.list_tasks_by_program(program_id, limit=10000)
    return [t.task_id for t in tasks]


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/programs")
def list_programs(
    status: str | None = Query(None, description="Filter by program status"),
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=200, description="Max programs to return"),
    offset: int = Query(0, ge=0, description="Number of programs to skip"),
) -> dict[str, Any]:
    """List programs with optional filters and pagination."""
    store = get_store()
    kwargs: dict[str, Any] = {"limit": limit + offset}
    if status:
        kwargs["status"] = status
    if priority:
        kwargs["priority"] = priority
    programs = store.list_programs(**kwargs)
    programs = programs[offset:]
    return {
        "programs": _record_dicts(programs),
        "limit": limit,
        "offset": offset,
        "count": len(programs),
    }


@router.post("/programs")
def create_program(body: ProgramCreateRequest) -> dict[str, Any]:
    """Create a new program."""
    store = get_store()
    title = body.title.strip()
    goal = body.goal.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title must not be empty")
    if not goal:
        raise HTTPException(status_code=422, detail="Goal must not be empty")

    try:
        program = store.create_program(
            title=title,
            goal=goal,
            description=body.description,
            priority=body.priority,
        )
    except Exception as exc:
        _log.exception("webui_program_create_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to create program: {exc}") from exc

    return _record_dict(program)


@router.get("/programs/{program_id}")
def get_program(program_id: str) -> dict[str, Any]:
    """Get program detail with team count."""
    store = get_store()
    program = _get_program_or_404(program_id)
    teams = store.list_teams_by_program(program_id=program_id, limit=1000)
    result = _record_dict(program)
    result["team_count"] = len(teams)
    return {"program": result}


@router.patch("/programs/{program_id}")
def update_program(program_id: str, body: ProgramUpdateRequest) -> dict[str, Any]:
    """Update program fields."""
    store = get_store()
    _get_program_or_404(program_id)

    # Build SQL SET clause for scalar fields
    updates: list[str] = []
    params: list[Any] = []
    if body.title is not None:
        updates.append("title = ?")
        params.append(body.title.strip())
    if body.goal is not None:
        updates.append("goal = ?")
        params.append(body.goal.strip())
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)
    if body.priority is not None:
        updates.append("priority = ?")
        params.append(body.priority)

    if updates:
        now = time.time()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(program_id)
        set_clause = ", ".join(updates)
        conn = store._get_conn()
        with conn:
            conn.execute(
                f"UPDATE programs SET {set_clause} WHERE program_id = ?",
                params,
            )

    # Handle metadata merge separately via the store method
    if body.metadata is not None:
        store.update_program_metadata(program_id, body.metadata)

    updated = store.get_program(program_id)
    return _record_dict(updated)


@router.post("/programs/{program_id}/status")
def transition_program_status(program_id: str, body: ProgramStatusRequest) -> dict[str, Any]:
    """Transition program status."""
    _get_program_or_404(program_id)
    store = get_store()

    try:
        store.update_program_status(program_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    updated = store.get_program(program_id)
    return _record_dict(updated)


@router.get("/programs/{program_id}/tasks")
def list_program_tasks(
    program_id: str,
    status: str | None = Query(None, description="Filter by task status"),
    limit: int = Query(50, ge=1, le=500, description="Max tasks to return"),
) -> dict[str, Any]:
    """List tasks scoped to a program."""
    _get_program_or_404(program_id)
    store = get_store()
    tasks = store.list_tasks_by_program(program_id, status=status, limit=limit)
    return {
        "program_id": program_id,
        "tasks": _record_dicts(tasks),
        "count": len(tasks),
    }


@router.get("/programs/{program_id}/memory")
def list_program_memory(
    program_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max memory records"),
) -> dict[str, Any]:
    """List memory records for a program's tasks."""
    _get_program_or_404(program_id)
    task_ids = _get_program_task_ids(program_id)
    if not task_ids:
        return {"program_id": program_id, "memories": [], "count": 0}

    store = get_store()
    task_id_set = set(task_ids)
    all_records = store.list_memory_records(limit=500)
    matched = [r.__dict__ for r in all_records if getattr(r, "task_id", None) in task_id_set][
        :limit
    ]

    return {
        "program_id": program_id,
        "memories": matched,
        "count": len(matched),
    }


@router.get("/programs/{program_id}/signals")
def list_program_signals(
    program_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max signals"),
) -> dict[str, Any]:
    """List signals for a program's tasks."""
    _get_program_or_404(program_id)
    task_ids = _get_program_task_ids(program_id)
    if not task_ids:
        return {"program_id": program_id, "signals": [], "count": 0}

    store = get_store()
    task_id_set = set(task_ids)
    all_signals = store.list_signals(limit=500)
    matched = [s.__dict__ for s in all_signals if getattr(s, "task_id", None) in task_id_set][
        :limit
    ]

    return {
        "program_id": program_id,
        "signals": matched,
        "count": len(matched),
    }


@router.get("/programs/{program_id}/approvals")
def list_program_approvals(
    program_id: str,
    status: str | None = Query(None, description="Filter by approval status"),
    limit: int = Query(50, ge=1, le=200, description="Max approvals"),
) -> dict[str, Any]:
    """List approvals for a program's tasks."""
    _get_program_or_404(program_id)
    task_ids = _get_program_task_ids(program_id)
    if not task_ids:
        return {"program_id": program_id, "approvals": [], "count": 0}

    store = get_store()
    results: list[dict[str, Any]] = []
    for task_id in task_ids:
        approvals = store.list_approvals(task_id=task_id, status=status, limit=limit)
        for a in approvals:
            results.append({k: v for k, v in a.__dict__.items() if not k.startswith("_")})
        if len(results) >= limit:
            break

    return {
        "program_id": program_id,
        "approvals": results[:limit],
        "count": len(results[:limit]),
    }


@router.post("/programs/{program_id}/tasks")
async def submit_program_task(program_id: str, body: ProgramTaskSubmitRequest) -> dict[str, Any]:
    """Submit a task under this program."""
    _get_program_or_404(program_id)
    runner = get_runner()

    session_id = f"webui-prog-{uuid4().hex[:8]}"
    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="Description must not be empty")

    policy_profile = body.policy_profile or "autonomous"

    def _enqueue() -> str | None:
        ingress_meta: dict[str, Any] = {
            "source": "webui",
            "entry_prompt": description,
            "policy_profile": policy_profile,
            "program_id": program_id,
        }
        if body.team_id:
            ingress_meta["team_id"] = body.team_id

        if body.team_id:
            # Team-aware path: decompose into DAG based on team topology.
            store = get_store()
            team = store.get_team(body.team_id)
            if team is None:
                raise HTTPException(status_code=404, detail="Team not found")

            from hermit.kernel.task.services.team_decomposer import decompose_team_to_steps

            step_nodes = decompose_team_to_steps(team=team, goal=description)
            ctx, _dag, _key_map, _root_ctxs = runner.task_controller.start_dag_task(
                conversation_id=session_id,
                goal=description,
                source_channel="webui",
                nodes=step_nodes,
                policy_profile=policy_profile,
                workspace_root=str(getattr(runner.agent, "workspace_root", "") or ""),
                requested_by="webui",
                ingress_metadata=ingress_meta,
                team_id=body.team_id,
            )
            tid = getattr(ctx, "task_id", None) if ctx else None

            # Associate the task with the program.
            if tid:
                store_conn = store._get_conn()
                with store_conn:
                    store_conn.execute(
                        "UPDATE tasks SET program_id = ? WHERE task_id = ?",
                        (program_id, tid),
                    )
        else:
            # Original non-team path: single "respond" step.
            ctx = runner.task_controller.enqueue_task(
                conversation_id=session_id,
                goal=description,
                source_channel="webui",
                kind="respond",
                policy_profile=policy_profile,
                workspace_root=str(getattr(runner.agent, "workspace_root", "") or ""),
                parent_task_id=None,
                requested_by="webui",
                ingress_metadata=ingress_meta,
                source_ref="webui",
            )
            tid = getattr(ctx, "task_id", None) if ctx else None

            # Associate the task with the program.
            if tid:
                store = get_store()
                conn = store._get_conn()
                with conn:
                    conn.execute(
                        "UPDATE tasks SET program_id = ? WHERE task_id = ?",
                        (program_id, tid),
                    )

        runner.wake_dispatcher()
        return tid

    try:
        task_id = await asyncio.to_thread(_enqueue)
    except Exception as exc:
        _log.exception("webui_program_task_submit_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to submit task: {exc}") from exc

    return {
        "task_id": task_id,
        "program_id": program_id,
        "session_id": session_id,
        "status": "queued",
        "policy_profile": policy_profile,
    }
