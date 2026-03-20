"""Tests for IterationLearner — lesson extraction from iteration outcomes."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.plugins.builtin.hooks.benchmark.learning import (
    IterationLearner,
    _describe_regression,
)
from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult, LessonLearned


@pytest.fixture()
def store() -> MagicMock:
    s = MagicMock()
    s.create_lesson = MagicMock()
    return s


@pytest.fixture()
def learner(store: MagicMock) -> IterationLearner:
    return IterationLearner(store=store)


def _make_result(**overrides: Any) -> BenchmarkResult:
    defaults: dict[str, Any] = {
        "iteration_id": "iter-1",
        "spec_id": "spec-1",
        "check_passed": True,
        "test_total": 10,
        "test_passed": 10,
        "coverage": 85.0,
        "lint_violations": 0,
    }
    defaults.update(overrides)
    return BenchmarkResult(**defaults)


# ---------------------------------------------------------------------------
# Lesson extraction from benchmark results
# ---------------------------------------------------------------------------


class TestAnalyzeBenchmark:
    @pytest.mark.asyncio()
    async def test_failed_check_produces_mistake(self, learner: IterationLearner) -> None:
        result = _make_result(check_passed=False)
        lessons = await learner.learn("iter-1", result)
        categories = [lesson.category for lesson in lessons]
        assert "mistake" in categories

    @pytest.mark.asyncio()
    async def test_regression_produces_rollback_pattern(self, learner: IterationLearner) -> None:
        compared = {"test_total_delta": -5, "coverage_delta": -3.0, "lint_delta": 2}
        result = _make_result(regression_detected=True, compared_to_baseline=compared)
        lessons = await learner.learn("iter-1", result)
        rollback_lessons = [lesson for lesson in lessons if lesson.category == "rollback_pattern"]
        assert len(rollback_lessons) >= 1
        assert "Regression" in rollback_lessons[0].summary

    @pytest.mark.asyncio()
    async def test_all_passing_produces_success(self, learner: IterationLearner) -> None:
        result = _make_result(test_total=20, test_passed=20)
        lessons = await learner.learn("iter-1", result)
        summaries = [lesson.summary for lesson in lessons]
        assert any("20 tests passed" in s for s in summaries)

    @pytest.mark.asyncio()
    async def test_high_quality_produces_optimization(self, learner: IterationLearner) -> None:
        result = _make_result(coverage=92.0, lint_violations=0, test_total=10, test_passed=10)
        lessons = await learner.learn("iter-1", result)
        categories = [lesson.category for lesson in lessons]
        assert "optimization" in categories

    @pytest.mark.asyncio()
    async def test_no_tests_skips_success_lesson(self, learner: IterationLearner) -> None:
        result = _make_result(test_total=0, test_passed=0)
        lessons = await learner.learn("iter-1", result)
        assert not any(lesson.category == "success_pattern" for lesson in lessons)


# ---------------------------------------------------------------------------
# Proof bundle analysis
# ---------------------------------------------------------------------------


class TestAnalyzeProof:
    @pytest.mark.asyncio()
    async def test_rollbacks_from_proof(self, learner: IterationLearner) -> None:
        proof = {
            "rollbacks": [
                {"action": "file_write", "reason": "test failure", "files": ["a.py"]},
            ],
        }
        result = _make_result()
        lessons = await learner.learn("iter-1", result, proof_bundle=proof)
        rb_lessons = [lesson for lesson in lessons if lesson.applicable_files == ("a.py",)]
        assert len(rb_lessons) == 1

    @pytest.mark.asyncio()
    async def test_review_findings_from_proof(self, learner: IterationLearner) -> None:
        proof = {
            "review_findings": [
                {"summary": "Unused import", "files": ["b.py"]},
                {"summary": "Missing docstring", "files": ["c.py"]},
            ],
        }
        result = _make_result()
        lessons = await learner.learn("iter-1", result, proof_bundle=proof)
        mistake_from_review = [
            lesson
            for lesson in lessons
            if lesson.category == "mistake" and "import" in lesson.summary.lower()
        ]
        assert len(mistake_from_review) == 1

    @pytest.mark.asyncio()
    async def test_empty_proof_no_extra_lessons(self, learner: IterationLearner) -> None:
        result = _make_result()
        lessons_without = await learner.learn("iter-1", result)
        lessons_with = await learner.learn("iter-1", result, proof_bundle={})
        # Empty proof should not add extra lessons beyond benchmark analysis
        assert len(lessons_with) == len(lessons_without)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio()
    async def test_lessons_persisted_to_store(
        self, learner: IterationLearner, store: MagicMock
    ) -> None:
        result = _make_result(check_passed=False)
        lessons = await learner.learn("iter-1", result)
        assert store.create_lesson.call_count == len(lessons)

    @pytest.mark.asyncio()
    async def test_store_without_create_lesson(self) -> None:
        bare_store = MagicMock(spec=[])  # no create_lesson attribute
        learner = IterationLearner(store=bare_store)
        result = _make_result(check_passed=False)
        # Should not raise
        lessons = await learner.learn("iter-1", result)
        assert len(lessons) >= 1

    @pytest.mark.asyncio()
    async def test_persist_error_is_swallowed(self, store: MagicMock) -> None:
        store.create_lesson.side_effect = RuntimeError("db error")
        learner = IterationLearner(store=store)
        result = _make_result(check_passed=False)
        # Should not raise despite store error
        lessons = await learner.learn("iter-1", result)
        assert len(lessons) >= 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestDescribeRegression:
    def test_test_drop(self) -> None:
        desc = _describe_regression({"test_total_delta": -3})
        assert "tests dropped" in desc

    def test_coverage_drop(self) -> None:
        desc = _describe_regression({"coverage_delta": -2.5})
        assert "coverage dropped" in desc

    def test_lint_increase(self) -> None:
        desc = _describe_regression({"lint_delta": 4})
        assert "lint violations increased" in desc

    def test_empty_compared(self) -> None:
        desc = _describe_regression({})
        assert "quality regression detected" in desc


class TestLessonLearnedModel:
    def test_frozen(self) -> None:
        lesson = LessonLearned(
            lesson_id="x",
            iteration_id="y",
            category="mistake",
            summary="oops",
        )
        with pytest.raises(AttributeError):
            lesson.summary = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        lesson = LessonLearned(
            lesson_id="a",
            iteration_id="b",
            category="success_pattern",
            summary="ok",
        )
        assert lesson.trigger_condition == ""
        assert lesson.resolution == ""
        assert lesson.applicable_files == ()
        assert lesson.metadata == {}
