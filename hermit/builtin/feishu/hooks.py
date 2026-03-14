"""Feishu plugin hooks — registers feishu_react, all Feishu API tools, and DISPATCH_RESULT handler."""
from __future__ import annotations

import logging
from typing import Any

from hermit.builtin.feishu._client import build_lark_client
from hermit.builtin.feishu.reaction import add_reaction
from hermit.builtin.feishu.tools import register_tools
from hermit.core.tools import ToolSpec
from hermit.plugin.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)


def _on_dispatch_result(
    *,
    source: str = "",
    title: str = "",
    result_text: str = "",
    success: bool = True,
    error: str | None = None,
    notify: dict[str, Any] | None = None,
    settings: Any = None,
    **kw: Any,
) -> None:
    """Push agent dispatch results to Feishu via proactive messaging."""
    chat_id = (notify or {}).get("feishu_chat_id", "")
    if not chat_id:
        return

    try:
        from hermit.builtin.feishu.reply import (
            _should_use_card,
            build_result_card,
            send_card,
            send_text_message,
        )

        client = build_lark_client(settings) if settings is not None else build_lark_client()
        display_title = title or source or "Result"
        if success:
            text = f"# {display_title}\n\n{result_text}"
        else:
            err_msg = error or "Unknown error"
            text = f"# {display_title} (failed)\n\n**Error:** {err_msg}\n\n{result_text}"

        if _should_use_card(text):
            card = build_result_card(text)
            send_card(client, chat_id, card)
        else:
            send_text_message(client, chat_id, text)
    except Exception:
        _log.exception("Failed to send dispatch result to Feishu chat_id=%s", chat_id)


def register(ctx: PluginContext) -> None:
    ctx.add_tool(_build_react_tool(ctx.settings))
    register_tools(ctx)
    ctx.add_hook(HookEvent.DISPATCH_RESULT, _on_dispatch_result, priority=50)


def _build_react_tool(settings: Any = None) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        message_id = str(payload.get("message_id", "")).strip()
        emoji_raw = str(payload.get("emoji_type", "") or payload.get("emoji", "")).strip()
        if not message_id:
            return {"success": False, "error": "message_id is required"}
        if not emoji_raw:
            return {"success": False, "error": "emoji_type is required"}

        emoji_type = emoji_raw
        try:
            client = build_lark_client(settings) if settings is not None else build_lark_client()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        success = add_reaction(client, message_id, emoji_type)
        return {"success": success, "emoji_type": emoji_type, "message_id": message_id}

    return ToolSpec(
        name="feishu_react",
        description=(
            "Add an emoji reaction to a Feishu message. "
            "Use this to make the bot feel more human — react to the user's message "
            "when there's a clear emotional signal (celebration, agreement, surprise, etc.). "
            "Pass the native Feishu emoji_type from the official docs, for example "
            "'Get', 'THUMBSUP', 'OK', 'THINKING', or 'Fire'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": (
                        "The Feishu message_id to react to. "
                        "Found in <feishu_msg_id>...</feishu_msg_id> at the top of the user message."
                    ),
                },
                "emoji_type": {
                    "type": "string",
                    "description": (
                        "The native Feishu emoji_type to react with, such as "
                        "'Get', 'THUMBSUP', 'OK', 'THINKING', or 'Fire'."
                    ),
                },
                "emoji": {
                    "type": "string",
                    "description": (
                        "Deprecated compatibility field. Prefer 'emoji_type' and pass a native "
                        "Feishu emoji_type value."
                    ),
                },
            },
            "required": ["message_id"],
        },
        handler=handler,
        # Internal UX affordance: this is an ephemeral reaction, not a durable
        # external mutation that should block the main reply behind approval.
        action_class="ephemeral_ui_mutation",
        risk_hint="low",
        requires_receipt=False,
    )
