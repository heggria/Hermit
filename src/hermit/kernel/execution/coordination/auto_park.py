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
        """Approval granted: re-evaluate focus (resumed task may be higher priority)."""
        scores = self._prioritizer.recalculate_priorities(conversation_id)
        if not scores:
            return

        best = scores[0]
        if best.task_id == resumed_task_id:
            self._store.set_conversation_focus(
                conversation_id, task_id=resumed_task_id, reason="auto_unpark"
            )
            log.info(
                "auto_unpark_focus_switched",
                to_task=resumed_task_id,
            )
