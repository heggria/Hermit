"""WebSocket chat endpoint — bridges async WebSocket I/O to the synchronous AgentRunner."""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner, get_store

_log = structlog.get_logger()

router = APIRouter()

_SENDER_TIMEOUT = 30.0
_RESULT_TRUNCATE = 2000


@router.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket) -> None:
    """Interactive chat over WebSocket.

    The runner's ``dispatch()`` is synchronous and blocks in a thread.
    We bridge it to the async WebSocket world via an ``asyncio.Queue``
    (same pattern as the TUI ``RunnerBridge``).
    """
    await websocket.accept()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    session_id = f"webui-chat-{uuid4().hex[:8]}"
    active = True

    def _enqueue(msg: dict[str, Any]) -> None:
        """Thread-safe helper: push a message onto the async queue."""
        if active:
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    # ------------------------------------------------------------------
    # Sender coroutine — drains queue and writes to the WebSocket
    # ------------------------------------------------------------------
    async def sender() -> None:
        nonlocal active
        try:
            while active:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=_SENDER_TIMEOUT)
                    await websocket.send_json(msg)
                except TimeoutError:
                    # Keep the connection alive with a heartbeat
                    try:
                        await websocket.send_json({"type": "heartbeat"})
                    except Exception:
                        active = False
        except (WebSocketDisconnect, asyncio.CancelledError):
            active = False

    sender_task = asyncio.create_task(sender())

    # ------------------------------------------------------------------
    # Receiver loop — reads client messages and dispatches accordingly
    # ------------------------------------------------------------------
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "message":
                text = data.get("text", "").strip()
                if not text:
                    continue

                def run_dispatch(user_text: str = text) -> None:
                    try:
                        runner = get_runner()

                        def on_tool_start(name: str, inputs: dict[str, Any]) -> None:
                            _enqueue(
                                {
                                    "type": "tool_start",
                                    "name": name,
                                    "inputs": inputs if isinstance(inputs, dict) else {},
                                }
                            )

                        def on_tool_call(
                            name: str,
                            _inputs: dict[str, Any],
                            result: Any,
                        ) -> None:
                            _enqueue(
                                {
                                    "type": "tool_complete",
                                    "name": name,
                                    "result": str(result)[:_RESULT_TRUNCATE],
                                }
                            )

                        dispatch_result = runner.dispatch(
                            session_id,
                            user_text,
                            on_tool_call=on_tool_call,
                            on_tool_start=on_tool_start,
                        )
                        _enqueue(
                            {
                                "type": "response",
                                "text": dispatch_result.text or "",
                                "is_command": dispatch_result.is_command,
                            }
                        )
                    except Exception as exc:
                        _log.warning(  # type: ignore[call-arg]
                            "webui_chat_dispatch_error",
                            error=str(exc),
                        )
                        _enqueue({"type": "error", "message": str(exc)})

                threading.Thread(target=run_dispatch, daemon=True).start()

            elif msg_type == "approve":
                approval_id = data.get("approval_id", "")
                if not approval_id:
                    continue

                def run_approve(aid: str = approval_id) -> None:
                    try:
                        runner = get_runner()
                        store = get_store()
                        approval = store.get_approval(aid)
                        if approval is None:
                            _enqueue(
                                {
                                    "type": "error",
                                    "message": f"Approval {aid} not found",
                                }
                            )
                            return

                        task = store.get_task(approval.task_id)
                        if task is None:
                            _enqueue(
                                {
                                    "type": "error",
                                    "message": f"Task {approval.task_id} not found",
                                }
                            )
                            return

                        result = runner._resolve_approval(
                            task.conversation_id,
                            action="approve",
                            approval_id=aid,
                        )
                        _enqueue(
                            {
                                "type": "approved",
                                "approval_id": aid,
                                "text": result.text or "",
                            }
                        )
                    except Exception as exc:
                        _log.warning(  # type: ignore[call-arg]
                            "webui_chat_approve_error",
                            error=str(exc),
                        )
                        _enqueue({"type": "error", "message": str(exc)})

                threading.Thread(target=run_approve, daemon=True).start()

    except WebSocketDisconnect:
        _log.debug("webui_chat_disconnected", session_id=session_id)  # type: ignore[call-arg]
    except Exception:
        _log.exception("webui_chat_unexpected_error")  # type: ignore[call-arg]
    finally:
        active = False
        sender_task.cancel()
        # Best-effort session cleanup
        try:
            runner = get_runner()
            runner.close_session(session_id)
        except Exception:
            pass
