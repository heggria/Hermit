"""Research plugin hooks — SERVE_START lifecycle."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.research.pipeline import ResearchPipeline
from hermit.plugins.builtin.hooks.research.strategies import (
    CodebaseStrategy,
    DocStrategy,
    GitHistoryStrategy,
    WebStrategy,
)
from hermit.plugins.builtin.hooks.research.tools import set_pipeline
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_pipeline: ResearchPipeline | None = None


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    global _pipeline
    if not bool(getattr(settings, "research_enabled", True)):
        log.info("research_disabled")
        return

    import os

    # Resolve workspace: settings → runner → env → cwd
    workspace = str(getattr(settings, "workspace_root", "") or "")
    if not workspace and runner is not None:
        workspace = str(getattr(runner, "workspace_root", "") or "")
    if not workspace:
        workspace = os.environ.get("HERMIT_WORKSPACE_ROOT", "")
    if not workspace:
        workspace = os.getcwd()

    web_enabled = bool(getattr(settings, "research_web_enabled", True))
    max_findings = int(getattr(settings, "research_max_findings", 20))

    strategies: list[Any] = [
        CodebaseStrategy(workspace=workspace),
        WebStrategy(enabled=web_enabled),
        DocStrategy(enabled=web_enabled),
        GitHistoryStrategy(workspace=workspace),
    ]

    _pipeline = ResearchPipeline(strategies=strategies, max_findings=max_findings)
    set_pipeline(_pipeline)
    log.info("research_pipeline_initialized", web_enabled=web_enabled, max_findings=max_findings)


def _on_serve_stop(**kw: Any) -> None:
    global _pipeline
    _pipeline = None
    set_pipeline(None)  # type: ignore[arg-type]


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=20)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=20)
