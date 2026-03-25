"""WebUI plugin lifecycle hooks — starts/stops the HTTP server on serve events."""

from __future__ import annotations

import logging
import threading
from typing import Any

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)

_server: Any = None
_server_lock = threading.Lock()


def _on_serve_start(
    *, settings: Any, runner: Any = None, reload_mode: bool = False, **kw: Any
) -> None:
    global _server

    if not bool(getattr(settings, "webui_enabled", True)):
        _log.info("webui_disabled")
        return

    with _server_lock:
        if reload_mode and _server is not None:
            _server.swap_runner(runner)
            _log.info("webui_runner_hot_swapped")
            return

        from hermit.plugins.builtin.hooks.webui.server import WebUIServer

        host = str(getattr(settings, "webui_host", "127.0.0.1"))
        port = int(getattr(settings, "webui_port", 8323))
        open_browser = bool(getattr(settings, "webui_open_browser", True))

        _server = WebUIServer(host=host, port=port, open_browser=open_browser)
        _server.start(runner)


def _on_serve_stop(*, reload_mode: bool = False, **kw: Any) -> None:
    global _server
    if reload_mode:
        return
    with _server_lock:
        if _server is not None:
            _server.stop()
            _server = None


def _on_tool_start(*, task_id: str, tool_name: str, input_summary: str, **kw: Any) -> None:
    from hermit.plugins.builtin.hooks.webui.api import tool_activity

    tool_activity.set_active(task_id, tool_name, input_summary)


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=30)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=30)
    ctx.add_hook(HookEvent.TOOL_START, _on_tool_start, priority=50)
