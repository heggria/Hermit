from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.execution.competition.evaluator import CompetitionEvaluator
from hermit.kernel.execution.competition.models import (
    CompetitionCandidateRecord,
    CompetitionRecord,
)


class FakeCriterion:
    name: str = "fake"

    def __init__(self, score_val: float = 0.8, pass_val: bool = True) -> None:
        self._score = score_val
        self._pass = pass_val

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        return self._score

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        return self._pass


class FailingCriterion:
    name: str = "failing"

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        raise RuntimeError("boom")

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        raise RuntimeError("boom")


def _make_competition(**overrides: Any) -> CompetitionRecord:
    defaults = dict(
        competition_id="comp_1",
        parent_task_id="task_parent",
        goal="test goal",
        strategy="parallel_tasks",
        candidate_count=3,
        min_candidates=1,
        evaluation_criteria={"fake": True},
        scoring_weights={},
        status="evaluating",
        timeout_policy="evaluate_completed",
    )
    defaults.update(overrides)
    return CompetitionRecord(**defaults)


def _make_candidate(idx: int, **overrides: Any) -> CompetitionCandidateRecord:
    defaults = dict(
        candidate_id=f"cand_{idx}",
        competition_id="comp_1",
        task_id=f"task_{idx}",
        label=f"candidate_{idx}",
        workspace_ref=f"/tmp/ws_{idx}",
        status="completed",
    )
    defaults.update(overrides)
    return CompetitionCandidateRecord(**defaults)


def test_evaluate_scores_completed_candidates() -> None:
    evaluator = CompetitionEvaluator(
        extra_criteria={"fake": FakeCriterion}  # type: ignore[dict-item]
    )
    competition = _make_competition()
    candidates = [_make_candidate(1), _make_candidate(2)]

    scores = evaluator.evaluate(competition, candidates)
    assert len(scores) == 2
    assert all(s.total == 0.8 for s in scores)
    assert all(s.passed is True for s in scores)


def test_evaluate_skips_non_completed_candidates() -> None:
    evaluator = CompetitionEvaluator(
        extra_criteria={"fake": FakeCriterion}  # type: ignore[dict-item]
    )
    competition = _make_competition()
    candidates = [
        _make_candidate(1),
        _make_candidate(2, status="failed"),
        _make_candidate(3, status="pending"),
    ]

    scores = evaluator.evaluate(competition, candidates)
    assert len(scores) == 1


def test_evaluate_skips_candidates_without_workspace() -> None:
    evaluator = CompetitionEvaluator(
        extra_criteria={"fake": FakeCriterion}  # type: ignore[dict-item]
    )
    competition = _make_competition()
    candidates = [_make_candidate(1, workspace_ref=None)]

    scores = evaluator.evaluate(competition, candidates)
    assert len(scores) == 0


def test_rank_candidates_with_weights() -> None:
    evaluator = CompetitionEvaluator()
    from hermit.kernel.execution.competition.models import CandidateScore

    scores = [
        CandidateScore(
            candidate_id="c1",
            task_id="t1",
            total=0.0,
            breakdown={"a": 0.9, "b": 0.5},
            passed=True,
        ),
        CandidateScore(
            candidate_id="c2",
            task_id="t2",
            total=0.0,
            breakdown={"a": 0.5, "b": 0.9},
            passed=True,
        ),
    ]

    # Weight "a" heavily
    ranked = evaluator.rank_candidates(scores, {"a": 0.8, "b": 0.2})
    assert ranked[0].candidate_id == "c1"

    # Weight "b" heavily
    ranked = evaluator.rank_candidates(scores, {"a": 0.2, "b": 0.8})
    assert ranked[0].candidate_id == "c2"


def test_rank_candidates_without_weights() -> None:
    evaluator = CompetitionEvaluator()
    from hermit.kernel.execution.competition.models import CandidateScore

    scores = [
        CandidateScore(candidate_id="c1", task_id="t1", total=0.7, breakdown={}, passed=True),
        CandidateScore(candidate_id="c2", task_id="t2", total=0.9, breakdown={}, passed=True),
    ]
    ranked = evaluator.rank_candidates(scores, {})
    assert ranked[0].candidate_id == "c2"


def test_failing_criterion_scores_zero() -> None:
    evaluator = CompetitionEvaluator(
        extra_criteria={"failing": FailingCriterion}  # type: ignore[dict-item]
    )
    competition = _make_competition(evaluation_criteria={"failing": True})
    candidates = [_make_candidate(1)]

    scores = evaluator.evaluate(competition, candidates)
    assert len(scores) == 1
    assert scores[0].total == 0.0
    assert scores[0].passed is False
