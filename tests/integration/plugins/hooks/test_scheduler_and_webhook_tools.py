from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.plugins.builtin.hooks.scheduler import tools as scheduler_tools
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.plugins.builtin.hooks.webhook import tools as webhook_tools
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


class _FakeSchedulerEngine:
    def __init__(self) -> None:
        self.jobs: list[ScheduledJob] = []
        self.history_records: list[JobExecutionRecord] = []

    def add_job(self, job: ScheduledJob) -> None:
        job.next_run_at = job.next_run_at or 1_700_000_000.0
        self.jobs.append(job)

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self.jobs)

    def remove_job(self, job_id: str) -> bool:
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                del self.jobs[index]
                return True
        return False

    def get_history(
        self, *, job_id: str | None = None, limit: int = 20
    ) -> list[JobExecutionRecord]:
        records = [r for r in self.history_records if job_id is None or r.job_id == job_id]
        return records[:limit]

    def update_job(self, job_id: str, **updates):
        for job in self.jobs:
            if job.id == job_id:
                for key, value in updates.items():
                    setattr(job, key, value)
                job.next_run_at = job.next_run_at or 1_700_000_000.0
                return job
        return None


def test_scheduler_tools_cover_create_list_delete_history_update_and_register(monkeypatch) -> None:
    engine = _FakeSchedulerEngine()
    scheduler_tools.set_engine(engine)
    ctx = PluginContext(HooksEngine())

    assert scheduler_tools._format_time(None) == "N/A"
    scheduler_tools.register(ctx)
    assert {tool.name for tool in ctx.tools} == {
        "schedule_create",
        "schedule_list",
        "schedule_delete",
        "schedule_update",
        "schedule_history",
    }

    scheduler_tools.set_engine(None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="Scheduler engine not running"):
        scheduler_tools._require_engine()
    scheduler_tools.set_engine(engine)

    assert (
        scheduler_tools._handle_create({}) == "Error: name, prompt, and schedule_type are required."
    )
    assert scheduler_tools._handle_create(
        {"name": "job", "prompt": "run", "schedule_type": "weird"}
    ) == ("Error: schedule_type must be 'cron', 'once', or 'interval'.")
    assert scheduler_tools._handle_create(
        {"name": "job", "prompt": "run", "schedule_type": "cron"}
    ) == ("Error: cron_expr is required for schedule_type 'cron'.")
    assert "invalid cron expression" in scheduler_tools._handle_create(
        {"name": "job", "prompt": "run", "schedule_type": "cron", "cron_expr": "bad cron"}
    )
    assert scheduler_tools._handle_create(
        {"name": "job", "prompt": "run", "schedule_type": "once"}
    ) == ("Error: once_at is required for schedule_type 'once'.")
    assert "invalid datetime format" in scheduler_tools._handle_create(
        {"name": "job", "prompt": "run", "schedule_type": "once", "once_at": "not-a-date"}
    )
    monkeypatch.setattr(scheduler_tools.time, "time", lambda: 100.0)
    assert (
        scheduler_tools._handle_create(
            {
                "name": "job",
                "prompt": "run",
                "schedule_type": "once",
                "once_at": "1970-01-01T00:01:00",
            }
        )
        == "Error: once_at must be in the future."
    )
    assert (
        scheduler_tools._handle_create(
            {"name": "job", "prompt": "run", "schedule_type": "interval", "interval_seconds": 30}
        )
        == "Error: interval_seconds is required and must be >= 60."
    )

    created_text = scheduler_tools._handle_create(
        {
            "name": "job",
            "prompt": "run",
            "schedule_type": "interval",
            "interval_seconds": 600,
            "max_retries": 3,
            "feishu_chat_id": " oc_123 ",
        }
    )
    assert "Scheduled task created" in created_text
    assert engine.jobs[0].interval_seconds == 600
    assert engine.jobs[0].feishu_chat_id == "oc_123"

    assert "Scheduled tasks (1)" in scheduler_tools._handle_list({})
    listed_job = engine.jobs[0]
    listed_job.last_run_at = 1_700_000_100.0
    listed_job.next_run_at = 1_700_000_200.0
    assert listed_job.id in scheduler_tools._handle_list({})

    assert scheduler_tools._handle_delete({}) == "Error: job_id is required."
    assert (
        scheduler_tools._handle_delete({"job_id": "missing"})
        == "Error: no task with id 'missing' found."
    )
    assert (
        scheduler_tools._handle_delete({"job_id": listed_job.id})
        == f"Deleted scheduled task '{listed_job.id}'."
    )
    assert scheduler_tools._handle_list({}) == "No scheduled tasks."

    engine.jobs.append(
        ScheduledJob(
            id="job-2",
            name="job-2",
            prompt="run",
            schedule_type="cron",
            cron_expr="0 9 * * *",
            next_run_at=1_700_000_300.0,
        )
    )
    assert scheduler_tools._handle_history({}) == "No execution history found."
    engine.history_records = [
        JobExecutionRecord(
            job_id="job-2",
            job_name="job-2",
            started_at=1_700_000_000.0,
            finished_at=1_700_000_005.0,
            success=False,
            result_text="hello world",
            error="boom",
        )
    ]
    history_text = scheduler_tools._handle_history({"job_id": "job-2", "limit": 1})
    assert "Execution history (1 records)" in history_text
    assert "Error: boom" in history_text

    assert scheduler_tools._handle_update({}) == "Error: job_id is required."
    assert "invalid cron expression" in scheduler_tools._handle_update(
        {"job_id": "job-2", "cron_expr": "bad cron"}
    )
    assert scheduler_tools._handle_update({"job_id": "job-2"}) == (
        "Error: no fields to update. Provide name, prompt, enabled, cron_expr, or feishu_chat_id."
    )
    assert (
        scheduler_tools._handle_update({"job_id": "missing", "name": "x"})
        == "Error: no task with id 'missing' found."
    )

    updated_text = scheduler_tools._handle_update(
        {
            "job_id": "job-2",
            "name": "renamed",
            "prompt": "new prompt",
            "enabled": False,
            "cron_expr": "0 10 * * *",
            "feishu_chat_id": "oc_456",
        }
    )
    assert "Updated task 'renamed' (job-2)." in updated_text
    assert engine.jobs[0].name == "renamed"
    assert engine.jobs[0].enabled is False
    assert engine.jobs[0].feishu_chat_id == "oc_456"


