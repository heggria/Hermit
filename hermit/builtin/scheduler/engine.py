"""Core scheduler engine — daemon thread with precise cron/interval/once timing."""
from __future__ import annotations

import datetime
import json
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from hermit.builtin.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.i18n import resolve_locale, tr
from hermit.kernel.store import KernelStore
from hermit.plugin.base import HookEvent
from hermit.storage import atomic_write

if TYPE_CHECKING:
    from hermit.config import Settings
    from hermit.plugin.hooks import HooksEngine

log = structlog.get_logger()

_RESULT_TEXT_LIMIT = 4000
_HISTORY_MAX_RECORDS = 200
_POLL_INTERVAL = 60


def _build_execution_prompt(task_prompt: str, *, locale: str | None = None) -> str:
    """Wrap a scheduled task prompt so the agent returns only the final deliverable.

    Scheduled jobs are already fully configured before they fire. When executed,
    the agent must not re-enter setup flow (for example, asking for chat_id,
    creating another schedule, or requesting missing parameters for the original
    user conversation). The scheduler itself handles delivery to Feishu.
    """
    clean_prompt = task_prompt.strip()
    return tr(
        "prompt.scheduler.execution",
        locale=resolve_locale(locale),
        task_prompt=clean_prompt,
    )


class SchedulerEngine:
    """Background scheduler that fires agent executions at precise times.

    Thread safety: all job mutations go through ``_jobs_store`` (which uses
    ``FileGuard`` internally) and the in-memory job list is protected by
    ``_lock``.
    """

    def __init__(self, settings: Settings, hooks: HooksEngine) -> None:
        self._settings = settings
        self._hooks = hooks
        self._runner: Any = None
        self._schedules_dir = settings.base_dir / "schedules"
        self._schedules_dir.mkdir(parents=True, exist_ok=True)
        kernel_db_path = getattr(settings, "kernel_db_path", settings.base_dir / "kernel" / "state.db")
        self._store = KernelStore(Path(kernel_db_path))
        self._history_path = self._schedules_dir / "history.json"
        self._logs_dir = self._schedules_dir / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)

        self._jobs: list[ScheduledJob] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_runner(self, runner: Any) -> None:
        """Store the serve-context AgentRunner so we can reuse its agent."""
        self._runner = runner

    def start(self, *, catch_up: bool = True) -> None:
        self._load_jobs()
        self._recalculate_all_next_run()
        if catch_up:
            self._catchup_missed_jobs()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="scheduler",
        )
        self._thread.start()
        log.info("scheduler_started", job_count=len(self._jobs))

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.info("scheduler_stopped")

    def wake(self) -> None:
        """Signal the scheduler loop to re-evaluate the job queue immediately."""
        self._wake_event.set()

    # ------------------------------------------------------------------
    # Job CRUD (called from tools / CLI)
    # ------------------------------------------------------------------

    def add_job(self, job: ScheduledJob) -> None:
        job.next_run_at = self._compute_next_run(job)
        with self._lock:
            self._jobs.append(job)
        self._persist_jobs()
        self.wake()

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.id != job_id]
            removed = len(self._jobs) < before
        if removed:
            self._persist_jobs()
            self.wake()
        return removed

    def update_job(self, job_id: str, **updates: Any) -> ScheduledJob | None:
        with self._lock:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if job is None:
                return None
            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.next_run_at = self._compute_next_run(job)
        self._persist_jobs()
        self.wake()
        return job

    def list_jobs(self) -> list[ScheduledJob]:
        with self._lock:
            return list(self._jobs)

    def get_job(self, job_id: str) -> ScheduledJob | None:
        with self._lock:
            return next((j for j in self._jobs if j.id == job_id), None)

    def get_history(self, job_id: str | None = None, limit: int = 20) -> list[JobExecutionRecord]:
        records = self._load_history()
        if job_id:
            records = [r for r in records if r.job_id == job_id]
        return records[-limit:]

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            job, wait = self._next_due_job()
            if job is None:
                self._stop_event.wait(_POLL_INTERVAL)
                continue
            if wait > 0:
                self._wake_event.wait(wait)
                if self._stop_event.is_set():
                    break
                if self._wake_event.is_set():
                    continue
            self._execute(job)

    def _next_due_job(self) -> tuple[ScheduledJob | None, float]:
        now = time.time()
        with self._lock:
            candidates = [
                j for j in self._jobs
                if j.enabled and j.next_run_at is not None
            ]
        if not candidates:
            return None, 0
        candidates.sort(key=lambda j: j.next_run_at or float("inf"))
        nearest = candidates[0]
        wait = max(0, (nearest.next_run_at or now) - now)
        return nearest, wait

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute(self, job: ScheduledJob) -> None:
        log.info("scheduler_executing", job_id=job.id, job_name=job.name)
        started_at = time.time()
        result_text = ""
        success = False
        error_msg: str | None = None

        for attempt in range(max(1, job.max_retries)):
            try:
                result = self._run_agent(job.prompt)
                result_text = (result.text or "")[:_RESULT_TEXT_LIMIT]
                success = True
                break
            except Exception as exc:
                error_msg = f"Attempt {attempt + 1}/{job.max_retries}: {exc}"
                log.error("scheduler_execute_error", job_id=job.id, error=str(exc))

        finished_at = time.time()

        notify: dict[str, str] = {}
        chat_id = getattr(job, "feishu_chat_id", None) or getattr(
            self._settings, "scheduler_feishu_chat_id", ""
        )
        if chat_id:
            notify["feishu_chat_id"] = chat_id

        self._hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="scheduler",
            title=job.name,
            result_text=result_text,
            success=success,
            error=error_msg,
            notify=notify,
            metadata={"job_id": job.id},
        )

        record = JobExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            started_at=started_at,
            finished_at=finished_at,
            success=success,
            result_text=result_text,
            error=error_msg,
        )
        self._append_history(record)
        self._write_log_file(record)

        with self._lock:
            job.last_run_at = time.time()
            if job.schedule_type == "once":
                job.enabled = False
                job.next_run_at = None
            else:
                job.next_run_at = self._compute_next_run(job)
        self._persist_jobs()

    def _run_agent(self, prompt: str) -> Any:
        execution_prompt = _build_execution_prompt(
            prompt,
            locale=getattr(self._settings, "locale", None),
        )
        if self._runner is not None:
            return self._run_agent_via_runner(execution_prompt)
        return self._run_agent_standalone(execution_prompt)

    def _run_agent_via_runner(self, prompt: str) -> Any:
        session_id = f"schedule-{uuid.uuid4().hex[:8]}"
        result = self._runner.dispatch(session_id, prompt)
        if result.agent_result is None:
            raise RuntimeError(result.text)
        return result.agent_result

    def _run_agent_standalone(self, prompt: str) -> Any:
        """Fallback: build a task-aware runner when running outside serve context."""
        from hermit.config import get_settings
        from hermit.core.runner import AgentRunner
        from hermit.core.session import SessionManager
        from hermit.provider.services import build_background_runtime

        settings = get_settings()
        agent, pm = build_background_runtime(settings, cwd=Path.home())
        runner = AgentRunner(
            agent,
            SessionManager(settings.sessions_dir),
            pm,
            task_controller=getattr(agent, "task_controller", None),
        )
        session_id = f"schedule-{uuid.uuid4().hex[:8]}"
        result = runner.dispatch(session_id, prompt)
        if result.agent_result is None:
            raise RuntimeError(result.text)
        return result.agent_result

    # ------------------------------------------------------------------
    # Catch-up: execute missed jobs on startup
    # ------------------------------------------------------------------

    def _catchup_missed_jobs(self) -> None:
        now = time.time()
        with self._lock:
            missed = [
                j for j in self._jobs
                if j.enabled and j.next_run_at is not None and j.next_run_at < now
            ]
        if not missed:
            return
        log.info("scheduler_catchup", count=len(missed))
        for job in missed:
            self._execute(job)

    # ------------------------------------------------------------------
    # Next-run calculation
    # ------------------------------------------------------------------

    def _compute_next_run(self, job: ScheduledJob) -> float | None:
        if not job.enabled:
            return None
        now = time.time()

        if job.schedule_type == "cron" and job.cron_expr:
            from croniter import croniter
            cron = croniter(job.cron_expr, now)
            return cron.get_next(float)

        if job.schedule_type == "once" and job.once_at is not None:
            return job.once_at if job.once_at > now else None

        if job.schedule_type == "interval" and job.interval_seconds:
            base = job.last_run_at or job.created_at
            nxt = base + job.interval_seconds
            while nxt <= now:
                nxt += job.interval_seconds
            return nxt

        return None

    def _recalculate_all_next_run(self) -> None:
        with self._lock:
            for job in self._jobs:
                if job.enabled:
                    job.next_run_at = self._compute_next_run(job)
        self._persist_jobs()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_jobs(self) -> None:
        with self._lock:
            self._jobs = list(self._store.list_schedules())

    def _persist_jobs(self) -> None:
        with self._lock:
            jobs = list(self._jobs)
        existing = {job.id for job in jobs}
        for job in jobs:
            self._store.create_schedule(job)
        for current in self._store.list_schedules():
            if current.id not in existing:
                self._store.delete_schedule(current.id)

    def _load_history(self) -> list[JobExecutionRecord]:
        return self._store.list_schedule_history(limit=_HISTORY_MAX_RECORDS)

    def _append_history(self, record: JobExecutionRecord) -> None:
        self._store.append_schedule_history(record)
        records = self._store.list_schedule_history(limit=_HISTORY_MAX_RECORDS)
        payload = {"records": [r.to_dict() for r in records]}
        atomic_write(self._history_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _write_log_file(self, record: JobExecutionRecord) -> None:
        ts = datetime.datetime.fromtimestamp(record.started_at).strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{record.job_id}.log"
        content_lines = [
            f"Job: {record.job_name} ({record.job_id})",
            f"Started: {datetime.datetime.fromtimestamp(record.started_at).isoformat()}",
            f"Finished: {datetime.datetime.fromtimestamp(record.finished_at).isoformat()}",
            f"Duration: {record.finished_at - record.started_at:.1f}s",
            f"Success: {record.success}",
        ]
        if record.error:
            content_lines.append(f"Error: {record.error}")
        content_lines.extend(["", "--- Result ---", record.result_text])
        atomic_write(self._logs_dir / filename, "\n".join(content_lines))
