"""Complexity classifier for task governance intensity.

Classifies tasks into complexity bands (trivial, simple, moderate, complex)
based on observable signals at task creation time. The classification
determines which governance stages the executor will run via GovernanceProfile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hermit.kernel.policy.models.enums import ComplexityBand

if TYPE_CHECKING:
    from hermit.kernel.task.models.records import TaskRecord


class ComplexityClassifier:
    """Classify tasks into complexity bands based on observable signals."""

    def classify(
        self,
        task: TaskRecord,
        *,
        dag_depth: int = 1,
        step_count: int = 1,
    ) -> str:
        """Compute the complexity band for a task.

        Signals used:
        - ``dag_depth``: depth of the step DAG (1 = flat task)
        - ``step_count``: total number of steps in the task
        - ``policy_profile``: supervised tasks are always complex
        - ``acceptance_criteria``: tasks with criteria are at least moderate
        - ``goal`` length: proxy for task ambiguity

        Returns a ComplexityBand string value.
        """
        # Supervised tasks are always treated as complex
        if task.policy_profile in ("supervised", "readonly"):
            return ComplexityBand.COMPLEX

        # DAG tasks with multiple steps are at least moderate
        if dag_depth >= 3 or step_count >= 5:
            return ComplexityBand.COMPLEX

        if dag_depth >= 2 or step_count >= 3:
            return ComplexityBand.MODERATE

        # Tasks with acceptance criteria need at least moderate governance
        if task.acceptance_criteria:
            return ComplexityBand.MODERATE

        # Short autonomous tasks are trivial or simple
        if task.policy_profile == "autonomous":
            goal_len = len(task.goal)
            if goal_len < 100 and step_count == 1:
                return ComplexityBand.TRIVIAL
            return ComplexityBand.SIMPLE

        # Default profile tasks
        goal_len = len(task.goal)
        if goal_len < 200 and step_count <= 2:
            return ComplexityBand.SIMPLE

        return ComplexityBand.MODERATE
