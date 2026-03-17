from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from hermit.kernel.execution.executor.executor import ToolExecutionResult
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec
from hermit.runtime.provider_host.execution.runtime import (
    AgentRuntime,
    _tool_result_json_text,
    format_tool_result_content,
    truncate_middle_text,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderEvent,
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)


class FakeProvider:
    def __init__(
        self,
        *,
        name: str = "fake",
        features: ProviderFeatures | None = None,
        responses: list[ProviderResponse] | None = None,
        stream_events: list[list[ProviderEvent | SimpleNamespace]] | None = None,
        generate_error: Exception | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.features = features or ProviderFeatures()
        self._responses = list(responses or [])
        self._stream_events = list(stream_events or [])
        self._generate_error = generate_error
        self._stream_error = stream_error
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._generate_error is not None:
            raise self._generate_error
        return self._responses.pop(0)

    def stream(self, request: ProviderRequest):
        self.requests.append(request)
        if self._stream_error is not None:
            raise self._stream_error
        yield from self._stream_events.pop(0)

    def clone(
        self, *, model: str | None = None, system_prompt: str | None = None
    ) -> "FakeProvider":
        return self


def test_truncate_middle_text_and_json_helpers() -> None:
    assert truncate_middle_text("abcdef", 0) == "abcdef"
    assert truncate_middle_text("abcdef", 4) == "abcd"

    truncated = truncate_middle_text("x" * 80, 40)
    assert "\n...\n" in truncated
    assert len(truncated) <= 40

    payload_text = _tool_result_json_text({"b": 2, "a": 1}, 20)
    assert payload_text.startswith("{")
    assert '"a"' in payload_text


def test_format_tool_result_content_handles_strings_dicts_and_blocks() -> None:
    assert format_tool_result_content("hello", 10) == "hello"
    assert isinstance(format_tool_result_content({"a": 1}, 50), str)
    assert format_tool_result_content({"type": "text", "text": "hi"}, 50) == [
        {"type": "text", "text": "hi"}
    ]
    assert format_tool_result_content(
        [{"type": "image", "source": {"type": "url", "url": "https://example.com"}}], 50
    ) == [{"type": "image", "source": {"type": "url", "url": "https://example.com"}}]
    list_payload = format_tool_result_content([{"a": 1}], 50)
    assert isinstance(list_payload, str)
    assert '"a"' in list_payload


def test_runtime_clone_and_request_handle_thinking_support() -> None:
    provider = FakeProvider(features=ProviderFeatures(supports_thinking=False))
    registry = ToolRegistry()
    runtime = AgentRuntime(
        provider=provider,
        registry=registry,
        model="base-model",
        system_prompt="sys",
        thinking_budget=10,
        max_turns=2,
    )

    clone = runtime.clone(model="child-model", system_prompt="child", max_turns=5)
    request = runtime._request(
        [{"role": "user", "content": "hi"}], disable_tools=True, readonly_only=False, stream=False
    )

    assert clone.model == "child-model"
    assert clone.system_prompt == "child"
    assert clone.max_turns == 5
    assert request.thinking_budget == 0
    assert request.system_prompt == "sys"


def test_runtime_resume_requires_tool_executor() -> None:
    runtime = AgentRuntime(provider=FakeProvider(), registry=ToolRegistry(), model="fake")
    with pytest.raises(RuntimeError, match="Task resume requires a configured ToolExecutor"):
        runtime.resume(step_attempt_id="attempt", task_context=SimpleNamespace())


def test_runtime_max_turn_fallback_is_localized(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {"type": "tool_use", "id": "call_1", "name": "echo", "input": {"value": "hi"}}
                ],
                stop_reason="tool_use",
            )
        ]
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo value",
            input_schema={"type": "object"},
            handler=lambda payload: payload,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
    runtime = AgentRuntime(
        provider=provider, registry=registry, model="fake", max_turns=1, locale="zh-CN"
    )

    result = runtime.run("hello")

    assert "已达到最大轮次限制" in result.text


def test_runtime_run_returns_provider_error_payload() -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(content=[{"type": "text", "text": "ignored"}], error="bad gateway")
        ]
    )
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake")

    result = runtime.run("hello")

    assert result.text == "[API Error] bad gateway"
    assert result.execution_status == "failed"


