from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.builtin.compact import commands as compact
from hermit.plugin.base import HookEvent, PluginContext
from hermit.plugin.hooks import HooksEngine


@pytest.fixture(autouse=True)
def _force_compact_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


class _FakeMessagesAPI:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeRunner:
    def __init__(self, session, response=None, error: Exception | None = None) -> None:
        self.saved_sessions: list[object] = []
        self.agent = SimpleNamespace(
            model="fake-model",
            client=SimpleNamespace(messages=_FakeMessagesAPI(response=response, error=error)),
        )
        self.session_manager = SimpleNamespace(
            save=lambda session: self.saved_sessions.append(session),
            get_or_create=lambda session_id: session,
        )


def test_serialize_and_sanitize_messages_cover_tool_use_edge_cases() -> None:
    serialized = compact._serialize_messages(
        [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "answer"},
                    {"type": "thinking", "thinking": "skip me"},
                    {"type": "tool_use", "name": "search", "input": {"q": "weather"}},
                    {"type": "tool_result", "content": {"ok": True}},
                ],
            },
        ]
    )

    sanitized = compact._sanitize_messages(
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "search", "input": {}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "later"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-2", "name": "write", "input": {}}]},
            "ignored",
        ]
    )

    assert "[user]: hello" in serialized
    assert "[assistant]: answer" in serialized
    assert "[call tool search" in serialized
    assert "[tool_result]" in serialized
    assert all(isinstance(item, dict) for item in sanitized)
    assert sanitized[-1]["content"] == [{"type": "text", "text": "later"}]
    assert sanitized[1]["role"] == "user"
    assert sanitized[1]["content"][0]["tool_use_id"] == "tool-1"
    assert all(block.get("id") != "tool-2" for msg in sanitized for block in msg.get("content", []) if isinstance(block, dict))


def test_do_compact_handles_empty_failure_and_success_paths() -> None:
    empty_session = SimpleNamespace(messages=[])
    assert compact._do_compact(SimpleNamespace(), empty_session) == (False, "Nothing to compact.")

    session = SimpleNamespace(
        messages=[{"role": "user", "content": "hello"}],
        total_input_tokens=10,
        total_output_tokens=20,
        total_cache_read_tokens=30,
        total_cache_creation_tokens=40,
    )
    runner = _FakeRunner(
        session,
        response=SimpleNamespace(content=[SimpleNamespace(text="summary"), {"text": " extra"}]),
    )

    compact._state["last_input_tokens"] = 999
    success, message = compact._do_compact(runner, session)

    assert success is True
    assert "Compacted 1 messages into 2 summary messages" in message
    assert session.messages[0]["content"] == "<compacted_context>\nsummary extra\n</compacted_context>"
    assert session.total_input_tokens == 0
    assert compact._state["last_input_tokens"] == 0
    assert runner.saved_sessions == [session]
    assert runner.agent.client.messages.calls[0]["model"] == "fake-model"

    failed_runner = _FakeRunner(session, response=SimpleNamespace(content=[]))
    assert compact._do_compact(failed_runner, SimpleNamespace(messages=[{"role": "user", "content": "x"}])) == (
        False,
        "The LLM did not return a summary, so compaction was cancelled.",
    )

    error_runner = _FakeRunner(session, error=RuntimeError("boom"))
    assert compact._do_compact(error_runner, SimpleNamespace(messages=[{"role": "user", "content": "x"}])) == (
        False,
        "Compaction failed: boom",
    )


def test_compact_hooks_command_and_registration(monkeypatch) -> None:
    session = SimpleNamespace(messages=[{"role": "user", "content": "hello"}])
    runner = _FakeRunner(session, response=SimpleNamespace(content=[{"text": "summary"}]))
    ctx = PluginContext(HooksEngine())

    compact._state["last_input_tokens"] = 1
    compact._post_run_hook(SimpleNamespace(input_tokens=321))
    assert compact._state["last_input_tokens"] == 321
    compact._post_run_hook(SimpleNamespace(input_tokens=0))
    assert compact._state["last_input_tokens"] == 321

    assert compact._pre_run_hook("hello") == "hello"
    compact._state["last_input_tokens"] = compact.AUTO_COMPACT_THRESHOLD - 1
    assert compact._pre_run_hook("hello", session=session, runner=runner) == "hello"

    compact._state["last_input_tokens"] = compact.AUTO_COMPACT_THRESHOLD + 1
    monkeypatch.setattr(compact, "_do_compact", lambda runner, session: (True, "done"))
    pre_run_result = compact._pre_run_hook("hello", session=session, runner=runner)
    assert isinstance(pre_run_result, dict)
    assert pre_run_result["prompt"].startswith("[System] Context was auto-compacted")

    compact._state["last_input_tokens"] = compact.AUTO_COMPACT_THRESHOLD + 1
    monkeypatch.setattr(compact, "_do_compact", lambda runner, session: (False, "failed"))
    assert compact._pre_run_hook("hello", session=session, runner=runner) == "hello"

    monkeypatch.setattr(compact, "_do_compact", lambda runner, session: (True, "manual compact"))
    result = compact._cmd_compact(runner, "session-1", "/compact")
    assert result.is_command is True
    assert result.text == "manual compact"

    compact.register(ctx)
    assert ctx.commands[0].name == "/compact"
    assert ctx._hooks.has_handlers(HookEvent.PRE_RUN) is True
    assert ctx._hooks.has_handlers(HookEvent.POST_RUN) is True


def test_compact_messages_can_render_zh_cn(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")

    serialized = compact._serialize_messages(
        [{"role": "assistant", "content": [{"type": "tool_use", "name": "search", "input": {"q": "weather"}}]}]
    )

    assert "[调用工具 search" in serialized
    assert compact._do_compact(SimpleNamespace(), SimpleNamespace(messages=[])) == (False, "没有可压缩的内容。")
