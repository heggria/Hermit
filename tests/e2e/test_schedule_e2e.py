"""E2E: Schedule — full lifecycle via CLI and kernel store.

Exercises the scheduled-task system from the user's perspective: creating jobs
via CLI, listing, enabling/disabling, removing, inspecting history, and verifying
the kernel store records match CLI output.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.surfaces.cli.main import app


@pytest.fixture
def schedule_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[KernelStore, CliRunner]:
    """Isolated schedule environment with HERMIT_BASE_DIR pointing to tmp."""
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    return store, CliRunner()


# ---------------------------------------------------------------------------
# 1. Create cron job via CLI
# ---------------------------------------------------------------------------


def test_schedule_add_cron_job(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Create a cron-based scheduled task via CLI."""
    store, cli = schedule_env

    result = cli.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "Daily AI News",
            "--prompt",
            "搜索今日 AI 最新动态并汇总",
            "--cron",
            "0 9 * * 1-5",
        ],
    )
    assert result.exit_code == 0
    assert "Daily AI News" in result.output
    assert "cron" in result.output

    # Verify stored in kernel
    jobs = store.list_schedules()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "Daily AI News"
    assert job.prompt == "搜索今日 AI 最新动态并汇总"
    assert job.schedule_type == "cron"
    assert job.cron_expr == "0 9 * * 1-5"
    assert job.enabled is True
    assert job.next_run_at is not None


# ---------------------------------------------------------------------------
# 2. Create interval job via CLI
# ---------------------------------------------------------------------------


def test_schedule_add_interval_job(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Create an interval-based scheduled task via CLI."""
    store, cli = schedule_env

    result = cli.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "Health Check",
            "--prompt",
            "检查服务健康状态",
            "--interval",
            "300",
        ],
    )
    assert result.exit_code == 0
    assert "Health Check" in result.output

    jobs = store.list_schedules()
    assert len(jobs) == 1
    assert jobs[0].schedule_type == "interval"
    assert jobs[0].interval_seconds == 300


# ---------------------------------------------------------------------------
# 3. Create one-time job via CLI
# ---------------------------------------------------------------------------


def test_schedule_add_once_job(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Create a one-time scheduled task via CLI."""
    store, cli = schedule_env

    # Schedule 1 hour in the future
    future_time = time.time() + 3600
    from datetime import datetime

    future_iso = datetime.fromtimestamp(future_time).strftime("%Y-%m-%dT%H:%M:%S")

    result = cli.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "One-time Deploy",
            "--prompt",
            "执行一次性部署任务",
            "--once",
            future_iso,
        ],
    )
    assert result.exit_code == 0
    assert "One-time Deploy" in result.output
    assert "once" in result.output

    jobs = store.list_schedules()
    assert len(jobs) == 1
    assert jobs[0].schedule_type == "once"
    assert jobs[0].once_at is not None
    assert jobs[0].once_at > time.time()


# ---------------------------------------------------------------------------
# 4. List jobs via CLI
# ---------------------------------------------------------------------------


