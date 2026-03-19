"""Tests for hermit.runtime.control.runner.runner — AgentRunner."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.runtime.control.runner.runner import (
    AgentRunner,
    _cmd_help,
    _cmd_history,
    _cmd_new,
    _cmd_quit,
    _cmd_task,
)
from hermit.runtime.control.runner.utils import DispatchResult
from hermit.runtime.provider_host.execution.runtime import AgentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(session_id: str = "sess_1"):
    from hermit.runtime.control.lifecycle.session import Session

    return Session(session_id=session_id)


def _make_agent_result(**overrides: Any) -> AgentResult:
    defaults = dict(
        text="result",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": "result"}],
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
        cache_creation_tokens=3,
    )
    defaults.update(overrides)
    return AgentResult(**defaults)


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


@pytest.fixture()
def mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.workspace_root = "/tmp/workspace"
    agent.kernel_store = MagicMock()
    agent.artifact_store = MagicMock()
    agent.run.return_value = _make_agent_result()
    agent.resume.return_value = _make_agent_result()
    return agent


@pytest.fixture()
def mock_session_manager() -> MagicMock:
    sm = MagicMock()
    sm.get_or_create.return_value = _make_session()
    sm.close = MagicMock()
    sm.save = MagicMock()
    return sm


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = SimpleNamespace(
        locale="en-US",
        base_dir="/tmp/test",
        max_session_messages=100,
        kernel_dispatch_worker_count=2,
    )
    pm.on_session_start = MagicMock()
    pm.on_session_end = MagicMock()
    pm.on_pre_run.return_value = ("processed prompt", {})
    pm.on_post_run = MagicMock()
    return pm


@pytest.fixture()
def mock_task_controller() -> MagicMock:
    tc = MagicMock()
    tc.store = MagicMock()
    tc.source_from_session.return_value = "cli"
    tc.resolve_text_command.return_value = None
    tc.start_task.return_value = _make_task_ctx()
    tc.finalize_result = MagicMock()
    tc.mark_suspended = MagicMock()
    tc.mark_blocked = MagicMock()
    tc.update_attempt_phase = MagicMock()
    tc.ensure_conversation = MagicMock()
    tc.context_for_attempt.return_value = _make_task_ctx()
    tc.resume_attempt.return_value = _make_task_ctx()
    return tc


@pytest.fixture()
def runner(
    mock_agent: MagicMock,
    mock_session_manager: MagicMock,
    mock_pm: MagicMock,
    mock_task_controller: MagicMock,
) -> AgentRunner:
    return AgentRunner(
        agent=mock_agent,
        session_manager=mock_session_manager,
        plugin_manager=mock_pm,
        task_controller=mock_task_controller,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_requires_task_controller(
        self, mock_agent: MagicMock, mock_session_manager: MagicMock, mock_pm: MagicMock
    ) -> None:
        with pytest.raises(ValueError, match="requires a TaskController"):
            AgentRunner(
                agent=mock_agent,
                session_manager=mock_session_manager,
                plugin_manager=mock_pm,
                task_controller=None,
            )

    def test_stores_dependencies(self, runner: AgentRunner) -> None:
        assert runner.agent is not None
        assert runner.session_manager is not None
        assert runner.pm is not None
        assert runner.task_controller is not None
        assert runner.serve_mode is False

    def test_serve_mode(
        self,
        mock_agent: MagicMock,
        mock_session_manager: MagicMock,
        mock_pm: MagicMock,
        mock_task_controller: MagicMock,
    ) -> None:
        r = AgentRunner(
            agent=mock_agent,
            session_manager=mock_session_manager,
            plugin_manager=mock_pm,
            task_controller=mock_task_controller,
            serve_mode=True,
        )
        assert r.serve_mode is True

    def test_commands_populated(self, runner: AgentRunner) -> None:
        # Core commands should be registered
        assert "/new" in runner._commands
        assert "/history" in runner._commands
        assert "/quit" in runner._commands
        assert "/help" in runner._commands
        assert "/task" in runner._commands


# ---------------------------------------------------------------------------
# register_command / core_command_specs / command_specs
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    def test_core_command_specs(self) -> None:
        specs = AgentRunner.core_command_specs()
        assert "/new" in specs
        assert "/help" in specs

    def test_command_specs_property(self, runner: AgentRunner) -> None:
        assert runner.command_specs is runner._commands

    def test_add_command(self, runner: AgentRunner) -> None:
        handler = MagicMock()
        runner.add_command("/custom", handler, "Custom command")
        assert "/custom" in runner._commands
        assert runner._commands["/custom"][0] is handler


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


class TestSessionHelpers:
    def test_max_session_messages(self, runner: AgentRunner) -> None:
        assert runner._max_session_messages() == 100

    def test_max_session_messages_default(self, runner: AgentRunner) -> None:
        runner.pm.settings = None
        assert runner._max_session_messages() == 100

    def test_ensure_session_started(self, runner: AgentRunner, mock_pm: MagicMock) -> None:
        runner._ensure_session_started("sess_1")
        mock_pm.on_session_start.assert_called_once_with("sess_1")
        assert "sess_1" in runner._session_started

    def test_ensure_session_started_idempotent(
        self, runner: AgentRunner, mock_pm: MagicMock
    ) -> None:
        runner._ensure_session_started("sess_1")
        runner._ensure_session_started("sess_1")
        mock_pm.on_session_start.assert_called_once()

    def test_close_session(
        self, runner: AgentRunner, mock_session_manager: MagicMock, mock_pm: MagicMock
    ) -> None:
        runner._session_started.add("sess_1")
        runner.close_session("sess_1")
        mock_pm.on_session_end.assert_called_once()
        mock_session_manager.close.assert_called_once_with("sess_1")
        assert "sess_1" not in runner._session_started

    def test_reset_session(self, runner: AgentRunner, mock_pm: MagicMock) -> None:
        runner.reset_session("sess_1")
        # close_session fires on_session_end, then new session fires on_session_start
        assert "sess_1" in runner._session_started

    def test_wake_dispatcher_no_service(self, runner: AgentRunner) -> None:
        runner._dispatch_service = None
        runner.wake_dispatcher()  # should not raise

    def test_wake_dispatcher_with_service(self, runner: AgentRunner) -> None:
        service = MagicMock()
        service.wake = MagicMock()
        runner._dispatch_service = service
        runner.wake_dispatcher()
        service.wake.assert_called_once()


# ---------------------------------------------------------------------------
# _get_store
# ---------------------------------------------------------------------------


class TestGetStore:
    def test_returns_store(self, runner: AgentRunner) -> None:
        store = runner._get_store()
        assert store is runner.task_controller.store


# ---------------------------------------------------------------------------
# dispatch — slash commands
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_unknown_command(self, runner: AgentRunner) -> None:
        result = runner.dispatch("sess_1", "/unknown_cmd")
        assert result.is_command is True

    def test_known_slash_command(self, runner: AgentRunner) -> None:
        result = runner.dispatch("sess_1", "/new")
        assert result.is_command is True

    def test_control_action_resolution(
        self, runner: AgentRunner, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.resolve_text_command.return_value = ("new_session", "", "")
        with patch.object(runner, "dispatch_control_action") as mock_dispatch:
            mock_dispatch.return_value = DispatchResult("ok", is_command=True)
            runner.dispatch("sess_1", "some text")
            mock_dispatch.assert_called_once()

    def test_regular_text_goes_to_handle(self, runner: AgentRunner) -> None:
        with patch.object(runner, "handle") as mock_handle:
            mock_handle.return_value = _make_agent_result()
            result = runner.dispatch("sess_1", "hello world")
            mock_handle.assert_called_once()
            assert result.is_command is False


# ---------------------------------------------------------------------------
# _result_status
# ---------------------------------------------------------------------------


class TestResultStatus:
    def test_static_method(self) -> None:
        result = _make_agent_result(execution_status="custom")
        assert AgentRunner._result_status(result) == "custom"


# ---------------------------------------------------------------------------
# Core slash commands
# ---------------------------------------------------------------------------


class TestCoreCommands:
    def test_cmd_new(self, runner: AgentRunner) -> None:
        result = _cmd_new(runner, "sess_1", "/new")
        assert result.is_command is True

    def test_cmd_history(self, runner: AgentRunner) -> None:
        session = _make_session()
        session.messages = [{"role": "user", "content": "hi"}]
        runner.session_manager.get_or_create.return_value = session
        result = _cmd_history(runner, "sess_1", "/history")
        assert result.is_command is True

    def test_cmd_quit(self, runner: AgentRunner) -> None:
        result = _cmd_quit(runner, "sess_1", "/quit")
        assert result.is_command is True
        assert result.should_exit is True

    def test_cmd_help(self, runner: AgentRunner) -> None:
        result = _cmd_help(runner, "sess_1", "/help")
        assert result.is_command is True

    def test_cmd_help_serve_mode_hides_cli_only(self, runner: AgentRunner) -> None:
        runner.serve_mode = True
        result = _cmd_help(runner, "sess_1", "/help")
        assert result.is_command is True
        assert "/quit" not in result.text

    def test_cmd_task_bad_usage(self, runner: AgentRunner) -> None:
        result = _cmd_task(runner, "sess_1", "/task")
        assert result.is_command is True

    def test_cmd_task_approve(self, runner: AgentRunner) -> None:
        with patch.object(runner, "dispatch_control_action") as mock_dispatch:
            mock_dispatch.return_value = DispatchResult("ok", is_command=True)
            result = _cmd_task(runner, "sess_1", "/task approve approval_1")
            mock_dispatch.assert_called_once()
            assert result.is_command is True

    def test_cmd_task_deny(self, runner: AgentRunner) -> None:
        with patch.object(runner, "dispatch_control_action") as mock_dispatch:
            mock_dispatch.return_value = DispatchResult("ok", is_command=True)
            _cmd_task(runner, "sess_1", "/task deny approval_1")
            mock_dispatch.assert_called_once()

    def test_cmd_task_case(self, runner: AgentRunner) -> None:
        with patch.object(runner, "dispatch_control_action") as mock_dispatch:
            mock_dispatch.return_value = DispatchResult("ok", is_command=True)
            _cmd_task(runner, "sess_1", "/task case task_1")
            mock_dispatch.assert_called_once()

    def test_cmd_task_rollback(self, runner: AgentRunner) -> None:
        with patch.object(runner, "dispatch_control_action") as mock_dispatch:
            mock_dispatch.return_value = DispatchResult("ok", is_command=True)
            _cmd_task(runner, "sess_1", "/task rollback task_1")
            mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Background services
# ---------------------------------------------------------------------------


class TestBackgroundServices:
    def test_start_background_services(self, runner: AgentRunner) -> None:
        with (
            patch("hermit.runtime.control.runner.runner.ObservationService") as MockObs,
            patch(
                "hermit.kernel.execution.coordination.dispatch.KernelDispatchService"
            ) as MockDisp,
        ):
            MockObs.return_value.start = MagicMock()
            MockDisp.return_value.start = MagicMock()
            runner.start_background_services()
            assert runner._observation_service is not None
            assert runner._dispatch_service is not None

    def test_stop_background_services(self, runner: AgentRunner) -> None:
        runner._dispatch_service = MagicMock()
        runner._observation_service = MagicMock()
        runner.stop_background_services()
        assert runner._dispatch_service is None
        assert runner._observation_service is None

    def test_stop_background_services_idempotent(self, runner: AgentRunner) -> None:
        runner._dispatch_service = None
        runner._observation_service = None
        runner.stop_background_services()  # should not raise


# ---------------------------------------------------------------------------
# resume_attempt
# ---------------------------------------------------------------------------


class TestResumeAttempt:
    def test_resumes_and_updates_session(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        session = _make_session()
        mock_session_manager.get_or_create.return_value = session

        result = runner.resume_attempt("attempt_1")
        mock_agent.resume.assert_called_once()
        mock_session_manager.save.assert_called_once()
        assert isinstance(result, AgentResult)

    def test_marks_suspended_when_blocked(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        mock_agent.resume.return_value = _make_agent_result(suspended=True)
        runner.resume_attempt("attempt_1")
        mock_task_controller.mark_suspended.assert_called_once()

    def test_finalizes_result_on_success(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        mock_agent.resume.return_value = _make_agent_result(suspended=False, blocked=False)
        runner.resume_attempt("attempt_1")
        mock_task_controller.finalize_result.assert_called_once()

    def test_fires_post_run_on_success(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_pm: MagicMock
    ) -> None:
        mock_agent.resume.return_value = _make_agent_result(suspended=False, blocked=False)
        runner.resume_attempt("attempt_1")
        mock_pm.on_post_run.assert_called_once()


# ---------------------------------------------------------------------------
# dispatch_control_action
# ---------------------------------------------------------------------------


class TestDispatchControlAction:
    def test_delegates_to_control_action_dispatcher(self, runner: AgentRunner) -> None:
        with patch(
            "hermit.runtime.control.runner.control_actions.ControlActionDispatcher"
        ) as MockCAD:
            MockCAD.return_value.dispatch.return_value = DispatchResult("ok", is_command=True)
            result = runner.dispatch_control_action("sess_1", action="new_session", target_id="")
            assert result.is_command is True

    def test_passes_all_params(self, runner: AgentRunner) -> None:
        with patch(
            "hermit.runtime.control.runner.control_actions.ControlActionDispatcher"
        ) as MockCAD:
            MockCAD.return_value.dispatch.return_value = DispatchResult("ok", is_command=True)
            runner.dispatch_control_action(
                "sess_1",
                action="approve_once",
                target_id="t1",
                reason="because",
                on_tool_call=MagicMock(),
                on_tool_start=MagicMock(),
            )
            MockCAD.return_value.dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# handle()
# ---------------------------------------------------------------------------


class TestHandle:
    def test_basic_handle_runs_agent(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        # cli-oneshot skips ingress
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)

            result = runner.handle("cli-oneshot", "hello")
            assert isinstance(result, AgentResult)
            mock_agent.run.assert_called_once()
            mock_task_controller.finalize_result.assert_called_once()

    def test_handle_with_planning_mode(self, runner: AgentRunner, mock_agent: MagicMock) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
            patch.object(runner, "_maybe_capture_planning_result") as mock_cap,
        ):
            mock_prep.return_value = (
                session,
                "prompt",
                {"planning_mode": True, "readonly_only": True},
                "goal",
            )
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)
            mock_cap.return_value = False

            result = runner.handle("cli-oneshot", "/plan hello")
            assert isinstance(result, AgentResult)

    def test_handle_suspended_result(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=True, blocked=False)

            result = runner.handle("cli-oneshot", "hello")
            mock_task_controller.mark_suspended.assert_called_once()
            assert result.suspended is True

    def test_handle_status_managed_by_kernel(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(
                suspended=True, status_managed_by_kernel=True
            )

            runner.handle("cli-oneshot", "hello")
            # Should return early without marking suspended
            mock_task_controller.mark_suspended.assert_not_called()

    def test_handle_with_ingress_decide(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        # Set up ingress that doesn't do note mode
        ingress = MagicMock()
        ingress.resolution = "new_task"
        ingress.mode = "new_task"
        ingress.parent_task_id = None
        ingress.anchor_task_id = None
        ingress.ingress_id = "ing_1"
        ingress.intent = "ask"
        ingress.reason = "new"
        ingress.reason_codes = []
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)

            result = runner.handle("sess_1", "hello")
            assert isinstance(result, AgentResult)

    def test_handle_pending_disambiguation(
        self, runner: AgentRunner, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        ingress = MagicMock()
        ingress.resolution = "pending_disambiguation"
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_pending_disambiguation_text") as mock_dis,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_dis.return_value = "Please choose a task"

            result = runner.handle("sess_1", "hello")
            assert result.execution_status == "pending_disambiguation"

    def test_handle_append_note_mode(
        self, runner: AgentRunner, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        ingress = MagicMock()
        ingress.resolution = "append_note"
        ingress.mode = "append_note"
        ingress.task_id = "task_1"
        ingress.note_event_seq = None
        ingress.ingress_id = "ing_1"
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_append_note_context") as mock_ctx,
            patch.object(runner, "_provider_input_compiler") as mock_compiler,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_ctx.return_value = _make_task_ctx()
            mock_compiler_inst = MagicMock()
            mock_compiler.return_value = mock_compiler_inst

            result = runner.handle("sess_1", "hello")
            assert result.execution_status == "note_appended"
            mock_task_controller.append_note.assert_called_once()

    def test_handle_append_note_already_appended(
        self, runner: AgentRunner, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        ingress = MagicMock()
        ingress.resolution = "append_note"
        ingress.mode = "append_note"
        ingress.task_id = "task_1"
        ingress.note_event_seq = 5  # already appended
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_append_note_context") as mock_ctx,
            patch.object(runner, "_provider_input_compiler") as mock_compiler,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_ctx.return_value = _make_task_ctx()
            mock_compiler.return_value = MagicMock()

            result = runner.handle("sess_1", "hello")
            assert result.execution_status == "note_appended"
            # Should NOT call append_note since note_event_seq is set
            mock_task_controller.append_note.assert_not_called()

    def test_handle_run_opts_override(self, runner: AgentRunner, mock_agent: MagicMock) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)

            result = runner.handle("cli-oneshot", "hello", run_opts={"disable_tools": True})
            assert isinstance(result, AgentResult)

    def test_handle_updates_session_tokens(
        self, runner: AgentRunner, mock_agent: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(input_tokens=10, output_tokens=20)

            runner.handle("cli-oneshot", "hello")
            assert session.total_input_tokens == 10
            assert session.total_output_tokens == 20

    def test_handle_fires_post_run_on_success(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_pm: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)

            runner.handle("cli-oneshot", "hello")
            mock_pm.on_post_run.assert_called_once()

    def test_handle_does_not_fire_post_run_when_suspended(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_pm: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=True)

            runner.handle("cli-oneshot", "hello")
            mock_pm.on_post_run.assert_not_called()

    def test_handle_mark_blocked_fallback(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        """When task_controller lacks mark_suspended, falls back to mark_blocked."""
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session
        del mock_task_controller.mark_suspended  # remove the attribute

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=True)

            runner.handle("cli-oneshot", "hello")
            mock_task_controller.mark_blocked.assert_called_once()

    def test_handle_append_note_runtime_error(
        self, runner: AgentRunner, mock_task_controller: MagicMock
    ) -> None:
        """When _provider_input_compiler().normalize_ingress raises, normalized stays None."""
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        ingress = MagicMock()
        ingress.resolution = "append_note"
        ingress.mode = "append_note"
        ingress.task_id = "task_1"
        ingress.note_event_seq = None
        ingress.ingress_id = "ing_1"
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_append_note_context") as mock_ctx,
            patch.object(runner, "_provider_input_compiler"),
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_ctx.side_effect = RuntimeError("no store")

            result = runner.handle("sess_1", "hello")
            assert result.execution_status == "note_appended"
            # normalized_payload should be None since RuntimeError was raised
            call_kwargs = mock_task_controller.append_note.call_args
            assert call_kwargs.kwargs.get("normalized_payload") is None

    def test_handle_ingress_with_anchor_task(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("sess_1")
        runner.session_manager.get_or_create.return_value = session

        ingress = MagicMock()
        ingress.resolution = "new_task"
        ingress.mode = "new_task"
        ingress.parent_task_id = None
        ingress.anchor_task_id = "anchor_1"
        ingress.continuation_anchor = {"key": "val"}
        ingress.ingress_id = "ing_1"
        ingress.intent = "ask"
        ingress.reason = "new"
        ingress.reason_codes = []
        mock_task_controller.decide_ingress.return_value = ingress

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)

            result = runner.handle("sess_1", "hello")
            assert isinstance(result, AgentResult)
            # Verify ingress_metadata had continuation_anchor
            call_kwargs = mock_task_controller.start_task.call_args
            metadata = call_kwargs.kwargs.get("ingress_metadata", {})
            assert "continuation_anchor" in metadata

    def test_handle_planning_captured_skips_finalize(
        self, runner: AgentRunner, mock_agent: MagicMock, mock_task_controller: MagicMock
    ) -> None:
        session = _make_session("cli-oneshot")
        runner.session_manager.get_or_create.return_value = session

        with (
            patch.object(runner, "_prepare_prompt_context") as mock_prep,
            patch.object(runner, "_compile_provider_input") as mock_compile,
            patch.object(runner, "_maybe_capture_planning_result") as mock_cap,
        ):
            mock_prep.return_value = (session, "prompt", {}, "goal")
            mock_compile.return_value = MagicMock(messages=[])
            mock_agent.run.return_value = _make_agent_result(suspended=False, blocked=False)
            mock_cap.return_value = True

            runner.handle("cli-oneshot", "hello")
            mock_task_controller.finalize_result.assert_not_called()


# ---------------------------------------------------------------------------
# Delegation methods
# ---------------------------------------------------------------------------


class TestDelegationMethods:
    def test_prepare_prompt_context_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.message_compiler.MessageCompiler") as MockMC:
            MockMC.return_value.prepare_prompt_context.return_value = (
                _make_session(),
                "prompt",
                {},
                "goal",
            )
            result = runner._prepare_prompt_context("sess_1", "hi", source_channel="cli")
            assert result is not None

    def test_provider_input_compiler_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.message_compiler.MessageCompiler") as MockMC:
            MockMC.return_value.provider_input_compiler.return_value = MagicMock()
            result = runner._provider_input_compiler()
            assert result is not None

    def test_compile_provider_input_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.message_compiler.MessageCompiler") as MockMC:
            MockMC.return_value.compile_provider_input.return_value = MagicMock()
            result = runner._compile_provider_input(
                task_ctx=_make_task_ctx(), prompt="hi", raw_text="hi"
            )
            assert result is not None

    def test_append_note_context_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.message_compiler.MessageCompiler") as MockMC:
            MockMC.return_value.append_note_context.return_value = _make_task_ctx()
            result = runner._append_note_context("sess_1", "task_1", "cli")
            assert result is not None

    def test_run_existing_task_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.task_executor.RunnerTaskExecutor") as MockTE:
            MockTE.return_value.run_existing_task.return_value = _make_agent_result()
            result = runner._run_existing_task(_make_task_ctx(), "prompt")
            assert isinstance(result, AgentResult)

    def test_maybe_capture_planning_no_store(self, runner: AgentRunner) -> None:
        with patch.object(runner, "_get_store", return_value=None):
            result = runner._maybe_capture_planning_result(
                _make_task_ctx(), _make_agent_result(), readonly_only=True
            )
            assert result is False

    def test_maybe_capture_planning_no_get_step(self, runner: AgentRunner) -> None:
        store = MagicMock(spec=[])  # no get_step
        with patch.object(runner, "_get_store", return_value=store):
            result = runner._maybe_capture_planning_result(
                _make_task_ctx(), _make_agent_result(), readonly_only=True
            )
            assert result is False

    def test_maybe_capture_planning_result_delegates(self, runner: AgentRunner) -> None:
        with (
            patch(
                "hermit.runtime.control.runner.session_context_builder.SessionContextBuilder"
            ) as MockSCB,
            patch("hermit.kernel.task.services.planning.PlanningService"),
        ):
            MockSCB.return_value.maybe_capture_planning_result.return_value = False
            result = runner._maybe_capture_planning_result(
                _make_task_ctx(), _make_agent_result(), readonly_only=True
            )
            assert result is False

    def test_emit_async_dispatch_result_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.async_dispatcher.AsyncDispatcher") as MockAD:
            MockAD.return_value.emit_async_dispatch_result.return_value = []
            result = runner._emit_async_dispatch_result(
                _make_task_ctx(), _make_agent_result(), started_at=1.0
            )
            assert result == []

    def test_record_scheduler_execution_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.async_dispatcher.AsyncDispatcher") as MockAD:
            MockAD.return_value.record_scheduler_execution.return_value = None
            runner._record_scheduler_execution(
                _make_task_ctx(), _make_agent_result(), started_at=1.0
            )

    def test_resolve_approval_no_store(self, runner: AgentRunner) -> None:
        with patch.object(runner, "_get_store", return_value=None):
            result = runner._resolve_approval("sess_1", action="approve_once", approval_id="a1")
            assert result.is_command is True

    def test_resolve_approval_with_store(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.approval_resolver.ApprovalResolver") as MockAR:
            MockAR.return_value.resolve_approval.return_value = DispatchResult(
                "ok", is_command=True
            )
            result = runner._resolve_approval("sess_1", action="approve_once", approval_id="a1")
            assert result.is_command is True


# ---------------------------------------------------------------------------
# enqueue_ingress / enqueue_approval_resume (delegation to AsyncDispatcher)
# ---------------------------------------------------------------------------


class TestAsyncIngress:
    def test_enqueue_ingress_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.async_dispatcher.AsyncDispatcher") as MockAD:
            MockAD.return_value.enqueue_ingress.return_value = _make_task_ctx()
            result = runner.enqueue_ingress("sess_1", "hello")
            assert result is not None

    def test_enqueue_approval_resume_delegates(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.async_dispatcher.AsyncDispatcher") as MockAD:
            MockAD.return_value.enqueue_approval_resume.return_value = DispatchResult(
                "ok", is_command=True
            )
            result = runner.enqueue_approval_resume(
                "sess_1", action="approve_once", approval_id="a1"
            )
            assert result.is_command is True


# ---------------------------------------------------------------------------
# process_claimed_attempt
# ---------------------------------------------------------------------------


class TestProcessClaimedAttempt:
    def test_delegates_to_task_executor(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.task_executor.RunnerTaskExecutor") as MockTE:
            MockTE.return_value.process_claimed_attempt.return_value = _make_agent_result()
            result = runner.process_claimed_attempt("attempt_1")
            assert isinstance(result, AgentResult)


# ---------------------------------------------------------------------------
# _pending_disambiguation_text
# ---------------------------------------------------------------------------


class TestPendingDisambiguationText:
    def test_returns_fallback_when_no_store(self, runner: AgentRunner) -> None:
        runner.task_controller.store = None
        # _get_store returns None
        with patch.object(runner, "_get_store", return_value=None):
            text = runner._pending_disambiguation_text(MagicMock())
            assert "couldn't determine" in text

    def test_delegates_to_approval_resolver(self, runner: AgentRunner) -> None:
        with patch("hermit.runtime.control.runner.approval_resolver.ApprovalResolver") as MockAR:
            MockAR.return_value.pending_disambiguation_text.return_value = "choose task"
            text = runner._pending_disambiguation_text(MagicMock())
            assert text == "choose task"
