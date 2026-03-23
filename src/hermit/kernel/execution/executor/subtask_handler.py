"""SubtaskSpawner — delegate handler for kernel-managed subtask spawning.

When a tool result contains the ``_hermit_spawn_subtasks`` envelope key,
``ToolExecutor.execute()`` intercepts it and calls
``SubtaskSpawner.handle_spawn()``.  The spawner creates child steps under the
current task, records a ``subtask.spawned`` audit event, marks the parent
step-attempt as ``awaiting_subtasks`` (suspended), and returns a
``ToolExecutionResult`` with ``waiting_kind="awaiting_subtasks"``.

Each descriptor in the spawn list supports:

    {
        "tool_name": str,          # required — child step kind / tool
        "tool_input": dict,        # optional — forwarded as step context
        "join_strategy": str,      # optional — default "all_required"
        "title": str,              # optional — human-readable label
    }

The envelope key used by tool handlers to signal a spawn request::

    {"_hermit_spawn_subtasks": [<descriptor>, ...]}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore

if TYPE_CHECKING:
    from hermit.kernel.execution.executor.executor import ToolExecutionResult

_SPAWN_ENVELOPE_KEY = "_hermit_spawn_subtasks"

# Valid join strategies accepted from descriptors.
_VALID_STRATEGIES = frozenset({"all_required", "any_sufficient", "majority", "best_effort"})
_DEFAULT_STRATEGY = "all_required"


def normalize_spawn_descriptors(value: Any) -> list[dict[str, Any]] | None:
    """Extract and normalise a list of subtask descriptors from a raw tool result.

    Returns ``None`` when *value* does not carry the spawn envelope key or
    when the descriptor list is empty/invalid.
    """
    if not isinstance(value, dict):
        return None
    typed_value: dict[str, Any] = cast(dict[str, Any], value)
    raw = typed_value.get(_SPAWN_ENVELOPE_KEY)
    if not isinstance(raw, list) or not raw:
        return None
    raw_list: list[Any] = cast(list[Any], raw)
    descriptors: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        typed_item: dict[str, Any] = cast(dict[str, Any], item)
        tool_name = str(typed_item.get("tool_name", "") or "").strip()
        if not tool_name:
            continue
        strategy = str(typed_item.get("join_strategy", "") or "").strip()
        if strategy not in _VALID_STRATEGIES:
            strategy = _DEFAULT_STRATEGY
        descriptor: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_input": dict(typed_item.get("tool_input", {}) or {}),
            "join_strategy": strategy,
            "title": str(typed_item.get("title", "") or tool_name).strip(),
        }
        descriptors.append(descriptor)
    return descriptors if descriptors else None


class SubtaskSpawner:
    """Create kernel-tracked child steps and suspend the parent step-attempt."""

    def __init__(self, *, store: KernelStore, executor: Any) -> None:
        self.store = store
        self._executor = executor

    # ------------------------------------------------------------------
    # Public API (called from ToolExecutor)
    # ------------------------------------------------------------------

    def handle_spawn(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        descriptors: list[dict[str, Any]],
    ) -> ToolExecutionResult:
        """Create child steps, suspend the parent, and return a ToolExecutionResult.

        Parameters
        ----------
        attempt_ctx:
            Execution context of the *parent* step-attempt.
        descriptors:
            Normalised list of subtask descriptors from ``normalize_spawn_descriptors()``.
        """
        from hermit.kernel.execution.executor.executor import ToolExecutionResult

        child_step_ids = self._spawn_children(attempt_ctx, descriptors)

        # Suspend parent
        self._executor._set_attempt_phase(
            attempt_ctx, "awaiting_subtasks", reason="subtask_spawned"
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="awaiting_subtasks",
            status_reason=f"Spawned {len(child_step_ids)} child step(s)",
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")

        self.store.append_event(
            event_type="subtask.spawned",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "child_step_ids": child_step_ids,
                "count": len(child_step_ids),
            },
        )

        summary = f"[Spawned {len(child_step_ids)} subtask(s)] Waiting for child steps to complete."
        return ToolExecutionResult(
            model_content=summary,
            raw_result={"child_step_ids": child_step_ids},
            blocked=True,
            suspended=True,
            waiting_kind="awaiting_subtasks",
            result_code="subtasks_spawned",
            execution_status="awaiting_subtasks",
            state_applied=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spawn_children(
        self,
        attempt_ctx: TaskExecutionContext,
        descriptors: list[dict[str, Any]],
    ) -> list[str]:
        """Create one child step per descriptor and return their step_ids."""
        child_step_ids: list[str] = []
        for desc in descriptors:
            child_step = self.store.create_step(
                task_id=attempt_ctx.task_id,
                kind=desc["tool_name"],
                status="ready",
                title=desc["title"],
                join_strategy=desc["join_strategy"],
            )
            # Persist the tool_input as context on a new attempt for the child step.
            self.store.create_step_attempt(
                task_id=attempt_ctx.task_id,
                step_id=child_step.step_id,
                attempt=1,
                status="ready",
                context={"tool_input": desc["tool_input"]},
            )
            child_step_ids.append(child_step.step_id)
        return child_step_ids