def test_schedule_list_shows_all_jobs(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """CLI list shows all created jobs with correct details."""
    store, cli = schedule_env

    # Create multiple jobs directly in store
    job1 = ScheduledJob.create(
        name="Morning Report",
        prompt="生成晨报",
        schedule_type="cron",
        cron_expr="0 8 * * *",
    )
    job2 = ScheduledJob.create(
        name="Hourly Check",
        prompt="检查状态",
        schedule_type="interval",
        interval_seconds=3600,
    )
    store.create_schedule(job1)
    store.create_schedule(job2)

    result = cli.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0
    assert "Morning Report" in result.output
    assert "Hourly Check" in result.output
    assert job1.id in result.output
    assert job2.id in result.output
    assert "0 8 * * *" in result.output


# ---------------------------------------------------------------------------
# 5. Empty list
# ---------------------------------------------------------------------------


def test_schedule_list_empty(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """CLI list shows appropriate message when no jobs exist."""
    _store, cli = schedule_env

    result = cli.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0
    # Should show "No scheduled tasks" or similar
    assert "No scheduled tasks" in result.output or "没有" in result.output


# ---------------------------------------------------------------------------
# 6. Disable and enable a job
# ---------------------------------------------------------------------------


def test_schedule_disable_then_enable(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Disable a job, verify state, then re-enable it."""
    store, cli = schedule_env

    job = ScheduledJob.create(
        name="Toggleable Task",
        prompt="可开关的任务",
        schedule_type="interval",
        interval_seconds=120,
    )
    store.create_schedule(job)

    # Disable
    disable_result = cli.invoke(app, ["schedule", "disable", job.id])
    assert disable_result.exit_code == 0
    assert job.id in disable_result.output

    # Verify disabled in store
    updated = store.get_schedule(job.id)
    assert updated is not None
    assert updated.enabled is False

    # List shows disabled status
    list_result = cli.invoke(app, ["schedule", "list"])
    assert "disabled" in list_result.output or "已禁用" in list_result.output

    # Enable
    enable_result = cli.invoke(app, ["schedule", "enable", job.id])
    assert enable_result.exit_code == 0

    # Verify enabled in store
    re_enabled = store.get_schedule(job.id)
    assert re_enabled is not None
    assert re_enabled.enabled is True


# ---------------------------------------------------------------------------
# 7. Remove a job
# ---------------------------------------------------------------------------


def test_schedule_remove_deletes_job(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Remove a job via CLI, verify it's gone from store."""
    store, cli = schedule_env

    job = ScheduledJob.create(
        name="Ephemeral Task",
        prompt="临时任务",
        schedule_type="interval",
        interval_seconds=60,
    )
    store.create_schedule(job)
    assert len(store.list_schedules()) == 1

    result = cli.invoke(app, ["schedule", "remove", job.id])
    assert result.exit_code == 0
    assert job.id in result.output

    assert len(store.list_schedules()) == 0
    assert store.get_schedule(job.id) is None


# ---------------------------------------------------------------------------
# 8. Remove non-existent job
# ---------------------------------------------------------------------------


def test_schedule_remove_nonexistent_fails(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """Removing a non-existent job returns error."""
    _store, cli = schedule_env

    result = cli.invoke(app, ["schedule", "remove", "nonexistent-id"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 9. History shows execution records
# ---------------------------------------------------------------------------


def test_schedule_history_shows_records(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Execution history is correctly displayed via CLI."""
    store, cli = schedule_env

    now = time.time()

    # Create a job and simulate execution records
    job = ScheduledJob.create(
        name="Reporting Task",
        prompt="生成报告",
        schedule_type="cron",
        cron_expr="0 9 * * *",
    )
    store.create_schedule(job)

    # Successful execution
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            started_at=now - 3600,
            finished_at=now - 3590,
            success=True,
            result_text="报告已生成：今日新增用户 150 人。",
        )
    )

    # Failed execution
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            started_at=now - 7200,
            finished_at=now - 7195,
            success=False,
            result_text="",
            error="API rate limit exceeded",
        )
    )

    result = cli.invoke(app, ["schedule", "history"])
    assert result.exit_code == 0
    assert "Reporting Task" in result.output
    assert "OK" in result.output
    assert "FAIL" in result.output
    assert "报告已生成" in result.output
    assert "API rate limit" in result.output


# ---------------------------------------------------------------------------
# 10. History filtered by job ID
# ---------------------------------------------------------------------------


def test_schedule_history_filtered_by_job(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """History filtered by --job-id only shows that job's records."""
    store, cli = schedule_env

    now = time.time()

    job_a = ScheduledJob.create(
        name="Task A", prompt="A", schedule_type="interval", interval_seconds=60
    )
    job_b = ScheduledJob.create(
        name="Task B", prompt="B", schedule_type="interval", interval_seconds=60
    )
    store.create_schedule(job_a)
    store.create_schedule(job_b)

    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job_a.id,
            job_name="Task A",
            started_at=now - 100,
            finished_at=now - 95,
            success=True,
            result_text="A done",
        )
    )
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job_b.id,
            job_name="Task B",
            started_at=now - 50,
            finished_at=now - 45,
            success=True,
            result_text="B done",
        )
    )

    # Filter by job A
    result = cli.invoke(app, ["schedule", "history", "--job-id", job_a.id])
    assert result.exit_code == 0
    assert "Task A" in result.output
    assert "Task B" not in result.output


# ---------------------------------------------------------------------------
# 11. Empty history
# ---------------------------------------------------------------------------


def test_schedule_history_empty(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """History shows appropriate message when no records exist."""
    _store, cli = schedule_env

    result = cli.invoke(app, ["schedule", "history"])
    assert result.exit_code == 0
    assert "No execution history" in result.output or "没有" in result.output


# ---------------------------------------------------------------------------
# 12. Validation errors
# ---------------------------------------------------------------------------


def test_schedule_add_validation_errors(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """CLI validates input: no schedule type, invalid cron, past once, short interval."""
    _store, cli = schedule_env

    # No schedule type specified
    no_type = cli.invoke(
        app,
        ["schedule", "add", "--name", "Bad", "--prompt", "test"],
    )
    assert no_type.exit_code == 1

    # Invalid cron
    bad_cron = cli.invoke(
        app,
        ["schedule", "add", "--name", "Bad", "--prompt", "test", "--cron", "invalid"],
    )
    assert bad_cron.exit_code == 1

    # Past once
    past_once = cli.invoke(
        app,
        ["schedule", "add", "--name", "Bad", "--prompt", "test", "--once", "2020-01-01T00:00"],
    )
    assert past_once.exit_code == 1

    # Interval too short
    short_interval = cli.invoke(
        app,
        ["schedule", "add", "--name", "Bad", "--prompt", "test", "--interval", "30"],
    )
    assert short_interval.exit_code == 1


# ---------------------------------------------------------------------------
# 13. Full lifecycle: add → list → disable → enable → history → remove
# ---------------------------------------------------------------------------


def test_schedule_full_lifecycle(schedule_env: tuple[KernelStore, CliRunner]) -> None:
    """Full user journey through the schedule system."""
    store, cli = schedule_env

    now = time.time()

    # 1. Add
    add_result = cli.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "Lifecycle Task",
            "--prompt",
            "定时检查系统状态",
            "--cron",
            "*/30 * * * *",
        ],
    )
    assert add_result.exit_code == 0

    jobs = store.list_schedules()
    assert len(jobs) == 1
    job_id = jobs[0].id

    # 2. List — visible
    list_result = cli.invoke(app, ["schedule", "list"])
    assert list_result.exit_code == 0
    assert "Lifecycle Task" in list_result.output
    assert "enabled" in list_result.output

    # 3. Disable
    disable_result = cli.invoke(app, ["schedule", "disable", job_id])
    assert disable_result.exit_code == 0

    list_disabled = cli.invoke(app, ["schedule", "list"])
    assert "disabled" in list_disabled.output or "已禁用" in list_disabled.output

    # 4. Enable
    enable_result = cli.invoke(app, ["schedule", "enable", job_id])
    assert enable_result.exit_code == 0

    # 5. Simulate execution history
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job_id,
            job_name="Lifecycle Task",
            started_at=now - 60,
            finished_at=now - 55,
            success=True,
            result_text="系统状态正常，无异常。",
        )
    )

    history_result = cli.invoke(app, ["schedule", "history"])
    assert history_result.exit_code == 0
    assert "Lifecycle Task" in history_result.output
    assert "OK" in history_result.output
    assert "系统状态正常" in history_result.output

    # 6. Remove
    remove_result = cli.invoke(app, ["schedule", "remove", job_id])
    assert remove_result.exit_code == 0

    # 7. Verify gone
    assert store.get_schedule(job_id) is None
    final_list = cli.invoke(app, ["schedule", "list"])
    assert "No scheduled tasks" in final_list.output or "Lifecycle Task" not in final_list.output


# ---------------------------------------------------------------------------
# 14. Multiple schedule types coexist
# ---------------------------------------------------------------------------


def test_schedule_multiple_types_coexist(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """Cron, interval, and once jobs can coexist and list correctly."""
    store, cli = schedule_env

    future_time = time.time() + 7200
    from datetime import datetime

    future_iso = datetime.fromtimestamp(future_time).strftime("%Y-%m-%dT%H:%M:%S")

    # Add all three types
    cli.invoke(
        app,
        ["schedule", "add", "--name", "Cron Job", "--prompt", "cron task", "--cron", "0 * * * *"],
    )
    cli.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "Interval Job",
            "--prompt",
            "interval task",
            "--interval",
            "600",
        ],
    )
    cli.invoke(
        app,
        ["schedule", "add", "--name", "Once Job", "--prompt", "once task", "--once", future_iso],
    )

    # All three stored
    jobs = store.list_schedules()
    assert len(jobs) == 3
    types = {j.schedule_type for j in jobs}
    assert types == {"cron", "interval", "once"}

    # All three listed
    list_result = cli.invoke(app, ["schedule", "list"])
    assert "Cron Job" in list_result.output
    assert "Interval Job" in list_result.output
    assert "Once Job" in list_result.output


