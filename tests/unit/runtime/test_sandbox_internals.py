"""Tests for sandbox.py internals — CommandSandbox methods not covered by test_tools.py."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.runtime.control.lifecycle.budgets import Deadline, ExecutionBudget
from hermit.runtime.provider_host.execution.sandbox import (
    CommandResult,
    CommandSandbox,
    _ObservedProcess,
)

# ── CommandSandbox init ───────────────────────────────────────────


def test_sandbox_init_default_l0(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    assert sandbox.mode == "l0"
    assert sandbox.cwd == tmp_path


def test_sandbox_init_l1(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l1", cwd=tmp_path)
    assert sandbox.mode == "l1"


def test_sandbox_init_invalid_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported sandbox mode"):
        CommandSandbox(mode="l2")


def test_sandbox_init_custom_budget(tmp_path: Path) -> None:
    budget = ExecutionBudget(tool_soft_deadline=30.0, tool_hard_deadline=60.0)
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, budget=budget)
    assert sandbox.budget.tool_soft_deadline == 30.0


def test_sandbox_init_timeout_overrides_budget(tmp_path: Path) -> None:
    budget = ExecutionBudget(tool_soft_deadline=30.0, tool_hard_deadline=60.0)
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=10.0, budget=budget)
    assert sandbox.budget.tool_soft_deadline == 10.0


# ── _normalize_payload ────────────────────────────────────────────


def test_normalize_payload_string() -> None:
    sandbox = CommandSandbox(mode="l0")
    payload = sandbox._normalize_payload("echo hello")
    assert payload == {"command": "echo hello"}


def test_normalize_payload_dict() -> None:
    sandbox = CommandSandbox(mode="l0")
    payload = sandbox._normalize_payload({"command": "ls", "display_name": "List"})
    assert payload["command"] == "ls"
    assert payload["display_name"] == "List"


def test_normalize_payload_empty_raises() -> None:
    sandbox = CommandSandbox(mode="l0")
    with pytest.raises(ValueError, match="non-empty command"):
        sandbox._normalize_payload({"command": ""})


def test_normalize_payload_whitespace_raises() -> None:
    sandbox = CommandSandbox(mode="l0")
    with pytest.raises(ValueError, match="non-empty command"):
        sandbox._normalize_payload({"command": "   "})


# ── _default_display_name ─────────────────────────────────────────


def test_default_display_name_short() -> None:
    sandbox = CommandSandbox(mode="l0")
    assert sandbox._default_display_name("echo hello") == "echo hello"


def test_default_display_name_multiline() -> None:
    sandbox = CommandSandbox(mode="l0")
    name = sandbox._default_display_name("echo hello\necho world")
    assert name == "echo hello"


def test_default_display_name_long() -> None:
    sandbox = CommandSandbox(mode="l0")
    long_cmd = "a" * 200
    name = sandbox._default_display_name(long_cmd)
    assert len(name) <= 80


def test_default_display_name_single_word() -> None:
    sandbox = CommandSandbox(mode="l0")
    name = sandbox._default_display_name("ls")
    assert name == "ls"


# ── _normalize_pattern_rules ──────────────────────────────────────


def test_normalize_pattern_rules_none() -> None:
    sandbox = CommandSandbox(mode="l0")
    assert sandbox._normalize_pattern_rules(None) == []


def test_normalize_pattern_rules_strings() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_pattern_rules(["READY", "STARTED"])
    assert len(rules) == 2
    assert rules[0] == {"pattern": "READY"}
    assert rules[1] == {"pattern": "STARTED"}


def test_normalize_pattern_rules_dicts() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_pattern_rules(
        [
            {"pattern": r"READY (?P<url>\S+)", "summary": "Ready at {url}"},
        ]
    )
    assert len(rules) == 1
    assert rules[0]["pattern"] == r"READY (?P<url>\S+)"


def test_normalize_pattern_rules_empty_string_skipped() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_pattern_rules(["", "  ", "OK"])
    assert len(rules) == 1


def test_normalize_pattern_rules_dict_empty_pattern_skipped() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_pattern_rules([{"pattern": ""}, {"pattern": "OK"}])
    assert len(rules) == 1


# ── _normalize_progress_rules ─────────────────────────────────────


def test_normalize_progress_rules_none() -> None:
    sandbox = CommandSandbox(mode="l0")
    assert sandbox._normalize_progress_rules(None) == []


def test_normalize_progress_rules_non_dict_skipped() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_progress_rules(["not a dict"])
    assert len(rules) == 0


def test_normalize_progress_rules_valid() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_progress_rules(
        [
            {"pattern": r"Step (\d+)", "phase": "running"},
        ]
    )
    assert len(rules) == 1


def test_normalize_progress_rules_empty_pattern_skipped() -> None:
    sandbox = CommandSandbox(mode="l0")
    rules = sandbox._normalize_progress_rules([{"pattern": "", "phase": "x"}])
    assert len(rules) == 0


# ── _pattern_match ────────────────────────────────────────────────


def test_pattern_match_valid() -> None:
    sandbox = CommandSandbox(mode="l0")
    match = sandbox._pattern_match({"pattern": r"READY"}, "Server READY on port 3000")
    assert match is not None


def test_pattern_match_no_match() -> None:
    sandbox = CommandSandbox(mode="l0")
    match = sandbox._pattern_match({"pattern": r"READY"}, "Still booting...")
    assert match is None


def test_pattern_match_empty_pattern() -> None:
    sandbox = CommandSandbox(mode="l0")
    match = sandbox._pattern_match({"pattern": ""}, "anything")
    assert match is None


def test_pattern_match_invalid_regex() -> None:
    sandbox = CommandSandbox(mode="l0")
    match = sandbox._pattern_match({"pattern": "[invalid"}, "test")
    assert match is None


def test_pattern_match_no_pattern_key() -> None:
    sandbox = CommandSandbox(mode="l0")
    match = sandbox._pattern_match({}, "test")
    assert match is None


# ── _render_text ──────────────────────────────────────────────────


def test_render_text_no_template() -> None:
    sandbox = CommandSandbox(mode="l0")
    result = sandbox._render_text(
        None, match=None, line="hello", stream_name="stdout", display_name="cmd"
    )
    assert result == "hello"


def test_render_text_empty_template() -> None:
    sandbox = CommandSandbox(mode="l0")
    result = sandbox._render_text(
        "", match=None, line="hello", stream_name="stdout", display_name="cmd"
    )
    assert result == "hello"


def test_render_text_with_fields() -> None:
    sandbox = CommandSandbox(mode="l0")
    result = sandbox._render_text(
        "{display_name} on {stream}",
        match=None,
        line="data",
        stream_name="stderr",
        display_name="Server",
    )
    assert result == "Server on stderr"


def test_render_text_with_match_groups() -> None:
    sandbox = CommandSandbox(mode="l0")
    m = re.search(r"port (?P<port>\d+)", "Listening on port 3000")
    result = sandbox._render_text(
        "Ready on port {port}",
        match=m,
        line="Listening on port 3000",
        stream_name="stdout",
        display_name="Server",
    )
    assert result == "Ready on port 3000"


def test_render_text_format_error_returns_template() -> None:
    sandbox = CommandSandbox(mode="l0")
    result = sandbox._render_text(
        "{missing_var}",
        match=None,
        line="data",
        stream_name="stdout",
        display_name="cmd",
    )
    assert result == "{missing_var}"


# ── _progress_from_rule ───────────────────────────────────────────


def _make_observed_process(**kwargs: Any) -> _ObservedProcess:
    defaults: dict[str, Any] = {
        "job_id": "job-1",
        "command": "echo test",
        "cwd": None,
        "proc": MagicMock(),
        "deadline": MagicMock(),
        "created_at": time.time(),
        "display_name": "TestCmd",
    }
    defaults.update(kwargs)
    return _ObservedProcess(**defaults)


def test_progress_from_rule_basic() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    rule = {"phase": "building", "summary": "Building..."}

    progress = sandbox._progress_from_rule(
        job,
        rule,
        match=None,
        line="compiling",
        stream_name="stdout",
        default_phase="running",
        default_summary="default",
    )
    assert progress["phase"] == "building"
    assert progress["summary"] == "Building..."


def test_progress_from_rule_defaults() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    rule = {}

    progress = sandbox._progress_from_rule(
        job,
        rule,
        match=None,
        line="data",
        stream_name="stdout",
        default_phase="running",
        default_summary="default summary",
    )
    assert progress["phase"] == "running"
    assert progress["summary"] == "default summary"


def test_progress_from_rule_progress_percent() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    rule = {"progress_percent": 50}

    progress = sandbox._progress_from_rule(
        job,
        rule,
        match=None,
        line="half done",
        stream_name="stdout",
        default_phase="running",
        default_summary="default",
    )
    assert progress["progress_percent"] == 50


def test_progress_from_rule_invalid_percent() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    rule = {"progress_percent": "not_a_number"}

    progress = sandbox._progress_from_rule(
        job,
        rule,
        match=None,
        line="data",
        stream_name="stdout",
        default_phase="running",
        default_summary="default",
    )
    assert progress["progress_percent"] is None


def test_progress_from_rule_ready_flag() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    rule = {}

    progress = sandbox._progress_from_rule(
        job,
        rule,
        match=None,
        line="data",
        stream_name="stdout",
        default_phase="ready",
        default_summary="Ready",
        ready=True,
    )
    assert progress["ready"] is True


# ── _match_output_rules ───────────────────────────────────────────


def test_match_output_rules_failure_pattern() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(
        failure_patterns=[{"pattern": r"ERROR"}],
    )

    result = sandbox._match_output_rules(job, "stderr", "ERROR: something failed")
    assert result is not None
    assert result["failed"] is True


def test_match_output_rules_ready_pattern() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(
        ready_patterns=[{"pattern": r"READY"}],
    )

    result = sandbox._match_output_rules(job, "stdout", "Server READY")
    assert result is not None
    assert result["ready"] is True


def test_match_output_rules_progress_pattern() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(
        progress_patterns=[{"pattern": r"Step \d+", "phase": "running"}],
    )

    result = sandbox._match_output_rules(job, "stdout", "Step 3 done")
    assert result is not None
    assert result["progress"]["phase"] == "running"


def test_match_output_rules_no_match() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(
        failure_patterns=[{"pattern": r"FATAL"}],
        ready_patterns=[{"pattern": r"READY"}],
    )

    result = sandbox._match_output_rules(job, "stdout", "normal output")
    assert result is None


def test_match_output_rules_failure_takes_priority() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(
        failure_patterns=[{"pattern": r"ERROR"}],
        ready_patterns=[{"pattern": r"ERROR"}],
    )

    result = sandbox._match_output_rules(job, "stdout", "ERROR found")
    assert result["failed"] is True


# ── run — quick command ───────────────────────────────────────────


def test_run_quick_command_success(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=10)
    result = sandbox.run("echo hello")
    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


def test_run_quick_command_failure(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=10)
    result = sandbox.run("exit 1")
    assert isinstance(result, CommandResult)
    assert result.returncode == 1


def test_run_dict_payload(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path, timeout_seconds=10)
    result = sandbox.run({"command": "echo test", "display_name": "Test"})
    assert isinstance(result, CommandResult)
    assert result.returncode == 0


# ── cancel ────────────────────────────────────────────────────────


def test_cancel_nonexistent_job(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    assert sandbox.cancel("nonexistent") is False


# ── poll — nonexistent job ────────────────────────────────────────


def test_poll_nonexistent_job(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    result = sandbox.poll("nonexistent")
    assert result["status"] == "failed"
    assert result["is_error"] is True


# ── _coarse_running_progress ──────────────────────────────────────


def test_coarse_running_progress() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(display_name="My Process")
    progress = sandbox._coarse_running_progress(job)
    assert progress["phase"] == "running"
    assert "My Process" in progress["summary"]


# ── _observing_payload ────────────────────────────────────────────


def test_observing_payload() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(display_name="Server")
    progress = {"phase": "running", "summary": "Server is starting"}
    payload = sandbox._observing_payload(job, progress=progress, poll_after_seconds=5.0)
    assert payload["status"] == "observing"
    assert payload["poll_after_seconds"] == 5.0
    assert payload["topic_summary"] == "Server is starting"


def test_observing_payload_empty_summary() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process(display_name="Server")
    progress = {"phase": "running", "summary": ""}
    payload = sandbox._observing_payload(job, progress=progress, poll_after_seconds=5.0)
    assert "Server" in payload["topic_summary"]


# ── _has_observation_output ───────────────────────────────────────


def test_has_observation_output_empty() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    assert sandbox._has_observation_output(job) is False


def test_has_observation_output_with_events() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.pending_events.append(("stdout", "line"))
    assert sandbox._has_observation_output(job) is True


def test_has_observation_output_with_stdout() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.stdout_chunks.append("data")
    assert sandbox._has_observation_output(job) is True


# ── _should_extend_coarse_observation ─────────────────────────────


def test_should_extend_coarse_observation_with_progress() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    # When matched_progress is set, should not extend
    result = sandbox._should_extend_coarse_observation(
        job,
        now=time.time(),
        matched_progress={"phase": "running"},
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


def test_should_extend_coarse_observation_already_emitted() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.coarse_observation_emitted = True
    result = sandbox._should_extend_coarse_observation(
        job,
        now=time.time(),
        matched_progress=None,
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


def test_should_extend_coarse_observation_has_output() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.stdout_chunks.append("data")
    result = sandbox._should_extend_coarse_observation(
        job,
        now=time.time(),
        matched_progress=None,
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


# ── _should_briefly_wait_for_completion ───────────────────────────


def test_should_briefly_wait_not_emitted() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.coarse_observation_emitted = False
    result = sandbox._should_briefly_wait_for_completion(
        job,
        matched_progress=None,
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


def test_should_briefly_wait_with_progress() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.coarse_observation_emitted = True
    result = sandbox._should_briefly_wait_for_completion(
        job,
        matched_progress={"phase": "x"},
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


def test_should_briefly_wait_with_output() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.coarse_observation_emitted = True
    job.stdout_chunks.append("data")
    result = sandbox._should_briefly_wait_for_completion(
        job,
        matched_progress=None,
        ready_progress=None,
        failure_progress=None,
    )
    assert result is False


def test_should_briefly_wait_true() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.coarse_observation_emitted = True
    result = sandbox._should_briefly_wait_for_completion(
        job,
        matched_progress=None,
        ready_progress=None,
        failure_progress=None,
    )
    assert result is True


# ── _store_terminal_result and _prune_terminal_results ────────────


def test_store_and_retrieve_terminal_result(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    payload = {"status": "completed", "result": {"returncode": 0}}
    now = time.time()
    sandbox._store_terminal_result("job-1", payload, now=now)

    # Directly check internal state instead of poll (which prunes by current time)
    assert "job-1" in sandbox._terminal_results
    _, stored = sandbox._terminal_results["job-1"]
    assert stored["status"] == "completed"


def test_prune_expired_terminal_results(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    payload = {"status": "completed", "result": {"returncode": 0}}
    sandbox._store_terminal_result("job-1", payload, now=100.0)

    # Prune with time far in the future
    sandbox._prune_terminal_results(now=999999.0)
    result = sandbox.poll("job-1")
    # After pruning, job should not be found
    assert result["status"] == "failed"
    assert "no longer available" in result["topic_summary"]


# ── _terminate_job ────────────────────────────────────────────────


def test_terminate_job_graceful() -> None:
    sandbox = CommandSandbox(mode="l0")
    proc = MagicMock()
    proc.wait.return_value = None
    proc.poll.return_value = 0
    job = _make_observed_process(proc=proc)

    sandbox._terminate_job(job)
    proc.terminate.assert_called_once()
    assert job.completed is True
    assert job.returncode == 0


def test_terminate_job_force() -> None:
    sandbox = CommandSandbox(mode="l0")
    proc = MagicMock()
    proc.poll.return_value = -9
    job = _make_observed_process(proc=proc)

    sandbox._terminate_job(job, force=True)
    proc.kill.assert_called_once()
    assert job.completed is True


def test_terminate_job_oserror() -> None:
    sandbox = CommandSandbox(mode="l0")
    proc = MagicMock()
    proc.terminate.side_effect = OSError("already dead")
    proc.poll.return_value = None
    job = _make_observed_process(proc=proc)

    # Should not raise
    sandbox._terminate_job(job)
    assert job.completed is True


# ── _output_text ──────────────────────────────────────────────────


def test_output_text() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.stdout_chunks = ["hello\n", "world\n"]
    job.stderr_chunks = ["err\n"]

    stdout, stderr = sandbox._output_text(job)
    assert stdout == "hello\nworld\n"
    assert stderr == "err\n"


# ── _drain_pending_events ─────────────────────────────────────────


def test_drain_pending_events() -> None:
    sandbox = CommandSandbox(mode="l0")
    job = _make_observed_process()
    job.pending_events = [("stdout", "line1"), ("stderr", "line2")]

    events = sandbox._drain_pending_events(job)
    assert len(events) == 2
    assert len(job.pending_events) == 0


# ── CommandResult ─────────────────────────────────────────────────


def test_command_result_dataclass() -> None:
    r = CommandResult(command="ls", returncode=0, stdout="out", stderr="err")
    assert r.command == "ls"
    assert r.timed_out is False


# ── poll with injected jobs ───────────────────────────────────────


def _inject_job(sandbox: CommandSandbox, job: _ObservedProcess) -> None:
    """Inject a mock job into the sandbox's internal job dict."""
    sandbox._jobs[job.job_id] = job


