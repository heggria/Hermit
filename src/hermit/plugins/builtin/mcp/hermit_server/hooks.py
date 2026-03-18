"""MCP Server plugin lifecycle hooks — starts/stops the MCP server on serve events."""

from __future__ import annotations

import logging
from typing import Any

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)

_server: Any = None


def _on_serve_start(
    *, settings: Any, runner: Any = None, reload_mode: bool = False, **kw: Any
) -> None:
    global _server

    if not bool(getattr(settings, "mcp_server_enabled", False)):
        _log.info("mcp_server_disabled")
        return

    if reload_mode and _server is not None:
        _server.swap_runner(runner)
        _log.info("mcp_server_runner_hot_swapped")
        return

    from hermit.plugins.builtin.mcp.hermit_server.server import HermitMcpServer

    host = str(getattr(settings, "mcp_server_host", "127.0.0.1"))
    port = int(getattr(settings, "mcp_server_port", 8322))
    _server = HermitMcpServer(host=host, port=port)
    _server.start(runner)


def _on_serve_stop(*, reload_mode: bool = False, **kw: Any) -> None:
    global _server
    if reload_mode:
        return
    if _server is not None:
        _server.stop()
        _server = None


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=25)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=25)
