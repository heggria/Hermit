from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_engine: TriggerEngine | None = None


def _on_serve_start(*, runner: Any = None, **kw: Any) -> None:
    if _engine is not None and runner is not None:
        _engine.set_runner(runner)
        log.info("trigger_runner_attached")


def _on_post_run(result: Any, session_id: str = "", **kwargs: Any) -> None:
    if _engine is None:
        return
    _engine.analyze_and_dispatch(result, session_id=session_id, **kwargs)


def register(ctx: PluginContext) -> None:
    global _engine
    enabled = bool(ctx.get_var("trigger_enabled", True))
    cooldown = int(ctx.get_var("trigger_cooldown_seconds", 86400))
    max_tasks = int(ctx.get_var("trigger_max_tasks_per_run", 3))
    if not enabled:
        log.info("trigger_disabled")
        return
    _engine = TriggerEngine(cooldown_seconds=cooldown, max_tasks_per_run=max_tasks)
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=25)
    ctx.add_hook(HookEvent.POST_RUN, _on_post_run, priority=30)
