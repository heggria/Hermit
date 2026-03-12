"""Agent-facing tools for managing scheduled tasks."""
from __future__ import annotations

import datetime
import time
from typing import Any

from hermit.builtin.scheduler.engine import SchedulerEngine
from hermit.builtin.scheduler.models import ScheduledJob
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext

_engine: SchedulerEngine | None = None


def set_engine(engine: SchedulerEngine) -> None:
    global _engine
    _engine = engine


def _require_engine() -> SchedulerEngine:
    if _engine is None:
        raise RuntimeError(
            "Scheduler engine not running. "
            "Scheduled tasks require `hermit serve` to be active."
        )
    return _engine


def _format_time(ts: float | None) -> str:
    if ts is None:
        return "N/A"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _handle_create(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    name = str(payload.get("name", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()
    schedule_type = str(payload.get("schedule_type", "")).strip()

    if not name or not prompt or not schedule_type:
        return "Error: name, prompt, and schedule_type are required."

    if schedule_type not in ("cron", "once", "interval"):
        return "Error: schedule_type must be 'cron', 'once', or 'interval'."

    cron_expr = payload.get("cron_expr")
    once_at_str = payload.get("once_at")
    interval_seconds = payload.get("interval_seconds")

    once_at: float | None = None
    if schedule_type == "cron":
        if not cron_expr:
            return "Error: cron_expr is required for schedule_type 'cron'."
        try:
            from croniter import croniter
            croniter(cron_expr)
        except (ValueError, KeyError) as exc:
            return f"Error: invalid cron expression '{cron_expr}': {exc}"

    elif schedule_type == "once":
        if not once_at_str:
            return "Error: once_at is required for schedule_type 'once'."
        try:
            dt = datetime.datetime.fromisoformat(str(once_at_str))
            once_at = dt.timestamp()
        except ValueError:
            return f"Error: invalid datetime format '{once_at_str}'. Use ISO format like '2026-03-15T14:00:00'."
        if once_at <= time.time():
            return "Error: once_at must be in the future."

    elif schedule_type == "interval":
        if not interval_seconds or int(interval_seconds) < 60:
            return "Error: interval_seconds is required and must be >= 60."
        interval_seconds = int(interval_seconds)

    feishu_chat_id = str(payload.get("feishu_chat_id", "") or "").strip() or None

    job = ScheduledJob.create(
        name=name,
        prompt=prompt,
        schedule_type=schedule_type,
        cron_expr=cron_expr,
        once_at=once_at,
        interval_seconds=interval_seconds,
        max_retries=int(payload.get("max_retries", 1)),
        feishu_chat_id=feishu_chat_id,
    )
    engine.add_job(job)

    return (
        f"Scheduled task created:\n"
        f"  ID: {job.id}\n"
        f"  Name: {job.name}\n"
        f"  Type: {job.schedule_type}\n"
        f"  Next run: {_format_time(job.next_run_at)}"
    )


def _handle_list(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    jobs = engine.list_jobs()
    if not jobs:
        return "No scheduled tasks."

    lines = [f"Scheduled tasks ({len(jobs)}):"]
    for j in jobs:
        status = "enabled" if j.enabled else "disabled"
        schedule_info = j.cron_expr or (
            f"once at {_format_time(j.once_at)}" if j.once_at
            else f"every {j.interval_seconds}s" if j.interval_seconds
            else "unknown"
        )
        lines.append(
            f"  [{j.id}] {j.name} ({status})\n"
            f"    Schedule: {schedule_info}\n"
            f"    Next run: {_format_time(j.next_run_at)}\n"
            f"    Last run: {_format_time(j.last_run_at)}"
        )
    return "\n".join(lines)


def _handle_delete(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip()
    if not job_id:
        return "Error: job_id is required."
    if engine.remove_job(job_id):
        return f"Deleted scheduled task '{job_id}'."
    return f"Error: no task with id '{job_id}' found."


def _handle_history(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip() or None
    limit = int(payload.get("limit", 20))
    records = engine.get_history(job_id=job_id, limit=limit)

    if not records:
        return "No execution history found."

    lines = [f"Execution history ({len(records)} records):"]
    for r in records:
        status = "✓" if r.success else "✗"
        started = datetime.datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M:%S")
        duration = r.finished_at - r.started_at
        lines.append(f"  [{status}] {r.job_name} @ {started} ({duration:.1f}s)")
        if r.result_text:
            preview = r.result_text[:200].replace("\n", " ")
            lines.append(f"      {preview}")
        if r.error:
            lines.append(f"      Error: {r.error}")
    return "\n".join(lines)


def _handle_update(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip()
    if not job_id:
        return "Error: job_id is required."

    updates: dict[str, Any] = {}
    if "name" in payload:
        updates["name"] = str(payload["name"])
    if "prompt" in payload:
        updates["prompt"] = str(payload["prompt"])
    if "enabled" in payload:
        updates["enabled"] = bool(payload["enabled"])
    if "cron_expr" in payload:
        cron_expr = str(payload["cron_expr"])
        try:
            from croniter import croniter
            croniter(cron_expr)
        except (ValueError, KeyError) as exc:
            return f"Error: invalid cron expression '{cron_expr}': {exc}"
        updates["cron_expr"] = cron_expr
    if "feishu_chat_id" in payload:
        updates["feishu_chat_id"] = str(payload["feishu_chat_id"]).strip() or None

    if not updates:
        return "Error: no fields to update. Provide name, prompt, enabled, cron_expr, or feishu_chat_id."

    job = engine.update_job(job_id, **updates)
    if job is None:
        return f"Error: no task with id '{job_id}' found."
    return (
        f"Updated task '{job.name}' ({job.id}).\n"
        f"  Next run: {_format_time(job.next_run_at)}"
    )


def register(ctx: PluginContext) -> None:
    ctx.add_tool(ToolSpec(
        name="schedule_create",
        description=(
            "Create a new scheduled task that will execute an agent prompt at specified times. "
            "Supports cron expressions (e.g. '0 9 * * 1-5' for weekdays at 9am), "
            "one-time execution, and fixed intervals. "
            "IMPORTANT: When called from a Feishu chat, always pass the feishu_chat_id "
            "from <feishu_chat_id>...</feishu_chat_id> in the context so results are pushed back to this chat."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name for the task (e.g. '每日站报').",
                },
                "prompt": {
                    "type": "string",
                    "description": "The prompt to send to the agent when the task fires.",
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["cron", "once", "interval"],
                    "description": "Type of schedule: 'cron' for cron expressions, 'once' for one-time, 'interval' for fixed intervals.",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression (required for type 'cron'). E.g. '0 9 * * *' = daily 9am, '0 9 * * 1-5' = weekdays 9am.",
                },
                "once_at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (required for type 'once'). E.g. '2026-03-15T14:00:00'.",
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (required for type 'interval', minimum 60).",
                },
                "max_retries": {
                    "type": "integer",
                    "description": "Number of retry attempts on failure (default 1).",
                },
                "feishu_chat_id": {
                    "type": "string",
                    "description": (
                        "Feishu chat_id to push results to when the task fires. "
                        "Read from <feishu_chat_id>...</feishu_chat_id> in the current message context. "
                        "Required for Feishu push notifications."
                    ),
                },
            },
            "required": ["name", "prompt", "schedule_type"],
        },
        handler=_handle_create,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))

    ctx.add_tool(ToolSpec(
        name="schedule_list",
        description="List all scheduled tasks with their status, schedule, and next/last run times.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=_handle_list,
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    ))

    ctx.add_tool(ToolSpec(
        name="schedule_delete",
        description="Delete a scheduled task by its ID.",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The ID of the scheduled task to delete.",
                },
            },
            "required": ["job_id"],
        },
        handler=_handle_delete,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))

    ctx.add_tool(ToolSpec(
        name="schedule_update",
        description="Update an existing scheduled task's name, prompt, enabled status, or cron expression.",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The ID of the scheduled task to update.",
                },
                "name": {"type": "string", "description": "New name."},
                "prompt": {"type": "string", "description": "New prompt."},
                "enabled": {"type": "boolean", "description": "Enable or disable the task."},
                "cron_expr": {"type": "string", "description": "New cron expression."},
                "feishu_chat_id": {"type": "string", "description": "Feishu chat_id for push notifications."},
            },
            "required": ["job_id"],
        },
        handler=_handle_update,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))

    ctx.add_tool(ToolSpec(
        name="schedule_history",
        description=(
            "Query the execution history of scheduled tasks. "
            "Use this to answer questions like 'how many times has a task run', "
            "'did it succeed', 'when was the last execution', or 'show me the results'. "
            "Returns recent execution records with timestamps, duration, and result previews."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Filter history by task ID. Omit to show all tasks.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of records to return (default 20).",
                },
            },
        },
        handler=_handle_history,
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    ))
