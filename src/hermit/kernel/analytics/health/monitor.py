"""TaskHealthMonitor — aggregate kernel health from stale detection, failure rate, and throughput."""

from __future__ import annotations

import time

from hermit.kernel.analytics.health.models import (
    HealthLevel,
    KernelHealthReport,
    StaleTaskInfo,
    TaskHealthStatus,
    ThroughputWindow,
)
from hermit.kernel.ledger.journal.store import KernelStore


class TaskHealthMonitor:
    """Computes an aggregate health report for the kernel's task subsystem.

    Combines stale-task detection, failure-rate analysis, and throughput
    measurement into a single ``KernelHealthReport`` with a numeric score
    and a human-readable level (healthy / degraded / unhealthy).
    """

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def check_health(
        self,
        *,
        stale_threshold_seconds: float = 600.0,
        window_seconds: float = 86400.0,
    ) -> KernelHealthReport:
        """Run all health checks and return an aggregate report.

        Args:
            stale_threshold_seconds: Tasks idle longer than this are flagged stale.
            window_seconds: Look-back window for throughput and failure-rate metrics.
        """
        now = time.time()
        notes: list[str] = []
        score = 100.0

        # ── Stale tasks ────────────────────────────────────────────────
        stale_records = self._store.list_stale_tasks(
            threshold_seconds=stale_threshold_seconds, limit=50
        )
        stale_tasks = [
            StaleTaskInfo(
                task_id=t.task_id,
                title=t.title,
                status=t.status,
                updated_at=t.updated_at,
                idle_seconds=now - t.updated_at,
                stale_threshold_seconds=stale_threshold_seconds,
            )
            for t in stale_records
        ]
        stale_deduction = min(len(stale_tasks) * 10, 40)
        score -= stale_deduction
        if stale_tasks:
            notes.append(f"{len(stale_tasks)} stale task(s) detected (-{stale_deduction} pts)")

        # ── Active task health ─────────────────────────────────────────
        status_counts = self._store.count_tasks_by_status()
        active_statuses = {"running", "blocked", "queued", "planning_ready", "reconciling"}
        total_active = sum(status_counts.get(s, 0) for s in active_statuses)
        blocked_count = status_counts.get("blocked", 0)

        blocked_deduction = min(blocked_count * 5, 20)
        score -= blocked_deduction
        if blocked_count:
            notes.append(f"{blocked_count} blocked task(s) (-{blocked_deduction} pts)")

        active_task_health: list[TaskHealthStatus] = []
        active_records = self._store.list_tasks(status=None, limit=200)
        for t in active_records:
            if t.status not in active_statuses:
                continue
            step_counts = self._store.count_steps_by_status(task_id=t.task_id)
            total_steps = sum(step_counts.values())
            failed_steps = step_counts.get("failed", 0) + step_counts.get("error", 0)
            step_failure_rate = (failed_steps / total_steps) if total_steps > 0 else 0.0
            active_task_health.append(
                TaskHealthStatus(
                    task_id=t.task_id,
                    title=t.title,
                    status=t.status,
                    is_stale=any(s.task_id == t.task_id for s in stale_tasks),
                    idle_seconds=now - t.updated_at,
                    total_steps=total_steps,
                    failed_steps=failed_steps,
                    step_failure_rate=step_failure_rate,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
            )

        # ── Throughput & failure rate ──────────────────────────────────
        completed_count = self._store.count_completed_in_window(window_seconds)
        recent_failures = self._store.list_recent_failures(window_seconds=window_seconds, limit=200)
        failed_count = len(recent_failures)
        total_terminal = completed_count + failed_count
        failure_rate = (failed_count / total_terminal) if total_terminal > 0 else 0.0
        hours = max(window_seconds / 3600.0, 0.001)
        throughput_per_hour = completed_count / hours

        throughput = ThroughputWindow(
            window_seconds=window_seconds,
            completed_tasks=completed_count,
            failed_tasks=failed_count,
            total_terminal_tasks=total_terminal,
            throughput_per_hour=throughput_per_hour,
            failure_rate=failure_rate,
        )

        if failure_rate > 0.5:
            score -= 30
            notes.append(f"High failure rate {failure_rate:.0%} (-30 pts)")
        elif failure_rate > 0.2:
            score -= 15
            notes.append(f"Elevated failure rate {failure_rate:.0%} (-15 pts)")
        elif failure_rate > 0.1:
            score -= 5
            notes.append(f"Moderate failure rate {failure_rate:.0%} (-5 pts)")

        # ── Final score & level ────────────────────────────────────────
        score = max(score, 0.0)
        if score >= 80:
            level = HealthLevel.HEALTHY
        elif score >= 50:
            level = HealthLevel.DEGRADED
        else:
            level = HealthLevel.UNHEALTHY

        if not notes:
            notes.append("All systems nominal")

        return KernelHealthReport(
            health_level=level,
            health_score=score,
            stale_tasks=stale_tasks,
            active_task_health=active_task_health,
            throughput=throughput,
            total_active_tasks=total_active,
            total_stale_tasks=len(stale_tasks),
            failure_rate=failure_rate,
            scored_at=now,
            stale_threshold_seconds=stale_threshold_seconds,
            window_seconds=window_seconds,
            notes=notes,
        )
