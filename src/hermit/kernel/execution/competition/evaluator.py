from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from hermit.kernel.execution.competition.criteria import BUILTIN_CRITERIA, EvaluationCriterion
from hermit.kernel.execution.competition.models import (
    CandidateScore,
    CompetitionCandidateRecord,
    CompetitionRecord,
)

logger = structlog.get_logger()


class CompetitionEvaluator:
    """Scores competition candidates against registered criteria."""

    def __init__(
        self,
        extra_criteria: dict[str, type[EvaluationCriterion]] | None = None,
    ) -> None:
        self._registry: dict[str, type[EvaluationCriterion]] = dict(BUILTIN_CRITERIA)
        if extra_criteria:
            self._registry.update(extra_criteria)

    def evaluate(
        self,
        competition: CompetitionRecord,
        candidates: list[CompetitionCandidateRecord],
    ) -> list[CandidateScore]:
        """Evaluate all completed candidates and return ranked scores."""
        criteria = self._resolve_criteria(competition.evaluation_criteria)
        scores: list[CandidateScore] = []
        for candidate in candidates:
            if candidate.status != "completed":
                continue
            if candidate.workspace_ref is None:
                continue
            score = self.score_candidate(candidate, criteria, competition.evaluation_criteria)
            scores.append(score)
        return self.rank_candidates(scores, competition.scoring_weights)

    def score_candidate(
        self,
        candidate: CompetitionCandidateRecord,
        criteria: list[EvaluationCriterion],
        criteria_config: dict[str, Any],
    ) -> CandidateScore:
        """Score a single candidate against all criteria."""
        workspace = Path(candidate.workspace_ref) if candidate.workspace_ref else Path(".")
        context: dict[str, Any] = {"candidate_id": candidate.candidate_id}
        breakdown: dict[str, float] = {}
        all_passed = True
        for criterion in criteria:
            try:
                value = criterion.score(workspace, context)
                breakdown[criterion.name] = max(0.0, min(1.0, value))
                if not criterion.passed(workspace, context):
                    all_passed = False
            except Exception:
                logger.warning(
                    "competition.criterion.error",
                    criterion=criterion.name,
                    candidate_id=candidate.candidate_id,
                    exc_info=True,
                )
                breakdown[criterion.name] = 0.0
                all_passed = False
        if not criteria:
            all_passed = False
        total = sum(breakdown.values()) / max(len(breakdown), 1)
        return CandidateScore(
            candidate_id=candidate.candidate_id,
            task_id=candidate.task_id,
            total=total,
            breakdown=breakdown,
            passed=all_passed,
        )

    def rank_candidates(
        self,
        scores: list[CandidateScore],
        weights: dict[str, float],
    ) -> list[CandidateScore]:
        """Apply scoring weights and sort descending by weighted total."""
        if not weights:
            return sorted(scores, key=lambda s: s.total, reverse=True)

        weight_sum = sum(weights.values()) or 1.0
        ranked: list[CandidateScore] = []
        for score in scores:
            weighted_total = 0.0
            for criterion_name, weight in weights.items():
                criterion_score = score.breakdown.get(criterion_name, 0.0)
                weighted_total += criterion_score * (weight / weight_sum)
            ranked.append(
                CandidateScore(
                    candidate_id=score.candidate_id,
                    task_id=score.task_id,
                    total=weighted_total,
                    breakdown=score.breakdown,
                    passed=score.passed,
                )
            )
        return sorted(ranked, key=lambda s: s.total, reverse=True)

    def _resolve_criteria(
        self,
        criteria_config: dict[str, Any],
    ) -> list[EvaluationCriterion]:
        """Instantiate criteria from config keys."""
        result: list[EvaluationCriterion] = []
        for name, enabled in criteria_config.items():
            if not enabled:
                continue
            cls = self._registry.get(name)
            if cls is None:
                logger.warning("competition.criterion.unknown", name=name)
                continue
            result.append(cls())
        return result
