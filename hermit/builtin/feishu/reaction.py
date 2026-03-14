"""Feishu message emoji reaction utilities."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


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
