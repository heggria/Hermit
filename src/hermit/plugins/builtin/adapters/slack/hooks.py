"""Slack plugin hooks — POST_RUN and DISPATCH_RESULT handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hermit.plugins.builtin.adapters.slack.adapter import get_active_adapter
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

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
    metadata: dict[str, Any] | None = None,
    **kw: Any,
) -> dict[str, Any] | None:
    """Push agent dispatch results to Slack via proactive messaging."""
    channel_id = (notify or {}).get("slack_channel_id", "")
    if not channel_id:
        return None

    job_id = str((metadata or {}).get("job_id", "") or "")
    _log.info(
        "slack_proactive_delivery_attempt",
        extra={"channel": "slack", "channel_id": channel_id, "job_id": job_id},
    )

    try:
        from hermit.plugins.builtin.adapters.slack.reply import send_message

        adapter = get_active_adapter()
        if adapter is None or adapter.app is None:
            _log.warning("Slack adapter not active, skipping proactive delivery")
            return {
                "channel": "slack",
                "status": "failure",
                "target": channel_id,
                "error": "adapter_not_active",
            }

        display_title = title or source or "Result"
        if success:
            text = f"**{display_title}**\n\n{result_text}"
        else:
            err_msg = error or "Unknown error"
            text = f"**{display_title} (failed)**\n\nError: {err_msg}\n\n{result_text}"

        client = adapter.app.client
        try:
            loop = asyncio.get_running_loop()
            _task = loop.create_task(send_message(client, channel_id, text))  # noqa: RUF006
        except RuntimeError:
            asyncio.run(send_message(client, channel_id, text))

        _log.info(
            "slack_proactive_delivery_success",
            extra={"channel": "slack", "channel_id": channel_id, "job_id": job_id},
        )
        return {
            "channel": "slack",
            "status": "success",
            "target": channel_id,
            "error": None,
        }
    except Exception:
        _log.exception("slack_proactive_delivery_exception")
        return {
            "channel": "slack",
            "status": "failure",
            "target": channel_id,
            "error": "exception",
        }


def _on_post_run(
    *,
    result: Any = None,
    session_id: str = "",
    runner: Any = None,
    **_: Any,
) -> None:
    """Deliver post-run results back to Slack."""
    adapter = get_active_adapter()
    if adapter is None:
        return
    # The main adapter._on_message already sends replies inline,
    # so POST_RUN is only needed for dispatch-triggered runs.


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.POST_RUN, _on_post_run, priority=40)
    ctx.add_hook(HookEvent.DISPATCH_RESULT, _on_dispatch_result, priority=50)
