"""Tests for the Task Health Monitor subsystem."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hermit.kernel.analytics.health.models import (
    HealthLevel,
    KernelHealthReport,
    StaleTaskInfo,
    TaskHealthStatus,
    ThroughputWindow,
)
from hermit.kernel.analytics.health.monitor import TaskHealthMonitor
from hermit.kernel.task.models.records import TaskRecord


def _make_task(
    task_id: str = "task_1",
    title: str = "Test task",
    status: str = "running",
    updated_at: float | None = None,
    created_at: float | None = None,
) -> TaskRecord:
    now = time.time()
    return TaskRecord(
        task_id=task_id,
        conversation_id="conv_1",
        title=title,
        goal="test goal",
        status=status,
        priority="normal",
        owner_principal_id="principal_test",
        policy_profile="autonomous",
        source_channel="test",
        parent_task_id=None,
        task_contract_ref=None,
        requested_by_principal_id=None,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    store.list_stale_tasks.return_value = []
    store.count_tasks_by_status.return_value = {}
    store.list_tasks.return_value = []
    store.list_recent_failures.return_value = []
    store.count_completed_in_window.return_value = 0
    store.count_steps_by_status.return_value = {}
    return store


class TestHealthModels:
    def test_health_level_values(self) -> None:
        assert HealthLevel.HEALTHY.value == "healthy"
        assert HealthLevel.DEGRADED.value == "degraded"
        assert HealthLevel.UNHEALTHY.value == "unhealthy"

    def test_stale_task_info_fields(self) -> None:
        info = StaleTaskInfo(
            task_id="t1",
            title="stuck",
            status="running",
            updated_at=1000.0,
            idle_seconds=900.0,
            stale_threshold_seconds=600.0,
        )
        assert info.task_id == "t1"
        assert info.idle_seconds == 900.0

    def test_throughput_window_defaults(self) -> None:
        tw = ThroughputWindow(
            window_seconds=3600,
            completed_tasks=10,
            failed_tasks=2,
            total_terminal_tasks=12,
            throughput_per_hour=10.0,
            failure_rate=2 / 12,
        )
        assert tw.completed_tasks == 10
        assert tw.failure_rate == pytest.approx(0.1667, abs=0.001)

    def test_health_report_defaults(self) -> None:
        report = KernelHealthReport(
            health_level=HealthLevel.HEALTHY,
            health_score=100.0,
        )
        assert report.stale_tasks == []
        assert report.notes == []
        assert report.total_active_tasks == 0

    def test_task_health_status_fields(self) -> None:
        now = time.time()
        ths = TaskHealthStatus(
            task_id="t1",
            title="test",
            status="running",
            is_stale=False,
            idle_seconds=5.0,
            total_steps=3,
            failed_steps=0,
            step_failure_rate=0.0,
            created_at=now,
            updated_at=now,
        )
        assert ths.is_stale is False
        assert ths.step_failure_rate == 0.0


class TestTaskHealthMonitor:
    def test_healthy_system(self, mock_store: MagicMock) -> None:
        mock_store.count_completed_in_window.return_value = 15
        mock_store.count_tasks_by_status.return_value = {"completed": 15, "running": 2}

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert report.health_level == HealthLevel.HEALTHY
        assert report.health_score >= 80
        assert report.total_stale_tasks == 0
        assert "All systems nominal" in report.notes

    def test_no_tasks_is_healthy(self, mock_store: MagicMock) -> None:
        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert report.health_level == HealthLevel.HEALTHY
        assert report.health_score == 100.0

    def test_stale_tasks_degrade_health(self, mock_store: MagicMock) -> None:
        old = time.time() - 1200  # 20 minutes ago
        stale_tasks = [
            _make_task(task_id=f"task_{i}", status="running", updated_at=old) for i in range(3)
        ]
        mock_store.list_stale_tasks.return_value = stale_tasks
        mock_store.count_tasks_by_status.return_value = {"running": 3}
        mock_store.list_tasks.return_value = stale_tasks

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert report.total_stale_tasks == 3
        assert report.health_score == 100 - 30  # 3 * 10
        assert report.health_level == HealthLevel.DEGRADED

    def test_max_stale_deduction_capped_at_40(self, mock_store: MagicMock) -> None:
        old = time.time() - 1200
        stale_tasks = [
            _make_task(task_id=f"task_{i}", status="running", updated_at=old) for i in range(10)
        ]
        mock_store.list_stale_tasks.return_value = stale_tasks
        mock_store.count_tasks_by_status.return_value = {"running": 10}
        mock_store.list_tasks.return_value = stale_tasks

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        # Capped at -40 for stale, so score = 60
        assert report.health_score == 60.0

    def test_high_failure_rate_unhealthy(self, mock_store: MagicMock) -> None:
        old = time.time() - 1200
        stale_tasks = [
            _make_task(task_id=f"stale_{i}", status="running", updated_at=old) for i in range(4)
        ]
        failures = [_make_task(task_id=f"fail_{i}", status="failed") for i in range(8)]
        mock_store.list_stale_tasks.return_value = stale_tasks
        mock_store.list_recent_failures.return_value = failures
        mock_store.count_completed_in_window.return_value = 2  # 8 failed, 2 completed = 80% failure
        mock_store.count_tasks_by_status.return_value = {"running": 4, "failed": 8}
        mock_store.list_tasks.return_value = stale_tasks

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert report.health_level == HealthLevel.UNHEALTHY
        assert report.health_score < 50
        assert report.failure_rate == pytest.approx(0.8, abs=0.01)

    def test_moderate_failure_rate(self, mock_store: MagicMock) -> None:
        failures = [_make_task(task_id="f1", status="failed")]
        mock_store.list_recent_failures.return_value = failures
        mock_store.count_completed_in_window.return_value = 3  # 1/4 = 25%

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert report.health_score == 85.0  # 100 - 15 for >20% failure rate
        assert report.health_level == HealthLevel.HEALTHY

    def test_blocked_tasks_deduct_points(self, mock_store: MagicMock) -> None:
        mock_store.count_tasks_by_status.return_value = {"blocked": 3, "running": 1}
        blocked_tasks = [_make_task(task_id=f"b_{i}", status="blocked") for i in range(3)]
        running_task = _make_task(task_id="r_0", status="running")
        mock_store.list_tasks.return_value = blocked_tasks + [running_task]

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        # -15 for 3 blocked tasks
        assert report.health_score == 85.0

    def test_throughput_calculation(self, mock_store: MagicMock) -> None:
        mock_store.count_completed_in_window.return_value = 48
        mock_store.list_recent_failures.return_value = [_make_task(task_id="f1", status="failed")]

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health(window_seconds=86400)

        assert report.throughput is not None
        assert report.throughput.completed_tasks == 48
        assert report.throughput.throughput_per_hour == pytest.approx(2.0, abs=0.01)
        assert report.throughput.failed_tasks == 1

    def test_report_includes_notes(self, mock_store: MagicMock) -> None:
        old = time.time() - 1200
        mock_store.list_stale_tasks.return_value = [
            _make_task(task_id="s1", status="running", updated_at=old)
        ]
        mock_store.count_tasks_by_status.return_value = {"running": 1}
        mock_store.list_tasks.return_value = [
            _make_task(task_id="s1", status="running", updated_at=old)
        ]

        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health()

        assert any("stale" in n.lower() for n in report.notes)

    def test_custom_thresholds(self, mock_store: MagicMock) -> None:
        monitor = TaskHealthMonitor(mock_store)
        report = monitor.check_health(
            stale_threshold_seconds=60.0,
            window_seconds=3600.0,
        )
        mock_store.list_stale_tasks.assert_called_once_with(threshold_seconds=60.0, limit=50)
        assert report.stale_threshold_seconds == 60.0
        assert report.window_seconds == 3600.0
