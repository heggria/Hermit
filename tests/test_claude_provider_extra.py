from __future__ import annotations
import httpx

import sys
from types import SimpleNamespace

import pytest

from hermit.core.tools import ToolSpec
from hermit.provider.contracts import ProviderRequest
from hermit.provider.providers.claude import (
    ClaudeProvider,
    _cache_tools,
    _inject_cache_control,
    _set_cache_on_message,
    _strip_thinking_blocks,
    build_claude_provider,
)


def _tool(name: str = "search") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Search docs",
        input_schema={"type": "object"},
        handler=lambda payload: payload,
    )


def test_cache_helpers_cover_string_list_and_thinking_cleanup() -> None:
    messages = [{"role": "user", "content": "hello"}]
    _set_cache_on_message(messages, 0)
    assert messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"

    list_messages = [{"role": "assistant", "content": [{"type": "text", "text": "a"}]}]
    _set_cache_on_message(list_messages, 0)
    assert list_messages[0]["content"][-1]["cache_control"]["type"] == "ephemeral"

    system_payload, cached_messages = _inject_cache_control(
        [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": [{"type": "text", "text": "four"}]},
        ],
        "system",
    )
    assert system_payload[0]["text"] == "system"
    assert cached_messages[1]["content"][-1]["cache_control"]["type"] == "ephemeral"
    assert cached_messages[-1]["content"][-1]["cache_control"]["type"] == "ephemeral"

    stripped = _strip_thinking_blocks(
        [
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "plan"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "p"}, {"type": "text", "text": "done"}]},
        ]
    )
    assert stripped[0]["content"] == [{"type": "text", "text": ""}]
    assert stripped[1]["content"] == [{"type": "text", "text": "done"}]


def test_cache_tools_only_marks_last_tool() -> None:
    cached = _cache_tools([{"name": "a"}, {"name": "b"}])
    assert "cache_control" not in cached[0]
    assert cached[-1]["cache_control"]["type"] == "ephemeral"
    assert _cache_tools([]) == []


def test_claude_payload_builds_system_thinking_tools_and_clone() -> None:
    provider = ClaudeProvider(client=object(), model="claude-3", system_prompt="fallback")
    request = ProviderRequest(
        model="override-model",
        max_tokens=512,
        messages=[
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}]},
            {"role": "user", "content": "hello"},
        ],
        tools=[_tool()],
        thinking_budget=128,
    )

    payload = provider._payload(request, stream=True)
    clone = provider.clone(model="child", system_prompt="child-system")

    assert payload["model"] == "override-model"
    assert payload["thinking"]["budget_tokens"] == 128
    assert payload["stream"] is True
    assert payload["tools"][-1]["cache_control"]["type"] == "ephemeral"
    assert payload["system"][0]["text"] == "fallback"
    assert payload["messages"][0]["content"] == [{"type": "text", "text": ""}]
    assert clone.model == "child"
    assert clone.system_prompt == "child-system"


def test_claude_payload_moves_internal_tool_context_into_system_prompt() -> None:
    provider = ClaudeProvider(client=object(), model="claude-3", system_prompt="fallback")
    request = ProviderRequest(
        model="claude-3",
        max_tokens=256,
        messages=[
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_skill", "name": "read_skill", "input": {"name": "grok-search"}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_skill",
                        "content": "<skill_content name=\"grok-search\">secret</skill_content>",
                        "internal_context": True,
                        "tool_name": "read_skill",
                    }
                ],
            },
        ],
    )

    payload = provider._payload(request)

    assert "secret" in payload["system"][0]["text"]
    assert "do not quote" in payload["system"][0]["text"]
    assert payload["messages"][1]["content"][0]["content"] == "[internal context loaded]"


def test_claude_generate_normalizes_usage_and_api_errors() -> None:
    dict_error_response = SimpleNamespace(
        content=[{"type": "text", "text": "hello"}],
        stop_reason="end_turn",
        error={"message": "backend failed"},
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=2,
        ),
    )
    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: dict_error_response)),
        model="claude-3",
    )

    response = provider.generate(
        ProviderRequest(model="claude-3", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    )

    assert response.error == "backend failed"
    assert response.usage.input_tokens == 11
    assert response.usage.cache_creation_tokens == 2

    provider.client.messages.create = lambda **_kwargs: SimpleNamespace(content=[], error="plain error", usage=None)
    plain = provider.generate(ProviderRequest(model="claude-3", max_tokens=10, messages=[{"role": "user", "content": "hi"}]))
    assert plain.error == "plain error"
    assert plain.usage.output_tokens == 0


def test_claude_stream_emits_text_thinking_json_and_message_end() -> None:
    stream_events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                stop_reason=None,
                usage=SimpleNamespace(
                    input_tokens=5,
                    output_tokens=0,
                    cache_read_input_tokens=1,
                    cache_creation_input_tokens=2,
                ),
            ),
        ),
        SimpleNamespace(type="content_block_start", content_block={"type": "thinking", "thinking": ""}),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="plan")),
        SimpleNamespace(type="content_block_stop"),
        SimpleNamespace(type="content_block_start", content_block={"type": "tool_use", "id": "1", "name": "search"}),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="input_json_delta", partial_json='{"q":"hi"}')),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="signature_delta", signature="sig")),
        SimpleNamespace(type="content_block_stop"),
        SimpleNamespace(type="content_block_start", content_block={"type": "text", "text": ""}),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="hello")),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"), usage=SimpleNamespace(output_tokens=9)),
        SimpleNamespace(type="content_block_stop"),
    ]
    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: iter(stream_events))),
        model="claude-3",
    )

    events = list(
        provider.stream(ProviderRequest(model="claude-3", max_tokens=10, messages=[{"role": "user", "content": "hi"}]))
    )

    assert events[0].type == "thinking"
    assert events[0].text == "plan"
    assert events[1].type == "block_end"
    assert events[1].block["thinking"] == "plan"
    assert events[2].type == "block_end"
    assert events[2].block["input"] == {"q": "hi"}
    assert events[2].block["signature"] == "sig"
    assert events[3].type == "text"
    assert events[3].text == "hello"
    assert events[-1].type == "message_end"
    assert events[-1].stop_reason == "end_turn"
    assert events[-1].usage.output_tokens == 9


def test_claude_stream_drops_invalid_partial_json() -> None:
    stream_events = [
        SimpleNamespace(type="content_block_start", content_block={"type": "tool_use", "id": "1", "name": "search"}),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="input_json_delta", partial_json="{bad")),
        SimpleNamespace(type="content_block_stop"),
    ]
    provider = ClaudeProvider(
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: iter(stream_events))),
        model="claude-3",
    )

    events = list(
        provider.stream(ProviderRequest(model="claude-3", max_tokens=10, messages=[{"role": "user", "content": "hi"}]))
    )
    assert events[0].block.get("input") is None


def test_build_claude_provider_passes_expected_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    settings = SimpleNamespace(
        claude_api_key="sk-ant",
        claude_auth_token="auth-token",
        claude_base_url="https://proxy.example.com",
        parsed_claude_headers={"X-Test": "1"},
        command_timeout_seconds=30,
    )

    provider = build_claude_provider(settings, model="claude-3", system_prompt="system")

    assert isinstance(provider, ClaudeProvider)
    assert captured == {
        "api_key": "sk-ant",
        "auth_token": "auth-token",
        "base_url": "https://proxy.example.com",
        "default_headers": {"X-Test": "1"},
        "timeout": httpx.Timeout(600.0, connect=30),
    }