def test_webhook_tools_cover_file_crud_and_register(tmp_path: Path) -> None:
    webhook_tools.set_settings(SimpleNamespace(base_dir=tmp_path))
    ctx = PluginContext(HooksEngine(), settings=SimpleNamespace(base_dir=tmp_path))
    webhook_tools.register(ctx)

    assert {tool.name for tool in ctx.tools} == {
        "webhook_list",
        "webhook_add",
        "webhook_delete",
        "webhook_update",
    }
    assert webhook_tools._config_path() == tmp_path / "webhooks.json"
    assert webhook_tools._load_raw() == {"host": "0.0.0.0", "port": 8321, "routes": {}}
    assert "No webhook routes configured." in webhook_tools._handle_list({})
    assert webhook_tools._handle_add({}) == "Error: name is required."
    assert webhook_tools._handle_add({"name": "github"}) == "Error: prompt_template is required."

    added_text = webhook_tools._handle_add(
        {
            "name": "github",
            "prompt_template": "PR: {title}",
            "path": "/hook/github",
            "secret": "top-secret",
            "signature_header": "X-Signature",
            "feishu_chat_id": "oc_123",
        }
    )
    assert "Webhook route 'github' added" in added_text
    raw = json.loads((tmp_path / "webhooks.json").read_text(encoding="utf-8"))
    assert raw["routes"]["github"]["secret"] == "top-secret"
    assert raw["routes"]["github"]["notify"]["feishu_chat_id"] == "oc_123"
    assert "route 'github' already exists" in webhook_tools._handle_add(
        {"name": "github", "prompt_template": "PR: {title}"}
    )
    assert "Routes (1):" in webhook_tools._handle_list({})
    assert "X-Signature" in webhook_tools._handle_list({})

    assert webhook_tools._handle_delete({}) == "Error: name is required."
    assert (
        webhook_tools._handle_delete({"name": "missing"})
        == "Error: route 'missing' not found. Existing routes: github"
    )

    assert webhook_tools._handle_update({}) == "Error: name is required."
    assert (
        webhook_tools._handle_update({"name": "missing"})
        == "Error: route 'missing' not found. Existing routes: github"
    )
    assert webhook_tools._handle_update({"name": "github"}) == (
        "Error: no fields to update. Provide prompt_template, path, secret, or feishu_chat_id."
    )

    updated_text = webhook_tools._handle_update(
        {
            "name": "github",
            "prompt_template": "Issue: {title}",
            "path": "/webhook/issues",
            "secret": "",
            "feishu_chat_id": "",
        }
    )
    assert (
        "Webhook route 'github' updated: prompt_template, path, secret, feishu_chat_id."
        in updated_text
    )
    raw = webhook_tools._load_raw()
    assert raw["routes"]["github"]["path"] == "/webhook/issues"
    assert "secret" not in raw["routes"]["github"]
    assert raw["routes"]["github"].get("notify", {}) == {}

    deleted_text = webhook_tools._handle_delete({"name": "github"})
    assert deleted_text == (
        "Webhook route 'github' deleted.\nRestart `hermit serve` for the change to take effect."
    )
    assert webhook_tools._load_raw()["routes"] == {}

    (tmp_path / "webhooks.json").write_text("{", encoding="utf-8")
    assert webhook_tools._load_raw() == {"host": "0.0.0.0", "port": 8321, "routes": {}}