# ---------------------------------------------------------------------------
# 15. Enable/disable non-existent job fails
# ---------------------------------------------------------------------------


def test_schedule_enable_disable_nonexistent_fails(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """Enable/disable non-existent jobs return error."""
    _store, cli = schedule_env

    enable_result = cli.invoke(app, ["schedule", "enable", "ghost-id"])
    assert enable_result.exit_code == 1

    disable_result = cli.invoke(app, ["schedule", "disable", "ghost-id"])
    assert disable_result.exit_code == 1


# ---------------------------------------------------------------------------
# 16. History with delivery metadata (Feishu)
# ---------------------------------------------------------------------------


def test_schedule_history_with_delivery_metadata(
    schedule_env: tuple[KernelStore, CliRunner],
) -> None:
    """Execution records with Feishu delivery metadata are stored and retrievable."""
    store, cli = schedule_env

    now = time.time()

    job = ScheduledJob.create(
        name="Feishu Report",
        prompt="发送飞书报告",
        schedule_type="cron",
        cron_expr="0 18 * * *",
        feishu_chat_id="oc_test123",
    )
    store.create_schedule(job)

    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            started_at=now - 120,
            finished_at=now - 110,
            success=True,
            result_text="日报已发送至飞书群。",
            delivery_status="delivered",
            delivery_channel="feishu",
            delivery_mode="card",
            delivery_target="oc_test123",
            delivery_message_id="om_abc123",
        )
    )

    # Verify record is stored and retrievable
    records = store.list_schedule_history(job_id=job.id, limit=5)
    assert len(records) == 1
    record = records[0]
    assert record.delivery_status == "delivered"
    assert record.delivery_channel == "feishu"
    assert record.delivery_target == "oc_test123"

    # CLI history still works
    history_result = cli.invoke(app, ["schedule", "history", "--job-id", job.id])
    assert history_result.exit_code == 0
    assert "Feishu Report" in history_result.output
    assert "日报已发送" in history_result.output
