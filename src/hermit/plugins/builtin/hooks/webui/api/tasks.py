"""Tasks API router for WebUI — task listing, detail, submission, and governance endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermit.kernel.execution.controller.supervision import SupervisionService
from hermit.kernel.signals.models import SteeringDirective
from hermit.kernel.signals.steering import SteeringProtocol
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.kernel.verification.rollbacks.rollbacks import RollbackService
from hermit.plugins.builtin.hooks.webui.api.deps import get_runner, get_store

_log = structlog.get_logger()

_ACTION_LABEL_MAX = 120

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TaskSubmitRequest(BaseModel):
    description: str
    policy_profile: str | None = None
    attachments: list[str] | None = None


class TaskSteerRequest(BaseModel):
    message: str


class TaskCancelRequest(BaseModel):
    reason: str = ""


class ProofExportRequest(BaseModel):
    detail: str = "standard"


class RollbackRequest(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_dict(task: Any) -> dict[str, Any]:
    """Convert a TaskRecord to a JSON-safe dict."""
    d = dict(task.__dict__)
    # Drop internal SQLAlchemy / dataclass state if present
    d.pop("_sa_instance_state", None)
    return d


def _record_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of record objects to dicts."""
    result: list[dict[str, Any]] = []
    for r in records:
        if isinstance(r, dict):
            result.append(r)
        else:
            d = dict(r.__dict__)
            d.pop("_sa_instance_state", None)
            result.append(d)
    return result


def _extract_action_label(store: Any, action_request_ref: str | None) -> str | None:
    """Derive a one-line action label from the action_request artifact.

    For bash/execute_command this returns the command string; for file tools
    it returns the target path.  Returns ``None`` when unavailable.
    """
    if not action_request_ref:
        return None
    try:
        artifact = store.get_artifact(action_request_ref)
        if artifact is None:
            return None
        payload = json.loads(Path(artifact.uri).read_text(encoding="utf-8"))
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        # bash / execute_command → command string
        cmd = tool_input.get("command") or tool_input.get("cmd")
        if cmd:
            return cmd[:_ACTION_LABEL_MAX]
        # file tools → path
        path = tool_input.get("path") or tool_input.get("file_path")
        if path:
            return path
        # write_file → target path
        target = tool_input.get("target")
        if target:
            return target
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


