"""CompetitionService — manages competition lifecycle: create, spawn, evaluate, promote."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.execution.competition.evaluator import CompetitionEvaluator
from hermit.kernel.execution.competition.models import CandidateRecord, CompetitionRecord

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.services.controller import TaskController

log = structlog.get_logger()


class CompetitionService:
    """Orchestrates competition-based execution."""

    def __init__(
        self,
        store: KernelStore,
        *,
        task_controller: TaskController,
        workspace_manager: Any = None,
        evaluator: CompetitionEvaluator | None = None,
    ) -> None:
        self._store = store
        self._task_controller = task_controller
        self._workspace_manager = workspace_manager
        self._evaluator = evaluator or CompetitionEvaluator()

    def create_competition(
        self,
        *,
        conversation_id: str,
        goal: str,
        candidate_count: int = 2,
        source_channel: str = "competition",
    ) -> CompetitionRecord:
        """Create a competition and its parent task."""
        ctx = self._task_controller.start_task(
            conversation_id=conversation_id,
            goal=f"Competition: {goal[:60]}",
            source_channel=source_channel,
            kind="competition",
        )
        competition = CompetitionRecord(
            parent_task_id=ctx.task_id,
            conversation_id=conversation_id,
            goal=goal,
            candidate_count=candidate_count,
            status="draft",
        )
        self._store.create_competition(competition)
        return competition

    def spawn_candidates(self, competition_id: str) -> list[str]:
        """Create candidate tasks for a competition."""
        comp = self._store.get_competition(competition_id)
        if comp is None:
            return []

        task_ids: list[str] = []
        for i in range(comp.candidate_count):
            ctx = self._task_controller.start_task(
                conversation_id=comp.conversation_id,
                goal=f"Candidate {i + 1}: {comp.goal[:50]}",
                source_channel="competition",
                kind="execute",
            )
            candidate = CandidateRecord(
                competition_id=competition_id,
                task_id=ctx.task_id,
                status="running",
            )
            self._store.create_candidate(candidate)
            task_ids.append(ctx.task_id)

        self._store.update_competition_status(competition_id, "running")
        return task_ids

    def on_candidate_task_completed(self, task_id: str) -> None:
        """Called when a candidate task finishes; checks if all done and evaluates."""
        candidate = self._store.find_candidate_by_task(task_id)
        if candidate is None:
            return
        comp = self._store.get_competition(candidate.competition_id)
        if comp is None or comp.status != "running":
            return

        candidates = self._store.list_candidates(candidate.competition_id)
        all_done = all(
            self._store.get_task(c.task_id) is not None
            and self._store.get_task(c.task_id).status in ("completed", "failed")  # type: ignore[union-attr]
            for c in candidates
        )
        if not all_done:
            return

        completed_ids = [
            c.task_id
            for c in candidates
            if self._store.get_task(c.task_id) is not None
            and self._store.get_task(c.task_id).status == "completed"  # type: ignore[union-attr]
        ]

        if not completed_ids:
            self._store.update_competition_status(candidate.competition_id, "cancelled")
            return

        results = self._evaluator.evaluate(
            candidate.competition_id,
            completed_ids,
            goal=comp.goal,
        )
        passed = [r for r in results if r.passed]
        if passed:
            winner = passed[0]
            self._store.update_competition_status(
                candidate.competition_id,
                "decided",
                winner_task_id=winner.candidate_id,
            )
        else:
            self._store.update_competition_status(candidate.competition_id, "cancelled")