def test_poll_cancelled_job(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    job = _make_observed_process(proc=proc, display_name="Cancelled Task")
    job.cancelled = True
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "cancelled"
    assert "Cancelled Task" in result["topic_summary"]
    assert result["result"]["returncode"] == 130


def test_poll_failure_pattern_detected(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.terminate.return_value = None
    proc.wait.return_value = None
    proc.returncode = 1
    deadline = Deadline.start(soft_seconds=3600, hard_seconds=3600)
    job = _make_observed_process(
        proc=proc,
        display_name="Build",
        deadline=deadline,
        failure_patterns=[{"pattern": r"FATAL"}],
    )
    job.pending_events = [("stderr", "FATAL: compilation failed")]
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "failed"
    assert result["is_error"] is True


def test_poll_ready_with_ready_return(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    deadline = MagicMock()
    deadline.hard_at = time.time() + 3600
    job = _make_observed_process(
        proc=proc,
        display_name="Server",
        deadline=deadline,
        ready_patterns=[{"pattern": r"READY"}],
        ready_return=True,
    )
    job.pending_events = [("stdout", "Server READY on port 3000")]
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "observing"
    assert result["progress"]["ready"] is True
    assert result["result"]["ready"] is True


def test_poll_running_still(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 0.1)
    deadline = Deadline.start(soft_seconds=3600, hard_seconds=3600)
    job = _make_observed_process(
        proc=proc,
        display_name="Long Task",
        deadline=deadline,
    )
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "observing"
    assert result["progress"]["phase"] == "running"


def test_poll_timeout(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    proc.kill.return_value = None
    deadline = Deadline(
        started_at=time.monotonic() - 10, soft_at=time.monotonic() - 5, hard_at=time.monotonic() - 1
    )
    job = _make_observed_process(
        proc=proc,
        display_name="Stuck Task",
        deadline=deadline,
    )
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "timeout"
    assert result["result"]["timed_out"] is True
    assert result["is_error"] is True


def test_poll_completed_success(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = 0  # completed
    proc.returncode = 0
    deadline = MagicMock()
    deadline.hard_at = time.time() + 3600
    job = _make_observed_process(
        proc=proc,
        display_name="Build",
        deadline=deadline,
    )
    job.coarse_observation_emitted = True  # So it doesn't try to extend
    job.stdout_chunks = ["build done\n"]  # Has output
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "completed"
    assert result["result"]["returncode"] == 0
    assert result["is_error"] is False


def test_poll_completed_failure(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = 1  # failed
    proc.returncode = 1
    deadline = MagicMock()
    deadline.hard_at = time.time() + 3600
    job = _make_observed_process(
        proc=proc,
        display_name="Build",
        deadline=deadline,
    )
    job.coarse_observation_emitted = True
    job.stderr_chunks = ["error\n"]
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "failed"
    assert result["result"]["returncode"] == 1
    assert result["is_error"] is True


def test_poll_with_progress_match(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 0.1)
    deadline = Deadline.start(soft_seconds=3600, hard_seconds=3600)
    job = _make_observed_process(
        proc=proc,
        display_name="Build",
        deadline=deadline,
        progress_patterns=[{"pattern": r"Step (?P<n>\d+)", "summary": "Step {n}"}],
    )
    job.pending_events = [("stdout", "Step 3 of 10")]
    _inject_job(sandbox, job)

    result = sandbox.poll(job.job_id)
    assert result["status"] == "observing"
    assert result["progress"]["summary"] == "Step 3"


def test_poll_cached_terminal_result(tmp_path: Path) -> None:
    sandbox = CommandSandbox(mode="l0", cwd=tmp_path)
    payload = {"status": "completed", "result": {"returncode": 0}}
    # Store with a future expiry
    sandbox._terminal_results["job-cached"] = (time.time() + 3600, payload)

    result = sandbox.poll("job-cached")
    assert result["status"] == "completed"


def test_terminate_job_graceful_with_timeout(tmp_path: Path) -> None:
    """Test terminate with graceful terminate that then times out, forcing kill."""
    sandbox = CommandSandbox(mode="l0")
    proc = MagicMock()
    proc.terminate.return_value = None
    proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 0.2)
    proc.kill.return_value = None
    proc.poll.return_value = -9
    job = _make_observed_process(proc=proc)

    sandbox._terminate_job(job, force=False)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
    assert job.completed is True
