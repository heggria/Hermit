from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from hermit.core.agent import ClaudeAgent, _normalize_block, truncate_middle_text
from hermit.core.tools import ToolRegistry, ToolSpec


@dataclass
class FakeResponse:
    content: list[dict[str, object]]
    stop_reason: str | None = None


class FakeMessagesAPI:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.messages = FakeMessagesAPI(responses)


def test_agent_runs_tool_loop_and_returns_text() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo a string.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=lambda payload: {"echo": payload["value"]},
        )
    )

    client = FakeClient(
        [
            FakeResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "echo",
                        "input": {"value": "hello"},
                    }
                ],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "text", "text": "final answer"}],
                stop_reason="end_turn",
            ),
        ]
    )

    result = ClaudeAgent(
        client=client,
        registry=registry,
        model="fake-model",
        tool_output_limit=200,
    ).run("say hi")

    assert result.text == "final answer"
    assert result.tool_calls == 1
    assert len(client.messages.calls) == 2
    follow_up_messages = client.messages.calls[1]["messages"]
    assert follow_up_messages[-1]["role"] == "user"
    assert follow_up_messages[-1]["content"][0]["type"] == "tool_result"


def test_agent_stops_after_max_turns() -> None:
    """When max_turns is reached the agent makes one final no-tool call to
    produce a graceful summary instead of raising RuntimeError."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="loop",
            description="Loop forever.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _: "ok",
        )
    )
    client = FakeClient(
        [
            FakeResponse(
                content=[{"type": "tool_use", "id": "1", "name": "loop", "input": {}}],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "tool_use", "id": "2", "name": "loop", "input": {}}],
                stop_reason="tool_use",
            ),
            # Final summary call after max_turns exceeded
            FakeResponse(
                content=[{"type": "text", "text": "Based on what I found so far…"}],
                stop_reason="end_turn",
            ),
        ]
    )

    agent = ClaudeAgent(client=client, registry=registry, model="fake-model", max_turns=2)
    result = agent.run("loop")

    assert result.tool_calls == 2
    assert result.turns == 3  # max_turns + 1 (the graceful summary turn)
    assert result.text == "Based on what I found so far…"
    # Verify the final call was made without tools (no tools= param)
    assert "tools" not in client.messages.calls[-1]


def test_agent_returns_messages_in_result() -> None:
    registry = ToolRegistry()
    client = FakeClient(
        [FakeResponse(content=[{"type": "text", "text": "hi"}], stop_reason="end_turn")]
    )

    result = ClaudeAgent(client=client, registry=registry, model="fake").run("hello")

    assert result.messages is not None
    assert result.messages[0] == {"role": "user", "content": "hello"}
    assert result.messages[1]["role"] == "assistant"


def test_agent_extracts_thinking_from_response() -> None:
    registry = ToolRegistry()
    client = FakeClient(
        [
            FakeResponse(
                content=[
                    {"type": "thinking", "thinking": "Let me consider..."},
                    {"type": "text", "text": "final answer"},
                ],
                stop_reason="end_turn",
            )
        ]
    )

    result = ClaudeAgent(client=client, registry=registry, model="fake").run("test")

    assert result.text == "final answer"
    assert result.thinking == "Let me consider..."


def test_agent_passes_thinking_budget_when_set() -> None:
    registry = ToolRegistry()
    client = FakeClient(
        [FakeResponse(content=[{"type": "text", "text": "ok"}], stop_reason="end_turn")]
    )

    ClaudeAgent(client=client, registry=registry, model="fake", thinking_budget=5000).run("test")

    payload = client.messages.calls[0]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 5000}


def test_agent_omits_thinking_when_budget_zero() -> None:
    registry = ToolRegistry()
    client = FakeClient(
        [FakeResponse(content=[{"type": "text", "text": "ok"}], stop_reason="end_turn")]
    )

    ClaudeAgent(client=client, registry=registry, model="fake", thinking_budget=0).run("test")

    payload = client.messages.calls[0]
    assert "thinking" not in payload


def test_agent_handles_unknown_tool_gracefully() -> None:
    registry = ToolRegistry()
    client = FakeClient(
        [
            FakeResponse(
                content=[{"type": "tool_use", "id": "t1", "name": "ghost_tool", "input": {}}],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "text", "text": "recovered"}],
                stop_reason="end_turn",
            ),
        ]
    )

    result = ClaudeAgent(client=client, registry=registry, model="fake").run("test")

    assert result.text == "recovered"
    follow_up = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "Unknown tool" in follow_up["content"]


def test_agent_handles_tool_exception_gracefully() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="boom",
            description="Always fails.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _: (_ for _ in ()).throw(ValueError("kaboom")),
        )
    )
    client = FakeClient(
        [
            FakeResponse(
                content=[{"type": "tool_use", "id": "t2", "name": "boom", "input": {}}],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "text", "text": "handled"}],
                stop_reason="end_turn",
            ),
        ]
    )

    result = ClaudeAgent(client=client, registry=registry, model="fake").run("test")

    assert result.text == "handled"
    follow_up = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "kaboom" in follow_up["content"]


def test_agent_stringifies_structured_tool_result_objects() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="structured",
            description="Return structured data.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _: {"items": [{"foo": "bar"}], "ok": True},
        )
    )
    client = FakeClient(
        [
            FakeResponse(
                content=[{"type": "tool_use", "id": "t3", "name": "structured", "input": {}}],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "text", "text": "done"}],
                stop_reason="end_turn",
            ),
        ]
    )

    ClaudeAgent(client=client, registry=registry, model="fake").run("test")

    follow_up = client.messages.calls[1]["messages"][-1]["content"][0]
    assert isinstance(follow_up["content"], str)
    assert '"foo": "bar"' in follow_up["content"]


def test_agent_preserves_image_tool_result_blocks() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="image_tool",
            description="Return an image block.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _: {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
            },
        )
    )
    client = FakeClient(
        [
            FakeResponse(
                content=[{"type": "tool_use", "id": "t4", "name": "image_tool", "input": {}}],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[{"type": "text", "text": "done"}],
                stop_reason="end_turn",
            ),
        ]
    )

    ClaudeAgent(client=client, registry=registry, model="fake").run("test")

    follow_up = client.messages.calls[1]["messages"][-1]["content"][0]
    assert isinstance(follow_up["content"], list)
    assert follow_up["content"][0]["type"] == "image"


def test_normalize_block_converts_sdk_objects_to_dict() -> None:
    class FakeTextBlock:
        def model_dump(self):
            return {"type": "text", "text": "hello from SDK"}

    result = _normalize_block(FakeTextBlock())

    assert result == {"type": "text", "text": "hello from SDK"}
    json.dumps(result)


def test_normalize_block_passes_through_plain_dicts() -> None:
    block = {"type": "text", "text": "already dict"}
    assert _normalize_block(block) == {"type": "text", "text": "already dict"}


def test_agent_messages_are_json_serializable() -> None:
    class SDKBlock:
        def model_dump(self):
            return {"type": "text", "text": "sdk response"}

    class FakeResponseSDK:
        content = [SDKBlock()]
        stop_reason = "end_turn"

    class FakeAPI:
        calls = []
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return FakeResponseSDK()

    class FakeSDKClient:
        messages = FakeAPI()

    agent = ClaudeAgent(client=FakeSDKClient(), registry=ToolRegistry(), model="fake")
    result = agent.run("test")

    serialized = json.dumps(result.messages)
    assert "sdk response" in serialized


def test_truncate_middle_text_preserves_head_and_tail() -> None:
    text = "0123456789" * 20
    truncated = truncate_middle_text(text, 40)

    assert truncated.startswith("0123456789")
    assert truncated.endswith("0123456789")
    assert "...\n" in truncated or "\n...\n" in truncated
