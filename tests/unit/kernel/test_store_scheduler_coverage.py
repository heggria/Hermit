"""Tests for KernelSchedulerStoreMixin — target 95%+ coverage on store_scheduler.py."""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob


def _make_job(
    *,
    job_id: str = "job-1",
    name: str = "Daily report",
    prompt: str = "Generate daily report",
    schedule_type: str = "cron",
    cron_expr: str | None = "0 9 * * *",
    once_at: float | None = None,
    interval_seconds: int | None = None,
    enabled: bool = True,
    created_at: float | None = None,
    last_run_at: float | None = None,
    next_run_at: float | None = None,
    max_retries: int = 3,
    feishu_chat_id: str | None = "chat-abc",
) -> ScheduledJob:
    return ScheduledJob(
        id=job_id,
        name=name,
        prompt=prompt,
        schedule_type=schedule_type,
        cron_expr=cron_expr,
        once_at=once_at,
        interval_seconds=interval_seconds,
        enabled=enabled,
        created_at=created_at or time.time(),
        last_run_at=last_run_at,
        next_run_at=next_run_at,
        max_retries=max_retries,
        feishu_chat_id=feishu_chat_id,
    )


def _make_execution_record(
    *,
    job_id: str = "job-1",
    job_name: str = "Daily report",
    success: bool = True,
    error: str | None = None,
    delivery_status: str | None = "delivered",
    delivery_channel: str | None = "feishu",
    delivery_mode: str | None = "card",
    delivery_target: str | None = "chat-abc",
    delivery_message_id: str | None = "msg-123",
    delivery_error: str | None = None,
) -> JobExecutionRecord:
    now = time.time()
    return JobExecutionRecord(
        job_id=job_id,
        job_name=job_name,
        started_at=now - 10,
        finished_at=now,
        success=success,
        result_text="Report generated" if success else "Failed",
        error=error,
        delivery_status=delivery_status,
        delivery_channel=delivery_channel,
        delivery_mode=delivery_mode,
        delivery_target=delivery_target,
        delivery_message_id=delivery_message_id,
        delivery_error=delivery_error,
    )


