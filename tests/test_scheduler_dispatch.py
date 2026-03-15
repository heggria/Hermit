"""Tests for scheduler DISPATCH_RESULT migration — only fires DISPATCH_RESULT."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.builtin.scheduler.engine import SchedulerEngine
from hermit.builtin.scheduler.models import ScheduledJob
from hermit.core.runner import AgentRunner
from hermit.plugin.base import HookEvent
from hermit.plugin.hooks import HooksEngine


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Any:
    settings = MagicMock()
    settings.base_dir = tmp_path
    settings.locale = "zh-CN"
    settings.sandbox_mode = "l0"
    settings.command_timeout_seconds = 30
    settings.model = "test-model"
    settings.scheduler_feishu_chat_id = ""
    return settings


@pytest.fixture
def hooks() -> HooksEngine:
    return HooksEngine()


@pytest.fixture
def engine(tmp_settings: Any, hooks: HooksEngine) -> SchedulerEngine:
    return SchedulerEngine(settings=tmp_settings, hooks=hooks)


def _make_real_runner() -> AgentRunner:
    return AgentRunner(
        agent=MagicMock(workspace_root="/tmp/workspace"),
        session_manager=SimpleNamespace(),
        plugin_manager=SimpleNamespace(settings=SimpleNamespace(locale="zh-CN")),
        task_controller=SimpleNamespace(source_from_session=lambda _session_id: "scheduler"),
    )


class TestSchedulerFiresDispatchResult:
    def test_execute_wraps_prompt_before_running_agent(self, engine: SchedulerEngine) -> None:
        job = ScheduledJob.create(
            name="drink-water",
            prompt="提醒我喝水",
            schedule_type="once",
            once_at=time.time() - 1,
        )

        captured_prompt: dict[str, str] = {}

        def fake_run(prompt: str) -> Any:
            captured_prompt["value"] = prompt
            mock_result = MagicMock()
            mock_result.text = "done"
            return mock_result

        with patch.object(engine, "_run_agent_via_runner", side_effect=fake_run):
            engine._runner = MagicMock()
            engine._execute(job)

        assert "已经创建好的定时任务" in captured_prompt["value"]
        assert "不要索要 chat_id、open_id" in captured_prompt["value"]
        assert "提醒我喝水" in captured_prompt["value"]

    def test_fires_dispatch_result_not_schedule_result(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))

        job = ScheduledJob.create(
            name="weekly-report",
            prompt="summarize the week",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "Summary complete"
            mock_run.return_value = mock_result
            engine._execute(job)

        assert len(dispatch_events) == 1
        ev = dispatch_events[0]
        assert ev["source"] == "scheduler"
        assert ev["title"] == "weekly-report"
        assert ev["result_text"] == "Summary complete"
        assert ev["success"] is True
        assert ev["error"] is None
        assert "job_id" in ev["metadata"]
        assert ev["metadata"]["job_id"] == job.id

    def test_notify_includes_feishu_chat_id_from_settings(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        engine._settings.scheduler_feishu_chat_id = "oc_env_chat"
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))

        job = ScheduledJob.create(
            name="env-test",
            prompt="go",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "done"
            mock_run.return_value = mock_result
            engine._execute(job)

        assert dispatch_events[0]["notify"] == {"feishu_chat_id": "oc_env_chat"}

    def test_notify_includes_feishu_chat_id_from_job(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))

        job = ScheduledJob.create(
            name="job-chat-test",
            prompt="go",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        job.feishu_chat_id = "oc_job_chat"  # type: ignore[attr-defined]
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent") as mock_run:
            mock_result = MagicMock()
            mock_result.text = "done"
            mock_run.return_value = mock_result
            engine._execute(job)

        assert dispatch_events[0]["notify"] == {"feishu_chat_id": "oc_job_chat"}

    def test_failed_execution_sets_success_false(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))

        job = ScheduledJob.create(
            name="fail-test",
            prompt="go",
            max_retries=1,
            schedule_type="once",
            once_at=time.time() - 1,
        )
        with engine._lock:
            engine._jobs.append(job)

        with patch.object(engine, "_run_agent", side_effect=RuntimeError("boom")):
            engine._execute(job)

        assert dispatch_events[0]["success"] is False
        assert "boom" in (dispatch_events[0]["error"] or "")

    def test_no_schedule_result_event_fired(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        schedule_events: list[Any] = []
        hooks.register("schedule_result", lambda **kw: schedule_events.append(kw))

        job = ScheduledJob.create(
            name="no-old-event",
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

        assert schedule_events == [], "SCHEDULE_RESULT should no longer be fired"

    def test_execute_enqueues_async_ingress_for_agent_runner(
        self, engine: SchedulerEngine, hooks: HooksEngine
    ) -> None:
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))
        runner = _make_real_runner()
        enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        runner.enqueue_ingress = lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_1")
        )  # type: ignore[method-assign]
        engine._runner = runner

        job = ScheduledJob.create(
            name="async-job",
            prompt="run async",
            schedule_type="once",
            once_at=time.time() - 1,
        )
        job.feishu_chat_id = "oc_async"  # type: ignore[attr-defined]

        engine._execute(job)

        assert len(enqueue_calls) == 1
        args, kwargs = enqueue_calls[0]
        assert args[1] == "run async"
        assert kwargs["source_channel"] == "scheduler"
        assert kwargs["notify"] == {"feishu_chat_id": "oc_async", "delivery_mode": "new_message"}
        assert kwargs["source_ref"] == "scheduler"
        assert kwargs["requested_by"] == "scheduler"
        assert kwargs["ingress_metadata"]["schedule_job_id"] == job.id
        assert kwargs["ingress_metadata"]["schedule_job_name"] == "async-job"
        assert dispatch_events == []
