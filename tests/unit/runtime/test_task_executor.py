"""Tests for hermit.runtime.control.runner.task_executor."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
from hermit.runtime.control.runner.task_executor import RunnerTaskExecutor
from hermit.runtime.provider_host.execution.runtime import AgentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = dict(
        conversation_id="conv_1",
        task_id="task_1",
        step_id="step_1",
        step_attempt_id="attempt_1",
        source_channel="test",
        policy_profile="default",
        workspace_root="",
        ingress_metadata={},
    )
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_agent_result(**overrides: Any) -> AgentResult:
    defaults = dict(
        text="result text",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": "result text"}],
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
        cache_creation_tokens=3,
    )
    defaults.update(overrides)
    return AgentResult(**defaults)


def _make_session(session_id: str = "conv_1"):
    from hermit.runtime.control.lifecycle.session import Session

    return Session(session_id=session_id)


@pytest.fixture()
def mock_session_manager() -> MagicMock:
    sm = MagicMock()
    sm.get_or_create.return_value = _make_session()
    return sm


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    store.db_path = None  # causes lightweight fallback
    return store


@pytest.fixture()
def mock_task_controller() -> MagicMock:
    tc = MagicMock()
    tc.finalize_result = MagicMock()
    tc.mark_suspended = MagicMock()
    tc.mark_blocked = MagicMock()
    tc.mark_planning_ready = MagicMock()
    tc.update_attempt_phase = MagicMock()
    tc.ensure_conversation = MagicMock()
    tc.context_for_attempt.return_value = _make_task_ctx()
    tc.store = MagicMock()
    tc.store.get_step_attempt.return_value = MagicMock(context={"execution_mode": "run"})
    tc.store.append_schedule_history = MagicMock()
    tc.store.list_schedule_history.return_value = []
    return tc


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = SimpleNamespace(locale="en-US", base_dir="/tmp/test", max_session_messages=100)
    pm.on_post_run = MagicMock()
    pm.hooks = MagicMock()
    pm.hooks.fire.return_value = []
    return pm


@pytest.fixture()
def mock_runtime() -> MagicMock:
    rt = MagicMock()
    rt.run.return_value = _make_agent_result()
    rt.resume.return_value = _make_agent_result()
    rt.artifact_store = None
    return rt


@pytest.fixture()
def executor(
    mock_session_manager: MagicMock,
    mock_store: MagicMock,
    mock_task_controller: MagicMock,
    mock_pm: MagicMock,
    mock_runtime: MagicMock,
) -> RunnerTaskExecutor:
    return RunnerTaskExecutor(
        session_manager=mock_session_manager,
        store=mock_store,
        task_controller=mock_task_controller,
        pm=mock_pm,
        runtime=mock_runtime,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_dependencies(self, executor: RunnerTaskExecutor) -> None:
        assert executor.session_manager is not None
        assert executor.store is not None
        assert executor.task_controller is not None
        assert executor.pm is not None
        assert executor.runtime is not None
        assert executor.observation_service is None


# ---------------------------------------------------------------------------
# _max_session_messages
# ---------------------------------------------------------------------------


class TestMaxSessionMessages:
    def test_returns_settings_value(self, executor: RunnerTaskExecutor) -> None:
        executor.pm.settings.max_session_messages = 50
        assert executor._max_session_messages() == 50

    def test_default_when_none(self, executor: RunnerTaskExecutor) -> None:
        executor.pm.settings.max_session_messages = None
        assert executor._max_session_messages() == 100

    def test_default_when_no_settings(self, executor: RunnerTaskExecutor) -> None:
        executor.pm.settings = None
        assert executor._max_session_messages() == 100


# ---------------------------------------------------------------------------
# _provider_input_compiler
# ---------------------------------------------------------------------------


class TestProviderInputCompiler:
    def test_raises_when_store_is_none(self, executor: RunnerTaskExecutor) -> None:
        executor.store = None
        with pytest.raises(RuntimeError, match="compiled_context_unavailable"):
            executor._provider_input_compiler()

    def test_raises_when_db_path_is_none(self, executor: RunnerTaskExecutor) -> None:
        executor.store.db_path = None
        with pytest.raises(RuntimeError, match="compiled_context_unavailable"):
            executor._provider_input_compiler()


# ---------------------------------------------------------------------------
# _compile_lightweight_input
# ---------------------------------------------------------------------------


class TestCompileLightweightInput:
    def test_basic_compilation(self, executor: RunnerTaskExecutor) -> None:
        result = executor._compile_lightweight_input(prompt="hello", session_messages=[])
        assert isinstance(result, CompiledProviderInput)
        assert result.source_mode == "lightweight"
        assert any(m.get("content") == "hello" for m in result.messages)

    def test_limits_messages(self, executor: RunnerTaskExecutor) -> None:
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
        result = executor._compile_lightweight_input(
            prompt="hello", session_messages=messages, max_recent=5
        )
        assert len(result.messages) <= 7


# ---------------------------------------------------------------------------
# _compile_provider_input — falls back to lightweight
# ---------------------------------------------------------------------------


class TestCompileProviderInput:
    def test_falls_back_to_lightweight(self, executor: RunnerTaskExecutor) -> None:
        executor.store = None
        task_ctx = _make_task_ctx()
        result = executor._compile_provider_input(
            task_ctx=task_ctx, prompt="hello", raw_text="hello"
        )
        assert result.source_mode == "lightweight"


# ---------------------------------------------------------------------------
# _result_status / _result_preview
# ---------------------------------------------------------------------------


class TestStaticHelpers:
    def test_result_status_succeeded(self) -> None:
        result = _make_agent_result(execution_status="succeeded")
        assert RunnerTaskExecutor._result_status(result) == "succeeded"

    def test_result_status_failed(self) -> None:
        result = _make_agent_result(text="[API Error] x", execution_status="")
        assert RunnerTaskExecutor._result_status(result) == "failed"

    def test_result_preview(self) -> None:
        preview = RunnerTaskExecutor._result_preview("hello world")
        assert preview == "hello world"

    def test_result_preview_truncates(self) -> None:
        text = "x" * 500
        preview = RunnerTaskExecutor._result_preview(text, limit=50)
        assert len(preview) <= 50


# ---------------------------------------------------------------------------
# _trim_session_messages
# ---------------------------------------------------------------------------


class TestTrimSessionMessages:
    def test_no_trim_needed(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        result = RunnerTaskExecutor._trim_session_messages(messages, max_messages=10)
        assert len(result) == 1

    def test_trims_to_max(self) -> None:
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = RunnerTaskExecutor._trim_session_messages(messages, max_messages=5)
        assert len(result) == 5

    def test_preserves_system_first(self) -> None:
        messages = [{"role": "system", "content": "sys"}]
        messages += [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = RunnerTaskExecutor._trim_session_messages(messages, max_messages=5)
        assert result[0]["role"] == "system"


# ---------------------------------------------------------------------------
# _maybe_capture_planning_result
# ---------------------------------------------------------------------------


class TestMaybeCapturePlanningResult:
    def test_returns_false_when_not_readonly(self, executor: RunnerTaskExecutor) -> None:
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            executor._maybe_capture_planning_result(task_ctx, result, readonly_only=False) is False
        )

    def test_returns_false_when_no_store(self, executor: RunnerTaskExecutor) -> None:
        executor.store = None
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            executor._maybe_capture_planning_result(task_ctx, result, readonly_only=True) is False
        )

    def test_returns_false_when_step_not_plan(self, executor: RunnerTaskExecutor) -> None:
        step = MagicMock()
        step.kind = "respond"
        executor.store.get_step.return_value = step
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            executor._maybe_capture_planning_result(task_ctx, result, readonly_only=True) is False
        )

    def test_captures_plan_result(self, executor: RunnerTaskExecutor) -> None:
        step = MagicMock()
        step.kind = "plan"
        executor.store.get_step.return_value = step

        task_ctx = _make_task_ctx()
        result = _make_agent_result(text="Plan: do things")

        with patch("hermit.runtime.control.runner.task_executor.PlanningService") as MockPS:
            MockPS.return_value.capture_plan_result.return_value = "ref_1"
            captured = executor._maybe_capture_planning_result(task_ctx, result, readonly_only=True)

        assert captured is True
        assert result.execution_status == "planning_ready"
        assert result.status_managed_by_kernel is True
        executor.task_controller.mark_planning_ready.assert_called_once()


# ---------------------------------------------------------------------------
# run_existing_task
# ---------------------------------------------------------------------------


class TestRunExistingTask:
    def test_runs_agent_and_returns_result(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        task_ctx = _make_task_ctx()
        result = executor.run_existing_task(task_ctx, "do something")
        mock_runtime.run.assert_called_once()
        assert isinstance(result, AgentResult)

    def test_updates_session_tokens(
        self, executor: RunnerTaskExecutor, mock_session_manager: MagicMock
    ) -> None:
        task_ctx = _make_task_ctx()
        session = _make_session()
        mock_session_manager.get_or_create.return_value = session

        executor.run_existing_task(task_ctx, "do something")
        assert session.total_input_tokens == 10
        assert session.total_output_tokens == 20
        mock_session_manager.save.assert_called_once()

    def test_marks_suspended_when_blocked(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=True)
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        executor.task_controller.mark_suspended.assert_called_once()

    def test_finalizes_result_on_success(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=False, blocked=False)
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        executor.task_controller.finalize_result.assert_called_once()

    def test_skips_finalize_when_kernel_managed(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(
            suspended=False, blocked=False, status_managed_by_kernel=True
        )
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        executor.task_controller.finalize_result.assert_not_called()

    def test_fires_post_run_hook(
        self, executor: RunnerTaskExecutor, mock_pm: MagicMock, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=False, blocked=False)
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        mock_pm.on_post_run.assert_called_once()

    def test_does_not_fire_post_run_when_suspended(
        self, executor: RunnerTaskExecutor, mock_pm: MagicMock, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=True)
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        mock_pm.on_post_run.assert_not_called()

    def test_updates_attempt_phase(
        self, executor: RunnerTaskExecutor, mock_task_controller: MagicMock
    ) -> None:
        task_ctx = _make_task_ctx()
        executor.run_existing_task(task_ctx, "do something")
        mock_task_controller.update_attempt_phase.assert_called_once_with(
            "attempt_1", phase="executing"
        )


# ---------------------------------------------------------------------------
# process_claimed_attempt — run mode
# ---------------------------------------------------------------------------


class TestProcessClaimedAttemptRun:
    def test_runs_agent(self, executor: RunnerTaskExecutor, mock_runtime: MagicMock) -> None:
        result = executor.process_claimed_attempt("attempt_1", ensure_session_started=MagicMock())
        mock_runtime.run.assert_called_once()
        assert isinstance(result, AgentResult)

    def test_calls_ensure_session_started(self, executor: RunnerTaskExecutor) -> None:
        callback = MagicMock()
        executor.process_claimed_attempt("attempt_1", ensure_session_started=callback)
        callback.assert_called_once_with("conv_1")

    def test_handles_exception(self, executor: RunnerTaskExecutor, mock_runtime: MagicMock) -> None:
        mock_runtime.run.side_effect = RuntimeError("API down")
        result = executor.process_claimed_attempt("attempt_1")
        assert "[API Error]" in result.text
        assert result.execution_status == "failed"

    def test_suspended_result_returns_early(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=True)
        result = executor.process_claimed_attempt("attempt_1")
        assert result.suspended is True
        executor.task_controller.mark_suspended.assert_called_once()
        executor.task_controller.finalize_result.assert_not_called()

    def test_finalizes_on_success(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock
    ) -> None:
        mock_runtime.run.return_value = _make_agent_result(suspended=False, blocked=False)
        executor.process_claimed_attempt("attempt_1")
        executor.task_controller.finalize_result.assert_called_once()


# ---------------------------------------------------------------------------
# process_claimed_attempt — resume mode
# ---------------------------------------------------------------------------


class TestProcessClaimedAttemptResume:
    def test_resume_mode(
        self, executor: RunnerTaskExecutor, mock_runtime: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_step_attempt.return_value = MagicMock(
            context={"execution_mode": "resume"}
        )
        executor.process_claimed_attempt("attempt_1")
        mock_runtime.resume.assert_called_once()


# ---------------------------------------------------------------------------
# _emit_async_dispatch_result
# ---------------------------------------------------------------------------


class TestEmitAsyncDispatchResult:
    def test_returns_empty_when_no_notify(self, executor: RunnerTaskExecutor) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={})
        result = _make_agent_result()
        outputs = executor._emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        assert outputs == []

    def test_fires_hook_with_notify(self, executor: RunnerTaskExecutor, mock_pm: MagicMock) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={"notify": {"feishu_chat_id": "chat_1"}})
        result = _make_agent_result()
        executor._emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        mock_pm.hooks.fire.assert_called_once()


# ---------------------------------------------------------------------------
# _record_scheduler_execution
# ---------------------------------------------------------------------------


class TestRecordSchedulerExecution:
    def test_skips_when_no_job_id(self, executor: RunnerTaskExecutor) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={})
        result = _make_agent_result()
        executor._record_scheduler_execution(task_ctx, result, started_at=time.time())
        executor.task_controller.store.append_schedule_history.assert_not_called()

    def test_skips_when_no_settings(self, executor: RunnerTaskExecutor, mock_pm: MagicMock) -> None:
        mock_pm.settings = None
        task_ctx = _make_task_ctx(ingress_metadata={"schedule_job_id": "job_1"})
        result = _make_agent_result()
        executor._record_scheduler_execution(task_ctx, result, started_at=time.time())
        executor.task_controller.store.append_schedule_history.assert_not_called()

    def test_records_when_job_id_present(
        self, executor: RunnerTaskExecutor, tmp_path: Path
    ) -> None:
        executor.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(
            ingress_metadata={"schedule_job_id": "job_1", "schedule_job_name": "Test"}
        )
        result = _make_agent_result(execution_status="succeeded")
        executor._record_scheduler_execution(task_ctx, result, started_at=time.time())
        executor.task_controller.store.append_schedule_history.assert_called_once()

    def test_records_with_feishu_delivery(
        self, executor: RunnerTaskExecutor, tmp_path: Path
    ) -> None:
        executor.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(ingress_metadata={"schedule_job_id": "job_1"})
        result = _make_agent_result()
        delivery = [{"channel": "feishu", "status": "ok", "message_id": "m1"}]
        executor._record_scheduler_execution(
            task_ctx, result, started_at=time.time(), delivery_results=delivery
        )
        executor.task_controller.store.append_schedule_history.assert_called_once()

    def test_records_feishu_failure_without_delivery(
        self, executor: RunnerTaskExecutor, tmp_path: Path
    ) -> None:
        executor.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(
            ingress_metadata={
                "schedule_job_id": "job_1",
                "notify": {"feishu_chat_id": "chat_1"},
            }
        )
        result = _make_agent_result()
        executor._record_scheduler_execution(
            task_ctx, result, started_at=time.time(), delivery_results=[]
        )
        executor.task_controller.store.append_schedule_history.assert_called_once()
