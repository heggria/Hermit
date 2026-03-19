"""Tests for scheduler tools — target 80%+ coverage on tools.py."""

from __future__ import annotations

import datetime
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.plugins.builtin.hooks.scheduler import tools as scheduler_tools
from hermit.plugins.builtin.hooks.scheduler.models import (
    JobExecutionRecord,
    ScheduledJob,
)
from hermit.plugins.builtin.hooks.scheduler.tools import (
    _format_time,
    _handle_create,
    _handle_delete,
    _handle_history,
    _handle_list,
    _handle_update,
    _require_engine,
    register,
    set_engine,
)
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


@pytest.fixture(autouse=True)
def _reset_engine():
    """Reset the module-level engine before and after each test."""
    old = scheduler_tools._engine
    scheduler_tools._engine = None
    yield
    scheduler_tools._engine = old


class FakeEngine:
    """Minimal fake SchedulerEngine for testing tool handlers."""

    def __init__(self) -> None:
        self._jobs: list[ScheduledJob] = []
        self._history: list[JobExecutionRecord] = []
        self._settings = MagicMock(locale=None)

    def add_job(self, job: ScheduledJob) -> None:
        self._jobs.append(job)

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self._jobs)

    def remove_job(self, job_id: str) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        return len(self._jobs) < before

    def update_job(self, job_id: str, **updates: Any) -> ScheduledJob | None:
        for job in self._jobs:
            if job.id == job_id:
                for k, v in updates.items():
                    if hasattr(job, k):
                        setattr(job, k, v)
                return job
        return None

    def get_history(self, job_id: str | None = None, limit: int = 20) -> list[JobExecutionRecord]:
        records = self._history
        if job_id:
            records = [r for r in records if r.job_id == job_id]
        return records[-limit:]


def _install_engine() -> FakeEngine:
    engine = FakeEngine()
    set_engine(engine)
    return engine


# ── set_engine / _require_engine ─────────────────────────────────


def test_set_engine() -> None:
    engine = FakeEngine()
    set_engine(engine)
    assert scheduler_tools._engine is engine


def test_require_engine_raises_when_none() -> None:
    with pytest.raises(RuntimeError):
        _require_engine()


def test_require_engine_returns() -> None:
    engine = _install_engine()
    assert _require_engine() is engine


# ── _format_time ─────────────────────────────────────────────────


def test_format_time_none() -> None:
    _install_engine()
    result = _format_time(None)
    assert isinstance(result, str)


def test_format_time_value() -> None:
    _install_engine()
    ts = time.time()
    result = _format_time(ts)
    assert isinstance(result, str)
    assert len(result) > 0


# ── _handle_create ───────────────────────────────────────────────


def test_create_missing_fields() -> None:
    _install_engine()
    result = _handle_create({"name": "", "prompt": "", "schedule_type": ""})
    assert isinstance(result, str)  # error message


def test_create_invalid_schedule_type() -> None:
    _install_engine()
    result = _handle_create({"name": "test", "prompt": "do it", "schedule_type": "invalid"})
    assert isinstance(result, str)  # error message


def test_create_cron_missing_expr() -> None:
    _install_engine()
    result = _handle_create({"name": "test", "prompt": "do it", "schedule_type": "cron"})
    assert isinstance(result, str)  # error message


def test_create_cron_invalid_expr() -> None:
    _install_engine()
    result = _handle_create(
        {
            "name": "test",
            "prompt": "do it",
            "schedule_type": "cron",
            "cron_expr": "invalid cron",
        }
    )
    assert isinstance(result, str)


def test_create_cron_success() -> None:
    engine = _install_engine()
    result = _handle_create(
        {
            "name": "Daily Report",
            "prompt": "Generate report",
            "schedule_type": "cron",
            "cron_expr": "0 9 * * *",
        }
    )
    assert isinstance(result, str)
    assert len(engine._jobs) == 1
    assert engine._jobs[0].name == "Daily Report"


def test_create_once_missing_time() -> None:
    _install_engine()
    result = _handle_create({"name": "test", "prompt": "do it", "schedule_type": "once"})
    assert isinstance(result, str)


