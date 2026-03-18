"""Patrol plugin hooks — SERVE_START / SERVE_STOP lifecycle."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.patrol.engine import PatrolEngine
from hermit.plugins.builtin.hooks.patrol.tools import set_engine
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_engine: PatrolEngine | None = None


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    global _engine
    if not bool(getattr(settings, "patrol_enabled", False)):
        log.info("patrol_disabled")
        return

    interval = int(getattr(settings, "patrol_interval_minutes", 60))
    checks = str(getattr(settings, "patrol_checks", "lint,test,todo_scan"))
    workspace = str(getattr(settings, "workspace_root", "") or "")

    _engine = PatrolEngine(
        interval_minutes=interval,
        enabled_checks=checks,
        workspace_root=workspace,
    )
    if runner is not None:
        _engine.set_runner(runner)
    set_engine(_engine)
    _engine.start()


def _on_serve_stop(**kw: Any) -> None:
    global _engine
    if _engine is not None:
        _engine.stop()
        _engine = None
        set_engine(None)  # type: ignore[arg-type]


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=15)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=15)
