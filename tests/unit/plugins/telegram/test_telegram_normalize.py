"""Tests for Telegram message normalization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.plugins.builtin.adapters.telegram.normalize import (
    TelegramMessage,
    normalize_update,
)


def _make_update(
    *,
    chat_id: int = 123,
    message_id: int = 1,
    sender_id: int = 42,
    text: str | None = "hello",
    chat_type: str = "private",
    username: str = "testuser",
    edited: bool = False,
) -> MagicMock:
    """Build a minimal Update-like mock."""
    user = SimpleNamespace(id=sender_id, username=username)
    chat = SimpleNamespace(type=chat_type)
    msg = SimpleNamespace(
        chat_id=chat_id,
        message_id=message_id,
        from_user=user,
        text=text,
        chat=chat,
    )
    update = MagicMock()
    if edited:
        update.message = None
        update.edited_message = msg
    else:
        update.message = msg
        update.edited_message = None
    return update


def test_normalize_private_message():
    update = _make_update(text="hi there")
    result = normalize_update(update)
    assert result is not None
    assert result.chat_id == 123
    assert result.message_id == 1
    assert result.sender_id == 42
    assert result.text == "hi there"
    assert result.chat_type == "private"
    assert result.username == "testuser"


def test_normalize_group_message():
    update = _make_update(chat_type="supergroup", text="  hello world  ")
    result = normalize_update(update)
    assert result is not None
    assert result.chat_type == "supergroup"
    assert result.text == "hello world"


def test_normalize_edited_message():
    update = _make_update(text="edited text", edited=True)
    result = normalize_update(update)
    assert result is not None
    assert result.text == "edited text"


def test_normalize_returns_none_for_no_message():
    update = MagicMock()
    update.message = None
    update.edited_message = None
    assert normalize_update(update) is None


def test_normalize_returns_none_for_no_text():
    update = _make_update(text=None)
    assert normalize_update(update) is None


def test_normalize_no_user():
    update = _make_update()
    update.message.from_user = None
    result = normalize_update(update)
    assert result is not None
    assert result.sender_id == 0
    assert result.username == ""


def test_telegram_message_is_frozen():
    msg = TelegramMessage(
        chat_id=1, message_id=2, sender_id=3, text="t", chat_type="private", username="u"
    )
    assert msg.chat_id == 1
    try:
        msg.chat_id = 99  # type: ignore[misc]
        raise AssertionError("Should have raised")
    except AttributeError:
        pass