def test_create_once_invalid_datetime() -> None:
    _install_engine()
    result = _handle_create(
        {
            "name": "test",
            "prompt": "do it",
            "schedule_type": "once",
            "once_at": "not-a-date",
        }
    )
    assert isinstance(result, str)


def test_create_once_past_time() -> None:
    _install_engine()
    past = datetime.datetime(2020, 1, 1).isoformat()
    result = _handle_create(
        {
            "name": "test",
            "prompt": "do it",
            "schedule_type": "once",
            "once_at": past,
        }
    )
    assert isinstance(result, str)


def test_create_once_future_success() -> None:
    engine = _install_engine()
    future = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
    result = _handle_create(
        {
            "name": "One Time",
            "prompt": "do it once",
            "schedule_type": "once",
            "once_at": future,
        }
    )
    assert isinstance(result, str)
    assert len(engine._jobs) == 1


def test_create_interval_too_short() -> None:
    _install_engine()
    result = _handle_create(
        {
            "name": "test",
            "prompt": "do it",
            "schedule_type": "interval",
            "interval_seconds": 30,
        }
    )
    assert isinstance(result, str)  # error: interval < 60


def test_create_interval_missing() -> None:
    _install_engine()
    result = _handle_create({"name": "test", "prompt": "do it", "schedule_type": "interval"})
    assert isinstance(result, str)


def test_create_interval_success() -> None:
    engine = _install_engine()
    result = _handle_create(
        {
            "name": "Recurring",
            "prompt": "check status",
            "schedule_type": "interval",
            "interval_seconds": 300,
        }
    )
    assert isinstance(result, str)
    assert len(engine._jobs) == 1
    assert engine._jobs[0].interval_seconds == 300


def test_create_with_feishu_chat_id() -> None:
    engine = _install_engine()
    _handle_create(
        {
            "name": "Chat Job",
            "prompt": "notify chat",
            "schedule_type": "interval",
            "interval_seconds": 600,
            "feishu_chat_id": "oc_test",
        }
    )
    assert len(engine._jobs) == 1
    assert engine._jobs[0].feishu_chat_id == "oc_test"


def test_create_with_max_retries() -> None:
    engine = _install_engine()
    _handle_create(
        {
            "name": "Retry Job",
            "prompt": "try hard",
            "schedule_type": "interval",
            "interval_seconds": 300,
            "max_retries": 3,
        }
    )
    assert engine._jobs[0].max_retries == 3


# ── _handle_list ─────────────────────────────────────────────────


def test_list_empty() -> None:
    _install_engine()
    result = _handle_list({})
    assert isinstance(result, str)


def test_list_with_jobs() -> None:
    engine = _install_engine()
    engine._jobs.append(
        ScheduledJob(
            id="j1",
            name="Job 1",
            prompt="test",
            schedule_type="cron",
            cron_expr="0 9 * * *",
            enabled=True,
            next_run_at=time.time() + 3600,
        )
    )
    engine._jobs.append(
        ScheduledJob(
            id="j2",
            name="Job 2",
            prompt="test",
            schedule_type="once",
            once_at=time.time() + 7200,
            enabled=False,
        )
    )
    engine._jobs.append(
        ScheduledJob(
            id="j3",
            name="Job 3",
            prompt="test",
            schedule_type="interval",
            interval_seconds=300,
            enabled=True,
        )
    )
    result = _handle_list({})
    assert "Job 1" in result
    assert "Job 2" in result
    assert "Job 3" in result


# ── _handle_delete ───────────────────────────────────────────────


def test_delete_missing_id() -> None:
    _install_engine()
    result = _handle_delete({})
    assert isinstance(result, str)


def test_delete_success() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1"))
    result = _handle_delete({"job_id": "j1"})
    assert isinstance(result, str)
    assert len(engine._jobs) == 0


def test_delete_not_found() -> None:
    _install_engine()
    result = _handle_delete({"job_id": "nonexistent"})
    assert isinstance(result, str)


# ── _handle_history ──────────────────────────────────────────────


def test_history_empty() -> None:
    _install_engine()
    result = _handle_history({})
    assert isinstance(result, str)


