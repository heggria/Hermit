"""Tests for TaskMetricsService — target 95%+ coverage on task_metrics.py."""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.analytics.task_metrics import TaskMetricsService
from hermit.kernel.ledger.journal.store import KernelStore


def _setup_store(tmp_path: Path) -> KernelStore:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-metrics", source_channel="chat")
    return store


def _create_task(store: KernelStore, title: str = "Metrics Task") -> str:
    task = store.create_task(
        conversation_id="conv-metrics",
        title=title,
        goal="Test metrics",
        source_channel="chat",
    )
    return task.task_id


def test_compute_task_metrics_nonexistent_task_returns_none(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    svc = TaskMetricsService(store)
    result = svc.compute_task_metrics("nonexistent-task-id")
    assert result is None


def test_compute_task_metrics_task_with_no_steps(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    svc = TaskMetricsService(store)

    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.task_id == task_id
    assert metrics.total_steps == 0
    assert metrics.completed_steps == 0
    assert metrics.failed_steps == 0
    assert metrics.skipped_steps == 0
    assert metrics.total_duration_seconds is None
    assert metrics.avg_step_duration_seconds is None
    assert metrics.min_step_duration_seconds is None
    assert metrics.max_step_duration_seconds is None
    assert metrics.step_timings == []


def test_compute_task_metrics_with_timed_steps(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step1 = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step1.step_id, status="succeeded", finished_at=now + 10)
    # Manually set started_at by going through the DB
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step1.step_id),
        )

    step2 = store.create_step(task_id=task_id, kind="review")
    store.update_step(step2.step_id, status="completed", finished_at=now + 20)
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step2.step_id),
        )

    step3 = store.create_step(task_id=task_id, kind="cleanup")
    store.update_step(step3.step_id, status="failed", finished_at=now + 5)
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step3.step_id),
        )

    step4 = store.create_step(task_id=task_id, kind="optional")
    store.update_step(step4.step_id, status="skipped")

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_steps == 4
    assert metrics.completed_steps == 2  # succeeded + completed
    assert metrics.failed_steps == 1
    assert metrics.skipped_steps == 1
    # 3 steps have timing: 10s, 20s, 5s
    assert metrics.total_duration_seconds is not None
    assert abs(metrics.total_duration_seconds - 35.0) < 1.0
    assert metrics.avg_step_duration_seconds is not None
    assert abs(metrics.avg_step_duration_seconds - 35.0 / 3) < 1.0
    assert metrics.min_step_duration_seconds is not None
    assert abs(metrics.min_step_duration_seconds - 5.0) < 1.0
    assert metrics.max_step_duration_seconds is not None
    assert abs(metrics.max_step_duration_seconds - 20.0) < 1.0
    assert len(metrics.step_timings) == 4


def test_compute_task_metrics_fallback_to_attempt_timing(tmp_path: Path) -> None:
    """Steps without started_at/finished_at should fall back to attempt timing."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step.step_id, status="succeeded")
    # Clear step timing
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = NULL, finished_at = NULL WHERE step_id = ?",
            (step.step_id,),
        )

    # Create attempt with timing
    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id)
    store.update_step_attempt(
        attempt.step_attempt_id,
        status="succeeded",
        finished_at=now + 15,
    )
    # Set claimed_at as the start time
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE step_attempts SET claimed_at = ? WHERE step_attempt_id = ?",
            (now, attempt.step_attempt_id),
        )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_duration_seconds is not None
    assert abs(metrics.total_duration_seconds - 15.0) < 1.0


def test_compute_task_metrics_attempt_fallback_uses_started_at_when_no_claimed_at(
    tmp_path: Path,
) -> None:
    """When attempt has no claimed_at, use started_at."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step.step_id, status="succeeded")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = NULL, finished_at = NULL WHERE step_id = ?",
            (step.step_id,),
        )

    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id)
    store.update_step_attempt(
        attempt.step_attempt_id,
        status="succeeded",
        finished_at=now + 8,
    )
    # started_at is set automatically, ensure claimed_at is NULL
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE step_attempts SET claimed_at = NULL WHERE step_attempt_id = ?",
            (attempt.step_attempt_id,),
        )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_duration_seconds is not None
    assert metrics.total_duration_seconds > 0


