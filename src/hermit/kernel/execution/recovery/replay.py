"""Replay-from capability: reconstruct task state from journal events and
re-execute from a specified step.

The replay creates a **new branch** of events — it does not overwrite or
mutate the original event history.  This is useful for debugging, "what-if"
analysis, and recovery from mid-execution failures.
"""

from __future__ import annotations

import time
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore


def replay_events_until(
    store: KernelStore,
    task_id: str,
    step_id: str,
) -> list[dict[str, Any]]:
    """Return all journal events for *task_id* up to and including events
    referencing *step_id*.

    Events are ordered by ``event_seq ASC``.  The boundary is inclusive:
    all events whose ``step_id`` matches (or is ``None`` / task-level) up to
    the last event referencing the target step are included.
    """
    all_events = store.list_events(task_id=task_id, limit=10000)
    result: list[dict[str, Any]] = []
    found_step = False
    for event in all_events:
        result.append(event)
        if event.get("step_id") == step_id:
            found_step = True
        # Once we've seen the target step and we encounter an event for a
        # *different* step (or a task-level event after it), stop collecting.
        if found_step and event.get("step_id") not in (step_id, None):
            result.pop()
            break
    return result


def replay_from(
    store: KernelStore,
    task_id: str,
    step_id: str,
) -> str:
    """Create a new task branched from *task_id* that re-executes from *step_id*.

    The new task:
    - Copies the original task metadata with a title suffix indicating replay.
    - Marks all steps before *step_id* as ``"skipped"`` (already completed).
    - Creates fresh ``"ready"`` attempts for *step_id* and all downstream steps.
    - Emits a ``replay.started`` event on the new task referencing the original.

    Returns the new task ID.
    """
    original_task = store.get_task(task_id)
    if original_task is None:
        raise ValueError(f"Task {task_id} not found")

    target_step = store.get_step(step_id)
    if target_step is None:
        raise ValueError(f"Step {step_id} not found in task {task_id}")

    # Create the replay task.
    replay_task = store.create_task(
        conversation_id=original_task.conversation_id,
        title=f"[Replay] {original_task.title}",
        goal=original_task.goal,
        source_channel=original_task.source_channel,
        parent_task_id=task_id,
        policy_profile=original_task.policy_profile,
    )
    now = time.time()

    # Collect all steps from the original task.
    all_steps = store.list_steps(task_id=task_id)
    step_by_id = {s.step_id: s for s in all_steps}

    # Determine which steps come before (upstream of) the target step.
    upstream_ids = _collect_upstream(step_id, step_by_id)

    # Map original step_id -> new step_id for dependency rewiring.
    old_to_new: dict[str, str] = {}

    for step in all_steps:
        if step.step_id in upstream_ids:
            # Steps before the replay point are marked as skipped.
            new_step = store.create_step(
                task_id=replay_task.task_id,
                kind=step.kind,
                status="skipped",
                title=step.title or step.kind,
                depends_on=[old_to_new[d] for d in step.depends_on if d in old_to_new],
                node_key=step.node_key,
                max_attempts=step.max_attempts,
            )
            store.update_step(new_step.step_id, status="skipped", finished_at=now)
        else:
            # The target step and all downstream steps get fresh attempts.
            # If all deps are upstream (skipped), clear deps so create_step
            # does not override status to "waiting".
            all_deps_upstream = all(d in upstream_ids for d in step.depends_on)
            if not step.depends_on or all_deps_upstream:
                new_deps: list[str] = []
                initial_status = "ready"
            else:
                new_deps = [old_to_new[d] for d in step.depends_on if d in old_to_new]
                initial_status = "waiting"
            new_step = store.create_step(
                task_id=replay_task.task_id,
                kind=step.kind,
                status=initial_status,
                title=step.title or step.kind,
                depends_on=new_deps,
                node_key=step.node_key,
                max_attempts=step.max_attempts,
            )
            store.create_step_attempt(
                task_id=replay_task.task_id,
                step_id=new_step.step_id,
                status=initial_status,
                context={
                    "ingress_metadata": {
                        "dispatch_mode": "async",
                        "source": "replay",
                        "entry_prompt": step.title or step.kind,
                        "original_task_id": task_id,
                        "original_step_id": step.step_id,
                    },
                },
            )
        old_to_new[step.step_id] = new_step.step_id

    store.append_event(
        event_type="replay.started",
        entity_type="task",
        entity_id=replay_task.task_id,
        task_id=replay_task.task_id,
        actor="kernel",
        payload={
            "original_task_id": task_id,
            "replay_from_step_id": step_id,
            "skipped_step_ids": list(upstream_ids),
        },
    )

    return replay_task.task_id


def _collect_upstream(
    step_id: str,
    step_by_id: dict[str, Any],
) -> set[str]:
    """Return all step IDs that are strict ancestors of *step_id*."""
    visited: set[str] = set()
    stack = list(step_by_id.get(step_id, _Sentinel()).depends_on or [])
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        parent = step_by_id.get(current)
        if parent is not None:
            stack.extend(parent.depends_on or [])
    return visited


class _Sentinel:
    """Fallback for missing step records."""

    depends_on: list[str] = []
