from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
from hermit.runtime.control.runner.runner import _trim_session_messages
from hermit.runtime.provider_host.execution.runtime import AgentRuntime
from hermit.runtime.provider_host.shared.contracts import (
    ProviderFeatures,
    ProviderResponse,
    UsageMetrics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = dict(
        conversation_id="conv-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="test",
    )
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_messages(n: int) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message-{i}"})
    return msgs


def _tool_pair(tool_id: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "bash", "input": {"cmd": "ls"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "output"},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Test 1: Lightweight fallback when store unavailable
# ---------------------------------------------------------------------------


class TestLightweightFallback:
    def test_compile_returns_compiled_input_when_store_unavailable(self) -> None:
        """When kernel store is unavailable, _compile_provider_input should
        return a CompiledProviderInput with source_mode='lightweight' instead of None."""
        from hermit.runtime.control.runner.runner import AgentRunner

        fake_store = SimpleNamespace(db_path=None)
        task_controller = MagicMock()
        task_controller.store = fake_store

        agent = MagicMock()
        session_manager = MagicMock()
        pm = MagicMock()
        pm.settings = SimpleNamespace(max_session_messages=100)

        runner = AgentRunner(
            agent=agent,
            session_manager=session_manager,
            plugin_manager=pm,
            task_controller=task_controller,
        )

        session_msgs = _make_messages(30)
        task_ctx = _make_task_ctx()

        result = runner._compile_provider_input(
            task_ctx=task_ctx,
            prompt="hello",
            raw_text="hello",
            session_messages=session_msgs,
        )

        assert isinstance(result, CompiledProviderInput)
        assert result.source_mode == "lightweight"
        assert len(result.messages) <= 21  # 20 recent + 1 prompt


# ---------------------------------------------------------------------------
# Test 2: Context-too-long auto retry
# ---------------------------------------------------------------------------


class TestContextTooLongAutoRetry:
    def test_retries_on_context_too_long(self) -> None:
        """_run_from_messages should trim and retry once on context-too-long errors."""
        provider = MagicMock()
        provider.name = "test"
        provider.features = ProviderFeatures(
            supports_streaming=False,
            supports_thinking=False,
        )

        call_count = 0
        success_response = ProviderResponse(
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            usage=UsageMetrics(input_tokens=10, output_tokens=5),
        )

        def fake_generate(request: Any) -> ProviderResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("prompt is too long for the model")
            return success_response

        provider.generate = fake_generate

        registry = MagicMock()
        registry.list_tools.return_value = []
        executor = MagicMock()

        runtime = AgentRuntime(
            provider=provider,
            registry=registry,
            model="test-model",
            tool_executor=executor,
        )

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            {"role": "user", "content": "question"},
        ]

        result = runtime._run_from_messages(
            messages,
            start_turn=1,
            on_tool_call=None,
            on_tool_start=None,
            disable_tools=True,
            readonly_only=False,
            task_context=None,
        )

        assert call_count == 2
        assert result.execution_status == "succeeded"
        assert result.text == "ok"

    def test_non_context_error_fails_immediately(self) -> None:
        """Non-context-too-long errors should fail without retry."""
        provider = MagicMock()
        provider.name = "test"
        provider.features = ProviderFeatures(
            supports_streaming=False,
            supports_thinking=False,
        )
        provider.generate.side_effect = Exception("rate limit exceeded")

        registry = MagicMock()
        registry.list_tools.return_value = []

        runtime = AgentRuntime(
            provider=provider,
            registry=registry,
            model="test-model",
        )

        messages = [{"role": "user", "content": "hello"}]
        result = runtime._run_from_messages(
            messages,
            start_turn=1,
            on_tool_call=None,
            on_tool_start=None,
            disable_tools=True,
            readonly_only=False,
            task_context=None,
        )

        assert result.execution_status == "failed"
        assert provider.generate.call_count == 1


# ---------------------------------------------------------------------------
# Test 3: Session messages auto trim
# ---------------------------------------------------------------------------


class TestSessionMessagesAutoTrim:
    def test_no_trim_under_limit(self) -> None:
        msgs = _make_messages(50)
        result = _trim_session_messages(msgs, max_messages=100)
        assert len(result) == 50

    def test_trims_to_max(self) -> None:
        msgs = _make_messages(150)
        result = _trim_session_messages(msgs, max_messages=100)
        assert len(result) <= 100

    def test_preserves_system_first(self) -> None:
        msgs = [{"role": "system", "content": "system prompt"}] + _make_messages(120)
        result = _trim_session_messages(msgs, max_messages=50)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "system prompt"
        assert len(result) <= 50


# ---------------------------------------------------------------------------
# Test 4: Trim preserves tool_use/tool_result pairs
# ---------------------------------------------------------------------------


class TestTrimPreservesToolUsePairs:
    def test_tool_pairs_intact_after_trim(self) -> None:
        msgs = _make_messages(80)
        msgs.extend(_tool_pair("tool-1"))
        msgs.extend(_tool_pair("tool-2"))
        msgs.extend(_make_messages(20))

        result = _trim_session_messages(msgs, max_messages=50)

        tool_use_ids: set[str] = set()
        tool_result_ids: set[str] = set()
        for msg in result:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        tool_use_ids.add(block["id"])
                    elif block.get("type") == "tool_result":
                        tool_result_ids.add(block["tool_use_id"])

        # Every tool_use must have a matching tool_result
        assert tool_use_ids <= tool_result_ids, (
            f"Orphaned tool_use IDs: {tool_use_ids - tool_result_ids}"
        )
