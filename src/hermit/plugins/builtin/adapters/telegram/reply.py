"""Telegram reply helpers — MarkdownV2 formatting and smart message splitting."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot

log = logging.getLogger(__name__)

# Telegram message length limit
_MAX_MESSAGE_LENGTH = 4096

# MarkdownV2 special characters that must be escaped outside of code spans.
_MDV2_SPECIAL_RE = re.compile(r"([_*\[\]()~\\`>#\+\-=|{}.!])")

# Markdown patterns
_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)")
_INLINE_CODE_RE = re.compile(r"(`[^`]+`)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def format_telegram_md(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2 format.

    Uses a placeholder approach to safely handle code blocks, bold, italic,
    strikethrough, and links without double-escaping or mis-converting.
    """
    # --- Phase 1: Extract code blocks and inline code into placeholders ---
    code_blocks: list[str] = []

    def _hold_code_block(m: re.Match[str]) -> str:
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f"\x00CODEBLOCK{idx}\x00"

    result = _CODE_BLOCK_RE.sub(_hold_code_block, text)

    inline_codes: list[str] = []

    def _hold_inline_code(m: re.Match[str]) -> str:
        idx = len(inline_codes)
        inline_codes.append(m.group(0))
        return f"\x00INLINECODE{idx}\x00"

    result = _INLINE_CODE_RE.sub(_hold_inline_code, result)

    # --- Phase 2: Convert Markdown formatting to placeholders ---
    bold_spans: list[str] = []

    def _hold_bold(m: re.Match[str]) -> str:
        idx = len(bold_spans)
        bold_spans.append(m.group(1))
        return f"\x00BOLD{idx}\x00"

    result = _MD_BOLD_RE.sub(_hold_bold, result)

    italic_spans: list[str] = []

    def _hold_italic(m: re.Match[str]) -> str:
        idx = len(italic_spans)
        italic_spans.append(m.group(1))
        return f"\x00ITALIC{idx}\x00"

    result = _MD_ITALIC_RE.sub(_hold_italic, result)

    strike_spans: list[str] = []

    def _hold_strike(m: re.Match[str]) -> str:
        idx = len(strike_spans)
        strike_spans.append(m.group(1))
        return f"\x00STRIKE{idx}\x00"

    result = _MD_STRIKE_RE.sub(_hold_strike, result)

    link_spans: list[tuple[str, str]] = []

    def _hold_link(m: re.Match[str]) -> str:
        idx = len(link_spans)
        link_spans.append((m.group(1), m.group(2)))
        return f"\x00LINK{idx}\x00"

    result = _MD_LINK_RE.sub(_hold_link, result)

    # --- Phase 3: Escape remaining special characters ---
    result = _MDV2_SPECIAL_RE.sub(r"\\\1", result)

    # --- Phase 4: Restore formatting placeholders with MarkdownV2 syntax ---
    for i, content in enumerate(bold_spans):
        escaped = _MDV2_SPECIAL_RE.sub(r"\\\1", content)
        result = result.replace(f"\x00BOLD{i}\x00", f"*{escaped}*")

    for i, content in enumerate(italic_spans):
        escaped = _MDV2_SPECIAL_RE.sub(r"\\\1", content)
        result = result.replace(f"\x00ITALIC{i}\x00", f"_{escaped}_")

    for i, content in enumerate(strike_spans):
        escaped = _MDV2_SPECIAL_RE.sub(r"\\\1", content)
        result = result.replace(f"\x00STRIKE{i}\x00", f"~{escaped}~")

    for i, (link_text, link_url) in enumerate(link_spans):
        esc_text = _MDV2_SPECIAL_RE.sub(r"\\\1", link_text)
        esc_url = link_url.replace("\\", "\\\\").replace(")", "\\)")
        result = result.replace(f"\x00LINK{i}\x00", f"[{esc_text}]({esc_url})")

    # --- Phase 5: Restore code (only escape ` and \ inside) ---
    for i, block in enumerate(code_blocks):
        # Keep code blocks as-is — MarkdownV2 handles ``` natively
        # Only escape inner ` and \ that aren't part of the fence
        inner = block[3:-3]
        escaped_inner = inner.replace("\\", "\\\\").replace("`", "\\`")
        result = result.replace(f"\x00CODEBLOCK{i}\x00", f"```{escaped_inner}```")

    for i, code in enumerate(inline_codes):
        # Inline code in MarkdownV2 doesn't need inner escaping
        result = result.replace(f"\x00INLINECODE{i}\x00", code)

    return result


def _split_text(text: str, limit: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    """Split long text into chunks that fit within the Telegram message limit.

    Tries to split at newline boundaries first, then falls back to hard splits.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Try to split at a newline
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            # Fall back to hard split
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


async def smart_reply(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
) -> None:
    """Send a reply, splitting into multiple messages if needed."""
    chunks = _split_text(text)
    for i, chunk in enumerate(chunks):
        formatted = format_telegram_md(chunk)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=formatted,
                parse_mode="MarkdownV2",
                reply_to_message_id=reply_to_message_id if i == 0 else None,
            )
        except Exception:
            # Fallback: send as plain text if MarkdownV2 parsing fails
            log.warning("MarkdownV2 send failed, falling back to plain text")
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                reply_to_message_id=reply_to_message_id if i == 0 else None,
            )


async def send_message(bot: Bot, chat_id: int, text: str) -> None:
    """Send a proactive message (no reply-to)."""
    chunks = _split_text(text)
    for chunk in chunks:
        formatted = format_telegram_md(chunk)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=formatted,
                parse_mode="MarkdownV2",
            )
        except Exception:
            log.warning("MarkdownV2 send failed, falling back to plain text")
            await bot.send_message(chat_id=chat_id, text=chunk)
