"""TaskMetricsService — aggregates per-task execution timing from step records."""

from __future__ import annotations

from dataclasses import dataclass, field

from hermit.kernel.ledger.journal.store import KernelStore


@dataclass
class StepTimingEntry:
    """Timing data for a single step within a task."""

    step_id: str
    kind: str
    status: str
    duration_seconds: float | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class TaskMetrics:
    """Aggregated execution timing metrics for a single task."""

    task_id: str
    task_status: str
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    total_duration_seconds: float | None = None
    avg_step_duration_seconds: float | None = None
    min_step_duration_seconds: float | None = None
    max_step_duration_seconds: float | None = None
    step_timings: list[StepTimingEntry] = field(default_factory=lambda: [])


@dataclass
class TaskMetricsSummary:
    """Aggregated timing metrics across one or more tasks."""

    tasks: list[TaskMetrics] = field(default_factory=lambda: [])
    total_tasks: int = 0
    tasks_with_timing: int = 0


class TaskMetricsService:
    """Aggregates per-task execution timing from StepRecord and StepAttemptRecord fields.

    Queries ``started_at`` and ``finished_at`` on both step and step-attempt rows
    to produce timing breakdowns without modifying any store data.
    """

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def compute_task_metrics(
        self,
        task_id: str,
        *,
        include_step_timings: bool = True,
        limit: int = 500,
    ) -> TaskMetrics | None:
        """Compute execution timing metrics for a single task.

        Returns ``None`` if the task does not exist.

        Args:
            task_id: The task to compute metrics for.
            include_step_timings: Include per-step timing breakdowns in the result.
            limit: Maximum number of step records to query.
        """
        task = self._store.get_task(task_id)
        if task is None:
            return None

        steps = self._store.list_steps(task_id=task_id, limit=limit)

        completed = sum(1 for s in steps if s.status in {"succeeded", "completed"})
        failed = sum(1 for s in steps if s.status in {"failed", "error"})
        skipped = sum(1 for s in steps if s.status == "skipped")

        step_timings: list[StepTimingEntry] = []
        durations: list[float] = []

        for step in steps:
            duration: float | None = None
            started = step.started_at
            finished = step.finished_at

            # Fall back to step-attempt timing when the step itself has no timing.
            # Walk all attempts and keep the timestamps of the last attempt that
            # has both a start and an end so we reflect the most-recent execution.
            if (started is None or finished is None) and step.status not in {
                "pending",
                "ready",
                "waiting",
            }:
                attempts = self._store.list_step_attempts(
                    step_id=step.step_id,
                    limit=10,
                )
                for attempt in attempts:
                    effective_start = attempt.claimed_at or attempt.started_at
                    if effective_start is not None and attempt.finished_at is not None:
                        started = effective_start
                        finished = attempt.finished_at

            if started is not None and finished is not None and finished >= started:
                duration = finished - started
                durations.append(duration)

            if include_step_timings:
                step_timings.append(
                    StepTimingEntry(
                        step_id=step.step_id,
                        kind=step.kind,
                        status=step.status,
                        duration_seconds=duration,
                        started_at=started,
                        finished_at=finished,
                    )
                )

        total_duration: float | None = sum(durations) if durations else None
        avg_duration: float | None = (sum(durations) / len(durations)) if durations else None
        min_duration = min(durations) if durations else None
        max_duration = max(durations) if durations else None

        return TaskMetrics(
            task_id=task_id,
            task_status=task.status,
            total_steps=len(steps),
            completed_steps=completed,
            failed_steps=failed,
            skipped_steps=skipped,
            total_duration_seconds=total_duration,
            avg_step_duration_seconds=avg_duration,
            min_step_duration_seconds=min_duration,
            max_step_duration_seconds=max_duration,
            step_timings=step_timings,
        )

    def compute_multi_task_metrics(
        self,
        task_ids: list[str],
        *,
        include_step_timings: bool = False,
        limit_per_task: int = 200,
    ) -> TaskMetricsSummary:
        """Compute timing metrics for a list of tasks.

        Args:
            task_ids: List of task IDs to compute metrics for.
            include_step_timings: Include per-step timing in each TaskMetrics.
            limit_per_task: Maximum step records per task.
        """
        results: list[TaskMetrics] = []
        tasks_with_timing = 0

        for tid in task_ids:
            metrics = self.compute_task_metrics(
                tid,
                include_step_timings=include_step_timings,
                limit=limit_per_task,
            )
            if metrics is not None:
                results.append(metrics)
                if metrics.total_duration_seconds is not None:
                    tasks_with_timing += 1

        return TaskMetricsSummary(
            tasks=results,
            total_tasks=len(results),
            tasks_with_timing=tasks_with_timing,
        )
