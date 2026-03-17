"""Tests for Slack reply helpers."""

from __future__ import annotations

import pytest

from hermit.plugins.builtin.adapters.slack.reply import (
    _split_text,
    format_mrkdwn,
    send_message,
    smart_reply,
)

# --- format_mrkdwn ---


def test_format_mrkdwn_bold():
    assert format_mrkdwn("**hello**") == "*hello*"


def test_format_mrkdwn_italic():
    assert format_mrkdwn("*hello*") == "_hello_"


def test_format_mrkdwn_bold_and_italic():
    result = format_mrkdwn("**bold** and *italic*")
    assert "*bold*" in result
    assert "_italic_" in result


def test_format_mrkdwn_no_italic_from_bold():
    """**text** should become *text*, not _text_."""
    result = format_mrkdwn("**text**")
    assert result == "*text*"
    assert "_text_" not in result


def test_format_mrkdwn_heading_not_italicized():
    """# Title should become *Title*, not _Title_."""
    result = format_mrkdwn("# Title")
    assert "*Title*" in result
    assert "_Title_" not in result


def test_format_mrkdwn_strikethrough():
    assert format_mrkdwn("~~deleted~~") == "~deleted~"


def test_format_mrkdwn_heading():
    result = format_mrkdwn("# Title\nsome text")
    assert "*Title*" in result
    assert "some text" in result


def test_format_mrkdwn_link():
    result = format_mrkdwn("[click here](https://example.com)")
    assert "<https://example.com|click here>" in result


def test_format_mrkdwn_combined():
    text = "## Section\n**bold** and ~~strike~~"
    result = format_mrkdwn(text)
    assert "*Section*" in result
    assert "*bold*" in result
    assert "~strike~" in result


def test_format_mrkdwn_no_change_plain():
    assert format_mrkdwn("plain text") == "plain text"


# --- _split_text ---


def test_split_text_short():
    assert _split_text("short") == ["short"]


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
    text = "a" * 3000
    assert _split_text(text) == [text]


def test_split_text_one_over():
    text = "a" * 3001
    chunks = _split_text(text)
    assert len(chunks) == 2


# --- smart_reply & send_message ---


@pytest.mark.asyncio
async def test_smart_reply():
    from unittest.mock import AsyncMock

    client = AsyncMock()
    await smart_reply(client, "C123", "**hello**", thread_ts="1.2")
    client.chat_postMessage.assert_called_once_with(channel="C123", text="*hello*", thread_ts="1.2")


@pytest.mark.asyncio
async def test_smart_reply_splits():
    from unittest.mock import AsyncMock

    client = AsyncMock()
    text = "a" * 4000
    await smart_reply(client, "C123", text, thread_ts="1.2")
    assert client.chat_postMessage.call_count == 2


@pytest.mark.asyncio
async def test_send_message():
    from unittest.mock import AsyncMock

    client = AsyncMock()
    await send_message(client, "C123", "**hello**")
    client.chat_postMessage.assert_called_once_with(channel="C123", text="*hello*")
