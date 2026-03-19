from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO, cast

from hermit.kernel.execution.coordination.observation import observation_envelope
from hermit.runtime.control.lifecycle.budgets import Deadline, ExecutionBudget, get_runtime_budget

_RECENT_LINE_BUFFER = 200
_COARSE_OBSERVATION_GRACE_SECONDS = 0.1


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class _ObservedProcess:
    job_id: str
    command: str
    cwd: Path | None
    proc: subprocess.Popen[str]
    deadline: Deadline
    created_at: float
    display_name: str
    ready_patterns: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    failure_patterns: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    progress_patterns: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    ready_return: bool = False
    coarse_observation_emitted: bool = False
    cancelled: bool = False
    completed: bool = False
    returncode: int | None = None
    completed_at: float | None = None
    stdout_chunks: list[str] = field(default_factory=list[str])
    stderr_chunks: list[str] = field(default_factory=list[str])
    pending_events: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    recent_events: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=_RECENT_LINE_BUFFER)
    )
    reader_threads: list[threading.Thread] = field(default_factory=list[threading.Thread])
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class CommandSandbox:
    """Budget-aware command executor for L0/L1 modes."""

    def __init__(
        self,
        mode: str = "l0",
        timeout_seconds: float = 30,
        cwd: Path | None = None,
        budget: ExecutionBudget | None = None,
    ) -> None:
        if mode not in {"l0", "l1"}:
            raise ValueError(f"Unsupported sandbox mode: {mode}")
        self.mode = mode
        base_budget = budget or get_runtime_budget()
        soft = float(timeout_seconds or 0) or base_budget.tool_soft_deadline
        self.budget = ExecutionBudget(
            ingress_ack_deadline=base_budget.ingress_ack_deadline,
            provider_connect_timeout=base_budget.provider_connect_timeout,
            provider_read_timeout=base_budget.provider_read_timeout,
            provider_stream_idle_timeout=base_budget.provider_stream_idle_timeout,
            tool_soft_deadline=soft,
            tool_hard_deadline=max(base_budget.tool_hard_deadline, soft),
            observation_window=base_budget.observation_window,
            observation_poll_interval=base_budget.observation_poll_interval,
        )
        self.timeout_seconds = soft
        self.cwd = cwd
        self._jobs: dict[str, _ObservedProcess] = {}
        self._terminal_results: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def run(self, command: str | dict[str, Any]) -> CommandResult | dict[str, Any]:
        payload = self._normalize_payload(command)
        command_text = str(payload["command"])
        deadline = self.budget.tool_deadline()
        proc = subprocess.Popen(
            command_text,
            shell=True,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        observed = _ObservedProcess(
            job_id=f"proc_{uuid.uuid4().hex[:10]}",
            command=command_text,
            cwd=self.cwd,
            proc=proc,
            deadline=deadline,
            created_at=time.time(),
            display_name=str(
                payload.get("display_name", "") or self._default_display_name(command_text)
            ),
            ready_patterns=self._normalize_pattern_rules(payload.get("ready_patterns")),
            failure_patterns=self._normalize_pattern_rules(payload.get("failure_patterns")),
            progress_patterns=self._normalize_progress_rules(payload.get("progress_patterns")),
            ready_return=bool(payload.get("ready_return", False)),
        )
        self._start_reader_threads(observed)

        try:
            proc.wait(timeout=max(deadline.soft_remaining(), 0.1))
        except subprocess.TimeoutExpired:
            with self._lock:
                self._jobs[observed.job_id] = observed
            summary = f"{observed.display_name} is still running."
            return observation_envelope(
                {
                    "observer_kind": "local_process",
                    "job_id": observed.job_id,
                    "status_ref": observed.job_id,
                    "poll_after_seconds": self.budget.observation_poll_interval,
                    "cancel_supported": True,
                    "resume_token": observed.job_id,
                    "topic_summary": summary,
                    "display_name": observed.display_name,
                    "ready_patterns": observed.ready_patterns,
                    "failure_patterns": observed.failure_patterns,
                    "progress_patterns": observed.progress_patterns,
                    "ready_return": observed.ready_return,
                    "started_at": deadline.started_at,
                    "hard_deadline_at": deadline.hard_at,
                }
            )

        observed.returncode = proc.returncode
        observed.completed = True
        self._join_reader_threads(observed)
        stdout, stderr = self._output_text(observed)
        return CommandResult(
            command=command_text,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
        )

    def poll(self, job_id: str) -> dict[str, Any]:
        self._prune_terminal_results()
        with self._lock:
            job = self._jobs.get(job_id)
            cached_terminal = self._terminal_results.get(job_id)
        if job is None:
            if cached_terminal is not None:
                return dict(cached_terminal[1])
            return {
                "status": "failed",
                "topic_summary": f"Observed command {job_id} is no longer available.",
                "result": {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"Observation job not found: {job_id}",
                    "timed_out": False,
                },
                "is_error": True,
            }

        now = time.time()
        new_events = self._drain_pending_events(job)
        matched_progress: dict[str, Any] | None = None
        failure_progress: dict[str, Any] | None = None
        ready_progress: dict[str, Any] | None = None

        for stream_name, line in new_events:
            outcome = self._match_output_rules(job, stream_name, line)
            if outcome is None:
                continue
            progress = dict(outcome["progress"])
            if outcome.get("failed"):
                failure_progress = progress
                break
            matched_progress = progress
            if outcome.get("ready"):
                ready_progress = progress
                if job.ready_return:
                    break

        if job.cancelled:
            return {
                "status": "cancelled",
                "topic_summary": f"{job.display_name} was cancelled.",
                "progress": {
                    "phase": "cancelled",
                    "summary": f"{job.display_name} was cancelled.",
                },
                "result": {
                    "returncode": 130,
                    "stdout": self._output_text(job)[0],
                    "stderr": self._output_text(job)[1] or "cancelled",
                    "timed_out": False,
                },
                "is_error": True,
            }

        if failure_progress is not None:
            self._terminate_job(job)
            stdout, stderr = self._output_text(job)
            payload = {
                "status": "failed",
                "topic_summary": failure_progress["summary"],
                "progress": failure_progress,
                "result": {
                    "returncode": job.returncode if job.returncode is not None else 1,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                },
                "is_error": True,
            }
            self._store_terminal_result(job_id, payload, now=now)
            with self._lock:
                self._jobs.pop(job_id, None)
            return payload

        if ready_progress is not None and job.ready_return:
            payload = {
                "status": "observing",
                "topic_summary": ready_progress["summary"],
                "progress": ready_progress,
                "result": {
                    "job_id": job.job_id,
                    "pid": job.proc.pid,
                    "ready": True,
                    "running": job.proc.poll() is None,
                },
                "is_error": False,
            }
            self._store_terminal_result(job_id, payload, now=now)
            with self._lock:
                self._jobs.pop(job_id, None)
            return payload

        running = job.proc.poll() is None
        if running and self._should_briefly_wait_for_completion(
            job,
            matched_progress=matched_progress,
            ready_progress=ready_progress,
            failure_progress=failure_progress,
        ):
            try:
                job.proc.wait(timeout=min(self.budget.observation_poll_interval, 0.1))
            except subprocess.TimeoutExpired:
                pass
            running = job.proc.poll() is None

        if running:
            if job.deadline.hard_exceeded():
                self._terminate_job(job, force=True)
                stdout, stderr = self._output_text(job)
                timeout_progress = {
                    "phase": "timeout",
                    "summary": f"{job.display_name} timed out.",
                }
                payload = {
                    "status": "timeout",
                    "topic_summary": timeout_progress["summary"],
                    "progress": timeout_progress,
                    "result": {
                        "returncode": 124,
                        "stdout": stdout,
                        "stderr": stderr,
                        "timed_out": True,
                    },
                    "is_error": True,
                }
                self._store_terminal_result(job_id, payload, now=now)
                with self._lock:
                    self._jobs.pop(job_id, None)
                return payload
            progress = matched_progress
            if progress is None:
                progress = self._coarse_running_progress(job)
                job.coarse_observation_emitted = True
            return self._observing_payload(
                job,
                progress=progress,
                poll_after_seconds=self.budget.observation_poll_interval,
            )

        job.returncode = job.proc.returncode
        job.completed = True
        if job.completed_at is None:
            job.completed_at = now
        if self._should_extend_coarse_observation(
            job,
            now=now,
            matched_progress=matched_progress,
            ready_progress=ready_progress,
            failure_progress=failure_progress,
        ):
            job.coarse_observation_emitted = True
            return self._observing_payload(
                job,
                progress=self._coarse_running_progress(job),
                poll_after_seconds=min(self.budget.observation_poll_interval, 0.05),
            )
        self._join_reader_threads(job)
        stdout, stderr = self._output_text(job)
        status = "completed" if job.proc.returncode == 0 else "failed"
        progress = matched_progress
        topic_summary = f"{job.display_name} finished ({status})."
        if progress and progress.get("summary"):
            topic_summary = str(progress["summary"])
        payload = {
            "status": status,
            "topic_summary": topic_summary,
            "progress": progress,
            "result": {
                "returncode": job.proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
            },
            "is_error": job.proc.returncode != 0,
        }
        self._store_terminal_result(job_id, payload, now=now)
        with self._lock:
            self._jobs.pop(job_id, None)
        return payload

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        try:
            job.proc.terminate()
        except OSError:
            return False
        job.cancelled = True
        return True

    def _normalize_payload(self, command: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(command, dict):
            payload = dict(command)
        else:
            payload = {"command": str(command)}
        if not str(payload.get("command", "") or "").strip():
            raise ValueError("CommandSandbox requires a non-empty command")
        return payload

    def _default_display_name(self, command: str) -> str:
        snippet = command.strip().splitlines()[0][:80]
        return snippet or "Command"

    def _normalize_pattern_rules(self, value: Any) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for item in list(value or []):
            if isinstance(item, str) and item.strip():
                rules.append({"pattern": item.strip()})
            elif isinstance(item, dict):
                typed_item = cast(dict[str, Any], item)
                if str(typed_item.get("pattern", "") or "").strip():
                    rules.append(dict(typed_item))
        return rules

    def _normalize_progress_rules(self, value: Any) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for item in list(value or []):
            if not isinstance(item, dict):
                continue
            typed_item = cast(dict[str, Any], item)
            pattern = str(typed_item.get("pattern", "") or "").strip()
            if not pattern:
                continue
            rules.append(dict(typed_item))
        return rules

    def _start_reader_threads(self, job: _ObservedProcess) -> None:
        if job.proc.stdout is not None:
            thread = threading.Thread(
                target=self._reader_loop,
                args=(job, "stdout", job.proc.stdout),
                daemon=True,
                name=f"{job.job_id}-stdout",
            )
            thread.start()
            job.reader_threads.append(thread)
        if job.proc.stderr is not None:
            thread = threading.Thread(
                target=self._reader_loop,
                args=(job, "stderr", job.proc.stderr),
                daemon=True,
                name=f"{job.job_id}-stderr",
            )
            thread.start()
            job.reader_threads.append(thread)

    def _reader_loop(self, job: _ObservedProcess, stream_name: str, stream: TextIO) -> None:
        try:
            for raw in iter(stream.readline, ""):
                if raw == "":
                    break
                line = raw.rstrip("\r\n")
                with job.lock:
                    if stream_name == "stdout":
                        job.stdout_chunks.append(raw)
                    else:
                        job.stderr_chunks.append(raw)
                    if line:
                        job.pending_events.append((stream_name, line))
                        job.recent_events.append((stream_name, line))
        finally:
            stream.close()

    def _join_reader_threads(self, job: _ObservedProcess) -> None:
        for thread in job.reader_threads:
            thread.join(timeout=0.5)

    def _output_text(self, job: _ObservedProcess) -> tuple[str, str]:
        with job.lock:
            stdout = "".join(job.stdout_chunks)
            stderr = "".join(job.stderr_chunks)
        return stdout, stderr

    def _drain_pending_events(self, job: _ObservedProcess) -> list[tuple[str, str]]:
        with job.lock:
            events = list(job.pending_events)
            job.pending_events.clear()
        return events

    def _pattern_match(self, rule: dict[str, Any], line: str) -> re.Match[str] | None:
        pattern = str(rule.get("pattern", "") or "")
        if not pattern:
            return None
        try:
            return re.search(pattern, line)
        except re.error:
            return None

    def _render_text(
        self,
        template: str | None,
        *,
        match: re.Match[str] | None,
        line: str,
        stream_name: str,
        display_name: str,
    ) -> str:
        text = str(template or "").strip()
        if not text:
            return line
        fields = {"line": line, "stream": stream_name, "display_name": display_name}
        if match is not None:
            fields.update(
                {key: value for key, value in match.groupdict().items() if value is not None}
            )
        try:
            return text.format(**fields)
        except Exception:
            return text

    def _progress_from_rule(
        self,
        job: _ObservedProcess,
        rule: dict[str, Any],
        *,
        match: re.Match[str] | None,
        line: str,
        stream_name: str,
        default_phase: str,
        default_summary: str,
        ready: bool = False,
    ) -> dict[str, Any]:
        percent_value = rule.get("progress_percent")
        try:
            progress_percent = int(percent_value) if percent_value is not None else None
        except (TypeError, ValueError):
            progress_percent = None
        detail = self._render_text(
            str(rule.get("detail", "") or "") or None,
            match=match,
            line=line,
            stream_name=stream_name,
            display_name=job.display_name,
        )
        if not detail:
            detail = line
        return {
            "phase": str(rule.get("phase", "") or default_phase),
            "summary": self._render_text(
                str(rule.get("summary", "") or "") or default_summary,
                match=match,
                line=line,
                stream_name=stream_name,
                display_name=job.display_name,
            ),
            "detail": detail,
            "progress_percent": progress_percent,
            "ready": bool(rule.get("ready", ready) or ready),
        }

    def _match_output_rules(
        self,
        job: _ObservedProcess,
        stream_name: str,
        line: str,
    ) -> dict[str, Any] | None:
        for rule in job.failure_patterns:
            match = self._pattern_match(rule, line)
            if match is None:
                continue
            return {
                "failed": True,
                "progress": self._progress_from_rule(
                    job,
                    rule,
                    match=match,
                    line=line,
                    stream_name=stream_name,
                    default_phase="failed",
                    default_summary=f"{job.display_name} reported a failure.",
                ),
            }
        for rule in job.ready_patterns:
            match = self._pattern_match(rule, line)
            if match is None:
                continue
            return {
                "ready": True,
                "progress": self._progress_from_rule(
                    job,
                    rule,
                    match=match,
                    line=line,
                    stream_name=stream_name,
                    default_phase="ready",
                    default_summary=f"{job.display_name} is ready.",
                    ready=True,
                ),
            }
        for rule in job.progress_patterns:
            match = self._pattern_match(rule, line)
            if match is None:
                continue
            progress = self._progress_from_rule(
                job,
                rule,
                match=match,
                line=line,
                stream_name=stream_name,
                default_phase="running",
                default_summary=line,
            )
            return {
                "ready": bool(progress.get("ready", False)),
                "progress": progress,
            }
        return None

    def _terminate_job(self, job: _ObservedProcess, *, force: bool = False) -> None:
        try:
            if force:
                job.proc.kill()
            else:
                job.proc.terminate()
                try:
                    job.proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    job.proc.kill()
        except OSError:
            pass
        job.returncode = job.proc.poll()
        job.completed = True
        if job.completed_at is None:
            job.completed_at = time.time()
        self._join_reader_threads(job)

    def _coarse_running_progress(self, job: _ObservedProcess) -> dict[str, Any]:
        return {
            "phase": "running",
            "summary": f"{job.display_name} is still running.",
        }

    def _observing_payload(
        self,
        job: _ObservedProcess,
        *,
        progress: dict[str, Any],
        poll_after_seconds: float,
    ) -> dict[str, Any]:
        summary = str(progress.get("summary", "") or self._coarse_running_progress(job)["summary"])
        return {
            "status": "observing",
            "topic_summary": summary,
            "progress": progress,
            "poll_after_seconds": poll_after_seconds,
        }

    def _has_observation_output(self, job: _ObservedProcess) -> bool:
        with job.lock:
            return bool(
                job.pending_events or job.recent_events or job.stdout_chunks or job.stderr_chunks
            )

    def _should_extend_coarse_observation(
        self,
        job: _ObservedProcess,
        *,
        now: float,
        matched_progress: dict[str, Any] | None,
        ready_progress: dict[str, Any] | None,
        failure_progress: dict[str, Any] | None,
    ) -> bool:
        if (
            matched_progress is not None
            or ready_progress is not None
            or failure_progress is not None
        ):
            return False
        if self._has_observation_output(job):
            return False
        if job.coarse_observation_emitted:
            return False
        completed_at = job.completed_at or now
        return (
            completed_at - job.created_at < _COARSE_OBSERVATION_GRACE_SECONDS
            and now - completed_at < _COARSE_OBSERVATION_GRACE_SECONDS
        )

    def _should_briefly_wait_for_completion(
        self,
        job: _ObservedProcess,
        *,
        matched_progress: dict[str, Any] | None,
        ready_progress: dict[str, Any] | None,
        failure_progress: dict[str, Any] | None,
    ) -> bool:
        if not job.coarse_observation_emitted:
            return False
        if (
            matched_progress is not None
            or ready_progress is not None
            or failure_progress is not None
        ):
            return False
        return not self._has_observation_output(job)

    def _store_terminal_result(
        self,
        job_id: str,
        payload: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else now
        ttl = max(float(self.budget.observation_poll_interval or 0.0) * 2, 1.0)
        ttl = min(ttl, float(self.budget.observation_window or ttl))
        with self._lock:
            self._terminal_results[job_id] = (current + ttl, dict(payload))

    def _prune_terminal_results(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._lock:
            expired = [
                job_id
                for job_id, (expires_at, _payload) in self._terminal_results.items()
                if expires_at <= current
            ]
            for job_id in expired:
                self._terminal_results.pop(job_id, None)
