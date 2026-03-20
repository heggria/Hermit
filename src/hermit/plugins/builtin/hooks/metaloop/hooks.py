"""Meta-loop plugin hooks — lifecycle management and subtask completion handling."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.metaloop.backlog import SpecBacklog
from hermit.plugins.builtin.hooks.metaloop.orchestrator import (
    MetaLoopOrchestrator,
    SignalToSpecConsumer,
    SpecBacklogPoller,
)
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_orchestrator: MetaLoopOrchestrator | None = None
_poller: SpecBacklogPoller | None = None
_signal_consumer: SignalToSpecConsumer | None = None


def _get_workspace_root(runner: Any) -> str:
    """Extract workspace root from runner or fall back to cwd."""
    if runner is not None:
        workspace = getattr(runner, "workspace_root", None)
        if workspace:
            return str(workspace)
    import os

    return os.getcwd()


def _on_serve_start(
    *, settings: Any, runner: Any = None, reload_mode: bool = False, **kw: Any
) -> None:
    global _orchestrator, _poller, _signal_consumer

    import os

    enabled = getattr(settings, "metaloop_enabled", None)
    if enabled is None:
        enabled = os.environ.get("HERMIT_METALOOP_ENABLED", "").lower() in ("true", "1", "yes")
    if not bool(enabled):
        log.info("metaloop_disabled")
        return

    if reload_mode and _orchestrator is not None:
        # Hot-swap: update runner reference without recreating
        _orchestrator.set_runner(runner)
        log.info("metaloop_runner_hot_swapped")
        return

    # Obtain the kernel store from the runner
    store = _get_store_from_runner(runner)
    if store is None:
        log.warning("metaloop_no_store")
        return

    # Check if self-iterate schema is available
    if not hasattr(store, "list_spec_backlog"):
        log.warning("metaloop_schema_not_available")
        return

    max_retries = int(getattr(settings, "metaloop_max_retries", 2))
    poll_interval = float(getattr(settings, "metaloop_poll_interval", 5))

    workspace_root = _get_workspace_root(runner)

    _orchestrator = MetaLoopOrchestrator(
        store,
        max_retries=max_retries,
        runner=runner,
        workspace_root=workspace_root,
    )
    backlog = SpecBacklog(store)

    _poller = SpecBacklogPoller(
        _orchestrator,
        backlog,
        poll_interval=poll_interval,
    )
    _poller.start()

    # Gap 3: Start signal-to-spec consumer if store supports signals
    signal_poll = float(getattr(settings, "metaloop_signal_poll_interval", 30))
    if hasattr(store, "actionable_signals") and hasattr(store, "create_spec_entry"):
        _signal_consumer = SignalToSpecConsumer(store, poll_interval=signal_poll)
        _signal_consumer.start()
        log.info("metaloop_signal_consumer_started", poll_interval=signal_poll)

    log.info("metaloop_started", poll_interval=poll_interval, max_retries=max_retries)


def _on_serve_stop(*, reload_mode: bool = False, **kw: Any) -> None:
    global _orchestrator, _poller, _signal_consumer

    if reload_mode:
        return

    if _signal_consumer is not None:
        _signal_consumer.stop()
        _signal_consumer = None

    if _poller is not None:
        _poller.stop()
        _poller = None

    _orchestrator = None
    log.info("metaloop_stopped")


def _on_subtask_complete(
    *, task_id: str = "", success: bool = True, error: str | None = None, **kw: Any
) -> None:
    """Check if a completed subtask belongs to a meta-loop iteration and advance it."""
    if _orchestrator is None:
        return
    if not task_id:
        return

    _orchestrator.on_subtask_complete(task_id, success=success, error=error)


def _get_store_from_runner(runner: Any) -> Any:
    """Extract kernel store from runner, matching the MCP server pattern."""
    if runner is None:
        return None
    task_controller = getattr(runner, "task_controller", None)
    if task_controller is not None:
        return task_controller.store
    return getattr(getattr(runner, "agent", None), "kernel_store", None)


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=20)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=20)
    ctx.add_hook(HookEvent.SUBTASK_COMPLETE, _on_subtask_complete, priority=10)
