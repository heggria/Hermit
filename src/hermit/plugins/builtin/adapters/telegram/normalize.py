"""Normalize Telegram Update objects into a simple dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    """Normalized representation of an incoming Telegram message."""

    chat_id: int
    message_id: int
    sender_id: int
    text: str
    chat_type: str  # "private", "group", "supergroup", "channel"
    username: str


def normalize_update(update: Update) -> TelegramMessage | None:
    """Extract a normalized message from a Telegram Update.

    Returns ``None`` when the update does not contain a usable text message.
    """
    msg = update.message or update.edited_message
    if msg is None or msg.text is None:
        return None

    user = msg.from_user
    return TelegramMessage(
        chat_id=msg.chat_id,
        message_id=msg.message_id,
        sender_id=user.id if user else 0,
        text=msg.text.strip(),
        chat_type=msg.chat.type,
        username=user.username or "" if user else "",
    )
