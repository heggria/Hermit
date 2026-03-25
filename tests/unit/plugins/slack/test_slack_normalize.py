"""Tests for Slack message normalization."""

from __future__ import annotations

import pytest

from hermit.plugins.builtin.adapters.slack.normalize import (
    SlackMessage,
    normalize_event,
)


def test_normalize_dm_message():
    event = {
        "channel": "D123",
        "ts": "1234567890.123456",
        "user": "U42",
        "text": "hello there",
        "channel_type": "im",
    }
    result = normalize_event(event)
    assert result is not None
    assert result.channel_id == "D123"
    assert result.message_ts == "1234567890.123456"
    assert result.user_id == "U42"
    assert result.text == "hello there"
    assert result.channel_type == "im"
    assert result.thread_ts is None


def test_normalize_channel_message():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "channel message",
        "channel_type": "channel",
    }
    result = normalize_event(event)
    assert result is not None
    assert result.channel_id == "C456"
    assert result.channel_type == "channel"


def test_normalize_thread_message():
    event = {
        "channel": "C456",
        "ts": "111.333",
        "user": "U42",
        "text": "thread reply",
        "channel_type": "channel",
        "thread_ts": "111.222",
    }
    result = normalize_event(event)
    assert result is not None
    assert result.thread_ts == "111.222"


def test_normalize_strips_mentions():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "<@U99BOT> do something",
        "channel_type": "channel",
    }
    result = normalize_event(event)
    assert result is not None
    assert result.text == "do something"


def test_normalize_multiple_mentions():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "<@U99BOT> <@U88OTHER> hello world",
        "channel_type": "channel",
    }
    result = normalize_event(event)
    assert result is not None
    assert result.text == "hello world"


def test_normalize_returns_none_for_bot_message():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "from bot",
        "bot_id": "B123",
    }
    assert normalize_event(event) is None


def test_normalize_returns_none_for_subtype():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "edited",
        "subtype": "message_changed",
    }
    assert normalize_event(event) is None


def test_normalize_returns_none_for_empty_text():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "",
    }
    assert normalize_event(event) is None


def test_normalize_returns_none_for_no_user():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "text": "hello",
    }
    assert normalize_event(event) is None


def test_normalize_returns_none_for_mention_only():
    event = {
        "channel": "C456",
        "ts": "111.222",
        "user": "U42",
        "text": "<@U99BOT>",
        "channel_type": "channel",
    }
    assert normalize_event(event) is None


def test_slack_message_is_frozen():
    msg = SlackMessage(
        channel_id="C1",
        message_ts="1.2",
        user_id="U1",
        text="t",
        channel_type="im",
        thread_ts=None,
    )
    assert msg.channel_id == "C1"
    with pytest.raises(AttributeError):
        msg.channel_id = "C2"  # type: ignore[misc]
