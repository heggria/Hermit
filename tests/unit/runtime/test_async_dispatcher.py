"""Tests for hermit.runtime.control.runner.async_dispatcher."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.runtime.control.runner.async_dispatcher import AsyncDispatcher
from hermit.runtime.control.runner.utils import DispatchResult
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
    defaults = dict(text="result text", turns=1, tool_calls=0)
    defaults.update(overrides)
    return AgentResult(**defaults)


def _make_session(session_id: str = "sess_1"):
    from hermit.runtime.control.lifecycle.session import Session

    return Session(session_id=session_id)


@pytest.fixture()
def mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.wake_dispatcher = MagicMock()
    runner._prepare_prompt_context.return_value = (
        _make_session(),
        "compiled prompt",
        {},
        "task goal",
    )
    runner.agent = MagicMock()
    runner.agent.workspace_root = "/tmp/workspace"
    return runner


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    return store


@pytest.fixture()
def mock_task_controller() -> MagicMock:
    tc = MagicMock()
    tc.source_from_session.return_value = "cli"
    tc.enqueue_task.return_value = _make_task_ctx()
    tc.enqueue_resume = MagicMock()
    return tc


@pytest.fixture()
def mock_session_manager() -> MagicMock:
    sm = MagicMock()
    sm.get_or_create.return_value = _make_session()
    return sm


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = SimpleNamespace(locale="en-US", base_dir="/tmp/test")
    pm.hooks = MagicMock()
    pm.hooks.fire.return_value = []
    return pm


@pytest.fixture()
def dispatcher(
    mock_runner: MagicMock,
    mock_store: MagicMock,
    mock_task_controller: MagicMock,
    mock_session_manager: MagicMock,
    mock_pm: MagicMock,
) -> AsyncDispatcher:
    return AsyncDispatcher(
        runner=mock_runner,
        store=mock_store,
        task_controller=mock_task_controller,
        session_manager=mock_session_manager,
        pm=mock_pm,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_dependencies(self, dispatcher: AsyncDispatcher) -> None:
        assert dispatcher._runner is not None
        assert dispatcher.store is not None
        assert dispatcher.task_controller is not None
        assert dispatcher.session_manager is not None
        assert dispatcher.pm is not None


# ---------------------------------------------------------------------------
# wake_dispatcher
# ---------------------------------------------------------------------------


class TestWakeDispatcher:
    def test_delegates_to_runner(self, dispatcher: AsyncDispatcher, mock_runner: MagicMock) -> None:
        dispatcher.wake_dispatcher()
        mock_runner.wake_dispatcher.assert_called_once()


# ---------------------------------------------------------------------------
# enqueue_ingress
# ---------------------------------------------------------------------------


class TestEnqueueIngress:
    def test_calls_enqueue_task(
        self, dispatcher: AsyncDispatcher, mock_task_controller: MagicMock
    ) -> None:
        result = dispatcher.enqueue_ingress("sess_1", "hello world")
        mock_task_controller.enqueue_task.assert_called_once()
        assert result is not None

    def test_uses_source_channel_override(
        self, dispatcher: AsyncDispatcher, mock_task_controller: MagicMock
    ) -> None:
        dispatcher.enqueue_ingress("sess_1", "hello", source_channel="feishu")
        call_kwargs = mock_task_controller.enqueue_task.call_args
        assert call_kwargs.kwargs["source_channel"] == "feishu"

    def test_wakes_dispatcher(self, dispatcher: AsyncDispatcher, mock_runner: MagicMock) -> None:
        dispatcher.enqueue_ingress("sess_1", "hello")
        mock_runner.wake_dispatcher.assert_called()

    def test_readonly_sets_plan_kind(
        self, dispatcher: AsyncDispatcher, mock_runner: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        mock_runner._prepare_prompt_context.return_value = (
            _make_session(),
            "prompt",
            {"readonly_only": True},
            "goal",
        )
        dispatcher.enqueue_ingress("sess_1", "hello")
        call_kwargs = mock_task_controller.enqueue_task.call_args
        assert call_kwargs.kwargs["kind"] == "plan"

    def test_planning_mode_enters_planning(
        self, dispatcher: AsyncDispatcher, mock_runner: MagicMock
    ) -> None:
        mock_runner._prepare_prompt_context.return_value = (
            _make_session(),
            "prompt",
            {"planning_mode": True},
            "goal",
        )
        with patch("hermit.runtime.control.runner.async_dispatcher.PlanningService") as MockPS:
            dispatcher.enqueue_ingress("sess_1", "hello")
            MockPS.return_value.enter_planning.assert_called_once()

    def test_metadata_includes_dispatch_mode(
        self, dispatcher: AsyncDispatcher, mock_task_controller: MagicMock
    ) -> None:
        dispatcher.enqueue_ingress("sess_1", "hello")
        call_kwargs = mock_task_controller.enqueue_task.call_args
        metadata = call_kwargs.kwargs["ingress_metadata"]
        assert metadata["dispatch_mode"] == "async"

    def test_custom_ingress_metadata(
        self, dispatcher: AsyncDispatcher, mock_task_controller: MagicMock
    ) -> None:
        dispatcher.enqueue_ingress("sess_1", "hello", ingress_metadata={"custom_key": "custom_val"})
        call_kwargs = mock_task_controller.enqueue_task.call_args
        metadata = call_kwargs.kwargs["ingress_metadata"]
        assert metadata["custom_key"] == "custom_val"


# ---------------------------------------------------------------------------
# enqueue_approval_resume
# ---------------------------------------------------------------------------


class TestEnqueueApprovalResume:
    def test_returns_not_found_when_approval_missing(
        self, dispatcher: AsyncDispatcher, mock_store: MagicMock
    ) -> None:
        mock_store.get_approval.return_value = None
        result = dispatcher.enqueue_approval_resume(
            "sess_1", action="approve_once", approval_id="missing_id"
        )
        assert isinstance(result, DispatchResult)
        assert result.is_command is True

    def test_deny_saves_session(
        self, dispatcher: AsyncDispatcher, mock_store: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        approval = MagicMock()
        approval.step_attempt_id = "attempt_1"
        mock_store.get_approval.return_value = approval

        result = dispatcher.enqueue_approval_resume(
            "sess_1", action="deny", approval_id="approval_1"
        )
        assert result.is_command is True
        mock_session_manager.save.assert_called_once()

    def test_approve_once_enqueues_resume(
        self,
        dispatcher: AsyncDispatcher,
        mock_store: MagicMock,
        mock_task_controller: MagicMock,
        mock_runner: MagicMock,
    ) -> None:
        approval = MagicMock()
        approval.step_attempt_id = "attempt_1"
        mock_store.get_approval.return_value = approval

        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = dispatcher.enqueue_approval_resume(
                "sess_1", action="approve_once", approval_id="approval_1"
            )
        assert result.is_command is True
        mock_task_controller.enqueue_resume.assert_called_once()
        mock_runner.wake_dispatcher.assert_called()

    def test_approve_mutable_workspace(
        self, dispatcher: AsyncDispatcher, mock_store: MagicMock
    ) -> None:
        approval = MagicMock()
        approval.step_attempt_id = "attempt_1"
        mock_store.get_approval.return_value = approval

        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService") as MockAS:
            dispatcher.enqueue_approval_resume(
                "sess_1", action="approve_mutable_workspace", approval_id="approval_1"
            )
            MockAS.return_value.approve_mutable_workspace.assert_called_once()


# ---------------------------------------------------------------------------
# emit_async_dispatch_result
# ---------------------------------------------------------------------------


class TestEmitAsyncDispatchResult:
    def test_returns_empty_when_no_notify(self, dispatcher: AsyncDispatcher) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={})
        result = _make_agent_result()
        outputs = dispatcher.emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        assert outputs == []

    def test_fires_hook_when_notify_present(
        self, dispatcher: AsyncDispatcher, mock_pm: MagicMock
    ) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={"notify": {"feishu_chat_id": "chat_123"}})
        result = _make_agent_result()
        dispatcher.emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        mock_pm.hooks.fire.assert_called_once()

    def test_passes_success_status(self, dispatcher: AsyncDispatcher, mock_pm: MagicMock) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={"notify": {"target": "someone"}})
        result = _make_agent_result(execution_status="succeeded")
        dispatcher.emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        call_kwargs = mock_pm.hooks.fire.call_args
        assert call_kwargs.kwargs["success"] is True

    def test_passes_failure_status(self, dispatcher: AsyncDispatcher, mock_pm: MagicMock) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={"notify": {"target": "someone"}})
        result = _make_agent_result(text="[API Error] fail", execution_status="")
        dispatcher.emit_async_dispatch_result(task_ctx, result, started_at=time.time())
        call_kwargs = mock_pm.hooks.fire.call_args
        assert call_kwargs.kwargs["success"] is False


# ---------------------------------------------------------------------------
# record_scheduler_execution
# ---------------------------------------------------------------------------


class TestRecordSchedulerExecution:
    def test_returns_early_when_no_job_id(self, dispatcher: AsyncDispatcher) -> None:
        task_ctx = _make_task_ctx(ingress_metadata={})
        result = _make_agent_result()
        # Should not raise
        dispatcher.record_scheduler_execution(task_ctx, result, started_at=time.time())
        dispatcher.task_controller.store.append_schedule_history.assert_not_called()

    def test_returns_early_when_no_settings(
        self, dispatcher: AsyncDispatcher, mock_pm: MagicMock
    ) -> None:
        mock_pm.settings = None
        task_ctx = _make_task_ctx(ingress_metadata={"schedule_job_id": "job_1"})
        result = _make_agent_result()
        dispatcher.record_scheduler_execution(task_ctx, result, started_at=time.time())
        dispatcher.task_controller.store.append_schedule_history.assert_not_called()

    def test_records_execution_with_job_id(
        self, dispatcher: AsyncDispatcher, tmp_path: Path
    ) -> None:
        dispatcher.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(
            ingress_metadata={
                "schedule_job_id": "job_1",
                "schedule_job_name": "Test Job",
            }
        )
        result = _make_agent_result(text="done", execution_status="succeeded")
        dispatcher.task_controller.store.list_schedule_history.return_value = []

        dispatcher.record_scheduler_execution(task_ctx, result, started_at=time.time())
        dispatcher.task_controller.store.append_schedule_history.assert_called_once()

    def test_records_feishu_delivery(self, dispatcher: AsyncDispatcher, tmp_path: Path) -> None:
        dispatcher.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(ingress_metadata={"schedule_job_id": "job_1"})
        result = _make_agent_result()
        dispatcher.task_controller.store.list_schedule_history.return_value = []

        delivery = [{"channel": "feishu", "status": "success", "message_id": "msg_1"}]
        dispatcher.record_scheduler_execution(
            task_ctx, result, started_at=time.time(), delivery_results=delivery
        )
        dispatcher.task_controller.store.append_schedule_history.assert_called_once()

    def test_records_feishu_failure_from_notify(
        self, dispatcher: AsyncDispatcher, tmp_path: Path
    ) -> None:
        dispatcher.pm.settings = SimpleNamespace(base_dir=str(tmp_path))
        task_ctx = _make_task_ctx(
            ingress_metadata={
                "schedule_job_id": "job_1",
                "notify": {"feishu_chat_id": "chat_1"},
            }
        )
        result = _make_agent_result()
        dispatcher.task_controller.store.list_schedule_history.return_value = []

        dispatcher.record_scheduler_execution(
            task_ctx, result, started_at=time.time(), delivery_results=[]
        )
        dispatcher.task_controller.store.append_schedule_history.assert_called_once()