def test_compute_task_metrics_pending_status_skips_attempt_fallback(
    tmp_path: Path,
) -> None:
    """Steps in pending/ready/waiting status should NOT trigger attempt fallback."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)

    for status in ("pending", "ready", "waiting"):
        step = store.create_step(task_id=task_id, kind="execute")
        with store._get_conn():
            store._get_conn().execute(
                "UPDATE steps SET status = ?, started_at = NULL, finished_at = NULL WHERE step_id = ?",
                (status, step.step_id),
            )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_duration_seconds is None  # no timing data
    assert metrics.total_steps == 3


def test_compute_task_metrics_exclude_step_timings(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step.step_id, status="succeeded", finished_at=now + 10)
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step.step_id),
        )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id, include_step_timings=False)
    assert metrics is not None
    assert metrics.step_timings == []
    assert metrics.total_duration_seconds is not None


def test_compute_multi_task_metrics_empty(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    svc = TaskMetricsService(store)
    summary = svc.compute_multi_task_metrics([])
    assert summary.total_tasks == 0
    assert summary.tasks_with_timing == 0
    assert summary.tasks == []


def test_compute_multi_task_metrics_multiple_tasks(tmp_path: Path) -> None:
    store = _setup_store(tmp_path)
    now = time.time()

    # Task with timing
    task1_id = _create_task(store, title="Task 1")
    step1 = store.create_step(task_id=task1_id, kind="execute")
    store.update_step(step1.step_id, status="succeeded", finished_at=now + 10)
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step1.step_id),
        )

    # Task without timing
    task2_id = _create_task(store, title="Task 2")

    # Nonexistent task
    svc = TaskMetricsService(store)
    summary = svc.compute_multi_task_metrics(
        [task1_id, task2_id, "nonexistent"],
        include_step_timings=False,
        limit_per_task=100,
    )
    assert summary.total_tasks == 2  # nonexistent excluded
    assert summary.tasks_with_timing == 1  # only task1 has timing


def test_compute_task_metrics_step_with_finished_before_started(tmp_path: Path) -> None:
    """finished < started should produce no duration."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step.step_id, status="succeeded", finished_at=now - 5)
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = ? WHERE step_id = ?",
            (now, step.step_id),
        )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_duration_seconds is None  # finished < started -> excluded


def test_compute_task_metrics_step_with_error_status(tmp_path: Path) -> None:
    """Steps with status 'error' should count as failed."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)

    step = store.create_step(task_id=task_id, kind="execute")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET status = 'error' WHERE step_id = ?",
            (step.step_id,),
        )

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.failed_steps == 1


def test_compute_task_metrics_multiple_attempts_uses_most_recent(tmp_path: Path) -> None:
    """When falling back to attempt timing, should use the most recent finished attempt."""
    store = _setup_store(tmp_path)
    task_id = _create_task(store)
    now = time.time()

    step = store.create_step(task_id=task_id, kind="execute")
    store.update_step(step.step_id, status="succeeded")
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE steps SET started_at = NULL, finished_at = NULL WHERE step_id = ?",
            (step.step_id,),
        )

    # First attempt (older)
    a1 = store.create_step_attempt(task_id=task_id, step_id=step.step_id, attempt=1)
    store.update_step_attempt(a1.step_attempt_id, status="failed", finished_at=now + 5)

    # Second attempt (newer — listed first in DESC order, so found first in loop)
    a2 = store.create_step_attempt(task_id=task_id, step_id=step.step_id, attempt=2)
    store.update_step_attempt(a2.step_attempt_id, status="succeeded", finished_at=now + 20)

    svc = TaskMetricsService(store)
    metrics = svc.compute_task_metrics(task_id)
    assert metrics is not None
    assert metrics.total_duration_seconds is not None
    # The most recent attempt (a2) is listed first in DESC order, its timing is used
    assert metrics.total_duration_seconds > 0
