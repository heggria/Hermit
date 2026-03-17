"""Telegram adapter plugin: bridges Telegram messaging to Hermit AgentRunner."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from hermit.plugins.builtin.adapters.telegram.normalize import TelegramMessage, normalize_update
from hermit.plugins.builtin.adapters.telegram.reply import smart_reply
from hermit.runtime.capability.contracts.base import AdapterSpec

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from hermit.runtime.control.runner.runner import AgentRunner

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes

_active_adapter: TelegramAdapter | None = None


def get_active_adapter() -> TelegramAdapter | None:
    return _active_adapter


class TelegramAdapter:
    """Connects to Telegram via long-polling using python-telegram-bot."""

    _DEDUP_MAX = 256

    @property
    def required_skills(self) -> list[str]:
        return ["telegram-output-format"]

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings
        self._token = str(
            getattr(settings, "telegram_bot_token", None)
            or os.environ.get("HERMIT_TELEGRAM_BOT_TOKEN", "")
        )
        self._runner: AgentRunner | None = None
        self._application: Any = None
        self._seen_msgs: OrderedDict[str, bool] = OrderedDict()
        self._stopped = False
        self._sweep_task: asyncio.Task[None] | None = None

    @property
    def application(self) -> Any:
        """Public accessor for the underlying python-telegram-bot Application instance."""
        return self._application

    async def start(self, runner: AgentRunner) -> None:  # pragma: no cover
        if not self._token:
            raise RuntimeError("Set HERMIT_TELEGRAM_BOT_TOKEN.")

        global _active_adapter
        self._runner = runner
        _active_adapter = self
        self._stopped = False

        from telegram.ext import AIORateLimiter, ApplicationBuilder, MessageHandler, filters

        self._application = (
            ApplicationBuilder().token(self._token).rate_limiter(AIORateLimiter()).build()
        )

        handler = MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._on_message,
        )
        self._application.add_handler(handler)

        log.info("Starting Telegram adapter (long-polling)...")
        self._sweep_task = asyncio.create_task(self._sweep_idle_sessions())

        async with self._application:
            await self._application.start()
            await self._application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message"],
            )

            # Block until stopped
            stop_event = asyncio.Event()
            self._stop_event = stop_event
            await stop_event.wait()

            await self._application.updater.stop()
            await self._application.stop()

    async def stop(self) -> None:
        global _active_adapter
        self._stopped = True
        _active_adapter = None

        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None

        # Flush all active sessions so SESSION_END fires for each.
        self._flush_all_sessions()

        if hasattr(self, "_stop_event"):
            self._stop_event.set()

        log.info("Telegram adapter stopped.")

    def _flush_all_sessions(self) -> None:
        """Close every active session on shutdown so SESSION_END fires for each."""
        if self._runner is None:
            return
        for sid in list(getattr(self._runner, "_session_started", [])):
            try:
                self._runner.close_session(sid)
            except Exception:
                log.exception("flush close_session error for %s", sid)

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:  # pragma: no cover
        msg = normalize_update(update)
        if msg is None:
            return

        # Dedup
        dedup_key = f"{msg.chat_id}:{msg.message_id}"
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
                bot = self._application.bot
                await smart_reply(
                    bot,
                    msg.chat_id,
                    result.text,
                    reply_to_message_id=msg.message_id,
                )
        except Exception:
            log.exception(
                "Error handling Telegram message chat_id=%s message_id=%s",
                msg.chat_id,
                msg.message_id,
            )

    @staticmethod
    def _build_session_id(msg: TelegramMessage) -> str:
        """Build session ID: private chats use chat_id, groups use chat_id:sender_id."""
        if msg.chat_type == "private":
            return f"tg:{msg.chat_id}"
        return f"tg:{msg.chat_id}:{msg.sender_id}"

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
                    log.info("Closing idle Telegram session %s (SESSION_END)", sid)
                    try:
                        self._runner.close_session(sid)
                    except Exception:
                        log.exception("sweep close_session error for %s", sid)
            except Exception:
                log.exception("Error sweeping idle Telegram sessions")


def register(ctx: Any) -> None:
    """Plugin entry point — register the Telegram adapter."""
    ctx.add_adapter(
        AdapterSpec(
            name="telegram",
            description="Telegram messaging via long-polling",
            factory=TelegramAdapter,
        )
    )
