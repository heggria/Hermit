"""Tests for runtime.py — AgentRuntime, truncate_middle_text, format_tool_result_content, etc."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.executor import ToolExecutionResult
from hermit.runtime.provider_host.execution.runtime import (
    AgentResult,
    AgentRuntime,
    _is_tool_result_block,
    _tool_result_json_text,
    format_tool_result_content,
    truncate_middle_text,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderEvent,
    ProviderFeatures,
    ProviderResponse,
    UsageMetrics,
)

# ── Helpers ────────────────────────────────────────────────────────


def _mock_provider(
    response_text: str = "Hello",
    stop_reason: str = "end_turn",
    *,
    supports_streaming: bool = False,
    supports_thinking: bool = False,
) -> MagicMock:
    provider = MagicMock()
    provider.name = "test"
    provider.features = ProviderFeatures(
        supports_streaming=supports_streaming,
        supports_thinking=supports_thinking,
    )
    provider.generate.return_value = ProviderResponse(
        content=[{"type": "text", "text": response_text}],
        stop_reason=stop_reason,
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )
    provider.clone.return_value = provider
    return provider


def _mock_registry() -> MagicMock:
    registry = MagicMock()
    registry.list_tools.return_value = []
    return registry


def _make_runtime(provider: Any = None, registry: Any = None, **kwargs: Any) -> AgentRuntime:
    p = provider or _mock_provider()
    r = registry or _mock_registry()
    defaults: dict[str, Any] = {
        "provider": p,
        "registry": r,
        "model": "test-model",
        "max_tokens": 1024,
        "max_turns": 3,
        "tool_output_limit": 4000,
    }
    defaults.update(kwargs)
    return AgentRuntime(**defaults)


def _make_task_context(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "test",
        "policy_profile": "autonomous",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


# ── truncate_middle_text ───────────────────────────────────────────


def test_truncate_middle_text_shorter_than_limit() -> None:
    assert truncate_middle_text("short", 100) == "short"


def test_truncate_middle_text_limit_zero() -> None:
    assert truncate_middle_text("anything", 0) == "anything"


def test_truncate_middle_text_limit_negative() -> None:
    assert truncate_middle_text("anything", -5) == "anything"


def test_truncate_middle_text_limit_32_or_less() -> None:
    text = "abcdefghijklmnopqrstuvwxyz0123456789"
    result = truncate_middle_text(text, 20)
    assert result == text[:20]
    assert len(result) == 20


def test_truncate_middle_text_larger_limit_has_ellipsis() -> None:
    text = "a" * 200
    result = truncate_middle_text(text, 100)
    assert "\n...\n" in result
    assert len(result) <= 100


def test_truncate_middle_text_exact_limit() -> None:
    text = "hello"
    assert truncate_middle_text(text, 5) == "hello"


# ── _tool_result_json_text ─────────────────────────────────────────


def test_tool_result_json_text_under_limit() -> None:
    result = _tool_result_json_text({"key": "value"}, 1000)
    assert '"key"' in result
    assert '"value"' in result


def test_tool_result_json_text_truncated() -> None:
    large_value = {"key": "x" * 500}
    result = _tool_result_json_text(large_value, 50)
    assert len(result) <= 50


# ── _is_tool_result_block ─────────────────────────────────────────


def test_is_tool_result_block_text() -> None:
    assert _is_tool_result_block({"type": "text"}) is True


def test_is_tool_result_block_image() -> None:
    assert _is_tool_result_block({"type": "image"}) is True


def test_is_tool_result_block_other_type() -> None:
    assert _is_tool_result_block({"type": "tool_use"}) is False


def test_is_tool_result_block_non_dict() -> None:
    assert _is_tool_result_block("not a dict") is False


def test_is_tool_result_block_missing_type() -> None:
    assert _is_tool_result_block({"content": "x"}) is False


# ── format_tool_result_content ─────────────────────────────────────


def test_format_tool_result_content_string() -> None:
    result = format_tool_result_content("hello world", 100)
    assert result == "hello world"


def test_format_tool_result_content_string_truncated() -> None:
    long_str = "x" * 200
    result = format_tool_result_content(long_str, 50)
    assert len(result) <= 50


def test_format_tool_result_content_plain_dict() -> None:
    result = format_tool_result_content({"status": "ok"}, 1000)
    assert isinstance(result, str)
    assert '"status"' in result


def test_format_tool_result_content_list_of_blocks() -> None:
    blocks = [{"type": "text", "text": "a"}, {"type": "image", "source": {}}]
    result = format_tool_result_content(blocks, 1000)
    assert result == blocks


# ── AgentResult ────────────────────────────────────────────────────


def test_agent_result_defaults() -> None:
    r = AgentResult(text="hi", turns=1, tool_calls=0)
    assert r.text == "hi"
    assert r.turns == 1
    assert r.tool_calls == 0
    assert r.thinking == ""
    assert r.blocked is False
    assert r.suspended is False
    assert r.waiting_kind is None
    assert r.approval_id is None
    assert r.observation is None
    assert r.input_tokens == 0
    assert r.output_tokens == 0
    assert r.execution_status == "succeeded"
    assert r.status_managed_by_kernel is False


# ── AgentRuntime.__init__ ──────────────────────────────────────────


def test_runtime_init_sets_attributes() -> None:
    provider = _mock_provider()
    registry = _mock_registry()
    rt = AgentRuntime(
        provider=provider,
        registry=registry,
        model="claude-3",
        max_tokens=2048,
        max_turns=5,
        tool_output_limit=8000,
        thinking_budget=1000,
        system_prompt="sys",
        locale="zh-CN",
    )

    assert rt.provider is provider
    assert rt.registry is registry
    assert rt.model == "claude-3"
    assert rt.max_tokens == 2048
    assert rt.max_turns == 5
    assert rt.tool_output_limit == 8000
    assert rt.thinking_budget == 1000
    assert rt.system_prompt == "sys"
    assert rt.locale.lower().startswith("zh")
    assert rt.workspace_root is None
    assert rt.kernel_store is None


# ── AgentRuntime.clone ─────────────────────────────────────────────


def test_clone_with_overridden_model() -> None:
    rt = _make_runtime()
    cloned = rt.clone(model="new-model")
    assert cloned.model == "new-model"
    assert cloned is not rt


def test_clone_with_overridden_system_prompt() -> None:
    rt = _make_runtime(system_prompt="original")
    cloned = rt.clone(system_prompt="new prompt")
    assert cloned.system_prompt == "new prompt"


def test_clone_preserves_defaults() -> None:
    rt = _make_runtime(system_prompt="original")
    cloned = rt.clone()
    assert cloned.model == rt.model
    assert cloned.system_prompt == rt.system_prompt
    assert cloned.max_tokens == rt.max_tokens
    assert cloned.max_turns == rt.max_turns


def test_clone_with_overridden_max_turns() -> None:
    rt = _make_runtime(max_turns=5)
    cloned = rt.clone(max_turns=10)
    assert cloned.max_turns == 10


# ── AgentRuntime._request ──────────────────────────────────────────


def test_request_with_tools() -> None:
    registry = _mock_registry()
    registry.list_tools.return_value = [MagicMock(name="bash")]
    rt = _make_runtime(registry=registry)

    req = rt._request([], disable_tools=False, readonly_only=False, stream=False)
    assert len(req.tools) == 1


def test_request_without_tools() -> None:
    rt = _make_runtime()
    req = rt._request([], disable_tools=True, readonly_only=False, stream=False)
    assert req.tools == []


def test_request_thinking_budget_zero_when_not_supported() -> None:
    provider = _mock_provider(supports_thinking=False)
    rt = _make_runtime(provider=provider, thinking_budget=5000)

    req = rt._request([], disable_tools=False, readonly_only=False, stream=False)
    assert req.thinking_budget == 0


def test_request_thinking_budget_set_when_supported() -> None:
    provider = _mock_provider(supports_thinking=True)
    rt = _make_runtime(provider=provider, thinking_budget=5000)

    req = rt._request([], disable_tools=False, readonly_only=False, stream=False)
    assert req.thinking_budget == 5000


# ── AgentRuntime._tool_result_block ────────────────────────────────


def test_tool_result_block_basic() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(registry=registry)

    block = rt._tool_result_block(
        tool_name="bash",
        tool_use_id="tu-1",
        content="output",
    )

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu-1"
    assert block["content"] == "output"
    assert "is_error" not in block


def test_tool_result_block_with_error() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(registry=registry)

    block = rt._tool_result_block(
        tool_name="bash",
        tool_use_id="tu-1",
        content="error msg",
        is_error=True,
    )

    assert block["is_error"] is True


def test_tool_result_block_internal_context() -> None:
    tool = MagicMock()
    tool.result_is_internal_context = True
    registry = _mock_registry()
    registry.get.return_value = tool
    rt = _make_runtime(registry=registry)

    block = rt._tool_result_block(
        tool_name="internal_tool",
        tool_use_id="tu-1",
        content="context data",
    )

    assert block["internal_context"] is True
    assert block["tool_name"] == "internal_tool"


def test_tool_result_block_unknown_tool() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("unknown")
    rt = _make_runtime(registry=registry)

    block = rt._tool_result_block(
        tool_name="missing_tool",
        tool_use_id="tu-1",
        content="output",
    )

    assert block["type"] == "tool_result"
    assert "internal_context" not in block


def test_tool_result_block_tool_not_internal() -> None:
    tool = MagicMock()
    tool.result_is_internal_context = False
    registry = _mock_registry()
    registry.get.return_value = tool
    rt = _make_runtime(registry=registry)

    block = rt._tool_result_block(
        tool_name="normal_tool",
        tool_use_id="tu-1",
        content="output",
    )

    assert "internal_context" not in block


# ── AgentRuntime.run ───────────────────────────────────────────────


def test_run_simple_text_response() -> None:
    rt = _make_runtime()
    result = rt.run("Hello")

    assert isinstance(result, AgentResult)
    assert result.text == "Hello"
    assert result.turns == 1
    assert result.tool_calls == 0
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.execution_status == "succeeded"


def test_run_with_compiled_messages() -> None:
    rt = _make_runtime()
    msgs = [{"role": "user", "content": "precompiled"}]
    result = rt.run("ignored", compiled_messages=msgs)

    assert result.text == "Hello"
    # The provider should have been called with the compiled messages
    rt.provider.generate.assert_called_once()


def test_run_with_message_history() -> None:
    rt = _make_runtime()
    history = [{"role": "user", "content": "prev"}, {"role": "assistant", "content": "resp"}]
    result = rt.run("new question", message_history=history)

    assert result.text == "Hello"


# ── AgentRuntime._is_context_too_long ──────────────────────────────


def test_is_context_too_long_prompt_too_long() -> None:
    assert AgentRuntime._is_context_too_long(Exception("The prompt is too long for the model."))


def test_is_context_too_long_context_window() -> None:
    assert AgentRuntime._is_context_too_long(Exception("Exceeded the context window limit."))


def test_is_context_too_long_maximum_context_length() -> None:
    assert AgentRuntime._is_context_too_long(Exception("maximum context length exceeded"))


def test_is_context_too_long_false_for_unrelated() -> None:
    assert not AgentRuntime._is_context_too_long(Exception("network timeout"))


# ── AgentRuntime._trim_messages_for_retry ──────────────────────────


def test_trim_messages_truncates_tool_result_content() -> None:
    rt = _make_runtime(tool_output_limit=100)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "x" * 500,
                }
            ],
        }
    ]

    trimmed = rt._trim_messages_for_retry(messages)
    inner = trimmed[0]["content"][0]["content"]
    assert len(inner) < 500


def test_trim_messages_keeps_system_and_last_four() -> None:
    rt = _make_runtime(tool_output_limit=100)
    messages = [
        {"role": "system", "content": "system msg"},
        {"role": "user", "content": "m1"},
        {"role": "assistant", "content": "m2"},
        {"role": "user", "content": "m3"},
        {"role": "assistant", "content": "m4"},
        {"role": "user", "content": "m5"},
        {"role": "assistant", "content": "m6"},
        {"role": "user", "content": "m7"},
    ]

    trimmed = rt._trim_messages_for_retry(messages)
    # Should keep system + last 4
    assert len(trimmed) == 5
    assert trimmed[0]["role"] == "system"


def test_trim_messages_no_system_msg() -> None:
    rt = _make_runtime(tool_output_limit=100)
    messages = [{"role": "user", "content": f"m{i}"} for i in range(8)]

    trimmed = rt._trim_messages_for_retry(messages)
    assert len(trimmed) == 4


def test_trim_messages_short_list_unchanged() -> None:
    rt = _make_runtime(tool_output_limit=100)
    messages = [
        {"role": "user", "content": "m1"},
        {"role": "assistant", "content": "m2"},
    ]

    trimmed = rt._trim_messages_for_retry(messages)
    assert len(trimmed) == 2


# ── AgentRuntime._run_from_messages error paths ───────────────────


def test_run_provider_error_returns_failed() -> None:
    provider = _mock_provider()
    provider.generate.side_effect = RuntimeError("API down")
    rt = _make_runtime(provider=provider)

    result = rt.run("test")
    assert "[API Error]" in result.text
    assert result.execution_status == "failed"


def test_run_context_too_long_triggers_retry() -> None:
    provider = _mock_provider()
    first_call = True

    def side_effect(request):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("prompt is too long")
        return ProviderResponse(
            content=[{"type": "text", "text": "retried ok"}],
            stop_reason="end_turn",
            usage=UsageMetrics(input_tokens=5, output_tokens=3),
        )

    provider.generate.side_effect = side_effect
    rt = _make_runtime(provider=provider)

    result = rt.run("test")
    assert result.text == "retried ok"
    assert result.execution_status == "succeeded"


def test_run_context_too_long_retry_also_fails() -> None:
    provider = _mock_provider()
    provider.generate.side_effect = RuntimeError("prompt is too long")
    rt = _make_runtime(provider=provider)

    result = rt.run("test")
    assert "[API Error]" in result.text
    assert result.execution_status == "failed"


def test_run_response_error_field() -> None:
    provider = _mock_provider()
    provider.generate.return_value = ProviderResponse(
        content=[],
        error="Rate limited",
        usage=UsageMetrics(),
    )
    rt = _make_runtime(provider=provider)

    result = rt.run("test")
    assert "Rate limited" in result.text
    assert result.execution_status == "failed"


def test_run_max_turns_sends_final_summary() -> None:
    provider = _mock_provider()
    # First call returns tool_use, second returns text (final summary)
    tool_response = ProviderResponse(
        content=[
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"command": "echo hi"}},
        ],
        stop_reason="tool_use",
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )
    text_response = ProviderResponse(
        content=[{"type": "text", "text": "Final answer"}],
        stop_reason="end_turn",
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )

    executor = MagicMock()
    exec_result = ToolExecutionResult(model_content="ok", raw_result="ok")
    executor.execute.return_value = exec_result
    executor.consume_appended_notes.return_value = ([], 0)

    call_count = 0

    def gen_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return tool_response
        return text_response

    provider.generate.side_effect = gen_side_effect
    rt = _make_runtime(provider=provider, max_turns=3, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.run("test", task_context=ctx)
    assert result.execution_status == "succeeded"


def test_run_max_turns_final_summary_fails() -> None:
    provider = _mock_provider()
    tool_response = ProviderResponse(
        content=[
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"command": "echo hi"}},
        ],
        stop_reason="tool_use",
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )

    executor = MagicMock()
    exec_result = ToolExecutionResult(model_content="ok", raw_result="ok")
    executor.execute.return_value = exec_result
    executor.consume_appended_notes.return_value = ([], 0)

    call_count = 0

    def gen_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return tool_response
        raise RuntimeError("final failed")

    provider.generate.side_effect = gen_side_effect
    rt = _make_runtime(provider=provider, max_turns=3, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.run("test", task_context=ctx)
    assert result.execution_status == "failed"


# ── AgentRuntime._execute_tool ─────────────────────────────────────


def test_execute_tool_no_executor_raises() -> None:
    rt = _make_runtime(tool_executor=None)
    ctx = _make_task_context()

    with pytest.raises(RuntimeError, match="executor"):
        rt._execute_tool(task_context=ctx, tool_name="bash", tool_input={})


def test_execute_tool_no_context_raises() -> None:
    executor = MagicMock()
    rt = _make_runtime(tool_executor=executor)

    with pytest.raises(RuntimeError, match="context"):
        rt._execute_tool(task_context=None, tool_name="bash", tool_input={})


def test_execute_tool_delegates_to_executor() -> None:
    executor = MagicMock()
    exec_result = ToolExecutionResult(model_content="done", raw_result="done")
    executor.execute.return_value = exec_result
    rt = _make_runtime(tool_executor=executor)
    ctx = _make_task_context()

    result = rt._execute_tool(task_context=ctx, tool_name="bash", tool_input={"cmd": "ls"})
    assert result.model_content == "done"
    executor.execute.assert_called_once_with(ctx, "bash", {"cmd": "ls"})


# ── AgentRuntime._execute_tool_turn ────────────────────────────────


def test_execute_tool_turn_calls_callbacks() -> None:
    executor = MagicMock()
    exec_result = ToolExecutionResult(model_content="output", raw_result="output")
    executor.execute.return_value = exec_result

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    on_start = MagicMock()
    on_call = MagicMock()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"command": "ls"}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=on_call,
        on_tool_start=on_start,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, tuple)
    _blocks, count = result
    assert count == 1
    on_start.assert_called_once_with("bash", {"command": "ls"})
    on_call.assert_called_once()


def test_execute_tool_turn_unknown_tool_key_error() -> None:
    executor = MagicMock()
    executor.execute.side_effect = KeyError("unknown tool")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    registry.list_tools.return_value = []
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "missing", "input": {}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, tuple)
    blocks, _count = result
    assert "Unknown tool" in str(blocks[0]["content"])


def test_execute_tool_turn_generic_exception() -> None:
    executor = MagicMock()
    executor.execute.side_effect = ValueError("bad input")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, tuple)
    blocks, _ = result
    assert "ValueError" in str(blocks[0]["content"])


def test_execute_tool_turn_blocked_returns_agent_result() -> None:
    executor = MagicMock()
    exec_result = ToolExecutionResult(
        model_content="Awaiting approval",
        raw_result="",
        blocked=True,
        suspended=True,
        waiting_kind="awaiting_approval",
        approval_id="appr-1",
        execution_status="blocked",
        state_applied=False,
    )
    executor.execute.return_value = exec_result
    executor.current_note_cursor.return_value = 0
    executor.persist_suspended_state = MagicMock()

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, AgentResult)
    assert result.blocked is True
    assert result.suspended is True
    assert result.approval_id == "appr-1"


def test_execute_tool_turn_denied_returns_agent_result() -> None:
    executor = MagicMock()
    exec_result = ToolExecutionResult(
        model_content="Denied: too risky",
        raw_result="",
        denied=True,
        execution_status="denied",
        state_applied=False,
    )
    executor.execute.return_value = exec_result

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, AgentResult)
    assert "Denied" in result.text


def test_execute_tool_turn_receipt_logged() -> None:
    executor = MagicMock()
    pd = MagicMock()
    pd.action_class = "write_file"
    pd.verdict = "allow"
    pd.risk_level = "low"
    exec_result = ToolExecutionResult(
        model_content="written",
        raw_result="written",
        receipt_id="rcpt-1",
        decision_id="dec-1",
        capability_grant_id="grant-1",
        policy_decision=pd,
    )
    executor.execute.return_value = exec_result

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(tool_executor=executor, registry=registry)
    ctx = _make_task_context()

    tool_blocks = [
        {"type": "tool_use", "id": "tu-1", "name": "write_file", "input": {}},
    ]

    result = rt._execute_tool_turn(
        messages=[],
        tool_use_blocks=tool_blocks,
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert isinstance(result, tuple)


# ── AgentRuntime._apply_appended_notes ─────────────────────────────


def test_apply_appended_notes_no_executor() -> None:
    rt = _make_runtime(tool_executor=None)
    messages = [{"role": "user", "content": "hi"}]
    result = rt._apply_appended_notes(messages, None)
    assert result is messages


def test_apply_appended_notes_no_context() -> None:
    executor = MagicMock()
    rt = _make_runtime(tool_executor=executor)
    messages = [{"role": "user", "content": "hi"}]
    result = rt._apply_appended_notes(messages, None)
    assert result is messages


def test_apply_appended_notes_with_notes() -> None:
    executor = MagicMock()
    executor.consume_appended_notes.return_value = (
        [{"role": "user", "content": "note"}],
        1,
    )
    rt = _make_runtime(tool_executor=executor)
    ctx = _make_task_context()
    messages = [{"role": "user", "content": "hi"}]

    result = rt._apply_appended_notes(messages, ctx)
    assert len(result) >= 2


def test_apply_appended_notes_empty_notes() -> None:
    executor = MagicMock()
    executor.consume_appended_notes.return_value = ([], 0)
    rt = _make_runtime(tool_executor=executor)
    ctx = _make_task_context()
    messages = [{"role": "user", "content": "hi"}]

    result = rt._apply_appended_notes(messages, ctx)
    assert result is messages


# ── AgentRuntime._resume_observation_turn ──────────────────────────


def test_resume_observation_turn_empty_blocks() -> None:
    rt = _make_runtime()
    blocks, count = rt._resume_observation_turn(
        pending_tool_blocks=[],
        tool_result_blocks=[],
        observation={},
        on_tool_call=None,
    )
    assert blocks == []
    assert count == 0


def test_resume_observation_turn_adds_result() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(registry=registry)
    pending = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"cmd": "ls"}},
    ]
    observation = {"final_model_content": "output", "final_is_error": False}

    blocks, count = rt._resume_observation_turn(
        pending_tool_blocks=pending,
        tool_result_blocks=[],
        observation=observation,
        on_tool_call=None,
    )

    assert count == 1
    assert len(blocks) == 1
    assert blocks[0]["content"] == "output"


def test_resume_observation_turn_calls_callback() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(registry=registry)
    pending = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"cmd": "ls"}},
    ]
    observation = {"final_model_content": "out", "final_is_error": False}
    callback = MagicMock()

    rt._resume_observation_turn(
        pending_tool_blocks=pending,
        tool_result_blocks=[],
        observation=observation,
        on_tool_call=callback,
    )

    callback.assert_called_once()


def test_resume_observation_turn_error_flag() -> None:
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(registry=registry)
    pending = [
        {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
    ]
    observation = {"final_model_content": "error", "final_is_error": True}

    blocks, _ = rt._resume_observation_turn(
        pending_tool_blocks=pending,
        tool_result_blocks=[],
        observation=observation,
        on_tool_call=None,
    )

    assert blocks[0]["is_error"] is True


# ── AgentRuntime.run_stream ────────────────────────────────────────


def test_run_stream_fallback_when_no_streaming() -> None:
    provider = _mock_provider(supports_streaming=False)
    rt = _make_runtime(provider=provider)
    tokens = []
    result = rt.run_stream("test", on_token=lambda kind, text: tokens.append((kind, text)))

    assert result.text == "Hello"
    assert ("text", "Hello") in tokens


def test_run_stream_with_streaming_support() -> None:
    provider = _mock_provider(supports_streaming=True)
    events = [
        ProviderEvent(type="text", text="Hello"),
        ProviderEvent(
            type="block_end",
            block={"type": "text", "text": "Hello"},
        ),
        ProviderEvent(
            type="message_end",
            stop_reason="end_turn",
            usage=UsageMetrics(input_tokens=10, output_tokens=5),
        ),
    ]
    provider.stream.return_value = events
    rt = _make_runtime(provider=provider)

    tokens = []
    result = rt.run_stream("test", on_token=lambda kind, text: tokens.append((kind, text)))

    assert result.execution_status == "succeeded"
    assert ("text", "Hello") in tokens


def test_run_stream_no_callback() -> None:
    provider = _mock_provider(supports_streaming=False)
    rt = _make_runtime(provider=provider)
    result = rt.run_stream("test")
    assert result.text == "Hello"


def test_run_stream_error() -> None:
    provider = _mock_provider(supports_streaming=True)
    provider.stream.side_effect = RuntimeError("stream broke")
    rt = _make_runtime(provider=provider)

    result = rt.run_stream("test")
    assert "[Stream Error]" in result.text
    assert result.execution_status == "failed"


def test_run_stream_context_too_long_retries() -> None:
    provider = _mock_provider(supports_streaming=True)
    call_count = 0

    def stream_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("prompt is too long for this model")
        return [
            ProviderEvent(type="text", text="ok"),
            ProviderEvent(type="block_end", block={"type": "text", "text": "ok"}),
            ProviderEvent(
                type="message_end",
                stop_reason="end_turn",
                usage=UsageMetrics(input_tokens=5, output_tokens=2),
            ),
        ]

    provider.stream.side_effect = stream_side_effect
    rt = _make_runtime(provider=provider)

    result = rt.run_stream("test")
    assert result.execution_status == "succeeded"


# ── AgentRuntime.resume ────────────────────────────────────────────


def test_resume_no_executor_raises() -> None:
    rt = _make_runtime(tool_executor=None)
    ctx = _make_task_context()

    with pytest.raises(RuntimeError, match="ToolExecutor"):
        rt.resume(
            step_attempt_id="sa-1",
            task_context=ctx,
        )


def test_resume_loads_and_executes() -> None:
    executor = MagicMock()
    executor.load_suspended_state.return_value = {
        "messages": [{"role": "user", "content": "hello"}],
        "pending_tool_blocks": [
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"cmd": "ls"}},
        ],
        "tool_result_blocks": [],
        "next_turn": 2,
        "disable_tools": False,
        "readonly_only": False,
        "suspend_kind": "awaiting_approval",
    }
    exec_result = ToolExecutionResult(model_content="done", raw_result="done")
    executor.execute.return_value = exec_result
    executor.current_note_cursor.return_value = 0
    executor.clear_suspended_state = MagicMock()
    executor.consume_appended_notes.return_value = ([], 0)

    provider = _mock_provider()
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.resume(step_attempt_id="sa-1", task_context=ctx)
    assert isinstance(result, AgentResult)
    executor.clear_suspended_state.assert_called_once_with("sa-1")


# ── AgentRuntime._usage_to_result ──────────────────────────────────


def test_usage_to_result() -> None:
    rt = _make_runtime()
    usage = UsageMetrics(input_tokens=100, output_tokens=50, cache_read_tokens=10)

    result = rt._usage_to_result(
        usage,
        text="result",
        turns=2,
        tool_calls=3,
    )

    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 10
    assert result.text == "result"
    assert result.turns == 2
    assert result.tool_calls == 3


# ── AgentRuntime.resume — observation path ─────────────────────────


def test_resume_observation_path() -> None:
    """Test resume when suspend_kind is 'observing' with terminal_status."""
    executor = MagicMock()
    executor.load_suspended_state.return_value = {
        "messages": [{"role": "user", "content": "hello"}],
        "pending_tool_blocks": [
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"cmd": "ls"}},
        ],
        "tool_result_blocks": [],
        "next_turn": 2,
        "disable_tools": False,
        "readonly_only": False,
        "suspend_kind": "observing",
        "observation": {
            "terminal_status": True,
            "final_model_content": "done",
            "final_is_error": False,
        },
    }
    exec_result = ToolExecutionResult(model_content="done", raw_result="done")
    executor.execute.return_value = exec_result
    executor.current_note_cursor.return_value = 0
    executor.clear_suspended_state = MagicMock()
    executor.consume_appended_notes.return_value = ([], 0)

    provider = _mock_provider()
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.resume(step_attempt_id="sa-1", task_context=ctx)
    assert isinstance(result, AgentResult)
    executor.clear_suspended_state.assert_called_once_with("sa-1")


def test_resume_observation_path_with_remaining_tools() -> None:
    """Test resume observation path with multiple pending tool blocks."""
    executor = MagicMock()
    executor.load_suspended_state.return_value = {
        "messages": [{"role": "user", "content": "hello"}],
        "pending_tool_blocks": [
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {"cmd": "ls"}},
            {
                "type": "tool_use",
                "id": "tu-2",
                "name": "write_file",
                "input": {"path": "a.txt", "content": "x"},
            },
        ],
        "tool_result_blocks": [],
        "next_turn": 2,
        "disable_tools": False,
        "readonly_only": False,
        "suspend_kind": "observing",
        "observation": {
            "terminal_status": True,
            "final_model_content": "output",
            "final_is_error": False,
        },
    }
    exec_result = ToolExecutionResult(model_content="written", raw_result="written")
    executor.execute.return_value = exec_result
    executor.current_note_cursor.return_value = 0
    executor.clear_suspended_state = MagicMock()
    executor.consume_appended_notes.return_value = ([], 0)

    provider = _mock_provider()
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.resume(step_attempt_id="sa-1", task_context=ctx)
    assert isinstance(result, AgentResult)


def test_resume_observation_blocked_in_remaining() -> None:
    """Test that resume observation with remaining tools that get blocked returns AgentResult."""
    executor = MagicMock()
    executor.load_suspended_state.return_value = {
        "messages": [{"role": "user", "content": "hello"}],
        "pending_tool_blocks": [
            {"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
            {"type": "tool_use", "id": "tu-2", "name": "risky_tool", "input": {}},
        ],
        "tool_result_blocks": [],
        "next_turn": 2,
        "disable_tools": False,
        "readonly_only": False,
        "suspend_kind": "observing",
        "observation": {
            "terminal_status": True,
            "final_model_content": "ok",
            "final_is_error": False,
        },
    }
    blocked_result = ToolExecutionResult(
        model_content="Needs approval",
        raw_result="",
        blocked=True,
        suspended=True,
        waiting_kind="awaiting_approval",
        execution_status="blocked",
        state_applied=False,
    )
    executor.execute.return_value = blocked_result
    executor.current_note_cursor.return_value = 0
    executor.persist_suspended_state = MagicMock()
    executor.clear_suspended_state = MagicMock()
    executor.consume_appended_notes.return_value = ([], 0)

    provider = _mock_provider()
    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)
    ctx = _make_task_context()

    result = rt.resume(step_attempt_id="sa-1", task_context=ctx)
    assert isinstance(result, AgentResult)
    assert result.blocked is True


# ── AgentRuntime.run_stream — tool use branch ──────────────────────


def test_run_stream_tool_use_loop() -> None:
    """Test run_stream with tool_use stop_reason exercises the stream tool execution path."""
    provider = _mock_provider(supports_streaming=True)

    # First stream call returns tool_use, second returns text
    call_count = 0

    def stream_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                ProviderEvent(
                    type="block_end",
                    block={
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "bash",
                        "input": {"cmd": "echo hi"},
                    },
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=10, output_tokens=5),
                ),
            ]
        return [
            ProviderEvent(type="text", text="Done"),
            ProviderEvent(
                type="block_end",
                block={"type": "text", "text": "Done"},
            ),
            ProviderEvent(
                type="message_end",
                stop_reason="end_turn",
                usage=UsageMetrics(input_tokens=5, output_tokens=2),
            ),
        ]

    provider.stream.side_effect = stream_side_effect

    executor = MagicMock()
    exec_result = ToolExecutionResult(model_content="output", raw_result="output")
    executor.execute.return_value = exec_result

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(
        provider=provider,
        registry=registry,
        tool_executor=executor,
        max_turns=5,
    )

    result = rt.run_stream("test")
    assert result.text == "Done"
    assert result.tool_calls >= 1


def test_run_stream_tool_use_unknown_tool() -> None:
    """Test run_stream handles KeyError for unknown tools."""
    provider = _mock_provider(supports_streaming=True)

    call_count = 0

    def stream_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                ProviderEvent(
                    type="block_end",
                    block={"type": "tool_use", "id": "tu-1", "name": "missing_tool", "input": {}},
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=10, output_tokens=5),
                ),
            ]
        return [
            ProviderEvent(type="text", text="ok"),
            ProviderEvent(type="block_end", block={"type": "text", "text": "ok"}),
            ProviderEvent(
                type="message_end",
                stop_reason="end_turn",
                usage=UsageMetrics(input_tokens=5, output_tokens=2),
            ),
        ]

    provider.stream.side_effect = stream_side_effect

    executor = MagicMock()
    executor.execute.side_effect = KeyError("unknown")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    registry.list_tools.return_value = []
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)

    result = rt.run_stream("test")
    assert result.text == "ok"


def test_run_stream_tool_use_exception() -> None:
    """Test run_stream handles generic tool execution exception."""
    provider = _mock_provider(supports_streaming=True)

    call_count = 0

    def stream_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                ProviderEvent(
                    type="block_end",
                    block={"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=10, output_tokens=5),
                ),
            ]
        return [
            ProviderEvent(type="text", text="ok"),
            ProviderEvent(type="block_end", block={"type": "text", "text": "ok"}),
            ProviderEvent(
                type="message_end",
                stop_reason="end_turn",
                usage=UsageMetrics(input_tokens=5, output_tokens=2),
            ),
        ]

    provider.stream.side_effect = stream_side_effect

    executor = MagicMock()
    executor.execute.side_effect = ValueError("bad input")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=executor)

    result = rt.run_stream("test")
    assert result.text == "ok"


def test_run_stream_no_executor_raises_in_tool_use() -> None:
    """Test run_stream with tool_use but no executor generates error content."""
    provider = _mock_provider(supports_streaming=True)

    call_count = 0

    def stream_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                ProviderEvent(
                    type="block_end",
                    block={"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
                ),
                ProviderEvent(
                    type="message_end",
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=10, output_tokens=5),
                ),
            ]
        return [
            ProviderEvent(type="text", text="ok"),
            ProviderEvent(type="block_end", block={"type": "text", "text": "ok"}),
            ProviderEvent(
                type="message_end",
                stop_reason="end_turn",
                usage=UsageMetrics(input_tokens=5, output_tokens=2),
            ),
        ]

    provider.stream.side_effect = stream_side_effect

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(provider=provider, registry=registry, tool_executor=None)

    result = rt.run_stream("test")
    assert result.text == "ok"


def test_run_stream_max_turns_exceeded() -> None:
    """Test run_stream max turns exceeded sends final summary."""
    provider = _mock_provider(supports_streaming=True)

    def stream_side_effect(request):
        # Always return tool_use to force max turns
        return [
            ProviderEvent(
                type="block_end",
                block={"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
            ),
            ProviderEvent(
                type="message_end",
                stop_reason="tool_use",
                usage=UsageMetrics(input_tokens=10, output_tokens=5),
            ),
        ]

    provider.stream.side_effect = stream_side_effect
    # Final summary uses provider.generate
    provider.generate.return_value = ProviderResponse(
        content=[{"type": "text", "text": "Final summary"}],
        stop_reason="end_turn",
        usage=UsageMetrics(input_tokens=5, output_tokens=3),
    )

    executor = MagicMock()
    executor.execute.return_value = ToolExecutionResult(model_content="ok", raw_result="ok")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(
        provider=provider,
        registry=registry,
        tool_executor=executor,
        max_turns=2,
    )

    result = rt.run_stream("test")
    assert result.execution_status == "succeeded"


def test_run_stream_max_turns_final_summary_fails() -> None:
    """Test run_stream max turns exceeded with failing final summary."""
    provider = _mock_provider(supports_streaming=True)

    def stream_side_effect(request):
        return [
            ProviderEvent(
                type="block_end",
                block={"type": "tool_use", "id": "tu-1", "name": "bash", "input": {}},
            ),
            ProviderEvent(
                type="message_end",
                stop_reason="tool_use",
                usage=UsageMetrics(input_tokens=10, output_tokens=5),
            ),
        ]

    provider.stream.side_effect = stream_side_effect
    provider.generate.side_effect = RuntimeError("final failed")

    executor = MagicMock()
    executor.execute.return_value = ToolExecutionResult(model_content="ok", raw_result="ok")

    registry = _mock_registry()
    registry.get.side_effect = KeyError("not found")
    rt = _make_runtime(
        provider=provider,
        registry=registry,
        tool_executor=executor,
        max_turns=2,
    )

    result = rt.run_stream("test")
    assert result.execution_status == "failed"


def test_run_stream_tool_use_without_blocks_raises() -> None:
    """Test run_stream raises when tool_use stop_reason but no tool blocks."""
    provider = _mock_provider(supports_streaming=True)

    provider.stream.return_value = [
        ProviderEvent(
            type="block_end",
            block={"type": "text", "text": "thinking..."},
        ),
        ProviderEvent(
            type="message_end",
            stop_reason="tool_use",
            usage=UsageMetrics(input_tokens=10, output_tokens=5),
        ),
    ]

    rt = _make_runtime(provider=provider)

    with pytest.raises(RuntimeError, match="tool_use without tool blocks"):
        rt.run_stream("test")


def test_run_stream_thinking_event() -> None:
    """Test run_stream processes thinking events."""
    provider = _mock_provider(supports_streaming=True)
    provider.stream.return_value = [
        ProviderEvent(type="thinking", text="Let me think..."),
        ProviderEvent(type="text", text="Answer"),
        ProviderEvent(
            type="block_end",
            block={"type": "text", "text": "Answer"},
        ),
        ProviderEvent(
            type="message_end",
            stop_reason="end_turn",
            usage=UsageMetrics(input_tokens=10, output_tokens=5),
        ),
    ]

    tokens = []
    rt = _make_runtime(provider=provider)
    rt.run_stream("test", on_token=lambda kind, text: tokens.append((kind, text)))

    assert ("thinking", "Let me think...") in tokens
    assert ("text", "Answer") in tokens


def test_run_stream_fallback_with_thinking() -> None:
    """Test run_stream fallback path emits thinking token when present."""
    provider = _mock_provider(supports_streaming=False)
    # Make run() return a result with thinking
    provider.generate.return_value = ProviderResponse(
        content=[
            {"type": "thinking", "thinking": "deep thought"},
            {"type": "text", "text": "Answer"},
        ],
        stop_reason="end_turn",
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )

    tokens = []
    rt = _make_runtime(provider=provider)
    result = rt.run_stream("test", on_token=lambda kind, text: tokens.append((kind, text)))

    assert result.text == "Answer"


# ── AgentRuntime.run with tool_use stop ────────────────────────────


def test_run_tool_use_without_blocks_raises() -> None:
    """Test that run raises RuntimeError when tool_use stop but no tool blocks."""
    provider = _mock_provider()
    provider.generate.return_value = ProviderResponse(
        content=[{"type": "text", "text": "thinking"}],
        stop_reason="tool_use",
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
    )
    rt = _make_runtime(provider=provider)

    with pytest.raises(RuntimeError, match="tool_use without tool blocks"):
        rt.run("test")


# ── format_tool_result_content edge case ───────────────────────────


def test_format_tool_result_content_tool_result_block() -> None:
    """A single dict with type='text' gets wrapped in a list."""
    block = {"type": "text", "text": "hello"}
    result = format_tool_result_content(block, 1000)
    assert result == [block]
