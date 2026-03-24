from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from hermit.kernel.execution.coordination.prioritizer import TaskPrioritizer
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


class AutoParkService:
    """Handles task parking and automatic focus switching."""

    def __init__(self, store: KernelStore, prioritizer: TaskPrioritizer) -> None:
        self._store = store
        self._prioritizer = prioritizer

    def on_task_parked(self, conversation_id: str, parked_task_id: str) -> str | None:
        """Task suspended: find next best candidate, switch focus, return new focus task_id."""
        candidate = self._prioritizer.best_candidate_after_park(parked_task_id, conversation_id)
        if candidate is None:
            log.info("auto_park_no_candidate", parked_task_id=parked_task_id)
            return None

        self._store.set_conversation_focus(conversation_id, task_id=candidate, reason="auto_park")
        log.info(
            "auto_park_focus_switched",
            from_task=parked_task_id,
            to_task=candidate,
        )
        return candidate

    def on_task_unparked(self, conversation_id: str, resumed_task_id: str) -> None:
        """Approval granted: restore focus to the resumed task.

        The resumed task is always given focus because it represents an explicit
        user intent (approval was just granted).  A subsequent scheduling cycle
        will re-evaluate priorities and may switch focus again if a higher-priority
        task is still runnable.
        """
        scores = self._prioritizer.recalculate_priorities(conversation_id)

        if scores and scores[0].task_id != resumed_task_id:
            log.info(
                "auto_unpark_higher_priority_exists",
                resumed_task=resumed_task_id,
                highest_priority_task=scores[0].task_id,
                note="Restoring focus to resumed task; scheduler will re-evaluate.",
            )

        self._store.set_conversation_focus(
            conversation_id, task_id=resumed_task_id, reason="auto_unpark"
        )
        log.info(
            "auto_unpark_focus_switched",
            to_task=resumed_task_id,
        )
