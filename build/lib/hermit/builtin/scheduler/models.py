"""Data models for the scheduler plugin."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScheduledJob:
    id: str
    name: str
    prompt: str
    schedule_type: str  # "cron" | "once" | "interval"
    cron_expr: str | None = None
    once_at: float | None = None
    interval_seconds: int | None = None
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_run_at: float | None = None
    next_run_at: float | None = None
    max_retries: int = 1
    feishu_chat_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule_type": self.schedule_type,
            "cron_expr": self.cron_expr,
            "once_at": self.once_at,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "max_retries": self.max_retries,
            "feishu_chat_id": self.feishu_chat_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledJob:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            prompt=str(data["prompt"]),
            schedule_type=str(data["schedule_type"]),
            cron_expr=data.get("cron_expr"),
            once_at=data.get("once_at"),
            interval_seconds=data.get("interval_seconds"),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at", time.time())),
            last_run_at=data.get("last_run_at"),
            next_run_at=data.get("next_run_at"),
            max_retries=int(data.get("max_retries", 1)),
            feishu_chat_id=data.get("feishu_chat_id"),
        )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        prompt: str,
        schedule_type: str,
        cron_expr: str | None = None,
        once_at: float | None = None,
        interval_seconds: int | None = None,
        max_retries: int = 1,
        feishu_chat_id: str | None = None,
    ) -> ScheduledJob:
        job = cls(
            id=uuid.uuid4().hex[:12],
            name=name,
            prompt=prompt,
            schedule_type=schedule_type,
            cron_expr=cron_expr,
            once_at=once_at,
            interval_seconds=interval_seconds,
            max_retries=max_retries,
            feishu_chat_id=feishu_chat_id,
        )
        job.next_run_at = compute_job_next_run(job)
        return job


def compute_job_next_run(job: ScheduledJob, *, now: float | None = None) -> float | None:
    if not job.enabled:
        return None
    current_time = time.time() if now is None else now

    if job.schedule_type == "cron" and job.cron_expr:
        from croniter import croniter

        cron = croniter(job.cron_expr, current_time)
        return cron.get_next(float)

    if job.schedule_type == "once" and job.once_at is not None:
        return job.once_at if job.once_at > current_time else None

    if job.schedule_type == "interval" and job.interval_seconds:
        base = job.last_run_at or job.created_at
        nxt = base + job.interval_seconds
        while nxt <= current_time:
            nxt += job.interval_seconds
        return nxt

    return None


@dataclass
class JobExecutionRecord:
    job_id: str
    job_name: str
    started_at: float
    finished_at: float
    success: bool
    result_text: str
    error: str | None = None
    delivery_status: str | None = None
    delivery_channel: str | None = None
    delivery_mode: str | None = None
    delivery_target: str | None = None
    delivery_message_id: str | None = None
    delivery_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success": self.success,
            "result_text": self.result_text,
            "error": self.error,
            "delivery_status": self.delivery_status,
            "delivery_channel": self.delivery_channel,
            "delivery_mode": self.delivery_mode,
            "delivery_target": self.delivery_target,
            "delivery_message_id": self.delivery_message_id,
            "delivery_error": self.delivery_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobExecutionRecord:
        return cls(
            job_id=str(data["job_id"]),
            job_name=str(data.get("job_name", "")),
            started_at=float(data["started_at"]),
            finished_at=float(data["finished_at"]),
            success=bool(data["success"]),
            result_text=str(data.get("result_text", "")),
            error=data.get("error"),
            delivery_status=data.get("delivery_status"),
            delivery_channel=data.get("delivery_channel"),
            delivery_mode=data.get("delivery_mode"),
            delivery_target=data.get("delivery_target"),
            delivery_message_id=data.get("delivery_message_id"),
            delivery_error=data.get("delivery_error"),
        )
