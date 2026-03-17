"""Tests for the scheduler plugin: models, engine, tools, and hook broadcast."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.scheduler.engine import SchedulerEngine, _build_execution_prompt
from hermit.plugins.builtin.hooks.scheduler.models import (
    JobExecutionRecord,
    ScheduledJob,
    compute_job_next_run,
)
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Any:
    """Minimal settings-like object for scheduler engine tests."""
    settings = MagicMock()
    settings.base_dir = tmp_path
    settings.locale = "zh-CN"
    settings.sandbox_mode = "l0"
    settings.command_timeout_seconds = 30
    settings.model = "test-model"
    settings.max_tokens = 1024
    settings.max_turns = 10
    settings.tool_output_limit = 2000
    settings.thinking_budget = 0
    settings.anthropic_api_key = "fake-key"
    settings.auth_token = None
    settings.base_url = None
    settings.parsed_custom_headers = {}
    schedules_dir = tmp_path / "schedules"
    schedules_dir.mkdir(parents=True)
    settings.schedules_dir = schedules_dir
    return settings


@pytest.fixture
def hooks() -> HooksEngine:
    return HooksEngine()


@pytest.fixture
def engine(tmp_settings: Any, hooks: HooksEngine) -> SchedulerEngine:
    return SchedulerEngine(tmp_settings, hooks)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestScheduledJob:
    def test_create_generates_id(self) -> None:
        job = ScheduledJob.create(
            name="test",
            prompt="do something",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        assert len(job.id) == 12
        assert job.name == "test"
        assert job.enabled is True

    def test_roundtrip_dict(self) -> None:
        job = ScheduledJob.create(
            name="daily",
            prompt="hello",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        data = job.to_dict()
        restored = ScheduledJob.from_dict(data)
        assert restored.id == job.id
        assert restored.name == job.name
        assert restored.prompt == job.prompt
        assert restored.schedule_type == job.schedule_type
        assert restored.cron_expr == job.cron_expr

    def test_create_interval(self) -> None:
        job = ScheduledJob.create(
            name="poller",
            prompt="check status",
            schedule_type="interval",
            interval_seconds=300,
        )
        assert job.interval_seconds == 300
        assert job.schedule_type == "interval"

    def test_create_once(self) -> None:
        future = time.time() + 3600
        job = ScheduledJob.create(
            name="reminder",
            prompt="remind me",
            schedule_type="once",
            once_at=future,
        )
        assert job.once_at == future
        assert job.next_run_at == future

    def test_create_interval_sets_initial_next_run(self) -> None:
        before = time.time()
        job = ScheduledJob.create(
            name="poller",
            prompt="check status",
            schedule_type="interval",
            interval_seconds=300,
        )
        assert job.next_run_at is not None
        assert job.next_run_at >= before + 299

    def test_compute_job_next_run_matches_engine_logic(self) -> None:
        future = time.time() + 600
        job = ScheduledJob.create(
            name="reminder",
            prompt="remind me",
            schedule_type="once",
            once_at=future,
        )
        assert compute_job_next_run(job) == future


class TestJobExecutionRecord:
    def test_roundtrip_dict(self) -> None:
        record = JobExecutionRecord(
            job_id="abc123",
            job_name="test",
            started_at=1000.0,
            finished_at=1010.0,
            success=True,
            result_text="done",
        )
        data = record.to_dict()
        restored = JobExecutionRecord.from_dict(data)
        assert restored.job_id == "abc123"
        assert restored.success is True
        assert restored.result_text == "done"


# ---------------------------------------------------------------------------
# Engine — next-run calculation
# ---------------------------------------------------------------------------


class TestEngineNextRun:
    def test_cron_next_run(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="daily",
            prompt="hi",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        nxt = engine._compute_next_run(job)
        assert nxt is not None
        assert nxt > time.time()

    def test_once_future(self, engine: SchedulerEngine) -> None:
        future = time.time() + 3600
        job = ScheduledJob.create(
            name="once",
            prompt="hi",
            schedule_type="once",
            once_at=future,
        )
        nxt = engine._compute_next_run(job)
        assert nxt == future

    def test_once_past_returns_none(self, engine: SchedulerEngine) -> None:
        past = time.time() - 100
        job = ScheduledJob.create(
            name="past",
            prompt="hi",
            schedule_type="once",
            once_at=past,
        )
        nxt = engine._compute_next_run(job)
        assert nxt is None

    def test_interval_next_run(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="interval",
            prompt="hi",
            schedule_type="interval",
            interval_seconds=300,
        )
        job.created_at = time.time() - 100
        nxt = engine._compute_next_run(job)
        assert nxt is not None
        assert nxt > time.time()

    def test_disabled_returns_none(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="disabled",
            prompt="hi",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        job.enabled = False
        assert engine._compute_next_run(job) is None


class TestExecutionPrompt:
    def test_wraps_prompt_as_final_deliverable(self) -> None:
        wrapped = _build_execution_prompt(
            "请用中文提醒用户现在是11:30，该喝水了。保持简短友好。", locale="zh-CN"
        )

        assert "已经创建好的定时任务" in wrapped
        assert "不要询问澄清问题" in wrapped
        assert "不要索要 chat_id、open_id" in wrapped
        assert "直接写出要发送给用户的提醒内容" in wrapped
        assert "请用中文提醒用户现在是11:30，该喝水了。保持简短友好。" in wrapped

    def test_wraps_prompt_as_final_deliverable_in_english(self) -> None:
        wrapped = _build_execution_prompt("Remind the user to stretch now.", locale="en-US")

        assert "already-configured scheduled task" in wrapped
        assert "Do not ask clarifying questions" in wrapped
        assert "Do not request chat_id, open_id" in wrapped
        assert "Remind the user to stretch now." in wrapped


# ---------------------------------------------------------------------------
# Engine — CRUD
# ---------------------------------------------------------------------------


class TestEngineCRUD:
    def test_add_and_list(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="task1",
            prompt="do it",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        engine.add_job(job)
        jobs = engine.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == job.id

    def test_remove(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="task1",
            prompt="do it",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        engine.add_job(job)
        assert engine.remove_job(job.id) is True
        assert len(engine.list_jobs()) == 0

    def test_remove_nonexistent(self, engine: SchedulerEngine) -> None:
        assert engine.remove_job("nonexistent") is False

    def test_update(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="task1",
            prompt="do it",
            schedule_type="cron",
            cron_expr="0 9 * * *",
        )
        engine.add_job(job)
        updated = engine.update_job(job.id, name="renamed", enabled=False)
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.enabled is False

    def test_update_nonexistent(self, engine: SchedulerEngine) -> None:
        assert engine.update_job("nope", name="x") is None

    def test_get_job(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="findme",
            prompt="yo",
            schedule_type="interval",
            interval_seconds=120,
        )
        engine.add_job(job)
        found = engine.get_job(job.id)
        assert found is not None
        assert found.name == "findme"

    def test_get_job_not_found(self, engine: SchedulerEngine) -> None:
        assert engine.get_job("nope") is None


# ---------------------------------------------------------------------------
# Engine — persistence
# ---------------------------------------------------------------------------


class TestEnginePersistence:
    def test_persist_and_reload(self, tmp_settings: Any, hooks: HooksEngine) -> None:
        engine1 = SchedulerEngine(tmp_settings, hooks)
        job = ScheduledJob.create(
            name="persistent",
            prompt="saved",
            schedule_type="cron",
            cron_expr="*/5 * * * *",
        )
        engine1.add_job(job)

        engine2 = SchedulerEngine(tmp_settings, hooks)
        engine2._load_jobs()
        jobs = engine2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == "persistent"

    def test_history_append_and_read(self, engine: SchedulerEngine) -> None:
        record = JobExecutionRecord(
            job_id="abc",
            job_name="test",
            started_at=1000.0,
            finished_at=1005.0,
            success=True,
            result_text="ok",
        )
        engine._append_history(record)
        history = engine.get_history()
        assert len(history) == 1
        assert history[0].job_id == "abc"

    def test_log_file_written(self, engine: SchedulerEngine) -> None:
        record = JobExecutionRecord(
            job_id="xyz",
            job_name="logged",
            started_at=time.time(),
            finished_at=time.time() + 1,
            success=True,
            result_text="log test",
        )
        engine._write_log_file(record)
        logs = list(engine._logs_dir.glob("*.log"))
        assert len(logs) == 1
        content = logs[0].read_text()
        assert "logged" in content
        assert "log test" in content


# ---------------------------------------------------------------------------
# Engine — catch-up
# ---------------------------------------------------------------------------


class TestEngineCatchUp:
    def test_catchup_executes_missed_jobs(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="missed",
            prompt="catch me",
            schedule_type="once",
            once_at=time.time() - 10,
        )
        job.next_run_at = time.time() - 10
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "caught up"
            mock_run.return_value = mock_result
            engine._catchup_missed_jobs()
            mock_run.assert_called_once_with("catch me")

    def test_start_catchup_executes_missed_once_job_from_store(
        self,
        tmp_settings: Any,
        hooks: HooksEngine,
    ) -> None:
        persisted = SchedulerEngine(tmp_settings, hooks)
        job = ScheduledJob.create(
            name="missed-on-start",
            prompt="catch me on start",
            schedule_type="once",
            once_at=time.time() - 10,
        )
        job.next_run_at = time.time() - 10
        persisted._store.create_schedule(job)

        engine = SchedulerEngine(tmp_settings, hooks)
        executed = Event()

        def fake_execute(scheduled_job: ScheduledJob) -> None:
            assert scheduled_job.id == job.id
            executed.set()

        with patch.object(engine, "_execute", side_effect=fake_execute):
            engine.start(catch_up=True)
            try:
                assert executed.wait(0.2), "missed once job was not caught up during start"
            finally:
                engine.stop()


# ---------------------------------------------------------------------------
# Engine — DISPATCH_RESULT hook broadcast
# ---------------------------------------------------------------------------


class TestScheduleResultBroadcast:
    def test_hook_fired_on_execute(self, engine: SchedulerEngine, hooks: HooksEngine) -> None:
        received: list[dict[str, Any]] = []

        def handler(*, source: str, title: str, result_text: str, success: bool, **kw: Any) -> None:
            received.append(
                {"title": title, "text": result_text, "success": success, "source": source}
            )

        hooks.register(str(HookEvent.DISPATCH_RESULT), handler, priority=0)

        job = ScheduledJob.create(
            name="broadcast-test",
            prompt="run this",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        job.next_run_at = time.time() - 1
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "result from agent"
            mock_run.return_value = mock_result
            engine._execute(job)

        assert len(received) == 1
        assert received[0]["title"] == "broadcast-test"
        assert received[0]["text"] == "result from agent"
        assert received[0]["success"] is True
        assert received[0]["source"] == "scheduler"

    def test_multiple_handlers_all_called(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        calls: list[str] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: calls.append("h1"), priority=10)
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: calls.append("h2"), priority=20)

        job = ScheduledJob.create(
            name="multi",
            prompt="go",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "ok"
            mock_run.return_value = mock_result
            engine._execute(job)

        assert "h1" in calls
        assert "h2" in calls


# ---------------------------------------------------------------------------
# Engine — start / stop lifecycle
# ---------------------------------------------------------------------------


class TestEngineLifecycle:
    def test_start_and_stop(self, engine: SchedulerEngine) -> None:
        engine.start(catch_up=False)
        assert engine._thread is not None
        assert engine._thread.is_alive()
        engine.stop()
        assert not engine._thread.is_alive()

    def test_idle_scheduler_wakes_for_new_once_job(
        self,
        engine: SchedulerEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import hermit.plugins.builtin.hooks.scheduler.engine as scheduler_engine

        monkeypatch.setattr(scheduler_engine, "_POLL_INTERVAL", 5)
        executed = Event()
        execution_meta: dict[str, float] = {}

        def fake_execute(job: ScheduledJob) -> None:
            execution_meta["executed_at"] = time.time()
            executed.set()

        monkeypatch.setattr(engine, "_execute", fake_execute)
        engine.start(catch_up=False)
        try:
            time.sleep(0.05)
            job = ScheduledJob.create(
                name="wake-once",
                prompt="ping",
                schedule_type="once",
                once_at=time.time() + 0.05,
            )
            added_at = time.time()
            engine.add_job(job)

            assert executed.wait(0.5), "scheduler did not wake up for a newly added once job"
            assert execution_meta["executed_at"] < added_at + 0.5
        finally:
            engine.stop()


# ---------------------------------------------------------------------------
# Tools — validation
# ---------------------------------------------------------------------------


class TestToolHandlers:
    def test_create_validates_name(self) -> None:
        from hermit.plugins.builtin.hooks.scheduler.tools import _handle_create, set_engine

        mock_engine = MagicMock()
        set_engine(mock_engine)
        result = _handle_create({"name": "", "prompt": "x", "schedule_type": "cron"})
        assert "Error" in result
        set_engine(None)  # type: ignore[arg-type]

    def test_create_validates_schedule_type(self) -> None:
        from hermit.plugins.builtin.hooks.scheduler.tools import _handle_create, set_engine

        mock_engine = MagicMock()
        set_engine(mock_engine)
        result = _handle_create({"name": "x", "prompt": "x", "schedule_type": "bad"})
        assert "Error" in result
        set_engine(None)  # type: ignore[arg-type]

    def test_create_validates_cron_expr(self) -> None:
        from hermit.plugins.builtin.hooks.scheduler.tools import _handle_create, set_engine

        mock_engine = MagicMock()
        set_engine(mock_engine)
        result = _handle_create(
            {
                "name": "x",
                "prompt": "x",
                "schedule_type": "cron",
                "cron_expr": "invalid cron",
            }
        )
        assert "Error" in result
        set_engine(None)  # type: ignore[arg-type]

    def test_list_empty(self) -> None:
        from hermit.plugins.builtin.hooks.scheduler.tools import _handle_list, set_engine

        mock_engine = MagicMock()
        mock_engine.list_jobs.return_value = []
        set_engine(mock_engine)
        result = _handle_list({})
        assert "No scheduled tasks" in result
        set_engine(None)  # type: ignore[arg-type]

    def test_engine_not_running_error(self) -> None:
        from hermit.plugins.builtin.hooks.scheduler.tools import _handle_list, set_engine

        set_engine(None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="Scheduler engine not running"):
            _handle_list({})
