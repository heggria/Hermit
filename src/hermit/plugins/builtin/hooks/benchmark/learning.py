"""IterationLearner — extracts structured lessons from iteration outcomes."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult, LessonLearned

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


class IterationLearner:
    """Analyze completed iterations and produce LessonLearned records."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    async def learn(
        self,
        iteration_id: str,
        benchmark_result: BenchmarkResult,
        proof_bundle: dict[str, Any] | None = None,
    ) -> list[LessonLearned]:
        """Extract lessons from a benchmark result and optional proof bundle."""
        lessons: list[LessonLearned] = []

        lessons.extend(self._analyze_benchmark(iteration_id, benchmark_result))
        if proof_bundle is not None:
            lessons.extend(self._analyze_proof(iteration_id, proof_bundle))

        for lesson in lessons:
            self._persist(lesson)

        log.info("lessons_extracted", iteration_id=iteration_id, count=len(lessons))
        return lessons

    def _analyze_benchmark(
        self,
        iteration_id: str,
        result: BenchmarkResult,
    ) -> list[LessonLearned]:
        lessons: list[LessonLearned] = []

        if not result.check_passed:
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="mistake",
                    summary="make check failed",
                    trigger_condition="check_passed=False",
                    resolution="Fix failing tests or lint errors before merge",
                )
            )

        if result.regression_detected:
            compared = result.compared_to_baseline
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="rollback_pattern",
                    summary=f"Regression detected: {_describe_regression(compared)}",
                    trigger_condition="regression_detected=True",
                    resolution="Revert or fix the regressing change",
                    metadata={"compared": compared},
                )
            )

        if result.test_passed == result.test_total and result.test_total > 0:
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="success_pattern",
                    summary=f"All {result.test_total} tests passed with {result.coverage}% coverage",
                )
            )

        if result.coverage >= 90.0 and result.lint_violations == 0:
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="optimization",
                    summary="High quality: 90%+ coverage and zero lint violations",
                )
            )

        return lessons

    def _analyze_proof(
        self,
        iteration_id: str,
        proof: dict[str, Any],
    ) -> list[LessonLearned]:
        lessons: list[LessonLearned] = []
        rollbacks = proof.get("rollbacks", [])
        for rb in rollbacks:
            files = tuple(rb.get("files", []))
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="rollback_pattern",
                    summary=f"Rollback on action: {rb.get('action', 'unknown')}",
                    trigger_condition=rb.get("reason", ""),
                    applicable_files=files,
                )
            )

        review_findings = proof.get("review_findings", [])
        for finding in review_findings:
            lessons.append(
                LessonLearned(
                    lesson_id=_uid(),
                    iteration_id=iteration_id,
                    category="mistake",
                    summary=finding.get("summary", "Review finding"),
                    applicable_files=tuple(finding.get("files", [])),
                )
            )

        return lessons

    def _persist(self, lesson: LessonLearned) -> None:
        if not hasattr(self._store, "create_lesson"):
            log.debug("store_missing_create_lesson", lesson_id=lesson.lesson_id)
            return
        try:
            self._store.create_lesson(
                lesson_id=lesson.lesson_id,
                iteration_id=lesson.iteration_id,
                category=lesson.category,
                summary=lesson.summary,
                trigger_condition=lesson.trigger_condition or None,
                resolution=lesson.resolution or None,
                applicable_files=list(lesson.applicable_files) or None,
                metadata=lesson.metadata or None,
            )
        except Exception:
            log.warning("lesson_persist_failed", lesson_id=lesson.lesson_id, exc_info=True)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _describe_regression(compared: dict[str, Any]) -> str:
    parts: list[str] = []
    if compared.get("test_total_delta", 0) < 0:
        parts.append(f"tests dropped by {abs(compared['test_total_delta'])}")
    if compared.get("coverage_delta", 0) < 0:
        parts.append(f"coverage dropped by {abs(compared['coverage_delta'])}%")
    if compared.get("lint_delta", 0) > 0:
        parts.append(f"lint violations increased by {compared['lint_delta']}")
    return "; ".join(parts) or "quality regression detected"
