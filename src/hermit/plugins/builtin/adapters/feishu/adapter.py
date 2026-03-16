"""Feishu adapter plugin: bridges Feishu messaging to Hermit AgentRunner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import warnings
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
from hermit.kernel.task.projections.projections import ProjectionService
from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage, normalize_event
from hermit.plugins.builtin.adapters.feishu.reaction import add_reaction, send_ack, send_done
from hermit.plugins.builtin.adapters.feishu.reply import (
    SKIP_TOOLS,
    ToolStep,
    build_approval_card,
    build_approval_resolution_card,
    build_completion_status_card,
    build_error_card,
    build_progress_card,
    build_result_card_with_process,
    build_thinking_card,
    format_tool_start_hint,
    make_tool_step,
    patch_card,
    reply_card_return_id,
    send_card,
    send_text_reply,
    smart_reply,
    smart_send_message,
    tool_display,
)
from hermit.runtime.capability.contracts.base import AdapterSpec

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

log = logging.getLogger(__name__)

# How often (seconds) to check for idle sessions and fire SESSION_END.
_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes
_TOPIC_REFRESH_INTERVAL_SECONDS = 5

# Minimum seconds between consecutive PATCH calls on the progress card.
_PATCH_MIN_INTERVAL = 1.0
_RAW_CONTROL_TEXT = {
    "开始执行",
    "执行吧",
    "确认执行",
    "继续执行",
    "approve",
    "deny",
    "通过",
    "批准",
    "同意",
}
_RAW_CONTROL_PREFIXES = (
    "批准 ",
    "批准一次 ",
    "批准可变工作区 ",
    "拒绝 ",
    "approve ",
    "approve_once ",
    "approve_mutable_workspace ",
    "deny ",
)
_SCHEDULE_REACTION_TOOLS = frozenset(
    {"schedule_list", "schedule_create", "schedule_update", "schedule_delete"}
)
_DISPLAYABLE_TOPIC_KINDS = {
    "tool.submitted",
    "tool.progressed",
    "tool.status.changed",
    "task.progress.summarized",
    "approval.requested",
    "approval.resolved",
}
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}

_active_adapter: "FeishuAdapter | None" = None
_lark_receive_loop_patched: bool = False
_lark_connect_patched: bool = False
_lark_runtime_patched: bool = False
_feishu_ws_shutdown: bool = False


def _is_expected_lark_ws_close(exc: BaseException) -> bool:
    message = str(exc)
    return (
        "sent 1000 (OK)" in message and "received 1000 (OK)" in message
    ) or message == "connection is closed"


def _patch_lark_receive_loop(ws_client_module: Any) -> None:
    global _lark_receive_loop_patched
    if _lark_receive_loop_patched:
        return

    loop_ref = ws_client_module.loop
    logger = ws_client_module.logger
    connection_closed = ws_client_module.ConnectionClosedException
    client_cls = ws_client_module.Client

    async def _receive_message_loop(client: Any) -> None:
        adapter_ref = getattr(client, "_hermit_adapter_ref", None)
        try:
            while True:
                if client._conn is None:
                    if adapter_ref is not None and adapter_ref._stopped:
                        return
                    raise connection_closed("connection is closed")
                msg = await client._conn.recv()
                loop_ref.create_task(client._handle_message(msg))
        except Exception as exc:
            graceful_close = (
                adapter_ref is not None and adapter_ref._stopped and _is_expected_lark_ws_close(exc)
            )
            if graceful_close:
                log.info("Feishu WebSocket receive loop closed cleanly during shutdown.")
            else:
                logger.error(client._fmt_log("receive message loop exit, err: {}", exc))
            with suppress(Exception):
                await client._disconnect()
            if client._auto_reconnect and not graceful_close:
                await client._reconnect()
            elif not client._auto_reconnect and not graceful_close:
                raise

    setattr(_receive_message_loop, "_hermit_patched", True)
    client_cls._receive_message_loop = _receive_message_loop
    _lark_receive_loop_patched = True


def _patch_lark_connect(ws_client_module: Any) -> None:
    global _lark_connect_patched
    if _lark_connect_patched:
        return

    loop_ref = ws_client_module.loop
    logger = ws_client_module.logger
    parse_ws_conn_exception = ws_client_module._parse_ws_conn_exception
    client_cls = ws_client_module.Client
    websockets_module = ws_client_module.websockets

    def _consume_receive_task(task: asyncio.Task[Any], *, client: Any) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        adapter_ref = getattr(client, "_hermit_adapter_ref", None)
        graceful_close = _is_expected_lark_ws_close(exc) and (
            _feishu_ws_shutdown or (adapter_ref is not None and adapter_ref._stopped)
        )
        if graceful_close:
            log.info("Suppressed expected Feishu WebSocket receive task exception during shutdown.")
            return
        loop_ref.call_exception_handler(
            {
                "message": "Unhandled exception in Feishu WebSocket receive task.",
                "exception": exc,
                "task": task,
            }
        )

    async def _connect(client: Any) -> None:
        await client._lock.acquire()
        try:
            if client._conn is not None:
                return
            conn_url = client._get_conn_url()
            u = ws_client_module.urlparse(conn_url)
            q = ws_client_module.parse_qs(u.query)
            conn_id = q[ws_client_module.DEVICE_ID][0]
            service_id = q[ws_client_module.SERVICE_ID][0]

            conn = await websockets_module.connect(conn_url)
            client._conn = conn
            client._conn_url = conn_url
            client._conn_id = conn_id
            client._service_id = service_id

            logger.info(client._fmt_log("connected to {}", conn_url))
            receive_task = loop_ref.create_task(client._receive_message_loop())
            setattr(client, "_hermit_receive_task", receive_task)
            import functools

            receive_task.add_done_callback(functools.partial(_consume_receive_task, client=client))
        except websockets_module.InvalidStatusCode as exc:
            parse_ws_conn_exception(exc)
        finally:
            client._lock.release()

    setattr(_connect, "_hermit_patched", True)
    client_cls._connect = _connect
    _lark_connect_patched = True


def _patch_lark_runtime(ws_client_module: Any) -> None:
    global _lark_runtime_patched
    if _lark_runtime_patched:
        return

    original_error = ws_client_module.logger.error

    def _error(message: Any, *args: Any, **kwargs: Any) -> Any:
        rendered = str(message)
        try:
            if args:
                rendered = rendered.format(*args)
        except Exception:
            rendered = str(message)
        if (
            _feishu_ws_shutdown
            and "receive message loop exit, err:" in rendered
            and "sent 1000 (OK)" in rendered
        ):
            log.info("Suppressed expected Feishu WebSocket close log during shutdown.")
            return None
        return original_error(message, *args, **kwargs)

    ws_client_module.logger.error = _error

    loop = ws_client_module.loop
    previous_handler = loop.get_exception_handler()

    def _handler(active_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if (
            _feishu_ws_shutdown
            and isinstance(exc, BaseException)
            and _is_expected_lark_ws_close(exc)
        ):
            log.info("Suppressed expected Feishu WebSocket close exception during shutdown.")
            return
        if previous_handler is not None:
            previous_handler(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    _lark_runtime_patched = True


with suppress(Exception), warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"websockets\.InvalidStatusCode is deprecated",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r"websockets\.legacy is deprecated.*",
        category=DeprecationWarning,
    )
    from lark_oapi.ws import client as _lark_ws_client_module

    _patch_lark_receive_loop(_lark_ws_client_module)
    _patch_lark_connect(_lark_ws_client_module)
    _patch_lark_runtime(_lark_ws_client_module)


def get_active_adapter() -> "FeishuAdapter | None":
    return _active_adapter


def _bind_lark_client_runtime(client: Any, ws_client_module: Any) -> None:
    for name in ("_receive_message_loop", "_connect"):
        method = getattr(ws_client_module.Client, name, None)
        if callable(method):
            setattr(client, name, method.__get__(client, cast(type[Any], type(client))))


class FeishuAdapter:
    """Connects to Feishu via lark-oapi WebSocket long connection."""

    _DEDUP_MAX = 256

    @property
    def required_skills(self) -> list[str]:
        return ["feishu-output-format", "feishu-emoji-reaction", "feishu-tools"]

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings
        self._app_id = str(
            getattr(settings, "feishu_app_id", None) or os.environ.get("HERMIT_FEISHU_APP_ID", "")
        )
        self._app_secret = str(
            getattr(settings, "feishu_app_secret", None)
            or os.environ.get("HERMIT_FEISHU_APP_SECRET", "")
        )
        self._client: Any = None
        self._ws_client: Any = None
        self._runner: AgentRunner | None = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu")
        self._seen_msgs: OrderedDict[str, bool] = OrderedDict()
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

    async def start(self, runner: AgentRunner) -> None:
        if not self._app_id or not self._app_secret:
            raise RuntimeError("Set HERMIT_FEISHU_APP_ID/HERMIT_FEISHU_APP_SECRET.")

        global _active_adapter
        self._runner = runner
        _active_adapter = self
        global _feishu_ws_shutdown
        _feishu_ws_shutdown = False
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
            lark.Client.builder().app_id(self._app_id).app_secret(self._app_secret).build()
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
        setattr(self._ws_client, "_hermit_adapter_ref", self)
        self._ensure_lark_receive_loop_patch(ws_client_module)
        _patch_lark_connect(ws_client_module)
        _bind_lark_client_runtime(self._ws_client, ws_client_module)
        _patch_lark_runtime(ws_client_module)
        self._install_ws_exception_handler()
        self._ws_client.start()

    def _is_expected_ws_close(self, exc: BaseException) -> bool:
        return _is_expected_lark_ws_close(exc)

    def _ensure_lark_receive_loop_patch(self, ws_client_module: Any) -> None:
        _patch_lark_receive_loop(ws_client_module)

    async def _cancel_ws_receive_task(self) -> None:
        if self._ws_client is None:
            return
        receive_task = getattr(self._ws_client, "_hermit_receive_task", None)
        if receive_task is None or receive_task.done():
            return
        receive_task.cancel()
        with suppress(asyncio.CancelledError):
            await receive_task

    def _install_ws_exception_handler(self) -> None:
        if self._ws_loop is None:
            return

        previous_handler = self._ws_loop.get_exception_handler()

        def _handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            exc = context.get("exception")
            if self._stopped and isinstance(exc, BaseException) and self._is_expected_ws_close(exc):
                log.info("Suppressed expected Feishu WebSocket close during shutdown.")
                return
            if previous_handler is not None:
                previous_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        self._ws_loop.set_exception_handler(_handler)

    async def stop(self) -> None:
        global _feishu_ws_shutdown
        global _active_adapter
        log.info("Stopping Feishu adapter...")
        self._stopped = True
        _feishu_ws_shutdown = True
        if _active_adapter is self:
            _active_adapter = None

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
        _feishu_ws_shutdown = False

    async def _shutdown_ws(self) -> None:
        if self._ws_client is None or self._ws_loop is None:
            return

        try:
            receive_future = asyncio.run_coroutine_threadsafe(
                self._cancel_ws_receive_task(),
                self._ws_loop,
            )
            await asyncio.wait_for(asyncio.wrap_future(receive_future), timeout=2)
        except Exception:
            log.debug("Best-effort Feishu receive task cancellation failed", exc_info=True)

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
            _SWEEP_INTERVAL_SECONDS,
            self._sweep_idle_sessions,
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
        active_sessions: Any = getattr(sm, "_active", {})
        expired = [
            sid
            for sid, session in list(active_sessions.items())
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
                metadata: dict[str, Any] = cast(dict[str, Any], dict(conversation.metadata or {}))
                mappings: dict[str, Any] = cast(
                    dict[str, Any], dict(metadata.get("feishu_task_topics", {}) or {})
                )
                mappings_changed = False
                for task_id, mapping in list(mappings.items()):
                    if not isinstance(mapping, dict):
                        mappings.pop(task_id, None)
                        mappings_changed = True
                        continue
                    mapping_d: dict[str, Any] = cast(dict[str, Any], mapping)
                    task = store.get_task(task_id)
                    if task is None or task.source_channel != "feishu":
                        mappings.pop(task_id, None)
                        mappings_changed = True
                        continue
                    card_mode = str(mapping_d.get("card_mode", "topic") or "topic")
                    if card_mode != "topic":
                        message_id = str(mapping_d.get("root_message_id", "") or "")
                        if not message_id:
                            mappings.pop(task_id, None)
                            mappings_changed = True
                            continue
                        approval_id = str(mapping_d.get("approval_id", "") or "").strip()
                        if approval_id and hasattr(store, "get_approval"):
                            approval = store.get_approval(approval_id)
                            if (
                                approval is None
                                or str(getattr(approval, "status", "") or "") != "pending"
                            ):
                                updated: dict[str, Any] = dict(mapping_d)
                                updated.pop("approval_id", None)
                                updated["card_mode"] = "topic"
                                mappings[task_id] = updated
                                mappings_changed = True
                                self._patch_task_topic(task_id, message_id=message_id)
                                continue
                        if task.status in _TERMINAL_TASK_STATUSES:
                            delivered = self._patch_terminal_result_card(
                                task_id, message_id=message_id
                            )
                            if delivered and approval_id:
                                updated = dict(mapping_d)
                                updated.pop("approval_id", None)
                                updated["card_mode"] = "topic"
                                mappings[task_id] = updated
                                mappings_changed = True
                        continue
                    message_id = str(mapping_d.get("root_message_id", "") or "")
                    pending = None
                    if task.status == "blocked" and hasattr(store, "list_approvals"):
                        pending_approvals = store.list_approvals(
                            task_id=task_id, status="pending", limit=1
                        )
                        pending = pending_approvals[0] if pending_approvals else None
                    if pending is not None:
                        approval_card, _approval = self._build_pending_approval_card(
                            pending.approval_id,
                            fallback_text=self._task_terminal_result_text(task_id)
                            or self._t("feishu.adapter.progress.thinking"),
                            approval=pending,
                        )
                        if message_id:
                            changed = patch_card(self._client, message_id, approval_card)
                        else:
                            message_id = self._send_task_card(
                                task.conversation_id,
                                task_id,
                                mapping=mapping_d,
                                card=approval_card,
                                card_mode="approval",
                                approval_id=pending.approval_id,
                            )
                            changed = bool(message_id)
                        if changed:
                            updated = dict(mapping_d)
                            updated["card_mode"] = "approval"
                            updated["approval_id"] = pending.approval_id
                            if message_id:
                                updated["root_message_id"] = message_id
                            mappings[task_id] = updated
                            mappings_changed = True
                        continue
                    if not message_id:
                        if task.status in _TERMINAL_TASK_STATUSES:
                            self._deliver_terminal_result_without_card(task_id, mapping=mapping_d)
                            continue
                        projection = ProjectionService(store).ensure_task_projection(task_id)
                        topic = dict(projection.get("topic", {}) or {})
                        if not self._topic_has_displayable_progress(topic):
                            continue
                        card = build_progress_card(
                            self._task_history_steps(task_id),
                            current_hint=self._progress_hint_from_topic(topic),
                            locale=self._locale(),
                        )
                        signature = self._card_signature(card)
                        message_id = self._send_task_card(
                            task.conversation_id,
                            task_id,
                            mapping=mapping_d,
                            card=card,
                            card_mode="topic",
                            topic_signature=signature,
                        )
                        if message_id:
                            updated = dict(mapping_d)
                            updated["root_message_id"] = message_id
                            updated["topic_signature"] = signature
                            mappings[task_id] = updated
                            mappings_changed = True
                        continue
                    if task.status in _TERMINAL_TASK_STATUSES:
                        self._patch_terminal_result_card(task_id, message_id=message_id)
                        continue
                    self._patch_task_topic(task_id, message_id=message_id)
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
        root_message_id: str | None = None,
        reply_to_message_id: str | None = None,
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
        updated = {
            "chat_id": chat_id,
            "completion_reply_sent": bool(existing.get("completion_reply_sent", False)),
            "card_mode": card_mode or "topic",
            "topic_signature": str(existing.get("topic_signature", "") or ""),
        }
        normalized_root = str(root_message_id or "").strip()
        if normalized_root:
            updated["root_message_id"] = normalized_root
        normalized_reply_to = str(reply_to_message_id or "").strip()
        if normalized_reply_to:
            updated["reply_to_message_id"] = normalized_reply_to
        if approval_id:
            updated["approval_id"] = approval_id
        mappings[task_id] = updated
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
        return dict(cast(dict[str, Any], value)) if isinstance(value, dict) else {}

    def _task_id_for_message_reference(
        self, conversation_id: str, message_id: str | None
    ) -> str | None:
        normalized = str(message_id or "").strip()
        if not normalized or self._runner is None:
            return None
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return None
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return None
        metadata: dict[str, Any] = cast(dict[str, Any], dict(conversation.metadata or {}))
        mappings: dict[str, Any] = cast(
            dict[str, Any], dict(metadata.get("feishu_task_topics", {}) or {})
        )
        for task_id, payload in mappings.items():
            mapping: dict[str, Any] = (
                dict(cast(dict[str, Any], payload)) if isinstance(payload, dict) else {}
            )
            if normalized in {
                str(mapping.get("root_message_id", "") or "").strip(),
                str(mapping.get("reply_to_message_id", "") or "").strip(),
            }:
                return str(task_id or "").strip() or None
        return None

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

    def _update_task_topic_mapping(
        self, conversation_id: str, task_id: str, **updates: Any
    ) -> None:
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
                return str(
                    payload.get("result_text", "") or payload.get("result_preview", "") or ""
                ).strip()
        return ""

    def _task_history_steps(
        self, task_id: str, *, live_steps: list[ToolStep] | None = None
    ) -> list[ToolStep]:
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
            if not tool_name or tool_name in SKIP_TOOLS:
                continue
            history_steps.append(
                ToolStep(
                    name=tool_name,
                    display=tool_display(tool_name, locale=self._locale()),
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
                if (
                    merged[idx].name == live_step.name
                    and merged[idx].key_input == live_step.key_input
                ):
                    merged[idx] = live_step
                    replaced = True
                    break
            if not replaced:
                merged.append(live_step)
        return merged

    @staticmethod
    def _card_signature(payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _topic_has_displayable_progress(self, topic: dict[str, Any]) -> bool:
        items = list(topic.get("items", []) or [])
        return any(str(item.get("kind", "")).strip() in _DISPLAYABLE_TOPIC_KINDS for item in items)

    def _progress_hint_from_topic(self, topic: dict[str, Any]) -> str:
        current_hint = str(topic.get("current_hint", "") or "").strip()
        current_phase = str(topic.get("current_phase", "") or "").strip()
        items = list(topic.get("items", []) or [])
        meaningful_kinds = _DISPLAYABLE_TOPIC_KINDS | {
            "task.completed",
            "task.failed",
            "task.cancelled",
        }
        has_meaningful_progress = any(
            str(item.get("kind", "")).strip() in meaningful_kinds for item in items
        )
        if current_phase in {"", "started", "submitted"} and not has_meaningful_progress:
            return self._t("feishu.adapter.progress.thinking")
        return current_hint or self._t("feishu.adapter.progress.thinking")

    def _send_task_card(
        self,
        conversation_id: str,
        task_id: str,
        *,
        mapping: dict[str, Any],
        card: dict[str, Any],
        card_mode: str,
        approval_id: str | None = None,
        topic_signature: str | None = None,
    ) -> str | None:
        if self._client is None:
            return None
        reply_to_message_id = str(mapping.get("reply_to_message_id", "") or "").strip()
        chat_id = str(
            mapping.get("chat_id", "") or ""
        ).strip() or self._chat_id_from_conversation_id(conversation_id)
        if reply_to_message_id:
            message_id = reply_card_return_id(self._client, reply_to_message_id, card)
        elif chat_id:
            message_id = send_card(self._client, chat_id, card)
        else:
            return None
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None
        updates: dict[str, Any] = {
            "root_message_id": normalized_message_id,
            "card_mode": card_mode,
        }
        if approval_id:
            updates["approval_id"] = approval_id
        if topic_signature is not None:
            updates["topic_signature"] = topic_signature
        self._update_task_topic_mapping(conversation_id, task_id, **updates)
        return normalized_message_id

    def _deliver_terminal_result_without_card(
        self, task_id: str, *, mapping: dict[str, Any]
    ) -> bool:
        if self._runner is None or self._client is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return False
        task = store.get_task(task_id)
        if task is None or task.status not in {"completed", "failed", "cancelled"}:
            return False
        if bool(mapping.get("completion_reply_sent", False)):
            return False
        text = self._task_terminal_result_text(task_id)
        if not text:
            return False
        reply_to_message_id = str(mapping.get("reply_to_message_id", "") or "").strip()
        chat_id = str(
            mapping.get("chat_id", "") or ""
        ).strip() or self._chat_id_from_conversation_id(task.conversation_id)
        sent_message_id = ""
        if reply_to_message_id:
            delivered = bool(
                smart_reply(self._client, reply_to_message_id, text, locale=self._locale())
            )
            if not delivered and chat_id:
                sent_message_id = str(
                    smart_send_message(self._client, chat_id, text, locale=self._locale()) or ""
                ).strip()
                delivered = bool(sent_message_id)
        elif chat_id:
            sent_message_id = str(
                smart_send_message(self._client, chat_id, text, locale=self._locale()) or ""
            ).strip()
            delivered = bool(sent_message_id)
        else:
            return False
        if not delivered:
            return False
        updates: dict[str, Any] = {"completion_reply_sent": True}
        if sent_message_id:
            updates["root_message_id"] = sent_message_id
        self._update_task_topic_mapping(task.conversation_id, task_id, **updates)
        return True

    def _is_async_feishu_task(self, task_id: str) -> bool:
        if self._runner is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None or not hasattr(store, "list_step_attempts"):
            return False
        attempts = store.list_step_attempts(task_id=task_id, limit=20)
        for attempt in attempts:
            context = dict(getattr(attempt, "context", {}) or {})
            ingress = dict(context.get("ingress_metadata", {}) or {})
            if str(ingress.get("dispatch_mode", "") or "") == "async":
                return True
        return False

    def handle_post_run_result(
        self,
        result: Any,
        *,
        session_id: str = "",
        runner: Any = None,
    ) -> bool:
        if self._client is None or self._runner is None:
            return False
        if runner is not None and runner is not self._runner:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return False

        task_id = str(getattr(result, "task_id", "") or "").strip()
        if not task_id and session_id and hasattr(store, "get_last_task_for_conversation"):
            latest = store.get_last_task_for_conversation(session_id)
            task_id = str(getattr(latest, "task_id", "") or "").strip()
        if not task_id:
            return False

        task = store.get_task(task_id) if hasattr(store, "get_task") else None
        if task is None or str(getattr(task, "source_channel", "") or "") != "feishu":
            return False
        if not self._is_async_feishu_task(task_id):
            return False

        mapping = self._task_topic_mapping(task.conversation_id, task_id)
        status = str(getattr(task, "status", "") or "")
        if status in _TERMINAL_TASK_STATUSES:
            root_message_id = str(mapping.get("root_message_id", "") or "").strip()
            if root_message_id:
                return self._patch_terminal_result_card(task_id, message_id=root_message_id)
            fallback_mapping = dict(mapping)
            if not fallback_mapping:
                chat_id = self._chat_id_from_conversation_id(task.conversation_id)
                if chat_id:
                    fallback_mapping["chat_id"] = chat_id
            return self._deliver_terminal_result_without_card(task_id, mapping=fallback_mapping)
        return False

    def _patch_terminal_result_card(self, task_id: str, *, message_id: str | None = None) -> bool:
        if self._runner is None or self._client is None:
            return False
        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return False
        task = store.get_task(task_id)
        if task is None:
            return False
        if task.status in _TERMINAL_TASK_STATUSES:
            return False
        mapping = self._task_topic_mapping(task.conversation_id, task_id)
        root_message_id = message_id or str(mapping.get("root_message_id", "") or "")
        if not root_message_id:
            return False
        result_text = self._task_terminal_result_text(task_id)
        if result_text:
            card = build_result_card_with_process(
                result_text,
                self._task_history_steps(task_id),
                locale=self._locale(),
            )
        else:
            card = build_completion_status_card(locale=self._locale())
        signature = self._card_signature(card)
        if str(mapping.get("topic_signature", "") or "") == signature:
            return False
        patched = patch_card(self._client, root_message_id, card)
        if patched:
            self._update_task_topic_mapping(
                task.conversation_id, task_id, topic_signature=signature
            )
        return patched

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
        if (
            task is None
            or task.source_channel != "feishu"
            or task.status not in {"completed", "failed", "cancelled"}
        ):
            return False
        if not self._task_has_appended_notes(task_id):
            return False
        mapping = self._task_topic_mapping(task.conversation_id, task_id)
        if bool(mapping.get("completion_reply_sent", False)):
            return False
        resolved_chat_id = (
            chat_id
            or str(mapping.get("chat_id", "") or "")
            or self._chat_id_from_conversation_id(task.conversation_id)
        )
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
        card = build_progress_card(
            self._task_history_steps(task_id),
            current_hint=self._progress_hint_from_topic(topic),
            locale=self._locale(),
        )
        signature = self._card_signature(card)
        if str(mapping.get("topic_signature", "") or "") == signature:
            return False
        patched = patch_card(self._client, root_message_id, card)
        if patched:
            self._update_task_topic_mapping(
                task.conversation_id, task_id, topic_signature=signature
            )
        return patched

    def _flush_all_sessions(self) -> None:
        """Close every active session on shutdown so SESSION_END fires for each."""
        if self._runner is None:
            return
        for sid in list(getattr(self._runner, "_session_started", [])):
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
                    "reply_to_message_id": (
                        getattr(event.message, "reply_to_message_id", "")
                        or getattr(event.message, "parent_id", "")
                        or getattr(event.message, "reply_in_thread_from_message_id", "")
                    ),
                    "quoted_message_id": (
                        getattr(event.message, "quoted_message_id", "")
                        or getattr(event.message, "root_id", "")
                        or getattr(event.message, "upper_message_id", "")
                    ),
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

        log.info(
            "Received msg_id=%s chat=%s chat_type=%s message_type=%s sender=%s text=%s images=%s",
            msg.message_id,
            msg.chat_id,
            msg.chat_type,
            msg.message_type,
            msg.sender_id,
            msg.text[:80],
            len(msg.image_keys),
        )
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

        if (
            value.get("kind") != "approval"
            or action_type not in {"approve_once", "approve_mutable_workspace", "deny"}
            or not approval_id
        ):
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
            status_text = self._t(
                "feishu.adapter.card_action.already_handled", status=approval.status
            )
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
            self._executor.submit(
                self._handle_approval_action, approval_id, action_type, message_id
            )
        except RuntimeError:
            return self._card_action_response(
                self._t("feishu.adapter.card_action.executor_stopped"),
                level="error",
            )

        if action_type in {"approve_once", "approve_mutable_workspace"}:
            action_text = self._t("feishu.adapter.card_action.approved_once")
            if action_type == "approve_mutable_workspace":
                action_text = self._t("feishu.adapter.card_action.approved_mutable_workspace")
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
        requested_action: dict[str, Any] = cast(
            dict[str, Any], dict(getattr(approval, "requested_action", {}) or {})
        )
        target_paths: list[Any] = cast(list[Any], requested_action.get("target_paths") or [])
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
            command_preview = (
                str(approval.requested_action.get("command_preview", "") or "").strip() or None
            )

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
                card_message_id = reply_card_return_id(
                    self._client, reply_to_message_id, approval_card
                )
            return card_message_id, blocked, task_id

        if not result_text:
            return card_message_id, blocked, task_id

        if card_message_id:
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

        if reply_to_message_id:
            smart_reply(self._client, reply_to_message_id, result_text, locale=self._locale())
        else:
            smart_send_message(self._client, chat_id, result_text, locale=self._locale())
        return card_message_id, blocked, task_id

    def _reply_task_topic_card(self, reply_to_message_id: str, _task_id: str) -> str | None:
        if self._client is None:
            return None
        return reply_card_return_id(
            self._client,
            reply_to_message_id,
            build_thinking_card(self._t("feishu.adapter.progress.thinking"), locale=self._locale()),
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
                log.info(
                    "reissued_pending_approval_card approval_id=%s chat_id=%s",
                    approval.approval_id,
                    chat_id,
                )

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

    @staticmethod
    def _is_short_text_message(raw_text: str) -> bool:
        cleaned = " ".join(str(raw_text or "").split()).strip()
        if not cleaned:
            return True
        if "\n" in str(raw_text or ""):
            return False
        return len(cleaned) <= 12

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
        resolve_approval_fn: Any = getattr(self._runner, "_resolve_approval", None)
        if callable(resolve_approval_fn):
            kwargs: dict[str, Any] = {
                "action": action,
                "approval_id": approval_id,
                "on_tool_call": on_tool_call,
                "on_tool_start": on_tool_start,
            }
            if reason:
                kwargs["reason"] = reason
            return resolve_approval_fn(session_id, **kwargs)
        raise AttributeError("runner does not support approval resolution")

    def _dispatch_message_sync_compat(
        self,
        *,
        session_id: str,
        msg: FeishuMessage,
        dispatch_text: str,
        enable_progress_card: bool = True,
    ) -> None:
        if self._runner is None:
            return

        steps: list[ToolStep] = []
        card_message_id: str | None = None
        current_hint = self._t("feishu.adapter.progress.thinking")
        last_patch_at = 0.0
        schedule_reacted = False
        progress_enabled = (
            enable_progress_card
            and bool(getattr(self._settings, "feishu_thread_progress", False))
            and self._client is not None
            and bool(msg.message_id)
        )

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
        if self._client is not None and msg.message_id:
            if progress_enabled:
                card_message_id = reply_card_return_id(
                    self._client,
                    msg.message_id,
                    build_progress_card([], current_hint=current_hint, locale=self._locale()),
                )

            def _on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
                nonlocal current_hint, schedule_reacted
                if not schedule_reacted:
                    if name in _SCHEDULE_REACTION_TOOLS or (
                        name == "read_skill"
                        and str(tool_input.get("name", "")).strip().lower() == "scheduler"
                    ):
                        schedule_reacted = True
                        add_reaction(self._client, msg.message_id, "Get")
                if not progress_enabled:
                    return
                current_hint = format_tool_start_hint(name, tool_input, locale=self._locale())
                maybe_patch_progress()

            on_tool_start = _on_tool_start

        if progress_enabled:

            def _on_tool_call(name: str, tool_input: dict[str, Any], result: Any) -> None:
                nonlocal current_hint
                steps.append(make_tool_step(name, tool_input, result, 0, locale=self._locale()))
                current_hint = self._t("feishu.adapter.progress.thinking")
                maybe_patch_progress(force=True)

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

    def _process_message(self, msg: FeishuMessage) -> None:
        """Queue normal Feishu ingress onto the kernel worker pool."""
        if self._runner is None:
            return

        session_id = self._build_session_id(msg)

        # Slash commands must be dispatched from the raw user text so that the
        # "/" prefix is preserved.  _build_prompt wraps the text with Feishu
        # metadata tags which would break the leading-slash detection in dispatch().
        raw_text = (msg.text or "").strip()
        if self._should_dispatch_raw(session_id, raw_text):
            dispatch_text = raw_text
        else:
            dispatch_text = self._build_prompt(session_id, msg)
        if self._client is not None and msg.message_id:
            send_ack(self._client, msg.message_id, self._settings)
        if self._should_dispatch_raw(session_id, raw_text):
            control = self._runner.task_controller.resolve_text_command(session_id, raw_text)
            if control is not None and control[0] in {
                "approve_once",
                "approve_mutable_workspace",
                "deny",
            }:
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
            return

        task_controller = getattr(self._runner, "task_controller", None)
        ingress = None
        reply_to_ref = str(getattr(msg, "reply_to_message_id", "") or "").strip() or None
        quoted_message_ref = str(getattr(msg, "quoted_message_id", "") or "").strip() or None
        reply_to_task_id = self._task_id_for_message_reference(
            session_id, reply_to_ref
        ) or self._task_id_for_message_reference(session_id, quoted_message_ref)
        if task_controller is not None and callable(
            getattr(task_controller, "decide_ingress", None)
        ):
            ingress = task_controller.decide_ingress(
                conversation_id=session_id,
                source_channel="feishu",
                raw_text=raw_text,
                prompt=dispatch_text,
                reply_to_task_id=reply_to_task_id,
                reply_to_ref=reply_to_ref,
                quoted_message_ref=quoted_message_ref,
            )
            if str(getattr(ingress, "resolution", "") or "") == "pending_disambiguation":
                _disambiguation_fn: Any = getattr(
                    self._runner, "_pending_disambiguation_text", None
                )
                text = (
                    _disambiguation_fn(ingress)
                    if _disambiguation_fn is not None
                    else "我没法确认你要继续哪个任务，请先切换任务。"
                )
                if self._client and msg.message_id:
                    smart_reply(self._client, msg.message_id, text, locale=self._locale())
                return
            if ingress.mode == "append_note":
                if ingress.task_id:
                    self._patch_task_topic(ingress.task_id)
                if self._client is not None and msg.message_id:
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
                reply_to_task_id=reply_to_task_id,
                reply_to_ref=reply_to_ref,
                quoted_message_ref=quoted_message_ref,
            )

        if str(getattr(ingress, "intent", "") or "") == "chat_only":
            self._dispatch_message_sync_compat(
                session_id=session_id,
                msg=msg,
                dispatch_text=dispatch_text,
                enable_progress_card=False,
            )
            return

        if self._is_short_text_message(raw_text):
            self._dispatch_message_sync_compat(
                session_id=session_id,
                msg=msg,
                dispatch_text=dispatch_text,
                enable_progress_card=False,
            )
            return

        if self._client is not None and msg.message_id:
            lowered = raw_text.lower()
            if any(token in lowered for token in ("schedule", "提醒", "定时")):
                add_reaction(self._client, msg.message_id, "Get")
        try:
            ingress_metadata: dict[str, Any] = {
                "feishu_chat_id": msg.chat_id,
                "feishu_message_id": msg.message_id,
                "title": raw_text[:80] or self._t("feishu.adapter.topic.default_title"),
                "ingress_id": str(getattr(ingress, "ingress_id", "") or ""),
                "ingress_intent": str(getattr(ingress, "intent", "") or ""),
                "ingress_reason": str(getattr(ingress, "reason", "") or ""),
                "ingress_resolution": str(getattr(ingress, "resolution", "") or ""),
                "binding_reason_codes": list(getattr(ingress, "reason_codes", []) or []),
            }
            if getattr(ingress, "anchor_task_id", None):
                ingress_metadata["continuation_anchor"] = dict(
                    getattr(ingress, "continuation_anchor", {}) or {}
                )
            enqueue_kwargs: dict[str, Any] = {
                "source_channel": "feishu",
                "source_ref": f"feishu:{msg.chat_id}:{msg.message_id}",
                "ingress_metadata": ingress_metadata,
            }
            if hasattr(ingress, "parent_task_id"):
                enqueue_kwargs["parent_task_id"] = getattr(ingress, "parent_task_id")
            ctx = self._runner.enqueue_ingress(
                session_id,
                dispatch_text,
                **enqueue_kwargs,
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
            self._bind_task_topic(
                session_id,
                ctx.task_id,
                chat_id=msg.chat_id,
                reply_to_message_id=msg.message_id,
                card_mode="topic",
            )

    def _card_action_response(
        self, content: str, *, level: str = "info", card: dict[str, Any] | None = None
    ) -> Any:
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
                        build_thinking_card(
                            result.text or self._t("feishu.adapter.progress.thinking"),
                            locale=self._locale(),
                        ),
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
                next_approval_id = str(
                    getattr(getattr(result, "agent_result", None), "approval_id", "") or ""
                )
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
                    build_error_card(
                        self._t("feishu.adapter.approval.failed"), locale=self._locale()
                    ),
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
            summary = str(record.get("summary", "")).strip() or self._t(
                "feishu.adapter.image_prompt.empty_summary"
            )
            image_id = str(record.get("image_id", "")).strip() or "unknown"
            tags = ", ".join(record.get("tags", [])[:5]) or self._t(
                "feishu.adapter.image_prompt.no_tags"
            )
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
            record = self._ingest_image_record(
                session_id=session_id, message_id=msg.message_id, image_key=image_key
            )
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
            log.warning(
                "image_store_from_feishu_unavailable image_key=%s reason=missing_task_kernel",
                image_key,
            )
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
            log.warning(
                "image_store_from_feishu_unavailable image_key=%s reason=tool_missing", image_key
            )
            task_controller.finalize_result(ctx, status="failed")
            return None
        except Exception as exc:
            log.warning("image_store_from_feishu_failed image_key=%s error=%s", image_key, exc)
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.blocked:
            log.warning(
                "image_store_from_feishu_blocked image_key=%s approval_id=%s",
                image_key,
                result.approval_id,
            )
            if result.approval_id:
                from hermit.kernel.policy.approvals.approvals import ApprovalService

                ApprovalService(task_controller.store).deny(
                    result.approval_id,
                    resolved_by="feishu_adapter",
                    reason="adapter ingress does not support interactive approval",
                )
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.execution_status in ("succeeded", "reconciling") and result.result_code in (
            "succeeded",
            None,
        ):
            raw_result: Any = result.raw_result
            if isinstance(raw_result, dict):
                task_controller.finalize_result(ctx, status="succeeded")
                return cast(dict[str, Any], raw_result)
            log.warning(
                "image_store_from_feishu_invalid_result image_key=%s result_type=%s",
                image_key,
                type(raw_result).__name__,
            )
            task_controller.finalize_result(ctx, status="failed")
            return None

        if result.execution_status == "failed":
            task_controller.finalize_result(ctx, status="failed")

        log.warning(
            "image_store_from_feishu_degraded image_key=%s execution_status=%s result_code=%s",
            image_key,
            result.execution_status,
            result.result_code,
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
