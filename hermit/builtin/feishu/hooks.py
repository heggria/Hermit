"""Feishu plugin hooks — registers feishu_react, all Feishu API tools, and DISPATCH_RESULT handler."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.builtin.feishu._client import build_lark_client
from hermit.builtin.feishu.adapter import get_active_adapter
from hermit.builtin.feishu.reaction import add_reaction, resolve_emoji_type
from hermit.builtin.feishu.tools import register_tools
from hermit.core.tools import ToolSpec
from hermit.plugin.base import HookEvent, PluginContext

_log = structlog.get_logger(__name__)


def _log_exception_with_compat(event: str, **kwargs: Any) -> None:
    """Log with structured kwargs, but tolerate simplified test doubles."""
    try:
        _log.exception(event, **kwargs)
    except TypeError:
        chat_id = str(kwargs.get("chat_id", "") or "")
        _log.exception(event, chat_id)


def _on_dispatch_result(
    *,
    source: str = "",
    title: str = "",
    result_text: str = "",
    success: bool = True,
    error: str | None = None,
    notify: dict[str, Any] | None = None,
    settings: Any = None,
    metadata: dict[str, Any] | None = None,
    **kw: Any,
) -> dict[str, Any] | None:
    """Push agent dispatch results to Feishu via proactive messaging."""
    chat_id = (notify or {}).get("feishu_chat_id", "")
    if not chat_id:
        return None
    mode = str((notify or {}).get("delivery_mode", "") or "new_message")
    job_id = str((metadata or {}).get("job_id", "") or "")
    _log.info(
        "feishu_proactive_delivery_attempt",
        channel="feishu",
        mode=mode,
        chat_id=chat_id,
        job_id=job_id,
    )

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

        message_id: str | None = None
        if _should_use_card(text):
            card = build_result_card(text)
            message_id = send_card(client, chat_id, card)
        else:
            message_id = send_text_message(client, chat_id, text)
        if not message_id:
            delivery_error = "message.create returned no message_id"
            _log.error(
                "feishu_proactive_delivery_failure",
                channel="feishu",
                mode=mode,
                chat_id=chat_id,
                job_id=job_id,
                error=delivery_error,
            )
            return {
                "channel": "feishu",
                "status": "failure",
                "mode": mode,
                "target": chat_id,
                "message_id": None,
                "error": delivery_error,
            }
        _log.info(
            "feishu_proactive_delivery_success",
            channel="feishu",
            mode=mode,
            chat_id=chat_id,
            job_id=job_id,
            message_id=message_id,
        )
        return {
            "channel": "feishu",
            "status": "success",
            "mode": mode,
            "target": chat_id,
            "message_id": message_id,
            "error": None,
        }
    except Exception:
        _log_exception_with_compat(
            "feishu_proactive_delivery_exception",
            channel="feishu",
            mode=mode,
            chat_id=chat_id,
            job_id=job_id,
        )
        return {
            "channel": "feishu",
            "status": "failure",
            "mode": mode,
            "target": chat_id,
            "message_id": None,
            "error": "exception",
        }


def _on_post_run(
    *,
    result: Any = None,
    session_id: str = "",
    runner: Any = None,
    **_: Any,
) -> None:
    adapter = get_active_adapter()
    if adapter is None:
        return
    try:
        adapter._handle_post_run_result(result, session_id=session_id, runner=runner)
    except Exception:
        _log.exception("Failed to deliver Feishu post-run result for session_id=%s", session_id)


def register(ctx: PluginContext) -> None:
    ctx.add_tool(_build_react_tool(ctx.settings))
    register_tools(ctx)
    ctx.add_hook(HookEvent.POST_RUN, _on_post_run, priority=40)
    ctx.add_hook(HookEvent.DISPATCH_RESULT, _on_dispatch_result, priority=50)


def _build_react_tool(settings: Any = None) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        message_id = str(payload.get("message_id", "")).strip()
        emoji_type_raw = str(payload.get("emoji_type", "")).strip()
        emoji_alias = str(payload.get("emoji", "")).strip()
        if not message_id:
            return {"success": False, "error": "message_id is required"}
        if not emoji_type_raw and not emoji_alias:
            return {"success": False, "error": "emoji is required"}

        emoji_type = emoji_type_raw or resolve_emoji_type(emoji_alias)
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
