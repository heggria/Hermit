"""CompetitionEvaluator — scores and ranks competition candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluationResult:
    """Result of evaluating a single candidate."""

    candidate_id: str = ""
    score: float = 0.0
    passed: bool = False
    details: dict[str, Any] = field(default_factory=dict[str, Any])


class CompetitionEvaluator:
    """Evaluates and ranks candidates in a competition."""

    def evaluate(
        self,
        competition_id: str,
        candidate_task_ids: list[str],
        *,
        goal: str = "",
    ) -> list[EvaluationResult]:
        """Evaluate all candidates and return results sorted by score descending."""
        results: list[EvaluationResult] = []
        for task_id in candidate_task_ids:
            results.append(
                EvaluationResult(
                    candidate_id=task_id,
                    score=0.0,
                    passed=False,
                    details={"reason": "default_evaluator"},
                )
            )
        return sorted(results, key=lambda r: r.score, reverse=True)
