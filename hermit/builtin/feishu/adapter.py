"""Feishu adapter plugin: bridges Feishu messaging to Hermit AgentRunner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Optional

from hermit.builtin.feishu.normalize import FeishuMessage, normalize_event
from hermit.builtin.feishu.reaction import send_ack, send_done
from hermit.builtin.feishu.reply import (
    _SKIP_TOOLS,
    _tool_display,
    ToolStep,
    build_approval_card,
    build_approval_resolution_card,
    build_completion_status_card,
    build_error_card,
    build_progress_card,
    build_result_card_with_process,
    build_task_topic_card,
    build_thinking_card,
    format_tool_start_hint,
    make_tool_step,
    patch_card,
    reply_card_return_id,
    send_card,
    send_text_reply,
    smart_send_message,
    smart_reply,
)
from hermit.i18n import resolve_locale, tr
from hermit.kernel.approval_copy import ApprovalCopyService
from hermit.kernel.projections import ProjectionService
from hermit.plugin.base import AdapterSpec

if TYPE_CHECKING:
    from hermit.core.runner import AgentRunner

log = logging.getLogger(__name__)

# How often (seconds) to check for idle sessions and fire SESSION_END.
_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes
_TOPIC_REFRESH_INTERVAL_SECONDS = 5

# Minimum seconds between consecutive PATCH calls on the progress card.
_PATCH_MIN_INTERVAL = 1.0
_RAW_CONTROL_TEXT = {"开始执行", "执行吧", "确认执行", "继续执行", "approve", "deny", "通过", "批准", "同意"}
_RAW_CONTROL_PREFIXES = (
    "批准 ",
    "批准一次 ",
    "始终允许此目录 ",
    "拒绝 ",
    "approve ",
    "approve_once ",
    "approve_always_directory ",
    "deny ",
)



class FeishuAdapter:
    """Connects to Feishu via lark-oapi WebSocket long connection."""

    _DEDUP_MAX = 256

    @property
    def required_skills(self) -> list[str]:
        return ["feishu-output-format", "feishu-emoji-reaction", "feishu-tools"]

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings
        self._app_id = str(
            getattr(settings, "feishu_app_id", None)
            or os.environ.get("HERMIT_FEISHU_APP_ID", os.environ.get("FEISHU_APP_ID", ""))
        )
        self._app_secret = str(
            getattr(settings, "feishu_app_secret", None)
            or os.environ.get("HERMIT_FEISHU_APP_SECRET", os.environ.get("FEISHU_APP_SECRET", ""))
        )
        self._client: Any = None
        self._ws_client: Any = None
        self._runner: AgentRunner | None = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu")
        self._seen_msgs: OrderedDict[str, bool] = OrderedDict()
        self._acked_msgs: OrderedDict[str, bool] = OrderedDict()
        self._seen_lock = threading.Lock()
        self._stopped = False
        self._sweep_timer: threading.Timer | None = None
        self._topic_timer: threading.Timer | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_exited = threading.Event()
        self._ws_error: BaseException | None = None
        self._approval_copy = ApprovalCopyService()

    def _locale(self) -> str:
        return resolve_locale(getattr(self._settings, "locale", None))

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=self._locale(), default=default, **kwargs)

    def _ack_message_once(self, message_id: str) -> bool:
        normalized = str(message_id or "").strip()
        if not normalized or self._client is None:
            return False
        with self._seen_lock:
            if normalized in self._acked_msgs:
                self._acked_msgs.move_to_end(normalized)
                return False
            self._acked_msgs[normalized] = True
            while len(self._acked_msgs) > self._DEDUP_MAX:
                self._acked_msgs.popitem(last=False)
        send_ack(self._client, normalized, self._settings)
        return True

    async def start(self, runner: AgentRunner) -> None:
        if not self._app_id or not self._app_secret:
            raise RuntimeError(
                "Set HERMIT_FEISHU_APP_ID/HERMIT_FEISHU_APP_SECRET "
                "(legacy FEISHU_APP_ID/FEISHU_APP_SECRET also supported)"
            )

        self._runner = runner
        self._stopped = False
        self._ws_error = None
        self._ws_loop = None
        self._ws_exited.clear()
        self._schedule_sweep()
        self._schedule_topic_refresh()

        # The Feishu SDK exposes a blocking start() call, so we keep it on a
        # dedicated daemon thread and shut it down explicitly on Ctrl+C.
        self._ws_thread = threading.Thread(
            target=self._run_ws_client,
            name="feishu-ws",
            daemon=True,
        )
        self._ws_thread.start()
        await asyncio.to_thread(self._ws_exited.wait)

        if self._ws_error is not None and not self._stopped:
            raise RuntimeError("Feishu adapter stopped unexpectedly") from self._ws_error

    def _run_ws_client(self) -> None:
        try:
            self._start_ws()
        except RuntimeError as exc:
            message = str(exc)
            if self._stopped and "Event loop stopped before Future completed" in message:
                log.info("Feishu WebSocket loop stopped.")
            else:
                self._ws_error = exc
                log.exception("Feishu WebSocket loop exited unexpectedly")
        except Exception as exc:
            self._ws_error = exc
            log.exception("Feishu WebSocket loop exited unexpectedly")
        finally:
            self._ws_exited.set()

    def _start_ws(self) -> None:
        """Set up and start the lark WebSocket client (blocking, runs in thread)."""
        import lark_oapi as lark
        from lark_oapi import ws
        from lark_oapi.ws import client as ws_client_module

        self._ws_loop = ws_client_module.loop

        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .build()
        )
        self._reissue_pending_approval_cards()

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_card_action_trigger(self._on_card_action)
            .build()
        )

        log.info("Starting Feishu adapter (WebSocket long connection)...")
        self._ws_client = ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        self._ws_client.start()

    async def stop(self) -> None:
        log.info("Stopping Feishu adapter...")
        self._stopped = True

        if self._sweep_timer is not None:
            self._sweep_timer.cancel()
            self._sweep_timer = None
        if self._topic_timer is not None:
            self._topic_timer.cancel()
            self._topic_timer = None

        # Fire SESSION_END for all sessions that were ever started so that
        # memories are saved before the process exits.
        self._flush_all_sessions()

        await self._shutdown_ws()
        self._executor.shutdown(wait=False, cancel_futures=True)
        await asyncio.to_thread(self._join_ws_thread)

    async def _shutdown_ws(self) -> None:
        if self._ws_client is None or self._ws_loop is None:
            return

        try:
            setattr(self._ws_client, "_auto_reconnect", False)
            future = asyncio.run_coroutine_threadsafe(
                self._ws_client._disconnect(),
                self._ws_loop,
            )
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=2)
        except Exception:
            log.debug("Best-effort Feishu disconnect failed", exc_info=True)
        finally:
            try:
                self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
            except Exception:
                log.debug("Failed to stop Feishu event loop", exc_info=True)

    def _join_ws_thread(self, timeout_seconds: float = 2.0) -> None:
        if self._ws_thread is None:
            return
        self._ws_thread.join(timeout=timeout_seconds)
        if self._ws_thread.is_alive():
            log.warning(
                "Feishu WebSocket thread did not exit within %.1fs; forcing process shutdown.",
                timeout_seconds,
            )

    def _schedule_sweep(self) -> None:
        """Schedule the next idle-session sweep."""
        if self._stopped:
            return
        self._sweep_timer = threading.Timer(
            _SWEEP_INTERVAL_SECONDS, self._sweep_idle_sessions,
        )
        self._sweep_timer.daemon = True
        self._sweep_timer.start()

    def _schedule_topic_refresh(self) -> None:
        if self._stopped:
            return
        self._topic_timer = threading.Timer(
            _TOPIC_REFRESH_INTERVAL_SECONDS,
            self._refresh_task_topics,
        )
        self._topic_timer.daemon = True
        self._topic_timer.start()

    def _sweep_idle_sessions(self) -> None:
        """Close sessions that have been idle past the timeout, firing SESSION_END."""
        if self._runner is None or self._stopped:
            return
        sm = self._runner.session_manager
        idle_timeout = sm.idle_timeout_seconds
        expired = [
            sid for sid, session in list(sm._active.items())
            if session.is_expired(idle_timeout)
        ]
        for sid in expired:
            log.info("Closing idle Feishu session %s (SESSION_END)", sid)
            try:
                self._runner.close_session(sid)
            except Exception:
                log.exception("sweep close_session error for %s", sid)
        self._schedule_sweep()

    def _refresh_task_topics(self) -> None:
        if self._runner is None or self._client is None or self._stopped:
            return
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return
        try:
            for conversation_id in store.list_conversations():
                conversation = store.get_conversation(conversation_id)
                if conversation is None:
                    continue
                metadata = dict(conversation.metadata or {})
                mappings = dict(metadata.get("feishu_task_topics", {}) or {})
                mappings_changed = False
                for task_id, mapping in list(mappings.items()):
                    if not isinstance(mapping, dict):
                        mappings.pop(task_id, None)
                        mappings_changed = True
                        continue
                    task = store.get_task(task_id)
                    if task is None or task.source_channel != "feishu":
                        mappings.pop(task_id, None)
                        mappings_changed = True
                        continue
                    card_mode = str(mapping.get("card_mode", "topic") or "topic")
                    if card_mode != "topic":
                        message_id = str(mapping.get("root_message_id", "") or "")
                        if not message_id:
                            mappings.pop(task_id, None)
                            mappings_changed = True
                            continue
                        approval_id = str(mapping.get("approval_id", "") or "").strip()
                        if approval_id and hasattr(store, "get_approval"):
                            approval = store.get_approval(approval_id)
                            if approval is None or str(getattr(approval, "status", "") or "") != "pending":
                                updated = dict(mapping)
                                updated.pop("approval_id", None)
                                updated["card_mode"] = "topic"
                                mappings[task_id] = updated
                                mappings_changed = True
                                self._patch_task_topic(task_id, message_id=message_id)
                                continue
                        if task.status in {"completed", "failed", "cancelled"}:
                            mappings.pop(task_id, None)
                            mappings_changed = True
                        continue
                    message_id = str(mapping.get("root_message_id", "") or "")
                    if not message_id:
                        mappings.pop(task_id, None)
                        mappings_changed = True
                        continue
                    pending = None
                    if task.status == "blocked" and hasattr(store, "list_approvals"):
                        pending_approvals = store.list_approvals(task_id=task_id, status="pending", limit=1)
                        pending = pending_approvals[0] if pending_approvals else None
                    if pending is not None:
                        approval_card, _approval = self._build_pending_approval_card(
                            pending.approval_id,
                            fallback_text=self._task_terminal_result_text(task_id) or self._t("feishu.adapter.progress.thinking"),
                            approval=pending,
                        )
                        if patch_card(self._client, message_id, approval_card):
                            updated = dict(mapping)
                            updated["card_mode"] = "approval"
                            updated["approval_id"] = pending.approval_id
                            mappings[task_id] = updated
                            mappings_changed = True
                        continue
                    self._patch_task_topic(task_id, message_id=message_id)
                    if task.status in {"completed", "failed", "cancelled"}:
                        completion_sent = self._maybe_send_completion_result_message(
                            task_id,
                            chat_id=str(mapping.get("chat_id", "") or "") or None,
                        )
                        if (
                            not self._task_has_appended_notes(task_id)
                            or bool(mapping.get("completion_reply_sent", False))
                            or completion_sent
                        ):
                            mappings.pop(task_id, None)
                            mappings_changed = True
                if mappings_changed:
                    metadata["feishu_task_topics"] = mappings
                    store.update_conversation_metadata(conversation_id, metadata)
        finally:
            self._schedule_topic_refresh()

    def _bind_task_topic(
        self,
        conversation_id: str,
        task_id: str,
        *,
        chat_id: str,
        root_message_id: str,
        card_mode: str = "topic",
        approval_id: str | None = None,
    ) -> None:
        if self._runner is None:
            return
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return
        conversation = store.get_conversation(conversation_id)
        metadata = dict(conversation.metadata if conversation is not None else {})
        mappings = dict(metadata.get("feishu_task_topics", {}) or {})
        existing = dict(mappings.get(task_id, {}) or {})
        mappings[task_id] = {
            "chat_id": chat_id,
            "root_message_id": root_message_id,
            "completion_reply_sent": bool(existing.get("completion_reply_sent", False)),
            "card_mode": card_mode or "topic",
            "topic_signature": str(existing.get("topic_signature", "") or ""),
        }
        if approval_id:
            mappings[task_id]["approval_id"] = approval_id
        metadata["feishu_task_topics"] = mappings
        store.update_conversation_metadata(conversation_id, metadata)

    def _task_topic_mapping(self, conversation_id: str, task_id: str) -> dict[str, Any]:
        if self._runner is None:
            return {}
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return {}
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return {}
        metadata = dict(conversation.metadata or {})
        mappings = dict(metadata.get("feishu_task_topics", {}) or {})
        value = mappings.get(task_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def _unbind_task_topic(self, conversation_id: str, task_id: str) -> None:
        if self._runner is None:
            return
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return
        metadata = dict(conversation.metadata or {})
        mappings = dict(metadata.get("feishu_task_topics", {}) or {})
        if task_id not in mappings:
            return
        mappings.pop(task_id, None)
        metadata["feishu_task_topics"] = mappings
        store.update_conversation_metadata(conversation_id, metadata)

    def _update_task_topic_mapping(self, conversation_id: str, task_id: str, **updates: Any) -> None:
        if self._runner is None:
            return
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return
        metadata = dict(conversation.metadata or {})
        mappings = dict(metadata.get("feishu_task_topics", {}) or {})
        existing = dict(mappings.get(task_id, {}) or {})
        if not existing:
            return
        existing.update(updates)
        mappings[task_id] = existing
        metadata["feishu_task_topics"] = mappings
        store.update_conversation_metadata(conversation_id, metadata)

    def _task_has_appended_notes(self, task_id: str) -> bool:
        if self._runner is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None or not hasattr(store, "list_events"):
            return False
        for event in reversed(store.list_events(task_id=task_id, limit=500)):
            if event["event_type"] == "task.note.appended":
                return True
        return False

    def _task_terminal_result_text(self, task_id: str) -> str:
        if self._runner is None:
            return ""
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None or not hasattr(store, "list_events"):
            return ""
        for event in reversed(store.list_events(task_id=task_id, limit=500)):
            if event["event_type"] in {"task.completed", "task.failed", "task.cancelled"}:
                payload = dict(event.get("payload", {}) or {})
                return str(payload.get("result_text", "") or payload.get("result_preview", "") or "").strip()
        return ""

    def _task_history_steps(self, task_id: str, *, live_steps: list[ToolStep] | None = None) -> list[ToolStep]:
        if self._runner is None:
            return list(live_steps or [])
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None or not hasattr(store, "list_events"):
            return list(live_steps or [])

        projection = ProjectionService(store).ensure_task_projection(task_id)
        raw_history = list(projection.get("tool_history", []) or [])
        history_steps: list[ToolStep] = []
        for raw_step in raw_history:
            tool_name = str(dict(raw_step).get("tool_name", "") or "").strip()
            if not tool_name or tool_name in _SKIP_TOOLS:
                continue
            history_steps.append(
                ToolStep(
                    name=tool_name,
                    display=_tool_display(tool_name, locale=self._locale()),
                    key_input=str(dict(raw_step).get("key_input", "") or ""),
                    summary="",
                    elapsed_ms=0,
                )
            )

        if not live_steps:
            return history_steps

        merged = list(history_steps)
        for live_step in live_steps:
            replaced = False
            for idx in range(len(merged) - 1, -1, -1):
                if merged[idx].name == live_step.name and merged[idx].key_input == live_step.key_input:
                    merged[idx] = live_step
                    replaced = True
                    break
            if not replaced:
                merged.append(live_step)
        return merged

    @staticmethod
    def _topic_signature(topic: dict[str, Any], title: str) -> str:
        payload = {
            "title": title,
            "topic": topic,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _maybe_send_completion_result_message(
        self,
        task_id: str,
        *,
        task_text: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        if self._runner is None or self._client is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return False
        task = store.get_task(task_id)
        if task is None or task.source_channel != "feishu" or task.status not in {"completed", "failed", "cancelled"}:
            return False
        if not self._task_has_appended_notes(task_id):
            return False
        mapping = self._task_topic_mapping(task.conversation_id, task_id)
        if bool(mapping.get("completion_reply_sent", False)):
            return False
        resolved_chat_id = chat_id or str(mapping.get("chat_id", "") or "") or self._chat_id_from_conversation_id(task.conversation_id)
        if not resolved_chat_id:
            return False
        text = str(task_text or "").strip() or self._task_terminal_result_text(task_id)
        if not text:
            return False
        message_id = smart_send_message(self._client, resolved_chat_id, text, locale=self._locale())
        if not message_id:
            return False
        self._update_task_topic_mapping(task.conversation_id, task_id, completion_reply_sent=True)
        return True

    def _patch_task_topic(self, task_id: str, *, message_id: str | None = None) -> bool:
        if self._runner is None or self._client is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return False
        task = store.get_task(task_id)
        if task is None:
            return False
        mapping = self._task_topic_mapping(task.conversation_id, task_id)
        root_message_id = message_id or str(mapping.get("root_message_id", "") or "")
        if not root_message_id:
            return False
        projection = ProjectionService(store).ensure_task_projection(task_id)
        topic = dict(projection.get("topic", {}) or {})
        default_title = self._t("feishu.adapter.topic.default_title")
        title = str(projection.get("task", {}).get("title", "") or default_title)[:40] or default_title
        signature = self._topic_signature(topic, title)
        if str(mapping.get("topic_signature", "") or "") == signature:
            return False
        patched = patch_card(
            self._client,
            root_message_id,
            build_task_topic_card(topic, title=title, locale=self._locale()),
        )
        if patched:
            self._update_task_topic_mapping(task.conversation_id, task_id, topic_signature=signature)
        return patched

    def _flush_all_sessions(self) -> None:
        """Close every active session on shutdown so SESSION_END fires for each."""
        if self._runner is None:
            return
        for sid in list(self._runner._session_started):
            try:
                self._runner.close_session(sid)
            except Exception:
                log.exception("flush close_session error for %s", sid)

    def _on_message(self, data: Any) -> None:
        """lark-oapi event callback — must return FAST so the SDK sends ACK.

        The SDK sends the ACK frame only after this callback returns.
        If we block here (e.g. waiting for AI), the server times out and
        re-delivers the event, causing duplicate replies.
        """
        try:
            event = data.event
            event_dict = {
                "message": {
                    "chat_id": getattr(event.message, "chat_id", ""),
                    "message_id": getattr(event.message, "message_id", ""),
                    "content": getattr(event.message, "content", ""),
                    "message_type": getattr(event.message, "message_type", "text"),
                    "chat_type": getattr(event.message, "chat_type", "p2p"),
                },
                "sender": {
                    "sender_id": {
                        "open_id": getattr(
                            getattr(event.sender, "sender_id", None),
                            "open_id",
                            "",
                        ),
                    },
                },
            }
        except Exception:
            log.exception("Failed to extract event fields")
            return

        msg = normalize_event(event_dict)
        if not msg.chat_id or (not msg.text and not msg.image_keys):
            log.info(
                "Skipping unsupported Feishu message msg_id=%s chat=%s message_type=%s content=%s",
                event_dict["message"]["message_id"],
                event_dict["message"]["chat_id"],
                event_dict["message"]["message_type"],
                str(event_dict["message"]["content"])[:200],
            )
            return
        if self._stopped:
            return

        if self._is_duplicate(msg.message_id):
            log.debug("Duplicate message_id=%s, skipping", msg.message_id)
            return

        log.info("Received msg_id=%s chat=%s chat_type=%s message_type=%s sender=%s text=%s images=%s",
                 msg.message_id, msg.chat_id, msg.chat_type,
                 msg.message_type, msg.sender_id, msg.text[:80], len(msg.image_keys))
        self._ack_message_once(msg.message_id)
        try:
            self._executor.submit(self._process_message, msg)
        except RuntimeError:
            log.debug("Feishu worker pool already stopped; dropping msg_id=%s", msg.message_id)

    def _on_card_action(self, data: Any) -> Any:
        """Handle interactive card button clicks for approvals."""
        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        context = getattr(event, "context", None)
        value = dict(getattr(action, "value", {}) or {})
        action_type = str(value.get("action", "")).strip().lower()
        approval_id = str(value.get("approval_id", "")).strip()
        message_id = str(getattr(context, "open_message_id", "") or "")

        if value.get("kind") != "approval" or action_type not in {"approve_once", "approve_always_directory", "deny"} or not approval_id:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.unsupported"),
                level="info",
            )
        if self._runner is None or self._client is None:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.unavailable"),
                level="error",
            )

        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.kernel_disabled"),
                level="error",
            )

        approval = store.get_approval(approval_id)
        if approval is None:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.not_found", approval_id=approval_id),
                level="error",
            )
        if approval.status != "pending":
            status_text = self._t("feishu.adapter.card_action.already_handled", status=approval.status)
            return self._card_action_response(
                status_text,
                level="info",
                card=build_approval_resolution_card(
                    approval.status,
                    approval_id,
                    status_text,
                    locale=self._locale(),
                ),
            )

        try:
            self._executor.submit(self._handle_approval_action, approval_id, action_type, message_id)
        except RuntimeError:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.executor_stopped"),
                level="error",
            )

        if action_type in {"approve_once", "approve_always_directory"}:
            action_text = self._t("feishu.adapter.card_action.approved_once")
            if action_type == "approve_always_directory":
                action_text = self._t("feishu.adapter.card_action.approved_always")
            return self._card_action_response(
                action_text,
                level="success",
                card=build_thinking_card(action_text, locale=self._locale()),
            )
        return self._card_action_response(
            self._t("feishu.adapter.card_action.denied"),
            level="success",
            card=build_approval_resolution_card(
                "deny",
                approval_id,
                self._t("feishu.adapter.card_action.denied_detail"),
                locale=self._locale(),
            ),
        )

    def _is_duplicate(self, message_id: str) -> bool:
        """Thread-safe dedup check. Feishu uses at-least-once delivery."""
        if not message_id:
            return False
        with self._seen_lock:
            if message_id in self._seen_msgs:
                return True
            self._seen_msgs[message_id] = True
            if len(self._seen_msgs) > self._DEDUP_MAX:
                self._seen_msgs.popitem(last=False)
            return False

    @staticmethod
    def _build_session_id(msg: FeishuMessage) -> str:
        """Build a session key that isolates conversations properly.

        - P2P: chat_id already uniquely identifies the user-bot conversation.
        - Group: chat_id is shared by all members, so we append sender_id
          to give each user their own conversation thread.
        """
        if msg.chat_type == "group" and msg.sender_id:
            return f"{msg.chat_id}:{msg.sender_id}"
        return msg.chat_id

    @staticmethod
    def _chat_id_from_conversation_id(conversation_id: str) -> str:
        if ":" in conversation_id:
            return conversation_id.split(":", 1)[0]
        return conversation_id

    @staticmethod
    def _approval_card_kwargs(approval: Any | None) -> dict[str, str | None]:
        if approval is None:
            return {
                "target_path": None,
                "workspace_root": None,
                "grant_scope_dir": None,
            }
        requested_action = dict(getattr(approval, "requested_action", {}) or {})
        target_paths = requested_action.get("target_paths") or []
        return {
            "target_path": str(target_paths[0]) if target_paths else None,
            "workspace_root": str(requested_action.get("workspace_root", "") or "") or None,
            "grant_scope_dir": str(requested_action.get("grant_scope_dir", "") or "") or None,
        }

    def _build_pending_approval_card(
        self,
        approval_id: str,
        *,
        fallback_text: str,
        steps: list[ToolStep] | None = None,
        detail_suffix: str | None = None,
        approval: Any | None = None,
    ) -> tuple[dict[str, Any], Any | None]:
        if approval is None and self._runner is not None:
            store = getattr(getattr(self._runner, "task_controller", None), "store", None)
            if store is not None and hasattr(store, "get_approval"):
                approval = store.get_approval(approval_id)

        approval_text = fallback_text
        approval_title = None
        approval_detail = None
        approval_sections: tuple[Any, ...] = ()
        command_preview = None

        if approval is not None:
            approval_copy = self._approval_copy.resolve_copy(approval.requested_action, approval_id)
            approval_text = approval_copy.summary
            approval_title = approval_copy.title
            approval_detail = approval_copy.detail
            approval_sections = approval_copy.sections
            command_preview = str(approval.requested_action.get("command_preview", "") or "").strip() or None

        suffix = str(detail_suffix or "").strip()
        if suffix:
            approval_detail = f"{approval_detail}\n{suffix}" if approval_detail else suffix

        return (
            build_approval_card(
                approval_text,
                approval_id,
                steps,
                title=approval_title,
                detail=approval_detail,
                sections=approval_sections,
                command_preview=command_preview,
                locale=self._locale(),
                **self._approval_card_kwargs(approval),
            ),
            approval,
        )

    def _present_task_result(
        self,
        *,
        reply_to_message_id: str | None,
        existing_card_message_id: str | None,
        chat_id: str,
        result: Any,
        steps: list[ToolStep],
    ) -> tuple[str | None, bool, str]:
        agent_result = getattr(result, "agent_result", None)
        blocked = bool(agent_result and (agent_result.blocked or agent_result.suspended))
        task_id = str(getattr(agent_result, "task_id", "") or "")
        approval_id = str(getattr(agent_result, "approval_id", "") or "")
        has_appended_notes = bool(task_id) and self._task_has_appended_notes(task_id)
        card_message_id = str(existing_card_message_id or "").strip() or None
        result_text = str(getattr(result, "text", "") or "")

        if self._client is None:
            return card_message_id, blocked, task_id

        if blocked and approval_id:
            approval_card, _approval = self._build_pending_approval_card(
                approval_id,
                fallback_text=result_text,
                steps=steps,
            )
            if card_message_id:
                patch_card(self._client, card_message_id, approval_card)
            elif reply_to_message_id:
                card_message_id = reply_card_return_id(self._client, reply_to_message_id, approval_card)
            return card_message_id, blocked, task_id

        if not result_text:
            return card_message_id, blocked, task_id

        if card_message_id:
            if has_appended_notes and task_id:
                patch_card(
                    self._client,
                    card_message_id,
                    build_completion_status_card(locale=self._locale()),
                )
                self._maybe_send_completion_result_message(
                    task_id,
                    task_text=result_text,
                    chat_id=chat_id,
                )
            else:
                patch_card(
                    self._client,
                    card_message_id,
                    build_result_card_with_process(
                        result_text,
                        self._task_history_steps(task_id, live_steps=steps),
                        locale=self._locale(),
                    ),
                )
            return card_message_id, blocked, task_id

        if has_appended_notes and task_id and reply_to_message_id:
            card_message_id = reply_card_return_id(
                self._client,
                reply_to_message_id,
                build_completion_status_card(locale=self._locale()),
            )
            self._maybe_send_completion_result_message(
                task_id,
                task_text=result_text,
                chat_id=chat_id,
            )
            return card_message_id, blocked, task_id

        if reply_to_message_id:
            smart_reply(self._client, reply_to_message_id, result_text, locale=self._locale())
        else:
            smart_send_message(self._client, chat_id, result_text, locale=self._locale())
        return card_message_id, blocked, task_id

    def _reply_task_topic_card(self, reply_to_message_id: str, task_id: str) -> str | None:
        if self._runner is None or self._client is None:
            return None
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return None
        projection = ProjectionService(store).ensure_task_projection(task_id)
        topic = dict(projection.get("topic", {}) or {})
        default_title = self._t("feishu.adapter.topic.default_title")
        title = str(projection.get("task", {}).get("title", "") or default_title)[:40] or default_title
        return reply_card_return_id(
            self._client,
            reply_to_message_id,
            build_task_topic_card(topic, title=title, locale=self._locale()),
        )

    def _reissue_pending_approval_cards(self) -> None:
        if self._runner is None or self._client is None:
            return
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return

        for approval in store.list_approvals(status="pending", limit=100):
            task = store.get_task(approval.task_id)
            if task is None or task.source_channel != "feishu":
                continue
            chat_id = self._chat_id_from_conversation_id(task.conversation_id)
            if not chat_id.startswith("oc_"):
                continue

            recovery_hint = self._t(
                "feishu.adapter.reissue.recovery_hint",
                approval_id=approval.approval_id,
            )
            card, _approval = self._build_pending_approval_card(
                approval.approval_id,
                fallback_text=self._t("feishu.adapter.progress.thinking"),
                detail_suffix=recovery_hint,
                approval=approval,
            )
            message_id = send_card(self._client, chat_id, card)
            if message_id:
                self._bind_task_topic(
                    task.conversation_id,
                    approval.task_id,
                    chat_id=chat_id,
                    root_message_id=message_id,
                    card_mode="approval",
                    approval_id=approval.approval_id,
                )
                log.info("reissued_pending_approval_card approval_id=%s chat_id=%s", approval.approval_id, chat_id)

    def _should_dispatch_raw(self, session_id: str, raw_text: str) -> bool:
        stripped = raw_text.strip()
        if not stripped:
            return False
        if stripped.startswith("/"):
            return True
        lowered = stripped.lower()
        if stripped in _RAW_CONTROL_TEXT or lowered in _RAW_CONTROL_TEXT:
            return True
        if stripped.startswith(_RAW_CONTROL_PREFIXES) or lowered.startswith(_RAW_CONTROL_PREFIXES):
            return True
        task_controller = getattr(self._runner, "task_controller", None)
        if task_controller is None:
            return False
        return task_controller.resolve_text_command(session_id, stripped) is not None

    def _supports_async_ingress(self) -> bool:
        runner = self._runner
        task_controller = getattr(runner, "task_controller", None)
        return bool(
            runner is not None
            and callable(getattr(runner, "enqueue_ingress", None))
            and task_controller is not None
            and callable(getattr(task_controller, "decide_ingress", None))
        )

    def _resolve_approval_from_feishu(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        on_tool_call: Any | None = None,
        on_tool_start: Any | None = None,
    ) -> Any:
        if self._runner is None:
            raise RuntimeError("runner unavailable")
        if callable(getattr(self._runner, "enqueue_approval_resume", None)):
            kwargs = {
                "action": action,
                "approval_id": approval_id,
            }
            if reason:
                kwargs["reason"] = reason
            return self._runner.enqueue_approval_resume(session_id, **kwargs)
        if callable(getattr(self._runner, "_resolve_approval", None)):
            kwargs = {
                "action": action,
                "approval_id": approval_id,
                "on_tool_call": on_tool_call,
                "on_tool_start": on_tool_start,
            }
            if reason:
                kwargs["reason"] = reason
            return self._runner._resolve_approval(session_id, **kwargs)
        raise AttributeError("runner does not support approval resolution")

    def _dispatch_message_sync_compat(
        self,
        *,
        session_id: str,
        msg: FeishuMessage,
        dispatch_text: str,
    ) -> None:
        if self._runner is None:
            return

        steps: list[ToolStep] = []
        card_message_id: str | None = None
        current_hint = self._t("feishu.adapter.progress.thinking")
        last_patch_at = 0.0

        def maybe_patch_progress(force: bool = False) -> None:
            nonlocal last_patch_at
            if self._client is None or not card_message_id:
                return
            now = time.monotonic()
            if not force and now - last_patch_at < _PATCH_MIN_INTERVAL:
                return
            patch_card(
                self._client,
                card_message_id,
                build_progress_card(steps, current_hint=current_hint, locale=self._locale()),
            )
            last_patch_at = now

        on_tool_start = None
        on_tool_call = None
        if bool(getattr(self._settings, "feishu_thread_progress", False)) and self._client is not None and msg.message_id:
            card_message_id = reply_card_return_id(
                self._client,
                msg.message_id,
                build_progress_card([], current_hint=current_hint, locale=self._locale()),
            )

            def _on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
                nonlocal current_hint
                current_hint = format_tool_start_hint(name, tool_input, locale=self._locale())
                maybe_patch_progress()

            def _on_tool_call(name: str, tool_input: dict[str, Any], result: Any) -> None:
                nonlocal current_hint
                steps.append(make_tool_step(name, tool_input, result, 0, locale=self._locale()))
                current_hint = self._t("feishu.adapter.progress.thinking")
                maybe_patch_progress(force=True)

            on_tool_start = _on_tool_start
            on_tool_call = _on_tool_call

        try:
            result = self._runner.dispatch(
                session_id=session_id,
                text=dispatch_text,
                on_tool_start=on_tool_start,
                on_tool_call=on_tool_call,
            )
        except Exception:
            log.exception("Agent error for chat_id=%s", msg.chat_id)
            if self._client and msg.message_id:
                send_text_reply(
                    self._client,
                    msg.message_id,
                    self._t("feishu.adapter.error.agent_failed_text"),
                )
            return

        agent_result = getattr(result, "agent_result", None)
        task_id = str(getattr(agent_result, "task_id", "") or "")
        execution_status = str(getattr(agent_result, "execution_status", "") or "")
        blocked = bool(agent_result and (agent_result.blocked or agent_result.suspended))

        if execution_status == "note_appended" and task_id:
            self._patch_task_topic(task_id)
            if self._client and msg.message_id:
                send_done(self._client, msg.message_id, self._settings)
            return

        card_message_id, blocked, task_id = self._present_task_result(
            reply_to_message_id=msg.message_id,
            existing_card_message_id=card_message_id,
            chat_id=msg.chat_id,
            result=result,
            steps=steps,
        )

        if blocked and card_message_id and task_id:
            approval_id = str(getattr(agent_result, "approval_id", "") or "")
            self._bind_task_topic(
                session_id,
                task_id,
                chat_id=msg.chat_id,
                root_message_id=card_message_id,
                card_mode="approval",
                approval_id=approval_id,
            )
        elif task_id and self._task_has_appended_notes(task_id):
            self._unbind_task_topic(session_id, task_id)

        if self._client and msg.message_id:
            send_done(self._client, msg.message_id, self._settings)

    def _process_message(self, msg: FeishuMessage) -> None:
        """Queue normal Feishu ingress onto the kernel worker pool."""
        if self._runner is None:
            return

        if self._client and msg.message_id:
            self._ack_message_once(msg.message_id)

        session_id = self._build_session_id(msg)

        # Slash commands must be dispatched from the raw user text so that the
        # "/" prefix is preserved.  _build_prompt wraps the text with Feishu
        # metadata tags which would break the leading-slash detection in dispatch().
        raw_text = (msg.text or "").strip()
        if self._should_dispatch_raw(session_id, raw_text):
            dispatch_text = raw_text
        else:
            dispatch_text = self._build_prompt(session_id, msg)
        if self._should_dispatch_raw(session_id, raw_text):
            control = self._runner.task_controller.resolve_text_command(session_id, raw_text)
            if control is not None and control[0] in {"approve_once", "approve_always_directory", "deny"}:
                result = self._resolve_approval_from_feishu(
                    session_id,
                    action=control[0],
                    approval_id=control[1],
                    reason=control[2],
                )
            else:
                try:
                    result = self._runner.dispatch(session_id=session_id, text=dispatch_text)
                except Exception:
                    log.exception("Agent error for chat_id=%s", msg.chat_id)
                    if self._client and msg.message_id:
                        send_text_reply(
                            self._client,
                            msg.message_id,
                            self._t("feishu.adapter.error.agent_failed_text"),
                        )
                    return
            if self._client and msg.message_id and result.text:
                smart_reply(self._client, msg.message_id, result.text, locale=self._locale())
                send_done(self._client, msg.message_id, self._settings)
            return

        task_controller = getattr(self._runner, "task_controller", None)
        ingress = None
        if task_controller is not None and callable(getattr(task_controller, "decide_ingress", None)):
            ingress = task_controller.decide_ingress(
                conversation_id=session_id,
                source_channel="feishu",
                raw_text=raw_text,
                prompt=dispatch_text,
            )
            if ingress.mode == "append_note":
                if ingress.task_id:
                    self._patch_task_topic(ingress.task_id)
                if self._client and msg.message_id:
                    send_done(self._client, msg.message_id, self._settings)
                return

        if not self._supports_async_ingress():
            self._dispatch_message_sync_compat(
                session_id=session_id,
                msg=msg,
                dispatch_text=dispatch_text,
            )
            return

        if ingress is None:
            ingress = self._runner.task_controller.decide_ingress(
                conversation_id=session_id,
                source_channel="feishu",
                raw_text=raw_text,
                prompt=dispatch_text,
            )

        try:
            ctx = self._runner.enqueue_ingress(
                session_id,
                dispatch_text,
                source_channel="feishu",
                source_ref=f"feishu:{msg.chat_id}:{msg.message_id}",
                ingress_metadata={
                    "feishu_chat_id": msg.chat_id,
                    "feishu_message_id": msg.message_id,
                    "title": raw_text[:80] or self._t("feishu.adapter.topic.default_title"),
                },
            )
        except Exception:
            log.exception("Failed to enqueue Feishu ingress chat_id=%s", msg.chat_id)
            if self._client and msg.message_id:
                send_text_reply(
                    self._client,
                    msg.message_id,
                    self._t("feishu.adapter.error.agent_failed_text"),
                )
            return

        if self._client and msg.message_id:
            root_message_id = self._reply_task_topic_card(msg.message_id, ctx.task_id)
            if root_message_id:
                self._bind_task_topic(
                    session_id,
                    ctx.task_id,
                    chat_id=msg.chat_id,
                    root_message_id=root_message_id,
                    card_mode="topic",
                )
            send_done(self._client, msg.message_id, self._settings)

    def _card_action_response(self, content: str, *, level: str = "info", card: dict[str, Any] | None = None) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        toast_type = level if level in {"info", "success", "error"} else "info"
        payload: dict[str, Any] = {"toast": {"type": toast_type, "content": content}}
        if card is not None:
            payload["card"] = {"type": "raw", "data": card}
        return P2CardActionTriggerResponse(payload)

    def _handle_approval_action(self, approval_id: str, action: str, message_id: str) -> None:
        if self._runner is None or self._client is None:
            return

        task_controller = getattr(self._runner, "task_controller", None)
        store = getattr(task_controller, "store", None)
        if store is None:
            return

        approval = store.get_approval(approval_id)
        if approval is None:
            if message_id:
                patch_card(
                    self._client,
                    message_id,
                    build_error_card(
                        self._t("feishu.adapter.approval.missing", approval_id=approval_id),
                        locale=self._locale(),
                    ),
                )
            return

        task = store.get_task(approval.task_id)
        if task is None:
            if message_id:
                patch_card(
                    self._client,
                    message_id,
                    build_error_card(
                        self._t("feishu.adapter.approval.task_missing", task_id=approval.task_id),
                        locale=self._locale(),
                    ),
                )
            return

        try:
            if action == "deny":
                result = self._resolve_approval_from_feishu(
                    task.conversation_id,
                    action="deny",
                    approval_id=approval_id,
                    reason="denied_from_feishu",
                )
                if message_id:
                    patch_card(
                        self._client,
                        message_id,
                        build_approval_resolution_card(
                            "deny",
                            approval_id,
                            result.text,
                            locale=self._locale(),
                        ),
                    )
                self._unbind_task_topic(task.conversation_id, task.task_id)
                return

            if callable(getattr(self._runner, "enqueue_approval_resume", None)):
                result = self._resolve_approval_from_feishu(
                    task.conversation_id,
                    action=action,
                    approval_id=approval_id,
                )
                if message_id:
                    patch_card(
                        self._client,
                        message_id,
                        build_thinking_card(result.text or self._t("feishu.adapter.progress.thinking"), locale=self._locale()),
                    )
                    self._update_task_topic_mapping(
                        task.conversation_id,
                        task.task_id,
                        card_mode="topic",
                        approval_id="",
                    )
                    self._patch_task_topic(task.task_id, message_id=message_id)
                return

            result = self._resolve_approval_from_feishu(
                task.conversation_id,
                action=action,
                approval_id=approval_id,
            )
            card_message_id, blocked, result_task_id = self._present_task_result(
                reply_to_message_id=None,
                existing_card_message_id=message_id,
                chat_id=self._chat_id_from_conversation_id(task.conversation_id),
                result=result,
                steps=[],
            )
            if blocked and card_message_id and result_task_id:
                next_approval_id = str(getattr(getattr(result, "agent_result", None), "approval_id", "") or "")
                self._update_task_topic_mapping(
                    task.conversation_id,
                    result_task_id,
                    card_mode="approval",
                    approval_id=next_approval_id,
                )
                return
            self._unbind_task_topic(task.conversation_id, task.task_id)
        except Exception:
            log.exception("Failed to resolve approval %s from Feishu card action", approval_id)
            if message_id:
                patch_card(
                    self._client,
                    message_id,
                    build_error_card(self._t("feishu.adapter.approval.failed"), locale=self._locale()),
                )

    def _build_prompt(self, session_id: str, msg: FeishuMessage) -> str:
        """Build the agent prompt, injecting message_id and chat_id for tool use."""
        text = msg.text
        if msg.image_keys:
            image_part = self._build_image_prompt(session_id, msg)
            text = f"{text}\n\n{image_part}" if text else image_part

        meta = ""
        if msg.message_id:
            meta += f"<feishu_msg_id>{msg.message_id}</feishu_msg_id>\n"
        if msg.chat_id:
            meta += f"<feishu_chat_id>{msg.chat_id}</feishu_chat_id>\n"
        return f"{meta}{text}" if meta else text

    def _build_image_prompt(self, session_id: str, msg: FeishuMessage) -> str:
        if self._runner is None:
            return self._t("feishu.adapter.image_prompt.single")

        records = self._ingest_image_records(session_id, msg)

        lines = [self._t("feishu.adapter.image_prompt.multi", count=len(msg.image_keys))]
        if not records:
            return "\n".join(lines)
        for index, record in enumerate(records, start=1):
            summary = str(record.get("summary", "")).strip() or self._t("feishu.adapter.image_prompt.empty_summary")
            image_id = str(record.get("image_id", "")).strip() or "unknown"
            tags = ", ".join(record.get("tags", [])[:5]) or self._t("feishu.adapter.image_prompt.no_tags")
            lines.append(
                self._t(
                    "feishu.adapter.image_prompt.entry",
                    index=index,
                    image_id=image_id,
                    summary=summary,
                    tags=tags,
                )
            )
        return "\n".join(lines)

    def _ingest_image_records(self, session_id: str, msg: FeishuMessage) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for image_key in msg.image_keys:
            record = self._ingest_image_record(session_id=session_id, message_id=msg.message_id, image_key=image_key)
            if record is not None:
                records.append(record)
        return records

    def _ingest_image_record(
        self,
        *,
        session_id: str,
        message_id: str,
        image_key: str,
    ) -> dict[str, Any] | None:
        runner = self._runner
        if runner is None:
            return None

        task_controller = getattr(runner, "task_controller", None)
        tool_executor = getattr(getattr(runner, "agent", None), "tool_executor", None)
        if task_controller is None or tool_executor is None:
            log.warning("image_store_from_feishu_unavailable", image_key=image_key, reason="missing_task_kernel")
            return None

        workspace_root = str(getattr(getattr(runner, "agent", None), "workspace_root", "") or "")
        ctx = task_controller.start_task(
            conversation_id=session_id,
            goal=f"Ingest Feishu image {image_key}",
            source_channel="feishu",
            kind="attachment_ingest",
            workspace_root=workspace_root,
            parent_task_id=None,
            requested_by="feishu_adapter",
        )

        try:
            result = tool_executor.execute(
                ctx,
                "image_store_from_feishu",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "image_key": image_key,
                },
                request_overrides={
                    "actor": {"kind": "adapter", "agent_id": "feishu_adapter"},
                    "context": {
                        "source_ingress": "feishu_adapter",
                        "feishu_message_id": message_id,
                    },
                    "idempotency_key": f"feishu-image:{message_id}:{image_key}",
                },
            )
        except KeyError:
            log.warning("image_store_from_feishu_unavailable", image_key=image_key, reason="tool_missing")
            task_controller.finalize_result(ctx, status="failed")
            return None
        except Exception as exc:
            log.warning("image_store_from_feishu_failed", image_key=image_key, error=str(exc))
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.blocked:
            log.warning("image_store_from_feishu_blocked", image_key=image_key, approval_id=result.approval_id)
            if result.approval_id:
                task_controller.store.resolve_approval(
                    result.approval_id,
                    status="denied",
                    resolved_by="feishu_adapter",
                    resolution={"reason": "adapter ingress does not support interactive approval"},
                )
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.execution_status == "succeeded":
            if isinstance(result.raw_result, dict):
                task_controller.finalize_result(ctx, status="succeeded")
                return result.raw_result
            log.warning("image_store_from_feishu_invalid_result", image_key=image_key, result_type=type(result.raw_result).__name__)
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.execution_status == "failed":
            task_controller.finalize_result(ctx, status="failed")

        log.warning(
            "image_store_from_feishu_degraded",
            image_key=image_key,
            execution_status=result.execution_status,
            result_code=result.result_code,
        )
        return None


def register(ctx: Any) -> None:
    """Plugin entry point — register the Feishu adapter."""
    ctx.add_adapter(
        AdapterSpec(
            name="feishu",
            description="Feishu (Lark) messaging via WebSocket long connection",
            factory=FeishuAdapter,
        )
    )