@router.get("/tasks")
def list_tasks(
    status: str | None = Query(None, description="Filter by task status"),
    limit: int = Query(20, ge=1, le=200, description="Maximum number of tasks to return"),
    offset: int = Query(0, ge=0, description="Number of tasks to skip"),
) -> dict[str, Any]:
    """List tasks with optional status filter and pagination."""
    store = get_store()

    kwargs: dict[str, Any] = {"limit": limit + offset}
    if status:
        kwargs["status"] = status
    tasks = store.list_tasks(**kwargs)

    # Apply offset
    tasks = tasks[offset:]

    return {
        "tasks": _record_dicts(tasks),
        "limit": limit,
        "offset": offset,
        "count": len(tasks),
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    """Get task detail with steps and pending approvals."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    steps = store.list_steps(task_id=task_id)
    approvals = store.list_approvals(task_id=task_id, limit=50)

    return {
        "task": _task_dict(task),
        "steps": _record_dicts(steps),
        "approvals": _record_dicts(approvals),
    }


@router.get("/tasks/{task_id}/steps")
def list_steps(
    task_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max step attempts to return"),
) -> dict[str, Any]:
    """Get steps and step attempts for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    steps = store.list_steps(task_id=task_id)
    attempts = store.list_step_attempts(task_id=task_id, limit=limit)

    return {
        "task_id": task_id,
        "steps": _record_dicts(steps),
        "attempts": _record_dicts(attempts),
    }


@router.get("/tasks/{task_id}/events")
def list_events(
    task_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
) -> dict[str, Any]:
    """Get event history for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    events = store.list_events(task_id=task_id, limit=limit)
    return {
        "task_id": task_id,
        "events": _record_dicts(events),
    }


@router.get("/tasks/{task_id}/receipts")
def list_receipts(task_id: str) -> dict[str, Any]:
    """Get receipts for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    receipts = store.list_receipts(task_id=task_id)
    result = _record_dicts(receipts)
    for i, r in enumerate(receipts):
        ref = getattr(r, "action_request_ref", None)
        label = _extract_action_label(store, ref)
        if label is not None:
            result[i]["action_label"] = label
    return {
        "task_id": task_id,
        "receipts": result,
    }


@router.get("/tasks/{task_id}/proof")
def get_proof(task_id: str) -> dict[str, Any]:
    """Get proof summary for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return ProofService(store).build_proof_summary(task_id)


@router.get("/tasks/{task_id}/case")
def get_case(task_id: str) -> dict[str, Any]:
    """Get supervision case for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return SupervisionService(store).build_task_case(task_id)


# ---------------------------------------------------------------------------
# POST endpoints
# ---------------------------------------------------------------------------


@router.post("/tasks")
def submit_task(body: TaskSubmitRequest) -> dict[str, Any]:
    """Submit a new task for execution."""
    runner = get_runner()

    session_id = f"webui-task-{uuid4().hex[:8]}"
    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="Description must not be empty")

    policy_profile = body.policy_profile or "autonomous"

    try:
        ctx = runner.task_controller.enqueue_task(
            conversation_id=session_id,
            goal=description,
            source_channel="webui",
            kind="respond",
            policy_profile=policy_profile,
            workspace_root=str(getattr(runner.agent, "workspace_root", "") or ""),
            parent_task_id=None,
            requested_by="webui",
            ingress_metadata={
                "source": "webui",
                "entry_prompt": description,
                "policy_profile": policy_profile,
                **({"attachments": body.attachments} if body.attachments else {}),
            },
            source_ref="webui",
        )
        runner.wake_dispatcher()
    except Exception as exc:
        _log.exception("webui_task_submit_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to submit task: {exc}") from exc

    task_id = getattr(ctx, "task_id", None) if ctx else None

    return {
        "task_id": task_id,
        "session_id": session_id,
        "status": "queued",
        "policy_profile": policy_profile,
    }


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: TaskCancelRequest | None = None) -> dict[str, Any]:
    """Cancel a running task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Task already in terminal state: {task.status}",
        )

    reason = body.reason if body else ""
    store.update_task_status(
        task_id,
        "cancelled",
        payload={"reason": reason, "cancelled_by": "webui"},
    )

    return {
        "task_id": task_id,
        "status": "cancelled",
        "reason": reason,
    }


@router.post("/tasks/{task_id}/proof/export")
def export_proof(task_id: str, body: ProofExportRequest | None = None) -> dict[str, Any]:
    """Export proof bundle for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    detail = body.detail if body else "standard"
    return ProofService(store).export_task_proof(task_id, detail=detail)


