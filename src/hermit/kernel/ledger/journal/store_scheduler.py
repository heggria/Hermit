from __future__ import annotations

from typing import Any

from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob


class KernelSchedulerStoreMixin(KernelStoreTypingBase):
    def create_schedule(self, job: ScheduledJob) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO schedule_specs (
                    id, name, prompt, schedule_type, cron_expr, once_at, interval_seconds,
                    enabled, created_at, last_run_at, next_run_at, max_retries, feishu_chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.name,
                    job.prompt,
                    job.schedule_type,
                    job.cron_expr,
                    job.once_at,
                    job.interval_seconds,
                    1 if job.enabled else 0,
                    job.created_at,
                    job.last_run_at,
                    job.next_run_at,
                    job.max_retries,
                    job.feishu_chat_id,
                ),
            )

    def update_schedule(self, job_id: str, **updates: Any) -> ScheduledJob | None:
        job = self.get_schedule(job_id)
        if job is None:
            return None
        for key, value in updates.items():
            if hasattr(job, key):
                setattr(job, key, value)
        self.create_schedule(job)
        return job

    def delete_schedule(self, job_id: str) -> bool:
        conn = self._get_conn()
        with conn:
            cursor = conn.execute("DELETE FROM schedule_specs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0

    def get_schedule(self, job_id: str) -> ScheduledJob | None:
        row = self._row("SELECT * FROM schedule_specs WHERE id = ?", (job_id,))
        return self._schedule_from_row(row) if row is not None else None

    def list_schedules(self) -> list[ScheduledJob]:
        rows = self._rows("SELECT * FROM schedule_specs ORDER BY created_at DESC")
        return [self._schedule_from_row(row) for row in rows]

    def append_schedule_history(self, record: JobExecutionRecord) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO schedule_history (
                    job_id, job_name, started_at, finished_at, success, result_text, error,
                    delivery_status, delivery_channel, delivery_mode, delivery_target,
                    delivery_message_id, delivery_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.job_name,
                    record.started_at,
                    record.finished_at,
                    1 if record.success else 0,
                    record.result_text,
                    record.error,
                    record.delivery_status,
                    record.delivery_channel,
                    record.delivery_mode,
                    record.delivery_target,
                    record.delivery_message_id,
                    record.delivery_error,
                ),
            )

    def list_schedule_history(
        self, *, job_id: str | None = None, limit: int = 20
    ) -> list[JobExecutionRecord]:
        if job_id:
            query = """
                SELECT job_id, job_name, started_at, finished_at, success, result_text, error,
                       delivery_status, delivery_channel, delivery_mode, delivery_target,
                       delivery_message_id, delivery_error
                FROM schedule_history WHERE job_id = ? ORDER BY started_at DESC LIMIT ?
            """
            params: tuple[Any, ...] = (job_id, limit)
        else:
            query = """
                SELECT job_id, job_name, started_at, finished_at, success, result_text, error,
                       delivery_status, delivery_channel, delivery_mode, delivery_target,
                       delivery_message_id, delivery_error
                FROM schedule_history ORDER BY started_at DESC LIMIT ?
            """
            params = (limit,)
        rows = self._rows(query, params)
        return [
            JobExecutionRecord(
                job_id=str(row["job_id"]),
                job_name=str(row["job_name"]),
                started_at=float(row["started_at"]),
                finished_at=float(row["finished_at"]),
                success=bool(row["success"]),
                result_text=str(row["result_text"]),
                error=row["error"],
                delivery_status=row["delivery_status"],
                delivery_channel=row["delivery_channel"],
                delivery_mode=row["delivery_mode"],
                delivery_target=row["delivery_target"],
                delivery_message_id=row["delivery_message_id"],
                delivery_error=row["delivery_error"],
            )
            for row in rows
        ]
