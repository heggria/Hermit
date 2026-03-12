"""Webhook plugin lifecycle hooks — starts/stops the HTTP server on serve events."""
from __future__ import annotations

import logging
from typing import Any

from hermit.plugin.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)

_server: Any = None
_hooks_ref: Any = None


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    global _server

    if not bool(getattr(settings, "webhook_enabled", True)):
        _log.info("webhook_disabled")
        return

    from hermit.builtin.webhook.models import load_config
    from hermit.builtin.webhook.server import WebhookServer

    config = load_config(settings)
    if not config.routes and not config.control_secret:
        _log.info("webhook_no_routes_configured")
        return

    _server = WebhookServer(config, _hooks_ref)
    _server.start(runner)


def _on_serve_stop(**kw: Any) -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def register(ctx: PluginContext) -> None:
    global _hooks_ref
    _hooks_ref = ctx._hooks

    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=20)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=20)
