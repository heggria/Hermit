"""Tests for Telegram reply helpers."""

from __future__ import annotations

import pytest

from hermit.plugins.builtin.adapters.telegram.reply import (
    _split_text,
    format_telegram_md,
    send_message,
    smart_reply,
)

# --- format_telegram_md ---


def test_format_telegram_md_bold():
    assert format_telegram_md("**hello**") == "*hello*"


def test_format_telegram_md_italic():
    assert format_telegram_md("*hello*") == "_hello_"


def test_format_telegram_md_strikethrough():
    assert format_telegram_md("~~text~~") == "~text~"


def test_format_telegram_md_code_preserved():
    result = format_telegram_md("before `code_here` after")
    assert "`code_here`" in result
    # "before" and "after" should have no unescaped special chars issue
    assert "before" in result


def test_format_telegram_md_code_block_preserved():
    result = format_telegram_md("text\n```\ncode\n```\nmore")
    assert "```" in result
    assert "code" in result


def test_format_telegram_md_special_chars_escaped():
    result = format_telegram_md("hello.world")
    assert result == r"hello\.world"


def test_format_telegram_md_mixed():
    result = format_telegram_md("**bold** and *italic* and `code`")
    assert "*bold*" in result
    assert "_italic_" in result
    assert "`code`" in result


def test_format_telegram_md_link():
    result = format_telegram_md("[click](https://example.com)")
    assert "[click](https://example.com)" in result or "[click]" in result


# --- _split_text ---


def test_split_text_short():
    assert _split_text("short text") == ["short text"]


def test_split_text_at_newline():
    text = "line1\nline2\nline3"
    chunks = _split_text(text, limit=12)
    assert len(chunks) >= 2
    assert "line1" in chunks[0]


def test_split_text_hard_split():
    text = "a" * 100
    chunks = _split_text(text, limit=30)
    assert len(chunks) >= 4
    total = "".join(chunks)
    assert len(total) == 100


def test_split_text_exact_limit():
    text = "a" * 4096
    assert _split_text(text) == [text]


def test_split_text_one_over():
    text = "a" * 4097
    chunks = _split_text(text)
    assert len(chunks) == 2


# --- smart_reply ---


@pytest.mark.asyncio
async def test_smart_reply_short():
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    await smart_reply(bot, 123, "hello", reply_to_message_id=1)
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 123
    assert call_kwargs["parse_mode"] == "MarkdownV2"
    assert call_kwargs["reply_to_message_id"] == 1


@pytest.mark.asyncio
async def test_smart_reply_splits():
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    text = "a" * 5000
    await smart_reply(bot, 123, text, reply_to_message_id=1)
    assert bot.send_message.call_count == 2
    first_call = bot.send_message.call_args_list[0]
    assert first_call.kwargs["reply_to_message_id"] == 1
    second_call = bot.send_message.call_args_list[1]
    assert second_call.kwargs["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_smart_reply_fallback_on_bad_request():
    """MarkdownV2 failure should fall back to plain text send."""
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    # First call (with parse_mode) raises, second call (plain) succeeds
    bot.send_message.side_effect = [Exception("Bad Request: can't parse"), None]
    await smart_reply(bot, 123, "hello", reply_to_message_id=1)
    assert bot.send_message.call_count == 2
    # Second call should NOT have parse_mode
    fallback_kwargs = bot.send_message.call_args_list[1].kwargs
    assert "parse_mode" not in fallback_kwargs
    assert fallback_kwargs["text"] == "hello"


# --- send_message ---


@pytest.mark.asyncio
async def test_send_message():
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    await send_message(bot, 123, "hello")
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 123
    assert call_kwargs["parse_mode"] == "MarkdownV2"


@pytest.mark.asyncio
async def test_send_message_fallback():
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    bot.send_message.side_effect = [Exception("parse error"), None]
    await send_message(bot, 123, "hello")
    assert bot.send_message.call_count == 2
    fallback_kwargs = bot.send_message.call_args_list[1].kwargs
    assert "parse_mode" not in fallback_kwargs
