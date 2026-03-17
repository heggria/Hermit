"""Normalize Slack event payloads into a simple dataclass."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Pattern for <@U12345> user mentions
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


@dataclass(frozen=True, slots=True)
class SlackMessage:
    """Normalized representation of an incoming Slack message."""

    channel_id: str
    message_ts: str
    user_id: str
    text: str
    channel_type: str  # "im", "channel", "group", "mpim"
    thread_ts: str | None


def normalize_event(event: dict[str, Any]) -> SlackMessage | None:
    """Extract a normalized message from a Slack event dict.

    Returns ``None`` when the event is not a usable user message
    (e.g. bot messages, message_changed subtypes, etc.).
    """
    # Skip bot messages and subtypes (edits, deletes, etc.)
    if event.get("bot_id") or event.get("subtype"):
        return None

    text = event.get("text", "")
    if not text or not text.strip():
        return None

    user_id = event.get("user", "")
    if not user_id:
        return None

    # Strip @mentions from the text for cleaner processing
    cleaned_text = _MENTION_RE.sub("", text).strip()
    if not cleaned_text:
        return None

    return SlackMessage(
        channel_id=event.get("channel", ""),
        message_ts=event.get("ts", ""),
        user_id=user_id,
        text=cleaned_text,
        channel_type=event.get("channel_type", ""),
        thread_ts=event.get("thread_ts"),
    )
