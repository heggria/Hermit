"""Memory injection hooks: SYSTEM_PROMPT and PRE_RUN context injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.compiler.compiler import ContextCompiler
from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.models.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.task.services.planning import PlanningService
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

log = structlog.get_logger()

_GOVERNANCE = MemoryGovernanceService()


def inject_memory(engine: MemoryEngine, settings: Any | None = None) -> str:
    """Build static memory context for SYSTEM_PROMPT injection."""
    compiler_result = _compile_context_pack(
        engine,
        settings,
        query="",
        conversation_id=None,
        runner=None,
    )
    if compiler_result is None:
        categories = _knowledge_categories(engine, settings)
        static_categories = _GOVERNANCE.filter_static_categories(categories)
        prompt = engine.summary_prompt(static_categories, limit_per_category=3)
        entry_count = sum(min(3, len(entries)) for entries in static_categories.values() if entries)
        category_count = sum(1 for entries in static_categories.values() if entries)
    else:
        prompt = compiler_result["static_prompt"]
        entry_count = len(compiler_result["pack"].static_memory)
        category_count = len({item["category"] for item in compiler_result["pack"].static_memory})
    if not prompt:
        log.info("memory_injected", categories=0, entries=0)
        return ""
    log.info("memory_injected", categories=category_count, entries=entry_count)
    return f"<memory_context>\n{prompt}\n</memory_context>"


def inject_relevant_memory(
    engine: MemoryEngine,
    settings: Any | str | None,
    prompt: str | None = None,
    session_id: str | None = None,
    runner: Any | None = None,
    **_: Any,
) -> str:
    """Build retrieval memory context for PRE_RUN injection."""
    if prompt is None:
        prompt = str(settings or "")
        settings = None
    compiler_result = _compile_context_pack(
        engine,
        settings,
        query=prompt,
        conversation_id=session_id,
        runner=runner,
    )
    if compiler_result is None:
        relevant = ""
    else:
        relevant = compiler_result["retrieval_prompt"]
    if not relevant:
        return prompt
    return f"<relevant_memory>\n{relevant}\n</relevant_memory>\n\n{prompt}"


def _knowledge_categories(
    engine: MemoryEngine, settings: Any | None
) -> dict[str, list[MemoryEntry]]:
    if settings is None:
        return {}
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    if not kernel_db_path:
        return {}
    from hermit.kernel.context.memory.knowledge import MemoryRecordService
    from hermit.kernel.ledger.journal.store import KernelStore

    store = KernelStore(Path(kernel_db_path))
    try:
        service = MemoryRecordService(store, mirror_path=Path(settings.memory_file))
        return service.active_categories()
    finally:
        store.close()


def _compile_context_pack(
    engine: MemoryEngine,
    settings: Any | None,
    *,
    query: str,
    conversation_id: str | None,
    runner: Any | None,
) -> dict[str, Any] | None:
    if settings is None:
        return None
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    if not kernel_db_path:
        return None
    from hermit.kernel.ledger.journal.store import KernelStore

    artifact_store = None
    kernel_artifacts_dir = getattr(settings, "kernel_artifacts_dir", None)
    if kernel_artifacts_dir:
        artifact_store = ArtifactStore(Path(kernel_artifacts_dir))
    store = KernelStore(Path(kernel_db_path))
    try:
        task_id = ""
        if (
            runner is not None
            and conversation_id
            and getattr(runner, "task_controller", None) is not None
        ):
            active_task = runner.task_controller.active_task_for_conversation(conversation_id)
            if active_task is not None:
                task_id = active_task.task_id
        workspace_root = str(Path(settings.memory_file).parent)
        ctx = TaskExecutionContext(
            conversation_id=conversation_id or "memory-system",
            task_id=task_id,
            step_id="context_pack",
            step_attempt_id="context_pack",
            source_channel="memory",
            workspace_root=workspace_root,
        )
        compiler = ContextCompiler(_GOVERNANCE, artifact_store)
        planning = PlanningService(store, artifact_store)
        planning_state = planning.state_for_task(task_id) if task_id else None
        pack = compiler.compile(
            context=ctx,
            working_state=WorkingStateSnapshot(
                goal_summary=query[:400],
                planning_mode=bool(planning_state.planning_mode) if planning_state else False,
                candidate_plan_refs=list(planning_state.candidate_plan_refs)
                if planning_state
                else [],
                selected_plan_ref=str(planning_state.selected_plan_ref or "")
                if planning_state
                else "",
                plan_status=str(planning_state.plan_status or "none") if planning_state else "none",
            ),
            beliefs=store.list_beliefs(status="active", limit=200),
            memories=store.list_memory_records(
                status="active", conversation_id=conversation_id, limit=500
            ),
            query=query,
        )
        if artifact_store is not None and pack.artifact_uri is not None:
            artifact = store.create_artifact(
                task_id=task_id or None,
                step_id=None,
                kind="context.pack/v3",
                uri=pack.artifact_uri,
                content_hash=str(pack.artifact_hash or pack.pack_hash),
                producer="memory_hook",
                retention_class="audit",
                trust_tier="derived",
                metadata={"pack_hash": pack.pack_hash, "conversation_id": conversation_id or ""},
            )
            if task_id:
                store.append_event(
                    event_type="context.pack.compiled",
                    entity_type="task",
                    entity_id=task_id,
                    task_id=task_id,
                    actor="kernel",
                    payload={
                        "artifact_ref": artifact.artifact_id,
                        "pack_hash": pack.pack_hash,
                        "kind": "context.pack/v3",
                    },
                )
        return {
            "pack": pack,
            "static_prompt": compiler.render_static_prompt(pack),
            "retrieval_prompt": compiler.render_retrieval_prompt(pack),
        }
    finally:
        store.close()


__all__ = ["inject_memory", "inject_relevant_memory"]
