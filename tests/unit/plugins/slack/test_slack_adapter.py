"""Tests for Slack adapter core logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.plugins.builtin.adapters.slack.adapter import SlackAdapter, register
from hermit.plugins.builtin.adapters.slack.normalize import SlackMessage


def _make_adapter(*, bot_token: str = "xoxb-test", app_token: str = "xapp-test") -> SlackAdapter:
    settings = SimpleNamespace(slack_bot_token=bot_token, slack_app_token=app_token)
    return SlackAdapter(settings=settings)


def test_adapter_init_from_settings():
    adapter = _make_adapter()
    assert adapter._bot_token == "xoxb-test"
    assert adapter._app_token == "xapp-test"
    assert adapter._runner is None
    assert adapter._stopped is False


def test_adapter_init_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMIT_SLACK_BOT_TOKEN", "env-bot")
    monkeypatch.setenv("HERMIT_SLACK_APP_TOKEN", "env-app")
    settings = SimpleNamespace(slack_bot_token=None, slack_app_token=None)
    adapter = SlackAdapter(settings=settings)
    assert adapter._bot_token == "env-bot"
    assert adapter._app_token == "env-app"


def test_adapter_init_no_tokens():
    settings = SimpleNamespace(slack_bot_token=None, slack_app_token=None)
    adapter = SlackAdapter(settings=settings)
    assert adapter._bot_token == ""
    assert adapter._app_token == ""


def test_required_skills():
    adapter = _make_adapter()
    assert adapter.required_skills == ["slack-output-format"]


def test_app_property_initially_none():
    adapter = _make_adapter()
    assert adapter.app is None


def test_build_session_id_dm():
    msg = SlackMessage(
        channel_id="C123",
        message_ts="1234.5678",
        user_id="U42",
        text="hi",
        channel_type="im",
        thread_ts=None,
    )
    sid = SlackAdapter._build_session_id(msg)
    assert sid == "slack:C123"


def test_build_session_id_channel():
    msg = SlackMessage(
        channel_id="C200",
        message_ts="1234.5678",
        user_id="U42",
        text="hi",
        channel_type="channel",
        thread_ts=None,
    )
    sid = SlackAdapter._build_session_id(msg)
    assert sid == "slack:C200:U42"


@pytest.mark.asyncio
async def test_start_raises_without_tokens():
    settings = SimpleNamespace(slack_bot_token=None, slack_app_token=None)
    adapter = SlackAdapter(settings=settings)
    runner = MagicMock()
    with pytest.raises(RuntimeError, match="HERMIT_SLACK_BOT_TOKEN"):
        await adapter.start(runner)


@pytest.mark.asyncio
async def test_stop_clears_state():
    import hermit.plugins.builtin.adapters.slack.adapter as adapter_mod

    adapter = _make_adapter()
    adapter._runner = MagicMock()
    adapter._runner._session_started = []
    mock_task = MagicMock()
    adapter._sweep_task = mock_task

    async def _noop() -> None:
        pass

    mock_handler = MagicMock()
    mock_handler.close_async = MagicMock(return_value=_noop())
    adapter._handler = mock_handler

    adapter_mod._active_adapter = adapter

    await adapter.stop()

    assert adapter._stopped is True
    assert adapter_mod._active_adapter is None
    mock_task.cancel.assert_called_once()
    assert adapter._sweep_task is None
    assert adapter._handler is None


def test_flush_all_sessions():
    adapter = _make_adapter()
    runner = MagicMock()
    runner._session_started = ["s1", "s2"]
    adapter._runner = runner

    adapter._flush_all_sessions()

    assert runner.close_session.call_count == 2


def test_flush_all_sessions_no_runner():
    adapter = _make_adapter()
    adapter._runner = None
    adapter._flush_all_sessions()


def test_flush_all_sessions_handles_exception():
    adapter = _make_adapter()
    runner = MagicMock()
    runner._session_started = ["s1"]
    runner.close_session.side_effect = RuntimeError("boom")
    adapter._runner = runner
    adapter._flush_all_sessions()


def test_dedup_logic():
    adapter = _make_adapter()
    adapter._DEDUP_MAX = 3

    for i in range(5):
        key = f"ch:{i}"
        adapter._seen_msgs[key] = True
        while len(adapter._seen_msgs) > adapter._DEDUP_MAX:
            adapter._seen_msgs.popitem(last=False)

    assert len(adapter._seen_msgs) == 3
    assert "ch:0" not in adapter._seen_msgs
    assert "ch:4" in adapter._seen_msgs


def test_register_adds_adapter():
    ctx = MagicMock()
    register(ctx)
    ctx.add_adapter.assert_called_once()
    spec = ctx.add_adapter.call_args[0][0]
    assert spec.name == "slack"
    assert spec.factory is SlackAdapter
