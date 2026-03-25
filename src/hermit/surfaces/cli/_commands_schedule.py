from __future__ import annotations

import time

import typer

from hermit.kernel.ledger.journal.store import KernelStore

from ._helpers import get_kernel_store
from .main import cli_t, schedule_app, t


def get_schedule_store() -> KernelStore:
    return get_kernel_store()


@schedule_app.command("list")
def schedule_list() -> None:
    """List all scheduled tasks."""
    import datetime

    store = get_schedule_store()
    jobs = store.list_schedules()
    if not jobs:
        typer.echo(t("cli.schedule.list.empty", "No scheduled tasks."))
        return

    def fmt(ts: float | None) -> str:
        if ts is None:
            return t("cli.schedule.common.not_available", "N/A")
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    for j in jobs:
        status = (
            t("cli.schedule.status.enabled", "enabled")
            if j.enabled
            else t("cli.schedule.status.disabled", "disabled")
        )
        schedule_info = j.cron_expr or (
            t("cli.schedule.list.once_at", "once at {time}", time=fmt(j.once_at))
            if j.once_at
            else t(
                "cli.schedule.list.every_seconds",
                "every {seconds}s",
                seconds=j.interval_seconds,
            )
            if j.interval_seconds
            else t("cli.schedule.list.unknown", "unknown")
        )
        typer.echo(
            f"  [{j.id}] {j.name} ({status})\n"
            f"    {t('cli.schedule.list.schedule', 'Schedule')}: {schedule_info}\n"
            f"    {t('cli.schedule.list.next_run', 'Next run')}: {fmt(j.next_run_at)}\n"
            f"    {t('cli.schedule.list.last_run', 'Last run')}: {fmt(j.last_run_at)}"
        )


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Option(..., help=cli_t("cli.schedule.add.name", "Task name.")),
    prompt: str = typer.Option(
        ...,
        help=cli_t("cli.schedule.add.prompt", "Agent prompt to execute."),
    ),
    cron: str | None = typer.Option(
        None,
        help=cli_t("cli.schedule.add.cron", "Cron expression (e.g. '0 9 * * 1-5')."),
    ),
    once: str | None = typer.Option(
        None,
        help=cli_t(
            "cli.schedule.add.once",
            "One-time datetime (ISO format, e.g. '2026-03-15T14:00').",
        ),
    ),
    interval: int | None = typer.Option(
        None,
        help=cli_t("cli.schedule.add.interval", "Interval in seconds (minimum 60)."),
    ),
) -> None:
    """Add a new scheduled task."""
    import datetime as dt

    from hermit.plugins.builtin.hooks.scheduler.models import ScheduledJob

    if sum(x is not None for x in (cron, once, interval)) != 1:
        typer.echo(
            t(
                "cli.schedule.add.error.schedule_choice",
                "Error: specify exactly one of --cron, --once, or --interval.",
            )
        )
        raise typer.Exit(1)

    schedule_type = "cron" if cron else "once" if once else "interval"
    once_at: float | None = None

    name_stripped = name.strip().replace("\n", " ").replace("\r", "")
    if not name_stripped:
        typer.echo(t("cli.schedule.add.error.empty_name", "Error: name cannot be empty."))
        raise typer.Exit(1)
    prompt_stripped = prompt.strip()
    if not prompt_stripped:
        typer.echo(t("cli.schedule.add.error.empty_prompt", "Error: prompt cannot be empty."))
        raise typer.Exit(1)
    name = name_stripped
    prompt = prompt_stripped

    if cron:
        try:
            from croniter import croniter

            croniter(cron)
        except (ValueError, KeyError) as exc:
            typer.echo(
                t(
                    "cli.schedule.add.error.invalid_cron",
                    "Error: invalid cron expression: {error}",
                    error=exc,
                )
            )
            raise typer.Exit(1)
    elif once:
        try:
            once_at = dt.datetime.fromisoformat(once).timestamp()
        except ValueError:
            typer.echo(
                t(
                    "cli.schedule.add.error.invalid_datetime",
                    "Error: invalid datetime format. Use ISO format.",
                )
            )
            raise typer.Exit(1)
        if once_at <= time.time():
            typer.echo(
                t(
                    "cli.schedule.add.error.once_past",
                    "Error: one-time schedule must be in the future.",
                )
            )
            raise typer.Exit(1)
    elif interval is not None and interval < 60:
        typer.echo(
            t(
                "cli.schedule.add.error.invalid_interval",
                "Error: interval must be >= 60 seconds.",
            )
        )
        raise typer.Exit(1)

    job = ScheduledJob.create(
        name=name,
        prompt=prompt,
        schedule_type=schedule_type,
        cron_expr=cron,
        once_at=once_at,
        interval_seconds=interval,
    )

    store = get_schedule_store()
    store.create_schedule(job)
    typer.echo(
        t(
            "cli.schedule.add.done",
            "Added task [{job_id}] '{name}' ({schedule_type}).",
            job_id=job.id,
            name=job.name,
            schedule_type=schedule_type,
        )
    )
    typer.echo(
        t(
            "cli.schedule.add.followup",
            "Task is now stored in the kernel ledger and will be picked up by `hermit serve`.",
        )
    )