@router.get("/tasks/{task_id}/output")
def get_task_output(task_id: str) -> dict[str, Any]:
    """Get task execution output — actions taken and their results."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get LLM response text — check task.completed, then step events
    response_text = ""

    # 1. Try task.completed event payload
    terminal_events = store.list_events(task_id=task_id, event_type="task.completed", limit=1)
    if terminal_events:
        ev = terminal_events[0]
        payload = (
            ev.get("payload", {}) if isinstance(ev, dict) else (getattr(ev, "payload", {}) or {})
        )
        if isinstance(payload, dict):
            response_text = payload.get("result_text", "") or ""

    # 2. Fallback: check task.result_text_attached (late-arriving LLM response)
    if not response_text:
        attached_events = store.list_events(
            task_id=task_id, event_type="task.result_text_attached", limit=1
        )
        for aev in attached_events:
            payload = (
                aev.get("payload", {})
                if isinstance(aev, dict)
                else (getattr(aev, "payload", {}) or {})
            )
            if isinstance(payload, dict) and payload.get("result_text"):
                response_text = payload["result_text"]
                break

    # 3. Fallback: check step.updated events for result_text
    if not response_text:
        step_events = store.list_events(task_id=task_id, event_type="step.updated", limit=20)
        for sev in reversed(step_events):
            payload = (
                sev.get("payload", {})
                if isinstance(sev, dict)
                else (getattr(sev, "payload", {}) or {})
            )
            if isinstance(payload, dict) and payload.get("result_text"):
                response_text = payload["result_text"]
                break

    # 3. Fallback: check step_attempt.updated for result_text in context
    if not response_text:
        attempt_events = store.list_events(
            task_id=task_id, event_type="step_attempt.updated", limit=5
        )
        for aev in reversed(attempt_events):
            payload = (
                aev.get("payload", {})
                if isinstance(aev, dict)
                else (getattr(aev, "payload", {}) or {})
            )
            if isinstance(payload, dict):
                ctx = payload.get("context", {})
                if isinstance(ctx, dict) and ctx.get("result_text"):
                    response_text = ctx["result_text"]
                    break

    # 4. Fallback: check task.failed event payload
    if not response_text:
        failed_events = store.list_events(task_id=task_id, event_type="task.failed", limit=1)
        if failed_events:
            ev = failed_events[0]
            payload = (
                ev.get("payload", {})
                if isinstance(ev, dict)
                else (getattr(ev, "payload", {}) or {})
            )
            if isinstance(payload, dict):
                response_text = payload.get("result_text", "") or payload.get("error", "") or ""

    # 5. Fallback: check step.completed events for result_text
    if not response_text:
        step_complete_events = store.list_events(
            task_id=task_id, event_type="step.completed", limit=10
        )
        for sev in reversed(step_complete_events):
            payload = (
                sev.get("payload", {})
                if isinstance(sev, dict)
                else (getattr(sev, "payload", {}) or {})
            )
            if isinstance(payload, dict) and payload.get("result_text"):
                response_text = payload["result_text"]
                break

    # 6. Fallback: build response from receipt result_summary
    # (applied after receipts are collected below)

    # Get receipts as the primary output
    receipts = store.list_receipts(task_id=task_id)
    receipt_summaries = []
    for r in receipts:
        entry: dict[str, Any] = {
            "action_type": getattr(r, "action_type", None),
            "result_code": getattr(r, "result_code", None),
            "result_summary": getattr(r, "result_summary", None),
            "observed_effect_summary": getattr(r, "observed_effect_summary", None),
            "rollback_supported": getattr(r, "rollback_supported", False),
            "receipt_id": getattr(r, "receipt_id", None),
        }
        label = _extract_action_label(store, getattr(r, "action_request_ref", None))
        if label is not None:
            entry["action_label"] = label
        receipt_summaries.append(entry)

    # 6. Apply receipt-based fallback
    if not response_text and receipt_summaries:
        summaries = [s["result_summary"] for s in receipt_summaries if s.get("result_summary")]
        if summaries:
            response_text = summaries[-1]

    return {
        "task_id": task_id,
        "status": task.status,
        "title": task.title,
        "goal": task.goal,
        "response_text": response_text,
        "receipts": receipt_summaries,
        "total_actions": len(receipt_summaries),
    }


@router.post("/tasks/{task_id}/rollback")
def rollback_task(task_id: str) -> dict[str, Any]:
    """Rollback all rollback-supported actions for a task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    receipts = store.list_receipts(task_id=task_id)
    rollback_results = []
    for r in receipts:
        if getattr(r, "rollback_supported", False):
            try:
                result = RollbackService(store).execute(r.receipt_id)
                rollback_results.append(
                    {"receipt_id": r.receipt_id, "status": "rolled_back", **result}
                )
            except Exception as e:
                rollback_results.append(
                    {"receipt_id": r.receipt_id, "status": "error", "error": str(e)}
                )

    return {
        "task_id": task_id,
        "rollbacks": rollback_results,
        "total": len(rollback_results),
    }


@router.post("/tasks/{task_id}/steer")
def steer_task(task_id: str, body: TaskSteerRequest) -> dict[str, Any]:
    """Send a steering directive to a running task."""
    store = get_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail="Task already in terminal state")

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message must not be empty")

    try:
        protocol = SteeringProtocol(store)
        directive = SteeringDirective(
            task_id=task_id,
            steering_type="scope",
            directive=message,
            issued_by="webui",
        )
        protocol.issue(directive)
        return {
            "task_id": task_id,
            "directive_id": directive.directive_id,
            "status": "steered",
        }
    except Exception as exc:
        _log.exception("webui_task_steer_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/receipts/{receipt_id}/rollback")
def rollback_receipt(receipt_id: str, body: RollbackRequest | None = None) -> dict[str, Any]:
    """Rollback a receipt."""
    store = get_store()
    return RollbackService(store).execute(receipt_id)
