"""Overnight plugin lifecycle hooks -- registers on SERVE_START."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    if not bool(getattr(settings, "overnight_enabled", True)):
        log.info("overnight_disabled")
        return
    log.info("overnight_plugin_ready")


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=20)