def test_runtime_resume_executes_pending_tool_results_before_appended_notes() -> None:
    provider = FakeProvider(
        responses=[ProviderResponse(content=[{"type": "text", "text": "done"}])]
    )
    executed: list[tuple[str, dict[str, object]]] = []

    runtime = AgentRuntime(
        provider=provider,
        registry=ToolRegistry(),
        model="fake",
        tool_executor=SimpleNamespace(
            load_blocked_state=lambda _step_attempt_id: {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_1",
                                "name": "echo",
                                "input": {"value": "hi"},
                            }
                        ],
                    }
                ],
                "pending_tool_blocks": [
                    {"type": "tool_use", "id": "call_1", "name": "echo", "input": {"value": "hi"}}
                ],
                "tool_result_blocks": [],
                "next_turn": 2,
                "disable_tools": False,
                "readonly_only": False,
                "suspend_kind": "awaiting_approval",
            },
            clear_blocked_state=lambda _step_attempt_id: None,
            execute=lambda _task_context, tool_name, tool_input: (
                executed.append((tool_name, tool_input))
                or ToolExecutionResult(model_content={"ok": True}, raw_result={"ok": True})
            ),
            consume_appended_notes=lambda _task_context: (
                [{"role": "user", "content": "[Task Note Appended]\n确认删除"}],
                1,
            ),
        ),
    )

    result = runtime.resume(
        step_attempt_id="attempt",
        task_context=SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt"),
    )

    assert result.text == "done"
    assert executed == [("echo", {"value": "hi"})]
    request_messages = provider.requests[0].messages
    assert request_messages[0]["role"] == "assistant"
    assert request_messages[1]["role"] == "user"
    assert request_messages[1]["content"][0]["type"] == "tool_result"
    assert request_messages[1]["content"][0]["tool_use_id"] == "call_1"
    assert request_messages[2]["role"] == "user"
    assert request_messages[2]["content"] == "[Task Note Appended]\n确认删除"


def test_runtime_marks_internal_context_tool_results() -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "read_skill",
                        "input": {"name": "grok-search"},
                    }
                ],
                stop_reason="tool_use",
            ),
            ProviderResponse(content=[{"type": "text", "text": "done"}], stop_reason="end_turn"),
        ]
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read_skill",
            description="Read a skill",
            input_schema={"type": "object"},
            handler=lambda payload: payload,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
            result_is_internal_context=True,
        )
    )
    runtime = AgentRuntime(
        provider=provider,
        registry=registry,
        model="fake",
        max_turns=2,
        tool_executor=SimpleNamespace(
            execute=lambda *_args, **_kwargs: ToolExecutionResult(
                model_content='<skill_content name="grok-search">secret</skill_content>',
                raw_result="secret",
            )
        ),
    )

    result = runtime.run(
        "hello",
        task_context=SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt"),
    )

    tool_result_block = provider.requests[1].messages[2]["content"][0]
    assert result.text == "done"
    assert tool_result_block["internal_context"] is True
    assert tool_result_block["tool_name"] == "read_skill"


def test_runtime_run_raises_when_tool_use_has_no_blocks() -> None:
    provider = FakeProvider(responses=[ProviderResponse(content=[], stop_reason="tool_use")])
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake")

    with pytest.raises(RuntimeError, match="Provider requested tool_use without tool blocks"):
        runtime.run("hello")


def test_runtime_errors_are_localized_when_locale_is_set() -> None:
    provider = FakeProvider(responses=[ProviderResponse(content=[], stop_reason="tool_use")])
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake", locale="zh-CN")

    with pytest.raises(
        RuntimeError, match="Provider 请求了 tool_use，但响应中没有对应的 tool block"
    ):
        runtime.run("hello")

    runtime = AgentRuntime(
        provider=FakeProvider(), registry=ToolRegistry(), model="fake", locale="zh-CN"
    )
    with pytest.raises(RuntimeError, match="恢复任务执行需要已配置的 ToolExecutor"):
        runtime.resume(step_attempt_id="attempt", task_context=SimpleNamespace())


