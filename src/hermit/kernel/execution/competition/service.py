from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.execution.competition.evaluator import CompetitionEvaluator
from hermit.kernel.execution.competition.models import (
    CandidateScore,
    CompetitionRecord,
)
from hermit.kernel.execution.competition.workspace import CompetitionWorkspaceManager

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.services.controller import TaskController

logger = structlog.get_logger()


class CompetitionService:
    """Orchestrates competitive execution: spawn → evaluate → select → promote."""

    def __init__(
        self,
        store: KernelStore,
        task_controller: TaskController | None = None,
        workspace_manager: CompetitionWorkspaceManager | None = None,
        evaluator: CompetitionEvaluator | None = None,
    ) -> None:
        self.store = store
        self._task_controller = task_controller
        self._workspace = workspace_manager
        self._evaluator = evaluator or CompetitionEvaluator()

    # -- Public API -----------------------------------------------------------

    def create_competition(
        self,
        *,
        conversation_id: str,
        goal: str,
        candidate_count: int,
        evaluation_criteria: dict[str, Any] | None = None,
        scoring_weights: dict[str, float] | None = None,
        min_candidates: int = 1,
        timeout_policy: str = "evaluate_completed",
        timeout_seconds: float | None = None,
        source_channel: str = "competition",
        requested_by: str | None = None,
    ) -> CompetitionRecord:
        """Create a parent task and competition record."""
        parent_task = self.store.create_task(
            conversation_id=conversation_id,
            title=f"Competition: {goal[:60]}",
            goal=goal,
            source_channel=source_channel,
            requested_by=requested_by,
        )
        competition = self.store.create_competition(
            parent_task_id=parent_task.task_id,
            goal=goal,
            candidate_count=candidate_count,
            min_candidates=min_candidates,
            evaluation_criteria=evaluation_criteria,
            scoring_weights=scoring_weights,
            timeout_policy=timeout_policy,
            timeout_seconds=timeout_seconds,
        )
        self.store.append_event(
            event_type="competition.created",
            entity_type="competition",
            entity_id=competition.competition_id,
            task_id=parent_task.task_id,
            payload={
                "competition_id": competition.competition_id,
                "candidate_count": candidate_count,
                "strategy": competition.strategy,
            },
        )
        logger.info(
            "competition.created",
            competition_id=competition.competition_id,
            parent_task_id=parent_task.task_id,
            candidate_count=candidate_count,
        )
        return competition

    def spawn_candidates(self, competition_id: str) -> list[str]:
        """Create worktrees and child tasks for each candidate."""
        competition = self.store.get_competition(competition_id)
        if competition is None:
            raise ValueError(f"Competition not found: {competition_id}")

        self.store.update_competition_status(competition_id, "spawning")

        candidate_ids: list[str] = []
        for i in range(competition.candidate_count):
            label = f"candidate_{i + 1}"
            workspace_ref: str | None = None
            if self._workspace is not None:
                workspace_ref = self._workspace.create_workspace(competition_id, label)

            # Create child task via TaskController or direct store
            if self._task_controller is not None:
                ctx = self._task_controller.enqueue_task(
                    conversation_id=self._get_conversation_id(competition),
                    goal=competition.goal,
                    source_channel="competition",
                    kind="competition_candidate",
                    parent_task_id=competition.parent_task_id,
                    workspace_root=workspace_ref or "",
                )
                child_task_id = ctx.task_id
            else:
                child_task = self.store.create_task(
                    conversation_id=self._get_conversation_id(competition),
                    title=f"{competition.goal[:50]} [{label}]",
                    goal=competition.goal,
                    source_channel="competition",
                    parent_task_id=competition.parent_task_id,
                )
                child_task_id = child_task.task_id

            candidate = self.store.create_candidate(
                competition_id=competition_id,
                task_id=child_task_id,
                label=label,
                workspace_ref=workspace_ref,
            )
            self.store.update_candidate_status(candidate.candidate_id, "running")
            self.store.append_event(
                event_type="candidate.started",
                entity_type="competition_candidate",
                entity_id=candidate.candidate_id,
                task_id=child_task_id,
                payload={
                    "task_id": child_task_id,
                    "workspace_ref": workspace_ref,
                },
            )
            candidate_ids.append(candidate.candidate_id)

        self.store.update_competition_status(competition_id, "running")
        self.store.append_event(
            event_type="competition.running",
            entity_type="competition",
            entity_id=competition_id,
            task_id=competition.parent_task_id,
            payload={"candidate_ids": candidate_ids},
        )
        logger.info(
            "competition.candidates_spawned",
            competition_id=competition_id,
            count=len(candidate_ids),
        )
        return candidate_ids

    def on_candidate_task_completed(self, task_id: str) -> None:
        """Called via DISPATCH_RESULT hook when a candidate task finishes."""
        candidate = self.store.find_candidate_by_task(task_id)
        if candidate is None:
            return

        competition = self.store.get_competition(candidate.competition_id)
        if competition is None or competition.status != "running":
            return

        # Update candidate status based on task outcome
        task = self.store.get_task(task_id)
        if task is None:
            return

        if task.status == "completed":
            self.store.update_candidate_status(candidate.candidate_id, "completed")
            self.store.append_event(
                event_type="candidate.completed",
                entity_type="competition_candidate",
                entity_id=candidate.candidate_id,
                task_id=task_id,
                payload={"task_id": task_id},
            )
        elif task.status in ("failed", "cancelled"):
            self.store.update_candidate_status(candidate.candidate_id, "failed")
            self.store.append_event(
                event_type="candidate.failed",
                entity_type="competition_candidate",
                entity_id=candidate.candidate_id,
                task_id=task_id,
                payload={"task_id": task_id, "task_status": task.status},
            )

        # Re-read competition status to guard against concurrent evaluation triggers.
        # Another thread may have already transitioned this competition past "running".
        competition = self.store.get_competition(candidate.competition_id)
        if competition is None or competition.status != "running":
            return

        # Check whether we should trigger evaluation
        candidates = self.store.list_candidates(candidate.competition_id)
        completed = [c for c in candidates if c.status == "completed"]
        terminal = [c for c in candidates if c.status in ("completed", "failed", "disqualified")]

        if len(terminal) == len(candidates):
            if not completed:
                self.cancel_competition(candidate.competition_id, reason="all_candidates_failed")
            else:
                self.trigger_evaluation(candidate.competition_id)
        elif len(completed) >= competition.min_candidates:
            # Check timeout
            elapsed = time.time() - competition.created_at
            if competition.timeout_seconds is not None and elapsed >= competition.timeout_seconds:
                if competition.timeout_policy == "evaluate_completed":
                    self.trigger_evaluation(candidate.competition_id)
                else:
                    self.cancel_competition(candidate.competition_id, reason="timeout")

    def trigger_evaluation(self, competition_id: str) -> None:
        """Run evaluation on all completed candidates.

        Idempotent: silently returns if the competition is no longer in "running" state
        (e.g. another thread already triggered evaluation).
        """
        competition = self.store.get_competition(competition_id)
        if competition is None or competition.status != "running":
            return
        self.store.update_competition_status(competition_id, "evaluating")

        candidates = self.store.list_candidates(competition_id, status="completed")
        self.store.append_event(
            event_type="competition.evaluating",
            entity_type="competition",
            entity_id=competition_id,
            task_id=competition.parent_task_id,
            payload={"completed_candidates": [c.candidate_id for c in candidates]},
        )

        scores = self._evaluator.evaluate(competition, candidates)

        # Persist scores
        for score in scores:
            self.store.update_candidate_score(
                score.candidate_id,
                score=score.total,
                score_breakdown=score.breakdown,
            )
            self.store.append_event(
                event_type="candidate.evaluated",
                entity_type="competition_candidate",
                entity_id=score.candidate_id,
                task_id=score.task_id,
                payload={"score": score.total, "score_breakdown": score.breakdown},
            )

        # Disqualify candidates that didn't pass minimum standards
        for score in scores:
            if not score.passed:
                self.store.update_candidate_status(
                    score.candidate_id,
                    "disqualified",
                    discard_reason="below_minimum_standards",
                )
                self.store.append_event(
                    event_type="candidate.discarded",
                    entity_type="competition_candidate",
                    entity_id=score.candidate_id,
                    task_id=score.task_id,
                    payload={"reason": "below_minimum_standards"},
                )

        # Select winner from passing candidates
        passing = [s for s in scores if s.passed]
        if not passing:
            self.cancel_competition(competition_id, reason="no_candidates_passed")
            return

        self.select_winner(competition_id, passing[0])

    def select_winner(self, competition_id: str, winner: CandidateScore) -> None:
        """Mark the highest-scoring passing candidate as winner."""
        competition = self.store.get_competition(competition_id)
        if competition is None:
            return

        self.store.update_competition_status(
            competition_id,
            "decided",
            winner_task_id=winner.task_id,
            winner_score=winner.total,
        )
        self.store.append_event(
            event_type="competition.decided",
            entity_type="competition",
            entity_id=competition_id,
            task_id=competition.parent_task_id,
            payload={
                "winner_task_id": winner.task_id,
                "winner_score": winner.total,
            },
        )

        # Mark winner candidate as promoted
        self.store.update_candidate_score(
            winner.candidate_id,
            score=winner.total,
            promoted=True,
        )
        self.store.append_event(
            event_type="candidate.promoted",
            entity_type="competition_candidate",
            entity_id=winner.candidate_id,
            task_id=winner.task_id,
            payload={"task_id": winner.task_id},
        )

        logger.info(
            "competition.winner_selected",
            competition_id=competition_id,
            winner_task_id=winner.task_id,
            winner_score=winner.total,
        )

        # Auto-promote: merge winner worktree and finalize
        self.promote_winner(competition_id)

    def promote_winner(self, competition_id: str) -> None:
        """Merge winner worktree and clean up all competition worktrees."""
        competition = self.store.get_competition(competition_id)
        if competition is None or competition.status != "decided":
            return
        if competition.winner_task_id is None:
            return

        winner_candidate = self.store.find_candidate_by_task(competition.winner_task_id)
        if (
            winner_candidate is not None
            and winner_candidate.workspace_ref is not None
            and self._workspace is not None
        ):
            try:
                self._workspace.merge_winner(competition_id, winner_candidate.workspace_ref)
            except Exception:
                logger.error(
                    "competition.merge_failed",
                    competition_id=competition_id,
                    workspace_ref=winner_candidate.workspace_ref,
                    exc_info=True,
                )
            finally:
                # Always clean up worktrees, even if merge failed
                self._workspace.cleanup_all(competition_id)

        # Mark parent task as completed
        self.store.update_task_status(competition.parent_task_id, "completed")
        logger.info("competition.promoted", competition_id=competition_id)

    def cancel_competition(self, competition_id: str, *, reason: str = "user_cancelled") -> None:
        """Cancel competition, all running candidates, and clean up worktrees."""
        competition = self.store.get_competition(competition_id)
        if competition is None:
            return
        if competition.status in ("decided", "cancelled"):
            return

        self.store.update_competition_status(competition_id, "cancelled")
        self.store.append_event(
            event_type="competition.cancelled",
            entity_type="competition",
            entity_id=competition_id,
            task_id=competition.parent_task_id,
            payload={"reason": reason},
        )

        # Disqualify running/pending candidates
        candidates = self.store.list_candidates(competition_id)
        for candidate in candidates:
            if candidate.status in ("pending", "running", "completed"):
                try:
                    self.store.update_candidate_status(
                        candidate.candidate_id,
                        "disqualified",
                        discard_reason=f"competition_cancelled:{reason}",
                    )
                except ValueError:
                    pass

        if self._workspace is not None:
            self._workspace.cleanup_all(competition_id)

        logger.info(
            "competition.cancelled",
            competition_id=competition_id,
            reason=reason,
        )

    def cleanup_orphan_worktrees(self) -> None:
        """Remove worktrees with no corresponding competition record."""
        if self._workspace is None:
            return
        orphans = self._workspace.list_orphans()
        for orphan_path in orphans:
            # Extract competition_id from path
            parts = orphan_path.split("/")
            competition_id = parts[-1] if parts else ""
            if not competition_id:
                continue
            existing = self.store.get_competition(competition_id)
            if existing is None:
                self._workspace.cleanup_all(competition_id)
                logger.info("competition.orphan_cleaned", path=orphan_path)

    # -- DISPATCH_RESULT hook handler -----------------------------------------

    def on_dispatch_result(self, task_id: str, **kwargs: Any) -> None:
        """DISPATCH_RESULT hook: check if completed task is a competition candidate."""
        competition = self.store.find_competition_by_candidate_task(task_id)
        if competition is None:
            return
        self.on_candidate_task_completed(task_id)

    # -- Internals ------------------------------------------------------------

    def _get_conversation_id(self, competition: CompetitionRecord) -> str:
        parent_task = self.store.get_task(competition.parent_task_id)
        if parent_task is not None:
            return parent_task.conversation_id
        return ""
