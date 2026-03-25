"""Tests for SchedulerEngine — target 80%+ coverage on engine.py."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.scheduler.engine import (
    _HISTORY_MAX_RECORDS,
    _POLL_INTERVAL,
    _RESULT_TEXT_LIMIT,
    SchedulerEngine,
    _build_execution_prompt,
)
from hermit.plugins.builtin.hooks.scheduler.models import (
    JobExecutionRecord,
    ScheduledJob,
)


def _mk_settings(tmp_path: Path) -> SimpleNamespace:
    base_dir = tmp_path / "hermit"
    base_dir.mkdir(parents=True, exist_ok=True)
    kernel_dir = base_dir / "kernel"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        base_dir=base_dir,
        kernel_db_path=kernel_dir / "state.db",
        locale=None,
        scheduler_feishu_chat_id="",
    )


def _mk_hooks() -> MagicMock:
    return MagicMock()


def _mk_engine(tmp_path: Path) -> SchedulerEngine:
    settings = _mk_settings(tmp_path)
    hooks = _mk_hooks()
    return SchedulerEngine(settings, hooks)


def _mk_job(**kwargs) -> ScheduledJob:
    defaults = {
        "id": "job_001",
        "name": "Test Job",
        "prompt": "Do something",
        "schedule_type": "interval",
        "interval_seconds": 3600,
        "enabled": True,
    }
    defaults.update(kwargs)
    return ScheduledJob(**defaults)


# ── _build_execution_prompt ──────────────────────────────────────


def test_build_execution_prompt() -> None:
    result = _build_execution_prompt("Do the task")
    assert isinstance(result, str)
    assert len(result) > 0


def test_build_execution_prompt_strips() -> None:
    result = _build_execution_prompt("  Do the task  ")
    assert isinstance(result, str)


# ── SchedulerEngine lifecycle ────────────────────────────────────


def test_engine_init(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine._jobs == []
    assert engine._thread is None
    assert engine._runner is None


def test_set_runner(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    runner = object()
    engine.set_runner(runner)
    assert engine._runner is runner


# ── Job CRUD ─────────────────────────────────────────────────────


def test_add_job(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job()
    engine.add_job(job)
    assert len(engine.list_jobs()) == 1
    assert engine.list_jobs()[0].id == "job_001"


def test_remove_job(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job()
    engine.add_job(job)
    assert engine.remove_job("job_001") is True
    assert len(engine.list_jobs()) == 0


def test_remove_job_not_found(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine.remove_job("nonexistent") is False


def test_update_job(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job()
    engine.add_job(job)
    updated = engine.update_job("job_001", name="Updated Job")
    assert updated is not None
    assert updated.name == "Updated Job"


def test_update_job_not_found(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine.update_job("nonexistent", name="test") is None


def test_list_jobs_empty(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine.list_jobs() == []


def test_get_job(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job()
    engine.add_job(job)
    found = engine.get_job("job_001")
    assert found is not None
    assert found.id == "job_001"


def test_get_job_not_found(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine.get_job("nonexistent") is None


# ── History ──────────────────────────────────────────────────────


def test_get_history_empty(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    assert engine.get_history() == []


def test_get_history_with_filter(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    record = JobExecutionRecord(
        job_id="job_001",
        job_name="Test",
        started_at=time.time() - 10,
        finished_at=time.time(),
        success=True,
        result_text="done",
    )
    engine._append_history(record)
    history = engine.get_history(job_id="job_001")
    assert len(history) == 1
    assert history[0].job_id == "job_001"


def test_get_history_wrong_filter(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    record = JobExecutionRecord(
        job_id="job_001",
        job_name="Test",
        started_at=time.time() - 10,
        finished_at=time.time(),
        success=True,
        result_text="done",
    )
    engine._append_history(record)
    history = engine.get_history(job_id="other_job")
    assert len(history) == 0


def test_get_history_limit(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    for i in range(5):
        record = JobExecutionRecord(
            job_id=f"job_{i}",
            job_name=f"Test {i}",
            started_at=time.time() - 10,
            finished_at=time.time(),
            success=True,
            result_text=f"result {i}",
        )
        engine._append_history(record)
    history = engine.get_history(limit=3)
    assert len(history) == 3


# ── _next_due_job ────────────────────────────────────────────────


def test_next_due_job_empty(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job, _wait = engine._next_due_job()
    assert job is None


def test_next_due_job_with_due(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(next_run_at=time.time() - 10)
    engine._jobs.append(job)
    found, wait = engine._next_due_job()
    assert found is not None
    assert found.id == "job_001"
    assert wait == 0


def test_next_due_job_future(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(next_run_at=time.time() + 300)
    engine._jobs.append(job)
    found, wait = engine._next_due_job()
    assert found is not None
    assert wait > 0


def test_next_due_job_disabled_skipped(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(enabled=False, next_run_at=time.time() - 10)
    engine._jobs.append(job)
    found, _wait = engine._next_due_job()
    assert found is None


# ── _compute_next_run ────────────────────────────────────────────


def test_compute_next_run_interval(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(schedule_type="interval", interval_seconds=3600)
    result = engine._compute_next_run(job)
    assert result is not None
    assert result > time.time()


def test_compute_next_run_once_future(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    future = time.time() + 3600
    job = _mk_job(schedule_type="once", once_at=future)
    result = engine._compute_next_run(job)
    assert result == future


def test_compute_next_run_once_past(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    past = time.time() - 3600
    job = _mk_job(schedule_type="once", once_at=past)
    result = engine._compute_next_run(job)
    assert result is None


def test_compute_next_run_disabled(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(enabled=False)
    result = engine._compute_next_run(job)
    assert result is None


# ── _recalculate_all_next_run ────────────────────────────────────


def test_recalculate_all_next_run(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job1 = _mk_job(id="j1", schedule_type="interval", interval_seconds=3600)
    job2 = _mk_job(id="j2", schedule_type="interval", interval_seconds=7200, enabled=False)
    engine._jobs = [job1, job2]
    engine._recalculate_all_next_run()
    assert job1.next_run_at is not None
    assert job2.next_run_at is None  # disabled


# ── _load_jobs / _persist_jobs ───────────────────────────────────


def test_persist_and_load_jobs(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job()
    engine.add_job(job)
    # Create a new engine and load
    engine2 = _mk_engine(tmp_path)
    engine2._load_jobs()
    assert len(engine2._jobs) == 1
    assert engine2._jobs[0].id == "job_001"


# ── _write_log_file ──────────────────────────────────────────────


def test_write_log_file(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    record = JobExecutionRecord(
        job_id="job_001",
        job_name="Test",
        started_at=time.time() - 10,
        finished_at=time.time(),
        success=True,
        result_text="All good",
    )
    engine._write_log_file(record)
    logs = list(engine._logs_dir.glob("*.log"))
    assert len(logs) == 1
    content = logs[0].read_text()
    assert "All good" in content


def test_write_log_file_with_error(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    record = JobExecutionRecord(
        job_id="job_001",
        job_name="Test",
        started_at=time.time() - 10,
        finished_at=time.time(),
        success=False,
        result_text="",
        error="Something failed",
    )
    engine._write_log_file(record)
    logs = list(engine._logs_dir.glob("*.log"))
    content = logs[0].read_text()
    assert "Something failed" in content


# ── wake ─────────────────────────────────────────────────────────


def test_wake(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    engine.wake()
    assert engine._wake_event.is_set()


# ── stop ─────────────────────────────────────────────────────────


def test_stop_without_thread(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    engine.stop()  # Should not raise


# ── _execute with AgentRunner (enqueue_ingress branch) ───────────


def test_execute_with_agent_runner(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    enqueued: list[dict[str, Any]] = []

    class FakeAgentRunner:
        def enqueue_ingress(self, session_id, prompt, **kwargs):
            enqueued.append({"session_id": session_id, "prompt": prompt, **kwargs})

    # Patch isinstance check
    from hermit.runtime.control.runner import runner as runner_mod

    with patch.object(runner_mod, "AgentRunner", FakeAgentRunner):
        engine._runner = FakeAgentRunner()
        job = _mk_job(schedule_type="interval", interval_seconds=3600)
        engine._jobs.append(job)
        engine._execute(job)

    assert len(enqueued) == 1
    assert job.last_run_at is not None


def test_execute_with_agent_runner_once_disables(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)

    class FakeAgentRunner:
        def enqueue_ingress(self, session_id, prompt, **kwargs):
            pass

    from hermit.runtime.control.runner import runner as runner_mod

    with patch.object(runner_mod, "AgentRunner", FakeAgentRunner):
        engine._runner = FakeAgentRunner()
        job = _mk_job(schedule_type="once", once_at=time.time() + 60)
        engine._jobs.append(job)
        engine._execute(job)

    assert job.enabled is False
    assert job.next_run_at is None


def test_execute_with_feishu_chat_id(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    enqueued: list[dict[str, Any]] = []

    class FakeAgentRunner:
        def enqueue_ingress(self, session_id, prompt, **kwargs):
            enqueued.append(kwargs)

    from hermit.runtime.control.runner import runner as runner_mod

    with patch.object(runner_mod, "AgentRunner", FakeAgentRunner):
        engine._runner = FakeAgentRunner()
        job = _mk_job(feishu_chat_id="oc_test_chat")
        engine._jobs.append(job)
        engine._execute(job)

    assert enqueued[0]["notify"]["feishu_chat_id"] == "oc_test_chat"


# ── _execute fallback (non-AgentRunner) ──────────────────────────


def test_execute_fallback_success(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    hooks = engine._hooks
    result_obj = SimpleNamespace(text="Result text")

    with patch.object(engine, "_run_agent", return_value=result_obj):
        job = _mk_job(schedule_type="interval", interval_seconds=3600)
        engine._jobs.append(job)
        engine._execute(job)

    hooks.fire.assert_called_once()
    call_kwargs = hooks.fire.call_args
    assert call_kwargs[1]["success"] is True


def test_execute_fallback_failure(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    hooks = engine._hooks

    with patch.object(engine, "_run_agent", side_effect=RuntimeError("boom")):
        job = _mk_job(schedule_type="interval", interval_seconds=3600, max_retries=2)
        engine._jobs.append(job)
        engine._execute(job)

    hooks.fire.assert_called_once()
    call_kwargs = hooks.fire.call_args
    assert call_kwargs[1]["success"] is False
    assert "boom" in call_kwargs[1]["error"]


def test_execute_fallback_once_disables(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    result_obj = SimpleNamespace(text="Result text")

    with patch.object(engine, "_run_agent", return_value=result_obj):
        job = _mk_job(schedule_type="once", once_at=time.time() + 60)
        engine._jobs.append(job)
        engine._execute(job)

    assert job.enabled is False
    assert job.next_run_at is None


def test_execute_fallback_with_feishu_notify(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    engine._settings.scheduler_feishu_chat_id = "oc_global_chat"
    result_obj = SimpleNamespace(text="Result text")

    with patch.object(engine, "_run_agent", return_value=result_obj):
        job = _mk_job(schedule_type="interval", interval_seconds=3600)
        engine._jobs.append(job)
        engine._execute(job)

    call_kwargs = engine._hooks.fire.call_args
    assert call_kwargs[1]["notify"]["feishu_chat_id"] == "oc_global_chat"


# ── _catchup_missed_jobs ────────────────────────────────────────


def test_catchup_no_missed(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(next_run_at=time.time() + 3600)
    engine._jobs.append(job)
    # Should not execute anything
    with patch.object(engine, "_execute") as mock_exec:
        engine._catchup_missed_jobs()
        mock_exec.assert_not_called()


def test_catchup_with_missed(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(next_run_at=time.time() - 100)
    engine._jobs.append(job)
    with patch.object(engine, "_execute") as mock_exec:
        engine._catchup_missed_jobs()
        mock_exec.assert_called_once_with(job)


def test_catchup_disabled_skipped(tmp_path: Path) -> None:
    engine = _mk_engine(tmp_path)
    job = _mk_job(next_run_at=time.time() - 100, enabled=False)
    engine._jobs.append(job)
    with patch.object(engine, "_execute") as mock_exec:
        engine._catchup_missed_jobs()
        mock_exec.assert_not_called()


# ── Constants ────────────────────────────────────────────────────


def test_constants() -> None:
    assert _RESULT_TEXT_LIMIT == 4000
    assert _HISTORY_MAX_RECORDS == 200
    assert _POLL_INTERVAL == 60
