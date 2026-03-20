"""Quality plugin hooks — DISPATCH_RESULT review trigger."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()


async def _on_dispatch_result(*, result: Any = None, **kw: Any) -> None:
    """Triggered after a step dispatch completes.

    Placeholder for v0.3 — will run GovernedReviewer on changed files
    from the dispatch result and append findings to the step record.
    """
    log.debug("quality.dispatch_result_hook", has_result=result is not None)


def register(ctx: PluginContext) -> None:
    """Register quality hooks at priority 18."""
    ctx.add_hook(HookEvent.DISPATCH_RESULT, _on_dispatch_result, priority=18)
