"""Data models for the Task Health Monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class HealthLevel(StrEnum):
    """Aggregate kernel health levels.

    - healthy:   All metrics within acceptable bounds.
    - degraded:  Some issues detected; kernel is operational but warrants attention.
    - unhealthy: Severe issues; kernel may need intervention.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class StaleTaskInfo:
    """A task that has not made progress beyond the configured stale threshold.

    Attributes:
        task_id: Identifier of the stale task.
        title: Human-readable task title.
        status: Current task status (e.g. ``"running"``, ``"blocked"``).
        updated_at: Unix timestamp of the last recorded update on the task.
        idle_seconds: Elapsed seconds since the task was last updated.
        stale_threshold_seconds: The threshold that was exceeded to flag this task.
    """

    task_id: str
    title: str
    status: str
    updated_at: float
    idle_seconds: float
    stale_threshold_seconds: float


@dataclass
class TaskHealthStatus:
    """Per-task health summary.

    Attributes:
        task_id: Task identifier.
        title: Human-readable title.
        status: Current kernel status of the task.
        is_stale: True when idle time exceeds the stale threshold.
        idle_seconds: Seconds elapsed since the last update.
        total_steps: Total number of steps for this task.
        failed_steps: Number of steps that reached a ``"failed"`` terminal state.
        step_failure_rate: Ratio of failed steps to total steps (0.0–1.0).
        created_at: Unix timestamp when the task was created.
        updated_at: Unix timestamp of the last update.
    """

    task_id: str
    title: str
    status: str
    is_stale: bool
    idle_seconds: float
    total_steps: int
    failed_steps: int
    step_failure_rate: float
    created_at: float
    updated_at: float


@dataclass
class ThroughputWindow:
    """Task throughput metrics for a time window.

    Attributes:
        window_seconds: Duration of the measurement window in seconds.
        completed_tasks: Number of tasks that reached ``"completed"`` within the window.
        failed_tasks: Number of tasks that reached ``"failed"`` or ``"cancelled"`` within the window.
        total_terminal_tasks: Total tasks that reached a terminal state within the window.
        throughput_per_hour: Completed tasks per hour within the window.
        failure_rate: Ratio of failed/cancelled tasks to all terminal tasks (0.0–1.0).
    """

    window_seconds: float
    completed_tasks: int
    failed_tasks: int
    total_terminal_tasks: int
    throughput_per_hour: float
    failure_rate: float


@dataclass
class KernelHealthReport:
    """Aggregate kernel health report.

    Attributes:
        health_level: Overall health level (``healthy``, ``degraded``, ``unhealthy``).
        health_score: Numeric score 0–100; higher is healthier.
        stale_tasks: List of tasks flagged as stale.
        active_task_health: Per-task health summaries for non-terminal tasks.
        throughput: Throughput metrics for the measurement window.
        total_active_tasks: Number of non-terminal tasks at report time.
        total_stale_tasks: Convenience count of stale tasks.
        failure_rate: Overall step failure rate across all active tasks.
        scored_at: Unix timestamp when the report was generated.
        stale_threshold_seconds: The stale threshold used for this report.
        window_seconds: Measurement window used for throughput calculation.
        notes: Human-readable diagnostic notes explaining the health score.
    """

    health_level: HealthLevel
    health_score: float
    stale_tasks: list[StaleTaskInfo] = field(default_factory=list)
    active_task_health: list[TaskHealthStatus] = field(default_factory=list)
    throughput: ThroughputWindow | None = None
    total_active_tasks: int = 0
    total_stale_tasks: int = 0
    failure_rate: float = 0.0
    scored_at: float = 0.0
    stale_threshold_seconds: float = 0.0
    window_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