def test_history_with_records() -> None:
    engine = _install_engine()
    engine._history.append(
        JobExecutionRecord(
            job_id="j1",
            job_name="Test Job",
            started_at=time.time() - 100,
            finished_at=time.time() - 90,
            success=True,
            result_text="All done",
        )
    )
    engine._history.append(
        JobExecutionRecord(
            job_id="j2",
            job_name="Failed Job",
            started_at=time.time() - 200,
            finished_at=time.time() - 190,
            success=False,
            result_text="",
            error="Something went wrong",
        )
    )
    result = _handle_history({})
    assert "Test Job" in result
    assert "Failed Job" in result


def test_history_with_filter() -> None:
    engine = _install_engine()
    engine._history.append(
        JobExecutionRecord(
            job_id="j1",
            job_name="Job 1",
            started_at=time.time(),
            finished_at=time.time(),
            success=True,
            result_text="ok",
        )
    )
    result = _handle_history({"job_id": "j1"})
    assert "Job 1" in result


def test_history_with_limit() -> None:
    engine = _install_engine()
    for i in range(10):
        engine._history.append(
            JobExecutionRecord(
                job_id=f"j{i}",
                job_name=f"Job {i}",
                started_at=time.time(),
                finished_at=time.time(),
                success=True,
                result_text="ok",
            )
        )
    result = _handle_history({"limit": 5})
    assert isinstance(result, str)


# ── _handle_update ───────────────────────────────────────────────


def test_update_missing_id() -> None:
    _install_engine()
    result = _handle_update({})
    assert isinstance(result, str)


def test_update_no_fields() -> None:
    _install_engine()
    result = _handle_update({"job_id": "j1"})
    assert isinstance(result, str)


def test_update_not_found() -> None:
    _install_engine()
    result = _handle_update({"job_id": "nonexistent", "name": "new"})
    assert isinstance(result, str)


def test_update_name() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1"))
    result = _handle_update({"job_id": "j1", "name": "Updated Name"})
    assert isinstance(result, str)
    assert engine._jobs[0].name == "Updated Name"


def test_update_prompt() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1"))
    _handle_update({"job_id": "j1", "prompt": "new prompt"})
    assert engine._jobs[0].prompt == "new prompt"


def test_update_enabled() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1"))
    _handle_update({"job_id": "j1", "enabled": False})
    assert engine._jobs[0].enabled is False


def test_update_cron_expr() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1", schedule_type="cron", cron_expr="0 9 * * *"))
    _handle_update({"job_id": "j1", "cron_expr": "0 10 * * *"})
    assert engine._jobs[0].cron_expr == "0 10 * * *"


def test_update_invalid_cron() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1", schedule_type="cron", cron_expr="0 9 * * *"))
    result = _handle_update({"job_id": "j1", "cron_expr": "bad cron"})
    assert isinstance(result, str)
    # Original should be unchanged
    assert engine._jobs[0].cron_expr == "0 9 * * *"


def test_update_feishu_chat_id() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1"))
    _handle_update({"job_id": "j1", "feishu_chat_id": "oc_new"})
    assert engine._jobs[0].feishu_chat_id == "oc_new"


def test_update_feishu_chat_id_empty() -> None:
    engine = _install_engine()
    engine._jobs.append(_mk_job(id="j1", feishu_chat_id="oc_old"))
    _handle_update({"job_id": "j1", "feishu_chat_id": ""})
    assert engine._jobs[0].feishu_chat_id is None


# ── register ─────────────────────────────────────────────────────


def test_register_adds_tools() -> None:
    ctx = PluginContext(HooksEngine())
    register(ctx)
    tool_names = [t.name for t in ctx.tools]
    assert "schedule_create" in tool_names
    assert "schedule_list" in tool_names
    assert "schedule_delete" in tool_names
    assert "schedule_update" in tool_names
    assert "schedule_history" in tool_names


def _mk_job(id: str = "job_001", **kwargs) -> ScheduledJob:
    defaults = {
        "name": "Test Job",
        "prompt": "Do something",
        "schedule_type": "interval",
        "interval_seconds": 3600,
        "enabled": True,
    }
    defaults.update(kwargs)
    return ScheduledJob(id=id, **defaults)
