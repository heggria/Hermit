"""Feishu adapter plugin: bridges Feishu messaging to Hermit AgentRunner."""
from __future__ import annotations

import asyncio
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
    ToolStep,
    build_approval_card,
    build_approval_resolution_card,
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
)
from hermit.kernel.approval_copy import ApprovalCopyService
from hermit.plugin.base import AdapterSpec

if TYPE_CHECKING:
    from hermit.core.runner import AgentRunner

log = logging.getLogger(__name__)

# How often (seconds) to check for idle sessions and fire SESSION_END.
_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes

# Minimum seconds between consecutive PATCH calls on the progress card.
_PATCH_MIN_INTERVAL = 1.0
_RAW_CONTROL_TEXT = {"开始执行", "执行吧", "确认执行", "继续执行", "approve", "deny", "通过", "批准", "同意"}
_RAW_CONTROL_PREFIXES = ("批准 ", "拒绝 ", "approve ", "deny ")



class FeishuAdapter:
    """Connects to Feishu via lark-oapi WebSocket long connection."""

    _DEDUP_MAX = 256

    @property
    def required_skills(self) -> list[str]:
        return ["feishu-output-format", "feishu-emoji-reaction"]

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
        self._seen_lock = threading.Lock()
        self._stopped = False
        self._sweep_timer: threading.Timer | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_exited = threading.Event()
        self._ws_error: BaseException | None = None
        self._approval_copy = ApprovalCopyService()

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

        if value.get("kind") != "approval" or action_type not in {"approve", "deny"} or not approval_id:
            return self._card_action_response(
                "暂不支持这个按钮操作。",
                level="info",
            )
        if self._runner is None or self._client is None:
            return self._card_action_response(
                "Hermit 当前不可用，请稍后重试。",
                level="error",
            )

        store = getattr(getattr(self._runner, "task_controller", None), "store", None)
        if store is None:
            return self._card_action_response(
                "审批内核未启用。",
                level="error",
            )

        approval = store.get_approval(approval_id)
        if approval is None:
            return self._card_action_response(
                f"未找到审批：{approval_id}",
                level="error",
            )
        if approval.status != "pending":
            status_text = f"该审批已处理：{approval.status}"
            return self._card_action_response(
                status_text,
                level="info",
                card=build_approval_resolution_card(approval.status, approval_id, status_text),
            )

        try:
            self._executor.submit(self._handle_approval_action, approval_id, action_type, message_id)
        except RuntimeError:
            return self._card_action_response(
                "后台处理器已停止，审批未提交。",
                level="error",
            )

        if action_type == "approve":
            return self._card_action_response(
                "已通过，正在继续执行。",
                level="success",
                card=build_thinking_card("已通过，正在继续执行..."),
            )
        return self._card_action_response(
            "已拒绝，本次操作不会继续执行。",
            level="success",
            card=build_approval_resolution_card(
                "deny",
                approval_id,
                "本次审批已拒绝，当前操作不会继续。\n如需继续，请重新发起请求；届时你可以对新的审批请求再次进行批准。",
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

            approval_copy = self._approval_copy.resolve_copy(approval.requested_action, approval.approval_id)
            command_preview = str(approval.requested_action.get("command_preview", "") or "").strip() or None
            recovery_hint = (
                "服务恢复后，旧审批卡片的按钮可能已失效；"
                f"请使用这张新卡片，或直接回复“批准 {approval.approval_id}”/“拒绝 {approval.approval_id}”。"
            )
            detail = approval_copy.detail.strip()
            detail = f"{detail}\n{recovery_hint}" if detail else recovery_hint
            card = build_approval_card(
                approval_copy.summary,
                approval.approval_id,
                title=approval_copy.title,
                detail=detail,
                command_preview=command_preview,
            )
            message_id = send_card(self._client, chat_id, card)
            if message_id:
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

    def _process_message(self, msg: FeishuMessage) -> None:
        """Run agent synchronously (runs in thread pool).

        When _THREAD_PROGRESS is enabled (default) the flow uses **lazy
        initialisation** — the "thinking" card is NOT sent upfront.  Instead
        the ACK emoji provides immediate feedback, and the first on_tool_call
        callback triggers the thinking card.  This means:

        * Simple queries (no tools):  ACK → agent → smart_reply  (1 API call,
          lightweight text or card just like before).
        * Complex queries (tools):  ACK → first tool call sends thinking card →
          subsequent tools update it via PATCH + thread replies → final PATCH
          with result card containing a collapsible work-process panel.

        When _THREAD_PROGRESS is disabled (or for slash commands) the original
        single-shot behaviour is used: wait for the agent then send one reply.
        """
        if self._runner is None:
            return

        if self._client and msg.message_id:
            send_ack(self._client, msg.message_id, self._settings)

        session_id = self._build_session_id(msg)

        # Slash commands must be dispatched from the raw user text so that the
        # "/" prefix is preserved.  _build_prompt wraps the text with Feishu
        # metadata tags which would break the leading-slash detection in dispatch().
        raw_text = (msg.text or "").strip()
        if self._should_dispatch_raw(session_id, raw_text):
            dispatch_text = raw_text
        else:
            dispatch_text = self._build_prompt(session_id, msg)

        use_progress = (
            bool(getattr(self._settings, "feishu_thread_progress", True))
            and not raw_text.startswith("/")
            and bool(self._client)
            and bool(msg.message_id)
        )

        # ── State shared with the on_tool_start / on_tool_call closures ─────
        # Using list[T] as mutable cells so nested functions can write back.
        steps: list[ToolStep] = []
        card_msg_id: list[Optional[str]] = [None]
        last_patch_time: list[float] = [0.0]
        step_start_time: list[float] = [time.monotonic()]

        def _ensure_card(hint: str) -> None:
            """Lazy-init the progress card on the first visible tool call."""
            if card_msg_id[0] is None:
                card_msg_id[0] = reply_card_return_id(
                    self._client,
                    msg.message_id,
                    build_thinking_card("思考中..."),
                )

        def _patch_progress(hint: str, throttle: bool = True) -> None:
            """PATCH the progress card, optionally throttled."""
            if not card_msg_id[0]:
                return
            now = time.monotonic()
            if throttle and now - last_patch_time[0] < _PATCH_MIN_INTERVAL:
                return
            patch_card(
                self._client, card_msg_id[0],
                build_progress_card(steps, hint),
            )
            last_patch_time[0] = now

        def on_tool_start(name: str, tool_input: dict) -> None:
            """Called immediately before each tool executes — updates card hint."""
            if name in _SKIP_TOOLS:
                return
            _ensure_card(hint="思考中...")
            # Show what we're about to do; never throttle (user is waiting).
            _patch_progress(format_tool_start_hint(name, tool_input), throttle=False)
            step_start_time[0] = time.monotonic()

        def on_tool_call(name: str, tool_input: dict, result: str) -> None:
            """Called after each tool completes — adds step + PATCHes card."""
            if name in _SKIP_TOOLS:
                return
            elapsed_ms = int((time.monotonic() - step_start_time[0]) * 1000)
            step_start_time[0] = time.monotonic()

            step = make_tool_step(name, tool_input, result, elapsed_ms)
            steps.append(step)
            _patch_progress("正在继续处理...", throttle=True)

        # ── Run agent ────────────────────────────────────────────────────────
        try:
            result = self._runner.dispatch(
                session_id=session_id,
                text=dispatch_text,
                on_tool_call=on_tool_call if use_progress else None,
                on_tool_start=on_tool_start if use_progress else None,
            )
        except Exception:
            log.exception("Agent error for chat_id=%s", msg.chat_id)
            if self._client and msg.message_id:
                if card_msg_id[0]:
                    patch_card(
                        self._client, card_msg_id[0],
                        build_error_card("Agent 处理出错，请稍后重试"),
                    )
                else:
                    send_text_reply(
                        self._client, msg.message_id, "[Error] Agent failed to process."
                    )
            return

        # ── Final result ─────────────────────────────────────────────────────
        if result.text and self._client and msg.message_id:
            blocked = bool(result.agent_result and result.agent_result.blocked)
            approval_id = str(getattr(result.agent_result, "approval_id", "") or "")
            if blocked and approval_id:
                approval = None
                task_controller = getattr(self._runner, "task_controller", None)
                store = getattr(task_controller, "store", None)
                if store is not None:
                    approval = store.get_approval(approval_id)
                approval_text = result.text
                approval_title = None
                approval_detail = None
                command_preview = None
                if approval is not None:
                    approval_copy = self._approval_copy.resolve_copy(approval.requested_action, approval_id)
                    approval_text = approval_copy.summary
                    approval_title = approval_copy.title
                    approval_detail = approval_copy.detail
                    command_preview = str(approval.requested_action.get("command_preview", "") or "").strip() or None
                approval_card = build_approval_card(
                    approval_text,
                    approval_id,
                    steps,
                    title=approval_title,
                    detail=approval_detail,
                    command_preview=command_preview,
                )
                if card_msg_id[0]:
                    patch_card(self._client, card_msg_id[0], approval_card)
                else:
                    reply_card_return_id(self._client, msg.message_id, approval_card)
            elif card_msg_id[0]:
                final_card = build_result_card_with_process(result.text, steps)
                patch_card(self._client, card_msg_id[0], final_card)
            else:
                smart_reply(self._client, msg.message_id, result.text)

            send_done(self._client, msg.message_id, self._settings)

    def _card_action_response(self, content: str, *, level: str = "info", card: dict[str, Any] | None = None) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

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
                    build_error_card(f"审批不存在：{approval_id}"),
                )
            return

        task = store.get_task(approval.task_id)
        if task is None:
            if message_id:
                patch_card(
                    self._client,
                    message_id,
                    build_error_card(f"审批关联任务不存在：{approval.task_id}"),
                )
            return

        steps: list[ToolStep] = []
        last_patch_time: list[float] = [0.0]
        step_start_time: list[float] = [time.monotonic()]

        def _patch_progress(hint: str, throttle: bool = True) -> None:
            if not message_id:
                return
            now = time.monotonic()
            if throttle and now - last_patch_time[0] < _PATCH_MIN_INTERVAL:
                return
            patch_card(self._client, message_id, build_progress_card(steps, hint))
            last_patch_time[0] = now

        def on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
            if name in _SKIP_TOOLS:
                return
            _patch_progress(format_tool_start_hint(name, tool_input), throttle=False)
            step_start_time[0] = time.monotonic()

        def on_tool_call(name: str, tool_input: dict[str, Any], result: str) -> None:
            if name in _SKIP_TOOLS:
                return
            elapsed_ms = int((time.monotonic() - step_start_time[0]) * 1000)
            step_start_time[0] = time.monotonic()
            steps.append(make_tool_step(name, tool_input, result, elapsed_ms))
            _patch_progress("正在继续处理...", throttle=True)

        try:
            if action == "deny":
                result = self._runner._resolve_approval(
                    task.conversation_id,
                    action="deny",
                    approval_id=approval_id,
                )
                if message_id:
                    patch_card(
                        self._client,
                        message_id,
                        build_approval_resolution_card("deny", approval_id, result.text),
                    )
                return

            result = self._runner._resolve_approval(
                task.conversation_id,
                action="approve",
                approval_id=approval_id,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
            )
            blocked = bool(result.agent_result and result.agent_result.blocked)
            next_approval_id = str(getattr(result.agent_result, "approval_id", "") or "")
            if message_id:
                if blocked and next_approval_id:
                    patch_card(
                        self._client,
                        message_id,
                        build_approval_card(result.text, next_approval_id, steps),
                    )
                elif result.text:
                    patch_card(
                        self._client,
                        message_id,
                        build_result_card_with_process(result.text, steps),
                    )
                else:
                    patch_card(
                        self._client,
                        message_id,
                        build_approval_resolution_card("approve", approval_id, "已通过并执行完成。"),
                    )
        except Exception:
            log.exception("Failed to resolve approval %s from Feishu card action", approval_id)
            if message_id:
                patch_card(
                    self._client,
                    message_id,
                    build_error_card("审批处理失败，请稍后重试"),
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
            return "用户发送了图片。"

        records = []
        for image_key in msg.image_keys:
            try:
                record = self._runner.agent.registry.call(
                    "image_store_from_feishu",
                    {
                        "session_id": session_id,
                        "message_id": msg.message_id,
                        "image_key": image_key,
                    },
                )
                records.append(record)
            except KeyError:
                records.append({"image_id": "", "summary": "", "tags": [], "analysis_status": "tool_missing"})
            except Exception as exc:
                log.warning("image_store_from_feishu_failed", image_key=image_key, error=str(exc))
                records.append({"image_id": "", "summary": "", "tags": [], "analysis_status": str(exc)})

        lines = [f"用户发送了 {len(msg.image_keys)} 张图片。"]
        for index, record in enumerate(records, start=1):
            summary = str(record.get("summary", "")).strip() or "暂无摘要"
            image_id = str(record.get("image_id", "")).strip() or "unknown"
            tags = ", ".join(record.get("tags", [])[:5]) or "无标签"
            lines.append(f"图片{index}（image_id={image_id}）：{summary}；标签：{tags}")
        return "\n".join(lines)


def register(ctx: Any) -> None:
    """Plugin entry point — register the Feishu adapter."""
    ctx.add_adapter(
        AdapterSpec(
            name="feishu",
            description="Feishu (Lark) messaging via WebSocket long connection",
            factory=FeishuAdapter,
        )
    )
