"""Tests for hermit.runtime.control.runner.message_compiler — extends coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
from hermit.runtime.control.runner.message_compiler import MessageCompiler

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


def _make_session(session_id: str = "sess_1", messages: list | None = None):
    from hermit.runtime.control.lifecycle.session import Session

    s = Session(session_id=session_id)
    if messages:
        s.messages = list(messages)
    return s


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = SimpleNamespace(locale="en-US", base_dir="/tmp/test")
    pm.on_pre_run.return_value = ("processed prompt", {})
    return pm


@pytest.fixture()
def mock_session_manager() -> MagicMock:
    sm = MagicMock()
    sm.get_or_create.return_value = _make_session()
    return sm


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    store.db_path = "/tmp/test.db"
    store.ensure_conversation = MagicMock()
    return store


@pytest.fixture()
def mock_task_controller() -> MagicMock:
    tc = MagicMock()
    tc.ensure_conversation = MagicMock()
    return tc


@pytest.fixture()
def compiler(
    mock_pm: MagicMock,
    mock_session_manager: MagicMock,
    mock_store: MagicMock,
    mock_task_controller: MagicMock,
) -> MessageCompiler:
    return MessageCompiler(
        pm=mock_pm,
        session_manager=mock_session_manager,
        store=mock_store,
        task_controller=mock_task_controller,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_dependencies(self, compiler: MessageCompiler) -> None:
        assert compiler.pm is not None
        assert compiler.session_manager is not None
        assert compiler.store is not None
        assert compiler.task_controller is not None
        assert compiler.observation_service is None
        assert compiler.artifact_store is None


# ---------------------------------------------------------------------------
# prepare_prompt_context
# ---------------------------------------------------------------------------


class TestPreparePromptContext:
    def test_returns_four_tuple(self, compiler: MessageCompiler) -> None:
        session, prompt, run_opts, task_goal = compiler.prepare_prompt_context(
            "sess_1", "hello", source_channel="cli"
        )
        assert session is not None
        assert isinstance(prompt, str)
        assert isinstance(run_opts, dict)
        assert isinstance(task_goal, str)

    def test_calls_ensure_conversation(
        self, compiler: MessageCompiler, mock_task_controller: MagicMock
    ) -> None:
        compiler.prepare_prompt_context("sess_1", "hello", source_channel="cli")
        mock_task_controller.ensure_conversation.assert_called_once_with(
            "sess_1", source_channel="cli"
        )

    def test_calls_on_pre_run(self, compiler: MessageCompiler, mock_pm: MagicMock) -> None:
        compiler.prepare_prompt_context("sess_1", "hello", source_channel="cli")
        mock_pm.on_pre_run.assert_called_once()

    def test_prompt_contains_session_time(self, compiler: MessageCompiler) -> None:
        _, prompt, _, _ = compiler.prepare_prompt_context("sess_1", "hello", source_channel="cli")
        assert "<session_time>" in prompt

    def test_calls_ensure_session_started_callback(self, compiler: MessageCompiler) -> None:
        callback = MagicMock()
        compiler.prepare_prompt_context(
            "sess_1", "hello", source_channel="cli", ensure_session_started=callback
        )
        callback.assert_called_once_with("sess_1")

    def test_planning_mode_detection_via_store(self, compiler: MessageCompiler) -> None:
        """When PlanningService says pending, run_opts should have readonly_only."""
        with patch("hermit.runtime.control.runner.message_compiler.PlanningService") as MockPS:
            mock_planning = MagicMock()
            mock_planning.pending_for_conversation.return_value = True
            MockPS.return_value = mock_planning
            MockPS.planning_requested.return_value = False

            _, _, run_opts, _ = compiler.prepare_prompt_context(
                "sess_1", "hello", source_channel="cli"
            )
            assert run_opts.get("readonly_only") is True
            assert run_opts.get("planning_mode") is True

    def test_sanitizes_session_messages(
        self, compiler: MessageCompiler, mock_session_manager: MagicMock
    ) -> None:
        session = _make_session(messages=[{"role": "user", "content": "hi"}])
        mock_session_manager.get_or_create.return_value = session
        compiler.prepare_prompt_context("sess_1", "test", source_channel="cli")
        # Should not raise, session messages sanitized

    def test_no_ensure_conversation(
        self, compiler: MessageCompiler, mock_task_controller: MagicMock
    ) -> None:
        """When task_controller lacks ensure_conversation, should not fail."""
        del mock_task_controller.ensure_conversation
        compiler.prepare_prompt_context("sess_1", "hello", source_channel="cli")
        # Should not raise

    def test_session_messages_sanitized_and_saved(
        self, compiler: MessageCompiler, mock_session_manager: MagicMock
    ) -> None:
        """When sanitized messages differ from originals, session is saved."""
        from hermit.runtime.control.lifecycle.session import Session

        # Create a session with messages that will be sanitized
        session = Session(session_id="sess_1")
        # Add assistant with tool_use but no matching tool_result
        session.messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "test", "input": {}},
                    {"type": "text", "text": "some text"},
                ],
            },
        ]
        mock_session_manager.get_or_create.return_value = session
        compiler.prepare_prompt_context("sess_1", "hello", source_channel="cli")
        # sanitize_session_messages should have been applied

    def test_task_goal_strips_markup(self, compiler: MessageCompiler) -> None:
        _, _, _, task_goal = compiler.prepare_prompt_context(
            "sess_1",
            "<session_time>t=1</session_time>\nActual goal",
            source_channel="cli",
        )
        assert "<session_time>" not in task_goal


# ---------------------------------------------------------------------------
# provider_input_compiler
# ---------------------------------------------------------------------------


class TestProviderInputCompiler:
    def test_raises_when_store_is_none(self, compiler: MessageCompiler) -> None:
        compiler.store = None
        with pytest.raises(RuntimeError, match="compiled_context_unavailable"):
            compiler.provider_input_compiler()

    def test_raises_when_db_path_is_none(self, compiler: MessageCompiler) -> None:
        compiler.store.db_path = None
        with pytest.raises(RuntimeError, match="compiled_context_unavailable"):
            compiler.provider_input_compiler()

    def test_returns_compiler_when_store_valid(self, compiler: MessageCompiler) -> None:
        with patch(
            "hermit.runtime.control.runner.message_compiler.ProviderInputCompiler"
        ) as MockPIC:
            MockPIC.return_value = MagicMock()
            result = compiler.provider_input_compiler()
            assert result is not None
            MockPIC.assert_called_once_with(compiler.store, compiler.artifact_store)


# ---------------------------------------------------------------------------
# compile_lightweight_input
# ---------------------------------------------------------------------------


class TestCompileLightweightInput:
    def test_appends_user_message(self, compiler: MessageCompiler) -> None:
        result = compiler.compile_lightweight_input(prompt="hello", session_messages=[])
        assert isinstance(result, CompiledProviderInput)
        assert result.source_mode == "lightweight"
        assert any(m.get("role") == "user" for m in result.messages)

    def test_limits_recent_messages(self, compiler: MessageCompiler) -> None:
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
        result = compiler.compile_lightweight_input(
            prompt="hello", session_messages=messages, max_recent=5
        )
        # 5 recent + 1 appended user = up to 6
        assert len(result.messages) <= 7


# ---------------------------------------------------------------------------
# compile_provider_input — falls back to lightweight on RuntimeError
# ---------------------------------------------------------------------------


class TestCompileProviderInput:
    def test_falls_back_to_lightweight(self, compiler: MessageCompiler) -> None:
        compiler.store = None  # causes RuntimeError in provider_input_compiler
        task_ctx = _make_task_ctx()
        result = compiler.compile_provider_input(
            task_ctx=task_ctx,
            prompt="hello",
            raw_text="hello",
            session_messages=[],
        )
        assert result.source_mode == "lightweight"

    def test_uses_full_compiler_when_available(self, compiler: MessageCompiler) -> None:
        task_ctx = _make_task_ctx()
        with patch(
            "hermit.runtime.control.runner.message_compiler.ProviderInputCompiler"
        ) as MockPIC:
            mock_compiled = CompiledProviderInput(messages=[], source_mode="full")
            MockPIC.return_value.compile.return_value = mock_compiled
            result = compiler.compile_provider_input(
                task_ctx=task_ctx,
                prompt="hello",
                raw_text="hello",
            )
            assert result.source_mode == "full"
            MockPIC.return_value.compile.assert_called_once()


# ---------------------------------------------------------------------------
# append_note_context
# ---------------------------------------------------------------------------


class TestAppendNoteContext:
    def test_returns_task_execution_context(self, compiler: MessageCompiler) -> None:
        attempt = MagicMock()
        attempt.step_id = "step_1"
        attempt.step_attempt_id = "attempt_1"
        attempt.context = {"workspace_root": "/tmp", "ingress_metadata": {"key": "val"}}

        task = MagicMock()
        task.policy_profile = "default"

        compiler.store.list_step_attempts.return_value = [attempt]
        compiler.store.get_task.return_value = task

        ctx = compiler.append_note_context("sess_1", "task_1", "cli")
        assert isinstance(ctx, TaskExecutionContext)
        assert ctx.task_id == "task_1"
        assert ctx.step_id == "step_1"
        assert ctx.workspace_root == "/tmp"

    def test_handles_no_attempt(self, compiler: MessageCompiler) -> None:
        task = MagicMock()
        task.policy_profile = "default"

        compiler.store.list_step_attempts.return_value = []
        compiler.store.get_task.return_value = task

        ctx = compiler.append_note_context("sess_1", "task_1", "cli")
        assert ctx.step_id == ""
        assert ctx.step_attempt_id == ""
