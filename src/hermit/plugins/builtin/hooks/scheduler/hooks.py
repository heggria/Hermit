"""Scheduler plugin hooks — lifecycle management and result logging."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.scheduler.engine import SchedulerEngine
from hermit.plugins.builtin.hooks.scheduler.tools import set_engine
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_engine: SchedulerEngine | None = None
_hooks_ref: Any = None


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    global _engine
    if not bool(getattr(settings, "scheduler_enabled", True)):
        log.info("scheduler_disabled")
        return

    if _hooks_ref is None:
        log.warning("scheduler_no_hooks_engine")
        return

    catch_up = bool(getattr(settings, "scheduler_catch_up", True))

    _engine = SchedulerEngine(settings, _hooks_ref)
    if runner is not None:
        _engine.set_runner(runner)
    set_engine(_engine)
    _engine.start(catch_up=catch_up)


def _on_serve_stop(**kw: Any) -> None:
    global _engine
    if _engine is not None:
        _engine.stop()
        _engine = None
        set_engine(None)  # type: ignore[arg-type]


def register(ctx: PluginContext) -> None:
    global _hooks_ref
    _hooks_ref = ctx._hooks  # pyright: ignore[reportPrivateUsage]

    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=10)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=10)