@schedule_app.command("remove")
def schedule_remove(
    job_id: str = typer.Argument(
        ...,
        help=cli_t("cli.schedule.common.job_id_remove", "Task ID to remove."),
    ),
) -> None:
    """Remove a scheduled task."""
    store = get_schedule_store()
    if not store.delete_schedule(job_id):
        typer.echo(
            t(
                "cli.schedule.common.job_not_found",
                "Error: no task with id '{job_id}' found.",
                job_id=job_id,
            )
        )
        raise typer.Exit(1)
    typer.echo(t("cli.schedule.remove.done", "Removed task '{job_id}'.", job_id=job_id))


@schedule_app.command("enable")
def schedule_enable(
    job_id: str = typer.Argument(
        ...,
        help=cli_t("cli.schedule.common.job_id_enable", "Task ID to enable."),
    ),
) -> None:
    """Enable a scheduled task."""
    store = get_schedule_store()
    job = store.get_schedule(job_id)
    if job is None:
        typer.echo(
            t(
                "cli.schedule.common.job_not_found",
                "Error: no task with id '{job_id}' found.",
                job_id=job_id,
            )
        )
        raise typer.Exit(1)

    # Recalculate next_run_at to avoid stale catch-up execution
    next_run: float | None = None
    if job.cron_expr:
        try:
            from croniter import croniter

            next_run = croniter(job.cron_expr, time.time()).get_next(float)
        except Exception:
            next_run = time.time() + 60
    elif job.interval_seconds:
        next_run = time.time() + job.interval_seconds
    elif job.once_at and job.once_at > time.time():
        next_run = job.once_at

    store.update_schedule(job_id, enabled=True, next_run_at=next_run)
    typer.echo(t("cli.schedule.enable.done", "Enabled task '{job_id}'.", job_id=job_id))


@schedule_app.command("disable")
def schedule_disable(
    job_id: str = typer.Argument(
        ...,
        help=cli_t("cli.schedule.common.job_id_disable", "Task ID to disable."),
    ),
) -> None:
    """Disable a scheduled task."""
    store = get_schedule_store()
    if store.update_schedule(job_id, enabled=False, next_run_at=None):
        typer.echo(t("cli.schedule.disable.done", "Disabled task '{job_id}'.", job_id=job_id))
        return
    typer.echo(
        t(
            "cli.schedule.common.job_not_found",
            "Error: no task with id '{job_id}' found.",
            job_id=job_id,
        )
    )
    raise typer.Exit(1)


@schedule_app.command("history")
def schedule_history(
    job_id: str | None = typer.Option(
        None,
        help=cli_t("cli.schedule.history.job_id", "Filter by task ID."),
    ),
    limit: int = typer.Option(
        10,
        help=cli_t("cli.schedule.history.limit", "Number of records to show."),
    ),
) -> None:
    """Show execution history for scheduled tasks."""
    import datetime

    store = get_schedule_store()
    records = [
        record.to_dict() for record in store.list_schedule_history(job_id=job_id, limit=limit)
    ]

    if not records:
        typer.echo(t("cli.schedule.history.empty", "No execution history."))
        return

    for r in records:
        status = (
            t("cli.schedule.history.status.ok", "OK")
            if r.get("success")
            else t("cli.schedule.history.status.fail", "FAIL")
        )
        started = datetime.datetime.fromtimestamp(r.get("started_at", 0)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        duration = r.get("finished_at", 0) - r.get("started_at", 0)
        preview = (r.get("result_text", "") or "")[:100].replace("\n", " ")
        typer.echo(f"  [{status}] {r.get('job_name', '?')} @ {started} ({duration:.1f}s)")
        if preview:
            typer.echo(f"    {preview}")
        if r.get("error"):
            typer.echo(
                t(
                    "cli.schedule.history.error",
                    "    Error: {error}",
                    error=r["error"],
                )
            )
