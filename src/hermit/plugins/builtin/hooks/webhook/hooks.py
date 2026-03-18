"""Webhook plugin lifecycle hooks — starts/stops the HTTP server on serve events."""

from __future__ import annotations

import logging
from typing import Any

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)

_server: Any = None
_hooks_ref: Any = None


def _on_serve_start(
    *, settings: Any, runner: Any = None, reload_mode: bool = False, **kw: Any
) -> None:
    global _server

    if not bool(getattr(settings, "webhook_enabled", True)):
        _log.info("webhook_disabled")
        return

    from hermit.plugins.builtin.hooks.webhook.models import load_config
    from hermit.plugins.builtin.hooks.webhook.server import WebhookServer

    config = load_config(settings)
    if not config.routes and not config.control_secret:
        _log.info("webhook_no_routes_configured")
        return

    if reload_mode and _server is not None:
        # Hot-swap: keep HTTP server alive, just replace the runner reference
        _server.swap_runner(runner)
        _log.info("webhook_runner_hot_swapped")
        return

    _server = WebhookServer(config, _hooks_ref)
    _server.start(runner)


def _on_serve_stop(*, reload_mode: bool = False, **kw: Any) -> None:
    global _server
    if reload_mode:
        # During reload, keep the webhook server running
        return
    if _server is not None:
        _server.stop()
        _server = None


def register(ctx: PluginContext) -> None:
    global _hooks_ref
    _hooks_ref = ctx._hooks  # pyright: ignore[reportPrivateUsage]

    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=20)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=20)
