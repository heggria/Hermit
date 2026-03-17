"""Slack reply helpers — mrkdwn formatting and smart message splitting."""

from __future__ import annotations

import re
from typing import Any

# Slack block text limit
_MAX_BLOCK_TEXT_LENGTH = 3000

# Standard Markdown → Slack mrkdwn conversions
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def format_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format."""
    # Use placeholders for bold/heading to avoid italic regex matching their *
    _placeholders: list[str] = []

    def _hold_bold(m: re.Match[str]) -> str:
        idx = len(_placeholders)
        _placeholders.append(m.group(1))
        return f"\x00BOLD{idx}\x00"

    # Step 1: Bold **text** → placeholder
    result = _MD_BOLD_RE.sub(_hold_bold, text)
    # Step 2: Headings # Title → placeholder (same bold style in Slack)
    result = _MD_HEADING_RE.sub(_hold_bold, result)
    # Step 3: Italic *text* → _text_ (safe now — bold already replaced)
    result = _MD_ITALIC_RE.sub(r"_\1_", result)
    # Step 4: Strikethrough ~~text~~ → ~text~
    result = _MD_STRIKE_RE.sub(r"~\1~", result)
    # Step 5: Links [text](url) → <url|text>
    result = _MD_LINK_RE.sub(r"<\2|\1>", result)
    # Step 6: Restore bold placeholders → *text*
    for i, content in enumerate(_placeholders):
        result = result.replace(f"\x00BOLD{i}\x00", f"*{content}*")
    return result


def _split_text(text: str, limit: int = _MAX_BLOCK_TEXT_LENGTH) -> list[str]:
    """Split text into chunks that fit within the Slack block limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


async def smart_reply(
    client: Any,
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> None:
    """Send a reply to Slack, splitting into multiple messages if needed."""
    formatted = format_mrkdwn(text)
    chunks = _split_text(formatted)
    for chunk in chunks:
        await client.chat_postMessage(
            channel=channel,
            text=chunk,
            thread_ts=thread_ts,
        )


async def send_message(client: Any, channel: str, text: str) -> None:
    """Send a proactive message to a Slack channel."""
    formatted = format_mrkdwn(text)
    chunks = _split_text(formatted)
    for chunk in chunks:
        await client.chat_postMessage(channel=channel, text=chunk)
