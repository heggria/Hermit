"""Slack adapter plugin: bridges Slack messaging to Hermit AgentRunner via Socket Mode."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from hermit.plugins.builtin.adapters.slack.normalize import SlackMessage, normalize_event
from hermit.plugins.builtin.adapters.slack.reply import smart_reply
from hermit.runtime.capability.contracts.base import AdapterSpec

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes

_active_adapter: SlackAdapter | None = None


def get_active_adapter() -> SlackAdapter | None:
    return _active_adapter


class SlackAdapter:
    """Connects to Slack via Socket Mode using slack-bolt."""

    _DEDUP_MAX = 256

    @property
    def required_skills(self) -> list[str]:
        return ["slack-output-format"]

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings
        self._bot_token = str(
            getattr(settings, "slack_bot_token", None)
            or os.environ.get("HERMIT_SLACK_BOT_TOKEN", "")
        )
        self._app_token = str(
            getattr(settings, "slack_app_token", None)
            or os.environ.get("HERMIT_SLACK_APP_TOKEN", "")
        )
        self._runner: AgentRunner | None = None
        self._app: Any = None
        self._handler: Any = None
        self._seen_msgs: OrderedDict[str, bool] = OrderedDict()
        self._stopped = False
        self._sweep_task: asyncio.Task[None] | None = None
        self._bot_user_id: str | None = None

    @property
    def app(self) -> Any:
        """Public accessor for the underlying Slack AsyncApp instance."""
        return self._app

    async def start(self, runner: AgentRunner) -> None:  # pragma: no cover
        if not self._bot_token or not self._app_token:
            raise RuntimeError("Set HERMIT_SLACK_BOT_TOKEN and HERMIT_SLACK_APP_TOKEN.")

        global _active_adapter
        self._runner = runner
        _active_adapter = self
        self._stopped = False

        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp

        self._app = AsyncApp(token=self._bot_token)

        # Resolve bot's own user ID so we can ignore self-messages.
        # Fail hard — without this we cannot filter bot's own messages and risk loops.
        auth_result = await self._app.client.auth_test()
        self._bot_user_id = auth_result.get("user_id")
        if not self._bot_user_id:
            raise RuntimeError(
                "Slack auth.test succeeded but returned no user_id — cannot filter self-messages"
            )

        @self._app.event("message")
        async def handle_message(event: dict[str, Any], say: Any) -> None:
            await self._on_message(event, say)

        @self._app.event("app_mention")
        async def handle_mention(event: dict[str, Any], say: Any) -> None:
            await self._on_message(event, say)

        # Retain references so pyright does not treat decorated handlers as unused.
        _ = handle_message, handle_mention

        log.info("Starting Slack adapter (Socket Mode)...")
        self._sweep_task = asyncio.create_task(self._sweep_idle_sessions())

        self._handler = AsyncSocketModeHandler(self._app, self._app_token)
        await self._handler.start_async()

    async def stop(self) -> None:
        global _active_adapter
        self._stopped = True
        _active_adapter = None

        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None

        if self._handler is not None:
            await self._handler.close_async()
            self._handler = None

        # Flush all active sessions so SESSION_END fires for each.
        self._flush_all_sessions()

        log.info("Slack adapter stopped.")

    def _flush_all_sessions(self) -> None:
        """Close every active session on shutdown so SESSION_END fires for each."""
        if self._runner is None:
            return
        for sid in list(getattr(self._runner, "_session_started", [])):
            try:
                self._runner.close_session(sid)
            except Exception:
                log.exception("flush close_session error for %s", sid)

    async def _on_message(self, event: dict[str, Any], say: Any) -> None:  # pragma: no cover
        # Ignore messages from the bot itself
        if self._bot_user_id and event.get("user") == self._bot_user_id:
            return

        msg = normalize_event(event)
        if msg is None:
            return

        # Dedup
        dedup_key = f"{msg.channel_id}:{msg.message_ts}"
        if dedup_key in self._seen_msgs:
            return
        self._seen_msgs[dedup_key] = True
        while len(self._seen_msgs) > self._DEDUP_MAX:
            self._seen_msgs.popitem(last=False)

        session_id = self._build_session_id(msg)

        if self._runner is None:
            return

        try:
            # runner.dispatch() is synchronous — run in a thread to avoid blocking
            result = await asyncio.to_thread(
                self._runner.dispatch,
                session_id,
                msg.text,
            )
            if result and result.text:
                await smart_reply(
                    self._app.client,
                    msg.channel_id,
                    result.text,
                    thread_ts=msg.thread_ts or msg.message_ts,
                )
        except Exception:
            log.exception(
                "Error handling Slack message channel=%s ts=%s",
                msg.channel_id,
                msg.message_ts,
            )

    @staticmethod
    def _build_session_id(msg: SlackMessage) -> str:
        """Build session ID: DMs use channel_id, channels use channel_id:user_id."""
        if msg.channel_type == "im":
            return f"slack:{msg.channel_id}"
        return f"slack:{msg.channel_id}:{msg.user_id}"

    async def _sweep_idle_sessions(self) -> None:  # pragma: no cover
        """Periodically close sessions that have been idle past the timeout."""
        while not self._stopped:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            if self._runner is None or self._stopped:
                continue
            try:
                sm = self._runner.session_manager
                idle_timeout = sm.idle_timeout_seconds
                active_sessions: Any = getattr(sm, "_active", {})
                expired = [
                    sid
                    for sid, session in list(active_sessions.items())
                    if session.is_expired(idle_timeout)
                ]
                for sid in expired:
                    log.info("Closing idle Slack session %s (SESSION_END)", sid)
                    try:
                        self._runner.close_session(sid)
                    except Exception:
                        log.exception("sweep close_session error for %s", sid)
            except Exception:
                log.exception("Error sweeping idle Slack sessions")


def register(ctx: Any) -> None:
    """Plugin entry point — register the Slack adapter."""
    ctx.add_adapter(
        AdapterSpec(
            name="slack",
            description="Slack messaging via Socket Mode",
            factory=SlackAdapter,
        )
    )
