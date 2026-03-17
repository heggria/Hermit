from __future__ import annotations

from hermit.runtime.provider_host.shared import messages


def test_provider_messages_normalize_blocks_and_extract_text() -> None:
    class ModelDumpBlock:
        def model_dump(self):
            return {"type": "text", "text": "hello", "ignored": True}

    class ToDictBlock:
        def to_dict(self):
            return {"type": "thinking", "thinking": "plan", "signature": "sig", "ignored": True}

    class FallbackBlock:
        type = "tool_use"
        id = "tool-1"
        name = "echo"
        input = {"value": "hi"}
        ignored = True

    dict_block = {
        "type": "tool_result",
        "tool_use_id": "1",
        "content": "ok",
        "is_error": False,
        "ignored": True,
    }

    assert messages.normalize_block(dict_block) == {
        "type": "tool_result",
        "tool_use_id": "1",
        "content": "ok",
        "is_error": False,
    }
    assert messages.normalize_block(ModelDumpBlock()) == {"type": "text", "text": "hello"}
    assert messages.normalize_block(ToDictBlock()) == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig",
    }
    assert messages.normalize_block(FallbackBlock()) == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "echo",
        "input": {"value": "hi"},
    }

    normalized = messages.normalize_messages(
        [
            {"role": "assistant", "content": [ModelDumpBlock(), ToDictBlock()]},
            {"role": "user", "content": "hello"},
        ]
    )

    assert normalized[0]["role"] == "assistant"
    assert (
        messages.extract_text(
            [{"type": "text", "text": "line-1"}, {"type": "text", "text": "line-2"}]
        )
        == "line-1\nline-2"
    )
    assert messages.extract_thinking([{"type": "thinking", "thinking": "step-1"}]) == "step-1"
