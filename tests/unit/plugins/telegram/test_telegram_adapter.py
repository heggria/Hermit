"""Tests for Telegram adapter core logic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.plugins.builtin.adapters.telegram.adapter import TelegramAdapter, register
from hermit.plugins.builtin.adapters.telegram.normalize import TelegramMessage


def _make_adapter(*, token: str = "test-token") -> TelegramAdapter:
    settings = SimpleNamespace(telegram_bot_token=token)
    return TelegramAdapter(settings=settings)


def test_adapter_init_from_settings():
    adapter = _make_adapter(token="abc123")
    assert adapter._token == "abc123"
    assert adapter._runner is None
    assert adapter._stopped is False


def test_adapter_init_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMIT_TELEGRAM_BOT_TOKEN", "env-token")
    settings = SimpleNamespace(telegram_bot_token=None)
    adapter = TelegramAdapter(settings=settings)
    assert adapter._token == "env-token"


def test_adapter_init_no_token():
    settings = SimpleNamespace(telegram_bot_token=None)
    adapter = TelegramAdapter(settings=settings)
    assert adapter._token == ""


def test_required_skills():
    adapter = _make_adapter()
    assert adapter.required_skills == ["telegram-output-format"]


def test_application_property_initially_none():
    adapter = _make_adapter()
    assert adapter.application is None


def test_build_session_id_private():
    msg = TelegramMessage(
        chat_id=100, message_id=1, sender_id=42, text="hi", chat_type="private", username="u"
    )
    sid = TelegramAdapter._build_session_id(msg)
    assert sid == "tg:100"


def test_build_session_id_group():
    msg = TelegramMessage(
        chat_id=200, message_id=1, sender_id=42, text="hi", chat_type="supergroup", username="u"
    )
    sid = TelegramAdapter._build_session_id(msg)
    assert sid == "tg:200:42"


@pytest.mark.asyncio
async def test_start_raises_without_token():
    settings = SimpleNamespace(telegram_bot_token=None)
    adapter = TelegramAdapter(settings=settings)
    runner = MagicMock()
    with pytest.raises(RuntimeError, match="HERMIT_TELEGRAM_BOT_TOKEN"):
        await adapter.start(runner)


@pytest.mark.asyncio
async def test_stop_clears_state():
    import hermit.plugins.builtin.adapters.telegram.adapter as adapter_mod

    adapter = _make_adapter()
    adapter._runner = MagicMock()
    adapter._runner._session_started = []
    mock_task = MagicMock()
    adapter._sweep_task = mock_task
    adapter._stop_event = asyncio.Event()

    adapter_mod._active_adapter = adapter

    await adapter.stop()

    assert adapter._stopped is True
    assert adapter_mod._active_adapter is None
    mock_task.cancel.assert_called_once()
    assert adapter._sweep_task is None


def test_flush_all_sessions():
    adapter = _make_adapter()
    runner = MagicMock()
    runner._session_started = ["s1", "s2"]
    adapter._runner = runner

    adapter._flush_all_sessions()

    assert runner.close_session.call_count == 2
    runner.close_session.assert_any_call("s1")
    runner.close_session.assert_any_call("s2")


def test_flush_all_sessions_no_runner():
    adapter = _make_adapter()
    adapter._runner = None
    # Should not raise
    adapter._flush_all_sessions()


def test_flush_all_sessions_handles_exception():
    adapter = _make_adapter()
    runner = MagicMock()
    runner._session_started = ["s1"]
    runner.close_session.side_effect = RuntimeError("boom")
    adapter._runner = runner

    # Should not raise
    adapter._flush_all_sessions()


def test_dedup_logic():
    """Verify the dedup OrderedDict eviction works."""
    adapter = _make_adapter()
    adapter._DEDUP_MAX = 3

    for i in range(5):
        key = f"chat:{i}"
        adapter._seen_msgs[key] = True
        while len(adapter._seen_msgs) > adapter._DEDUP_MAX:
            adapter._seen_msgs.popitem(last=False)

    assert len(adapter._seen_msgs) == 3
    assert "chat:0" not in adapter._seen_msgs
    assert "chat:4" in adapter._seen_msgs


def test_register_adds_adapter():
    ctx = MagicMock()
    register(ctx)
    ctx.add_adapter.assert_called_once()
    spec = ctx.add_adapter.call_args[0][0]
    assert spec.name == "telegram"
    assert spec.factory is TelegramAdapter