def test_create_and_get_schedule(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job = _make_job(job_id="sched-1")
    store.create_schedule(job)

    fetched = store.get_schedule("sched-1")
    assert fetched is not None
    assert fetched.id == "sched-1"
    assert fetched.name == "Daily report"
    assert fetched.prompt == "Generate daily report"
    assert fetched.schedule_type == "cron"
    assert fetched.cron_expr == "0 9 * * *"
    assert fetched.enabled is True
    assert fetched.max_retries == 3
    assert fetched.feishu_chat_id == "chat-abc"


def test_get_schedule_missing_returns_none(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    assert store.get_schedule("nonexistent") is None


def test_list_schedules_ordered_by_created_at_desc(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    now = time.time()
    job_a = _make_job(job_id="a", name="First", created_at=now - 100)
    job_b = _make_job(job_id="b", name="Second", created_at=now - 50)
    job_c = _make_job(job_id="c", name="Third", created_at=now)
    store.create_schedule(job_a)
    store.create_schedule(job_b)
    store.create_schedule(job_c)

    schedules = store.list_schedules()
    assert len(schedules) == 3
    assert schedules[0].id == "c"
    assert schedules[1].id == "b"
    assert schedules[2].id == "a"


def test_update_schedule_modifies_fields(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job = _make_job(job_id="upd-1", enabled=True)
    store.create_schedule(job)

    updated = store.update_schedule("upd-1", enabled=False, name="Updated name")
    assert updated is not None
    assert updated.enabled is False
    assert updated.name == "Updated name"

    # Verify persisted
    fetched = store.get_schedule("upd-1")
    assert fetched is not None
    assert fetched.enabled is False
    assert fetched.name == "Updated name"


def test_update_schedule_nonexistent_returns_none(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    result = store.update_schedule("missing-id", name="nope")
    assert result is None


def test_update_schedule_ignores_unknown_fields(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job = _make_job(job_id="unk-field")
    store.create_schedule(job)

    updated = store.update_schedule("unk-field", nonexistent_field="value")
    assert updated is not None
    assert updated.name == "Daily report"  # unchanged


def test_delete_schedule_existing(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job = _make_job(job_id="del-1")
    store.create_schedule(job)

    assert store.delete_schedule("del-1") is True
    assert store.get_schedule("del-1") is None


def test_delete_schedule_missing_returns_false(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    assert store.delete_schedule("nonexistent") is False


def test_create_schedule_upserts(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job1 = _make_job(job_id="upsert-1", name="Original")
    store.create_schedule(job1)

    job2 = _make_job(job_id="upsert-1", name="Replaced")
    store.create_schedule(job2)

    fetched = store.get_schedule("upsert-1")
    assert fetched is not None
    assert fetched.name == "Replaced"
    assert len(store.list_schedules()) == 1


def test_append_and_list_schedule_history_all(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    rec1 = _make_execution_record(job_id="hist-1", job_name="Job A", success=True)
    rec2 = _make_execution_record(
        job_id="hist-2",
        job_name="Job B",
        success=False,
        error="timeout",
        delivery_status="failed",
        delivery_error="network error",
    )
    store.append_schedule_history(rec1)
    store.append_schedule_history(rec2)

    history = store.list_schedule_history()
    assert len(history) == 2
    # Check all fields round-trip
    found = [h for h in history if h.job_id == "hist-2"]
    assert len(found) == 1
    h = found[0]
    assert h.success is False
    assert h.error == "timeout"
    assert h.delivery_status == "failed"
    assert h.delivery_channel == "feishu"
    assert h.delivery_mode == "card"
    assert h.delivery_target == "chat-abc"
    assert h.delivery_message_id == "msg-123"
    assert h.delivery_error == "network error"


def test_list_schedule_history_with_job_id_filter(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.append_schedule_history(_make_execution_record(job_id="filter-a", job_name="A"))
    store.append_schedule_history(_make_execution_record(job_id="filter-b", job_name="B"))
    store.append_schedule_history(_make_execution_record(job_id="filter-a", job_name="A run 2"))

    filtered = store.list_schedule_history(job_id="filter-a")
    assert len(filtered) == 2
    assert all(h.job_id == "filter-a" for h in filtered)

    # With limit
    limited = store.list_schedule_history(job_id="filter-a", limit=1)
    assert len(limited) == 1


def test_list_schedule_history_respects_limit(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    for i in range(5):
        store.append_schedule_history(
            _make_execution_record(job_id=f"lim-{i}", job_name=f"Job {i}")
        )

    history = store.list_schedule_history(limit=3)
    assert len(history) == 3


def test_schedule_with_all_optional_fields_none(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    job = ScheduledJob(
        id="minimal",
        name="Minimal",
        prompt="do something",
        schedule_type="once",
        cron_expr=None,
        once_at=None,
        interval_seconds=None,
        enabled=False,
        created_at=time.time(),
        last_run_at=None,
        next_run_at=None,
        max_retries=0,
        feishu_chat_id=None,
    )
    store.create_schedule(job)

    fetched = store.get_schedule("minimal")
    assert fetched is not None
    assert fetched.cron_expr is None
    assert fetched.once_at is None
    assert fetched.interval_seconds is None
    assert fetched.last_run_at is None
    assert fetched.next_run_at is None
    assert fetched.feishu_chat_id is None
    assert fetched.enabled is False
    assert fetched.max_retries == 0


def test_execution_record_with_all_delivery_fields_none(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    rec = JobExecutionRecord(
        job_id="no-delivery",
        job_name="Plain job",
        started_at=time.time() - 5,
        finished_at=time.time(),
        success=True,
        result_text="Done",
        error=None,
        delivery_status=None,
        delivery_channel=None,
        delivery_mode=None,
        delivery_target=None,
        delivery_message_id=None,
        delivery_error=None,
    )
    store.append_schedule_history(rec)

    history = store.list_schedule_history(job_id="no-delivery")
    assert len(history) == 1
    h = history[0]
    assert h.delivery_status is None
    assert h.delivery_channel is None
    assert h.delivery_mode is None
    assert h.delivery_target is None
    assert h.delivery_message_id is None
    assert h.delivery_error is None