def test_runtime_run_max_turns_final_summary_success() -> None:
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                content=[
                    {"type": "tool_use", "id": "call_1", "name": "echo", "input": {"value": "hi"}}
                ],
                stop_reason="tool_use",
            ),
            ProviderResponse(content=[{"type": "text", "text": "summary"}], stop_reason="end_turn"),
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        registry=ToolRegistry(),
        model="fake",
        max_turns=1,
        locale="en-US",
        tool_executor=SimpleNamespace(
            execute=lambda *_args, **_kwargs: ToolExecutionResult(
                model_content="ok", raw_result="ok"
            )
        ),
    )
    task_ctx = SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt")

    result = runtime.run("hello", task_context=task_ctx)

    assert result.text == "summary"
    assert result.turns == 2
    assert result.messages[-2]["role"] == "user"
    assert "maximum turn limit" in result.messages[-2]["content"]


def test_runtime_run_max_turns_final_summary_failure() -> None:
    class FailingSummaryProvider(FakeProvider):
        def generate(self, request: ProviderRequest) -> ProviderResponse:
            self.requests.append(request)
            if len(self.requests) > 1:
                raise RuntimeError("summary boom")
            return self._responses.pop(0)

    provider = FailingSummaryProvider(
        responses=[
            ProviderResponse(
                content=[
                    {"type": "tool_use", "id": "call_1", "name": "echo", "input": {"value": "hi"}}
                ],
                stop_reason="tool_use",
            )
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        registry=ToolRegistry(),
        model="fake",
        max_turns=1,
        locale="en-US",
        tool_executor=SimpleNamespace(
            execute=lambda *_args, **_kwargs: ToolExecutionResult(
                model_content="ok", raw_result="ok"
            )
        ),
    )
    task_ctx = SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt")

    result = runtime.run("hello", task_context=task_ctx)

    assert "final summary request failed: summary boom" in result.text
    assert result.execution_status == "failed"


def test_execute_tool_turn_handles_callbacks_unknown_tools_and_failures() -> None:
    registry = ToolRegistry()
    call_names: list[str] = []

    def execute(_task_context, tool_name: str, _tool_input: dict) -> ToolExecutionResult:
        call_names.append(tool_name)
        raise KeyError(tool_name)

    runtime = AgentRuntime(
        provider=FakeProvider(),
        registry=registry,
        model="fake",
        tool_executor=SimpleNamespace(execute=execute),
    )
    started: list[tuple[str, dict]] = []
    called: list[tuple[str, dict, object]] = []

    result_blocks, tool_calls = runtime._execute_tool_turn(
        messages=[],
        tool_use_blocks=[
            {"type": "tool_use", "id": "1", "name": "missing", "input": {"a": 1}},
            {"type": "tool_use", "id": "2", "name": "explode", "input": {"b": 2}},
        ],
        tool_result_blocks=[],
        turn=1,
        on_tool_call=lambda name, payload, result: called.append((name, payload, result)),
        on_tool_start=lambda name, payload: started.append((name, payload)),
        disable_tools=False,
        readonly_only=False,
        task_context=SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt"),
        usage=UsageMetrics(),
        tool_calls=0,
    )

    assert tool_calls == 2
    assert started == [("missing", {"a": 1}), ("explode", {"b": 2})]
    assert "Unknown tool" in result_blocks[0]["content"]
    assert "Unknown tool" in called[0][2]
    assert "Unknown tool" in result_blocks[1]["content"]
    assert call_names == ["missing", "explode"]


def test_run_stream_serializes_localized_tool_execution_errors() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [
                SimpleNamespace(
                    type="block_end",
                    block={"type": "tool_use", "id": "1", "name": "write_file", "input": {}},
                ),
                SimpleNamespace(type="message_end", stop_reason="tool_use", usage=UsageMetrics()),
            ]
        ],
    )
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake", locale="zh-CN")

    result = runtime.run_stream("hello")

    assert result.messages[-1]["content"][0]["content"] == (
        "执行 write_file 出错：RuntimeError: 流式执行工具需要任务作用域的 kernel executor。"
    )


