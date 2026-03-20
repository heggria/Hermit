"""Benchmark plugin hooks — triggers on SUBTASK_COMPLETE."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.benchmark.learning import IterationLearner
from hermit.plugins.builtin.hooks.benchmark.runner import BenchmarkRunner
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_enabled: bool = False
_timeout: int = 600
_background_tasks: set[asyncio.Task[None]] = set()


def _on_subtask_complete(
    *,
    store: Any = None,
    task_id: str = "",
    step_id: str = "",
    status: str = "",
    result: Any = None,
    settings: Any = None,
    **kw: Any,
) -> None:
    """Fire benchmark + learning when a meta-loop subtask completes."""
    if not _enabled:
        return
    if status != "succeeded":
        log.debug("benchmark_skip_non_success", task_id=task_id, status=status)
        return
    if store is None:
        log.warning("benchmark_no_store")
        return

    meta = getattr(result, "metadata", None) or {}
    iteration_id = meta.get("iteration_id", task_id)
    spec_id = meta.get("spec_id", "")
    worktree_path = meta.get("worktree_path")

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _run_benchmark_and_learn(
                store,
                iteration_id,
                spec_id,
                worktree_path,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        asyncio.run(
            _run_benchmark_and_learn(
                store,
                iteration_id,
                spec_id,
                worktree_path,
            )
        )


async def _run_benchmark_and_learn(
    store: Any,
    iteration_id: str,
    spec_id: str,
    worktree_path: str | None,
) -> None:
    runner = BenchmarkRunner(store=store, timeout=_timeout)
    result = await runner.run(iteration_id, spec_id, worktree_path)

    learner = IterationLearner(store=store)
    lessons = await learner.learn(iteration_id, result)

    log.info(
        "benchmark_cycle_done",
        iteration_id=iteration_id,
        passed=result.check_passed,
        lessons=len(lessons),
    )


def register(ctx: PluginContext) -> None:
    global _enabled, _timeout
    _enabled = bool(ctx.get_var("benchmark_enabled", False))
    _timeout = int(ctx.get_var("benchmark_check_timeout", 600))
    ctx.add_hook(HookEvent.SUBTASK_COMPLETE, _on_subtask_complete, priority=22)
    log.info("benchmark_plugin_registered", enabled=_enabled)
