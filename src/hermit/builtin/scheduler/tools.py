"""Agent-facing tools for managing scheduled tasks."""

from __future__ import annotations

import datetime
import time
from typing import Any

from hermit.builtin.scheduler.engine import SchedulerEngine
from hermit.builtin.scheduler.models import ScheduledJob
from hermit.core.tools import ToolSpec
from hermit.i18n import resolve_locale, tr
from hermit.plugin.base import PluginContext

_engine: SchedulerEngine | None = None


def set_engine(engine: SchedulerEngine) -> None:
    global _engine
    _engine = engine


def _require_engine() -> SchedulerEngine:
    if _engine is None:
        raise RuntimeError(_t("tools.scheduler.common.engine_not_running"))
    return _engine


def _locale() -> str:
    settings = getattr(_engine, "_settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=_locale(), default=default, **kwargs)


def _format_time(ts: float | None) -> str:
    if ts is None:
        return _t("tools.scheduler.common.not_available", default="N/A")
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _handle_create(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    name = str(payload.get("name", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()
    schedule_type = str(payload.get("schedule_type", "")).strip()

    if not name or not prompt or not schedule_type:
        return _t("tools.scheduler.create.error.required")

    if schedule_type not in ("cron", "once", "interval"):
        return _t("tools.scheduler.create.error.schedule_type")

    cron_expr = payload.get("cron_expr")
    once_at_str = payload.get("once_at")
    interval_seconds = payload.get("interval_seconds")

    once_at: float | None = None
    if schedule_type == "cron":
        if not cron_expr:
            return _t("tools.scheduler.create.error.cron_required")
        try:
            from croniter import croniter

            croniter(cron_expr)
        except (ValueError, KeyError) as exc:
            return _t("tools.scheduler.create.error.invalid_cron", cron_expr=cron_expr, error=exc)

    elif schedule_type == "once":
        if not once_at_str:
            return _t("tools.scheduler.create.error.once_required")
        try:
            dt = datetime.datetime.fromisoformat(str(once_at_str))
            once_at = dt.timestamp()
        except ValueError:
            return _t("tools.scheduler.create.error.invalid_datetime", once_at=once_at_str)
        if once_at <= time.time():
            return _t("tools.scheduler.create.error.once_past")

    elif schedule_type == "interval":
        if not interval_seconds or int(interval_seconds) < 60:
            return _t("tools.scheduler.create.error.interval_required")
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

    return _t(
        "tools.scheduler.create.success",
        job_id=job.id,
        name=job.name,
        schedule_type=job.schedule_type,
        next_run=_format_time(job.next_run_at),
    )


def _handle_list(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    jobs = engine.list_jobs()
    if not jobs:
        return _t("tools.scheduler.list.empty")

    lines = [_t("tools.scheduler.list.title", count=len(jobs))]
    for j in jobs:
        status = (
            _t("tools.scheduler.list.status.enabled")
            if j.enabled
            else _t("tools.scheduler.list.status.disabled")
        )
        schedule_info = j.cron_expr or (
            _t("tools.scheduler.list.once_at", time=_format_time(j.once_at))
            if j.once_at
            else _t("tools.scheduler.list.every_seconds", seconds=j.interval_seconds)
            if j.interval_seconds
            else _t("tools.scheduler.list.unknown")
        )
        lines.append(
            f"  [{j.id}] {j.name} ({status})\n"
            f"    {_t('tools.scheduler.list.schedule_label')}: {schedule_info}\n"
            f"    {_t('tools.scheduler.list.next_run_label')}: {_format_time(j.next_run_at)}\n"
            f"    {_t('tools.scheduler.list.last_run_label')}: {_format_time(j.last_run_at)}"
        )
    return "\n".join(lines)


def _handle_delete(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip()
    if not job_id:
        return _t("tools.scheduler.delete.error.required")
    if engine.remove_job(job_id):
        return _t("tools.scheduler.delete.success", job_id=job_id)
    return _t("tools.scheduler.common.not_found", job_id=job_id)


def _handle_history(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip() or None
    limit = int(payload.get("limit", 20))
    records = engine.get_history(job_id=job_id, limit=limit)

    if not records:
        return _t("tools.scheduler.history.empty")

    lines = [_t("tools.scheduler.history.title", count=len(records))]
    for r in records:
        status = "✓" if r.success else "✗"
        started = datetime.datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M:%S")
        duration = r.finished_at - r.started_at
        lines.append(f"  [{status}] {r.job_name} @ {started} ({duration:.1f}s)")
        if r.result_text:
            preview = r.result_text[:200].replace("\n", " ")
            lines.append(f"      {preview}")
        if r.error:
            lines.append(f"      {_t('tools.scheduler.history.error_label')}: {r.error}")
    return "\n".join(lines)


def _handle_update(payload: dict[str, Any]) -> str:
    engine = _require_engine()
    job_id = str(payload.get("job_id", "")).strip()
    if not job_id:
        return _t("tools.scheduler.update.error.required")

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
            return _t("tools.scheduler.create.error.invalid_cron", cron_expr=cron_expr, error=exc)
        updates["cron_expr"] = cron_expr
    if "feishu_chat_id" in payload:
        updates["feishu_chat_id"] = str(payload["feishu_chat_id"]).strip() or None

    if not updates:
        return _t("tools.scheduler.update.error.no_fields")

    job = engine.update_job(job_id, **updates)
    if job is None:
        return _t("tools.scheduler.common.not_found", job_id=job_id)
    return _t(
        "tools.scheduler.update.success",
        name=job.name,
        job_id=job.id,
        next_run=_format_time(job.next_run_at),
    )


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="schedule_create",
            description=(
                "Create a new scheduled task that will execute an agent prompt at specified times. "
                "Supports cron expressions (e.g. '0 9 * * 1-5' for weekdays at 9am), "
                "one-time execution, and fixed intervals. "
                "IMPORTANT: When called from a Feishu chat, always pass the feishu_chat_id "
                "from <feishu_chat_id>...</feishu_chat_id> in the context so results are pushed back to this chat."
            ),
            description_key="tools.scheduler.create.description",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description_key": "tools.scheduler.create.name",
                    },
                    "prompt": {
                        "type": "string",
                        "description_key": "tools.scheduler.create.prompt",
                    },
                    "schedule_type": {
                        "type": "string",
                        "enum": ["cron", "once", "interval"],
                        "description_key": "tools.scheduler.create.schedule_type",
                    },
                    "cron_expr": {
                        "type": "string",
                        "description_key": "tools.scheduler.create.cron_expr",
                    },
                    "once_at": {
                        "type": "string",
                        "description_key": "tools.scheduler.create.once_at",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description_key": "tools.scheduler.create.interval_seconds",
                    },
                    "max_retries": {
                        "type": "integer",
                        "description_key": "tools.scheduler.create.max_retries",
                    },
                    "feishu_chat_id": {
                        "type": "string",
                        "description_key": "tools.scheduler.create.feishu_chat_id",
                    },
                },
                "required": ["name", "prompt", "schedule_type"],
            },
            handler=_handle_create,
            action_class="scheduler_mutation",
            risk_hint="medium",
            requires_receipt=True,
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="schedule_list",
            description="List all scheduled tasks with their status, schedule, and next/last run times.",
            description_key="tools.scheduler.list.description",
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
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="schedule_delete",
            description="Delete a scheduled task by its ID.",
            description_key="tools.scheduler.delete.description",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description_key": "tools.scheduler.delete.job_id",
                    },
                },
                "required": ["job_id"],
            },
            handler=_handle_delete,
            action_class="scheduler_mutation",
            risk_hint="medium",
            requires_receipt=True,
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="schedule_update",
            description="Update an existing scheduled task's name, prompt, enabled status, or cron expression.",
            description_key="tools.scheduler.update.description",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description_key": "tools.scheduler.update.job_id",
                    },
                    "name": {"type": "string", "description_key": "tools.scheduler.update.name"},
                    "prompt": {
                        "type": "string",
                        "description_key": "tools.scheduler.update.prompt",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description_key": "tools.scheduler.update.enabled",
                    },
                    "cron_expr": {
                        "type": "string",
                        "description_key": "tools.scheduler.update.cron_expr",
                    },
                    "feishu_chat_id": {
                        "type": "string",
                        "description_key": "tools.scheduler.update.feishu_chat_id",
                    },
                },
                "required": ["job_id"],
            },
            handler=_handle_update,
            action_class="scheduler_mutation",
            risk_hint="medium",
            requires_receipt=True,
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="schedule_history",
            description=(
                "Query the execution history of scheduled tasks. "
                "Use this to answer questions like 'how many times has a task run', "
                "'did it succeed', 'when was the last execution', or 'show me the results'. "
                "Returns recent execution records with timestamps, duration, and result previews."
            ),
            description_key="tools.scheduler.history.description",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description_key": "tools.scheduler.history.job_id",
                    },
                    "limit": {
                        "type": "integer",
                        "description_key": "tools.scheduler.history.limit",
                    },
                },
            },
            handler=_handle_history,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
