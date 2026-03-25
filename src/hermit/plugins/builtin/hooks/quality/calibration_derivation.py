"""Derive calibration examples from LessonLearned records.

Converts iteration lessons (extracted by IterationLearner from benchmark
outcomes and proof bundles) into CalibrationExample instances that can be
injected into reviewer prompts as few-shot anchors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hermit.plugins.builtin.hooks.quality.calibration import CalibrationExample

if TYPE_CHECKING:
    from hermit.plugins.builtin.hooks.benchmark.models import LessonLearned


class CalibrationDeriver:
    """Converts LessonLearned records into CalibrationExample instances."""

    def derive_from_lessons(
        self,
        lessons: list[LessonLearned],
        *,
        max_examples: int = 5,
    ) -> list[CalibrationExample]:
        """Extract calibration examples from iteration lessons.

        - ``mistake`` and ``rollback_pattern`` lessons become FAIL examples.
        - ``success_pattern`` and ``optimization`` lessons become PASS examples.

        Returns at most *max_examples*, balanced between positive and negative.
        """
        negatives: list[CalibrationExample] = []
        positives: list[CalibrationExample] = []

        for lesson in lessons:
            if lesson.category in ("mistake", "rollback_pattern"):
                negatives.append(self._to_negative(lesson))
            elif lesson.category in ("success_pattern", "optimization"):
                positives.append(self._to_positive(lesson))

        # Balance: prefer negatives (calibration against leniency) but include
        # some positives to prevent over-rejection.
        neg_limit = min(len(negatives), (max_examples * 3 + 3) // 4)  # ~75%
        pos_limit = min(len(positives), max_examples - neg_limit)

        return negatives[:neg_limit] + positives[:pos_limit]

    @staticmethod
    def _to_negative(lesson: LessonLearned) -> CalibrationExample:
        findings: tuple[dict[str, str], ...] = ()
        if lesson.trigger_condition:
            findings = ({"severity": "high", "message": lesson.trigger_condition},)

        return CalibrationExample(
            input_summary=lesson.summary,
            expected_findings=findings,
            expected_pass=False,
            reasoning=lesson.resolution or f"Lesson from iteration {lesson.iteration_id}",
            source="lesson_derived",
        )

    @staticmethod
    def _to_positive(lesson: LessonLearned) -> CalibrationExample:
        return CalibrationExample(
            input_summary=lesson.summary,
            expected_findings=(),
            expected_pass=True,
            reasoning=lesson.summary,
            source="lesson_derived",
        )