def test_execute_tool_turn_handles_blocked_and_denied_results() -> None:
    blocked_exec = ToolExecutionResult(
        model_content="blocked",
        raw_result="blocked",
        blocked=True,
        approval_id="approval-1",
        approval_message="needs approval",
        execution_status="blocked",
        state_applied=True,
    )
    denied_exec = replace(
        blocked_exec,
        blocked=False,
        denied=True,
        model_content="[Policy Denied] nope",
        execution_status="failed",
    )
    persisted: list[dict] = []
    runtime = AgentRuntime(
        provider=FakeProvider(),
        registry=ToolRegistry(),
        model="fake",
        tool_executor=SimpleNamespace(
            execute=lambda *_args, **_kwargs: blocked_exec,
            persist_blocked_state=lambda task_context, **kwargs: persisted.append(
                {"task_context": task_context, **kwargs}
            ),
        ),
    )
    task_ctx = SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt")

    blocked = runtime._execute_tool_turn(
        messages=[{"role": "assistant", "content": []}],
        tool_use_blocks=[{"type": "tool_use", "id": "1", "name": "write", "input": {}}],
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=task_ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )
    assert blocked.blocked is True
    assert blocked.text == "needs approval"
    assert persisted[0]["pending_tool_blocks"][0]["name"] == "write"

    runtime.tool_executor.execute = lambda *_args, **_kwargs: denied_exec  # type: ignore[attr-defined]
    denied = runtime._execute_tool_turn(
        messages=[],
        tool_use_blocks=[{"type": "tool_use", "id": "1", "name": "write", "input": {}}],
        tool_result_blocks=[],
        turn=1,
        on_tool_call=None,
        on_tool_start=None,
        disable_tools=False,
        readonly_only=False,
        task_context=task_ctx,
        usage=UsageMetrics(),
        tool_calls=0,
    )
    assert denied.text == "[Policy Denied] nope"
    assert denied.execution_status == "failed"


def test_execute_tool_requires_executor_and_task_context() -> None:
    runtime = AgentRuntime(provider=FakeProvider(), registry=ToolRegistry(), model="fake")
    with pytest.raises(RuntimeError, match="kernel executor is required"):
        runtime._execute_tool(task_context=None, tool_name="echo", tool_input={})

    runtime.tool_executor = SimpleNamespace(execute=lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="task context is missing"):
        runtime._execute_tool(task_context=None, tool_name="echo", tool_input={})


def test_run_stream_raises_when_tool_use_has_no_blocks() -> None:
    provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [SimpleNamespace(type="message_end", stop_reason="tool_use", usage=UsageMetrics())]
        ],
    )
    runtime = AgentRuntime(provider=provider, registry=ToolRegistry(), model="fake")

    with pytest.raises(RuntimeError, match="Provider requested tool_use without tool blocks"):
        runtime.run_stream("hello")


def test_run_stream_max_turns_summary_success_and_failure() -> None:
    success_provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [
                SimpleNamespace(
                    type="block_end",
                    block={
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "echo",
                        "input": {"value": "hi"},
                    },
                ),
                SimpleNamespace(type="message_end", stop_reason="tool_use", usage=UsageMetrics()),
            ]
        ],
        responses=[ProviderResponse(content=[{"type": "text", "text": "summary"}])],
    )
    runtime = AgentRuntime(
        provider=success_provider, registry=ToolRegistry(), model="fake", max_turns=1
    )
    success = runtime.run_stream("hello")
    assert success.text == "summary"
    assert success.turns == 2

    fail_provider = FakeProvider(
        features=ProviderFeatures(supports_streaming=True),
        stream_events=[
            [
                SimpleNamespace(
                    type="block_end",
                    block={
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "echo",
                        "input": {"value": "hi"},
                    },
                ),
                SimpleNamespace(type="message_end", stop_reason="tool_use", usage=UsageMetrics()),
            ]
        ],
        generate_error=RuntimeError("summary boom"),
    )
    failed = AgentRuntime(
        provider=fail_provider,
        registry=ToolRegistry(),
        model="fake",
        max_turns=1,
        locale="en-US",
    ).run_stream("hello")
    assert "final summary request failed: summary boom" in failed.text
    assert failed.execution_status == "failed"
