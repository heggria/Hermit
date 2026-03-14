"""Feishu message emoji reaction utilities."""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_EMOJI_ALIASES = {
    "get": "Get",
    "ok": "OK",
    "thinking": "THINKING",
    "thumbsup": "THUMBSUP",
    "thumbs_up": "THUMBSUP",
    "+1": "THUMBSUP",
    "fire": "Fire",
}


def add_reaction(client: Any, message_id: str, emoji_type: str) -> bool:
    """Add an emoji reaction to a Feishu message. Returns True on success."""
    if not client or not message_id or not emoji_type:
        return False
    try:
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
        )
        from lark_oapi.api.im.v1.model.emoji import Emoji

        emoji = Emoji.builder().emoji_type(emoji_type).build()
        body = CreateMessageReactionRequestBody.builder().reaction_type(emoji).build()
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = client.im.v1.message_reaction.create(request)
        if not response.success():
            log.debug(
                "reaction_failed msg=%s emoji=%s code=%s msg=%s",
                message_id,
                emoji_type,
                response.code,
                response.msg,
            )
            return False
        log.debug("reaction_ok msg=%s emoji=%s", message_id, emoji_type)
        return True
    except Exception as exc:
        log.debug("reaction_error msg=%s emoji=%s err=%s", message_id, emoji_type, exc)
        return False


def resolve_emoji_type(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return _EMOJI_ALIASES.get(normalized.lower(), normalized)


def _reaction_enabled(settings: Any = None) -> bool:
    if settings is not None and getattr(settings, "feishu_reaction_enabled", None) is not None:
        return bool(getattr(settings, "feishu_reaction_enabled"))
    return os.environ.get("HERMIT_FEISHU_REACTION_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _reaction_value(name: str, settings: Any = None) -> str:
    if settings is not None:
        configured = getattr(settings, name, None)
        if configured is not None:
            return str(configured).strip()
    env_name = f"HERMIT_{name.upper()}"
    return str(os.environ.get(env_name, "")).strip()


def send_ack(client: Any, message_id: str, settings: Any = None) -> bool:
    if not _reaction_enabled(settings):
        return False
    emoji_type = _reaction_value("feishu_reaction_ack", settings)
    if not emoji_type:
        return False
    return add_reaction(client, message_id, emoji_type)


def send_done(client: Any, message_id: str, settings: Any = None) -> bool:
    if not _reaction_enabled(settings):
        return False
    emoji_type = _reaction_value("feishu_reaction_done", settings)
    if not emoji_type:
        return False
    return add_reaction(client, message_id, emoji_type)
