"""Edge-case tests for KernelDispatchService."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

# ---------------------------------------------------------------------------
# Fake objects
# ---------------------------------------------------------------------------


def _fake_attempt(
    step_attempt_id: str = "sa-1",
    step_id: str = "s-1",
    task_id: str = "t-1",
    status: str = "running",
    context: dict[str, Any] | None = None,
    last_heartbeat_at: float | None = None,
    claimed_at: float | None = None,
    started_at: float | None = None,
    capability_grant_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        step_id=step_id,
        task_id=task_id,
        status=status,
        context=context or {},
        last_heartbeat_at=last_heartbeat_at,
        claimed_at=claimed_at,
        started_at=started_at,
        capability_grant_id=capability_grant_id,
    )


def _fake_step(
    step_id: str = "s-1",
    kind: str = "execute",
    attempt: int = 1,
    max_attempts: int = 1,
    status: str = "running",
) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id,
        kind=kind,
        attempt=attempt,
        max_attempts=max_attempts,
        status=status,
    )


class FakeDispatchStore:
    """Store that can be configured for dispatch tests."""

    def __init__(self) -> None:
        self._attempts: dict[str, SimpleNamespace] = {}
        self._steps: dict[str, SimpleNamespace] = {}
        self._attempts_by_status: dict[str, list[SimpleNamespace]] = {}
        self.updates: list[tuple[str, dict]] = []
        self._next_ready: SimpleNamespace | None = None

    def add_attempt(self, attempt: SimpleNamespace) -> None:
        self._attempts[attempt.step_attempt_id] = attempt
        self._attempts_by_status.setdefault(attempt.status, []).append(attempt)

    def add_step(self, step: SimpleNamespace) -> None:
        self._steps[step.step_id] = step

    def get_step_attempt(self, step_attempt_id: str) -> SimpleNamespace | None:
        return self._attempts.get(step_attempt_id)

    def get_step(self, step_id: str) -> SimpleNamespace | None:
        return self._steps.get(step_id)

    def list_step_attempts(self, *, status: str, limit: int = 500) -> list[SimpleNamespace]:
        return self._attempts_by_status.get(status, [])[:limit]

    def update_step_attempt(self, step_attempt_id: str, **kwargs: Any) -> None:
        self.updates.append((step_attempt_id, kwargs))
        attempt = self._attempts.get(step_attempt_id)
        if attempt is not None:
            for k, v in kwargs.items():
                setattr(attempt, k, v)

    def update_step(self, step_id: str, **kwargs: Any) -> None:
        step = self._steps.get(step_id)
        if step is not None:
            for k, v in kwargs.items():
                setattr(step, k, v)

    def retry_step(self, task_id: str, step_id: str) -> None:
        pass

    def propagate_step_failure(self, task_id: str, step_id: str) -> None:
        pass

    def has_non_terminal_steps(self, task_id: str) -> bool:
        return False

    def update_task_status(self, task_id: str, status: str, *, payload: dict | None = None) -> None:
        pass

    def claim_next_ready_step_attempt(self) -> SimpleNamespace | None:
        result = self._next_ready
        self._next_ready = None
        return result

    def append_event(self, **kwargs: Any) -> None:
        pass

    def set_next_ready(self, attempt: SimpleNamespace | None) -> None:
        self._next_ready = attempt

    def close_thread_conn(self) -> None:
        pass

    def touch_heartbeat(self, step_attempt_id: str) -> None:
        self.updates.append((step_attempt_id, {"touch_heartbeat": True}))


def _build_service(store: FakeDispatchStore, worker_count: int = 2) -> KernelDispatchService:
    """Build a KernelDispatchService with a fake runner wired to *store*."""
    controller = SimpleNamespace(store=store)
    runner = SimpleNamespace(
        task_controller=controller,
        process_claimed_attempt=MagicMock(),
    )
    return KernelDispatchService(runner, worker_count=worker_count)


# ---------------------------------------------------------------------------
# Tests: check_deliberation_needed
# ---------------------------------------------------------------------------


class TestCheckDeliberationNeeded:
    """Edge cases for check_deliberation_needed."""

    def test_missing_attempt_returns_false(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store)
        assert svc.check_deliberation_needed("nonexistent") is False

    def test_missing_context_defaults_low_risk(self) -> None:
        """When context has no risk_band, default is 'low' which should not trigger."""
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-1", context={})
        step = _fake_step(step_id="s-1", kind="execute")
        store.add_attempt(attempt)
        store.add_step(step)
        svc = _build_service(store)
        # low risk + execute kind → no deliberation
        assert svc.check_deliberation_needed("sa-1") is False

    def test_high_risk_triggers_deliberation(self) -> None:
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-2", context={"risk_band": "high"})
        step = _fake_step(step_id="s-1", kind="execute_command")
        store.add_attempt(attempt)
        store.add_step(step)
        svc = _build_service(store)
        assert svc.check_deliberation_needed("sa-2") is True

    def test_missing_step_defaults_to_execute_kind(self) -> None:
        """When step is not found, step_kind defaults to 'execute' which is not
        in _MEDIUM_RISK_DELIBERATION_ACTIONS, so medium risk should not trigger."""
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-3",
            context={"risk_band": "medium"},
        )
        store.add_attempt(attempt)
        # No step added — get_step returns None, defaults to "execute"
        svc = _build_service(store)
        # medium risk + "execute" (not a recognized action_class) → no deliberation
        assert svc.check_deliberation_needed("sa-3") is False

    def test_medium_risk_with_mutation_action_triggers(self) -> None:
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-4", context={"risk_band": "medium"})
        step = _fake_step(step_id="s-1", kind="write_local")
        store.add_attempt(attempt)
        store.add_step(step)
        svc = _build_service(store)
        assert svc.check_deliberation_needed("sa-4") is True


# ---------------------------------------------------------------------------
# Tests: dispatch loop — no ready attempts
# ---------------------------------------------------------------------------


class TestDispatchNoReadyAttempts:
    """When claim_next_ready_step_attempt returns None, the loop exits cleanly."""

    def test_capacity_available_with_no_futures(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store, worker_count=4)
        assert svc._capacity_available() is True

    def test_capacity_not_available_when_full(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store, worker_count=1)
        # Simulate a future in-flight
        fake_future = MagicMock()
        fake_future.done.return_value = False
        svc.futures[fake_future] = "sa-1"
        assert svc._capacity_available() is False


# ---------------------------------------------------------------------------
# Tests: heartbeat timeout detection
# ---------------------------------------------------------------------------


class TestHeartbeatTimeout:
    """check_heartbeat_timeouts should fail timed-out attempts."""

    def test_no_heartbeat_interval_skipped(self) -> None:
        """Attempts without heartbeat_interval_seconds are not checked."""
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            status="running",
            context={},
            claimed_at=time.time() - 9999,
        )
        store.add_attempt(attempt)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        # No updates should have been made
        assert store.updates == []

    def test_heartbeat_within_interval_not_timed_out(self) -> None:
        now = time.time()
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            status="running",
            context={"heartbeat_interval_seconds": 60},
            last_heartbeat_at=now - 10,
        )
        step = _fake_step(step_id="s-1", attempt=1, max_attempts=1)
        store.add_attempt(attempt)
        store.add_step(step)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        assert store.updates == []

    def test_heartbeat_expired_marks_failed(self) -> None:
        now = time.time()
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            step_id="s-1",
            task_id="t-1",
            status="running",
            context={"heartbeat_interval_seconds": 30},
            last_heartbeat_at=now - 60,
        )
        step = _fake_step(step_id="s-1", attempt=1, max_attempts=1)
        store.add_attempt(attempt)
        store.add_step(step)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        # Should have updated the attempt status to failed
        assert len(store.updates) >= 1
        sa_id, kwargs = store.updates[0]
        assert sa_id == "sa-1"
        assert kwargs["status"] == "failed"
        assert kwargs["waiting_reason"] == "heartbeat_timeout"

    def test_heartbeat_falls_back_to_claimed_at(self) -> None:
        """When no heartbeat reported, uses claimed_at as last beat."""
        now = time.time()
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            step_id="s-1",
            task_id="t-1",
            status="running",
            context={"heartbeat_interval_seconds": 30},
            last_heartbeat_at=None,
            claimed_at=now - 60,
        )
        step = _fake_step(step_id="s-1", attempt=1, max_attempts=1)
        store.add_attempt(attempt)
        store.add_step(step)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        assert len(store.updates) >= 1
        _, kwargs = store.updates[0]
        assert kwargs["status"] == "failed"

    def test_heartbeat_retry_when_max_attempts_allows(self) -> None:
        """When max_attempts > attempt, retry_step is called instead of propagate_step_failure."""
        now = time.time()
        store = FakeDispatchStore()
        store.retry_step = MagicMock()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            step_id="s-1",
            task_id="t-1",
            status="running",
            context={"heartbeat_interval_seconds": 30},
            last_heartbeat_at=now - 60,
        )
        step = _fake_step(step_id="s-1", attempt=1, max_attempts=3)
        store.add_attempt(attempt)
        store.add_step(step)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        store.retry_step.assert_called_once_with("t-1", "s-1")

    def test_heartbeat_no_last_beat_timestamps_skipped(self) -> None:
        """When last_heartbeat_at, claimed_at, and started_at are all None, skip."""
        store = FakeDispatchStore()
        attempt = _fake_attempt(
            step_attempt_id="sa-1",
            status="running",
            context={"heartbeat_interval_seconds": 30},
            last_heartbeat_at=None,
            claimed_at=None,
            started_at=None,
        )
        store.add_attempt(attempt)
        store._attempts_by_status["running"] = [attempt]
        svc = _build_service(store)
        svc.check_heartbeat_timeouts()
        assert store.updates == []


# ---------------------------------------------------------------------------
# Tests: capacity check
# ---------------------------------------------------------------------------


class TestCapacityCheck:
    def test_capacity_below_worker_count(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store, worker_count=4)
        f1 = MagicMock()
        f1.done.return_value = False
        svc.futures[f1] = "sa-1"
        assert svc._capacity_available() is True
        assert len(svc.futures) < svc.worker_count

    def test_capacity_at_worker_count(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store, worker_count=2)
        for i in range(2):
            f = MagicMock()
            f.done.return_value = False
            svc.futures[f] = f"sa-{i}"
        assert svc._capacity_available() is False

    def test_capacity_restored_after_reap(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store, worker_count=2)
        f1 = MagicMock()
        f1.done.return_value = True
        f1.result.return_value = None
        svc.futures[f1] = "sa-1"
        svc._reap_futures()
        assert svc._capacity_available() is True


# ---------------------------------------------------------------------------
# Tests: _force_fail_attempt
# ---------------------------------------------------------------------------


class TestForceFailAttempt:
    def test_force_fail_empty_id_is_noop(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store)
        svc.force_fail_attempt("")
        assert store.updates == []

    def test_force_fail_nonexistent_attempt_is_noop(self) -> None:
        store = FakeDispatchStore()
        svc = _build_service(store)
        svc.force_fail_attempt("nonexistent")
        assert store.updates == []

    def test_force_fail_marks_attempt_failed(self) -> None:
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-1", status="running")
        step = _fake_step(step_id="s-1")
        store.add_attempt(attempt)
        store.add_step(step)
        svc = _build_service(store)
        svc.force_fail_attempt("sa-1")
        assert len(store.updates) >= 1
        _, kwargs = store.updates[0]
        assert kwargs["status"] == "failed"
        assert kwargs["waiting_reason"] == "worker_exception"

    def test_force_fail_already_terminal_skips_update(self) -> None:
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-1", status="failed")
        step = _fake_step(step_id="s-1")
        store.add_attempt(attempt)
        store.add_step(step)
        svc = _build_service(store)
        svc.force_fail_attempt("sa-1")
        # No status update for already-terminal attempt
        status_updates = [u for u in store.updates if u[1].get("status") == "failed"]
        assert len(status_updates) == 0


# ---------------------------------------------------------------------------
# Tests: report_heartbeat
# ---------------------------------------------------------------------------


class TestReportHeartbeat:
    def test_report_heartbeat_updates_store(self) -> None:
        store = FakeDispatchStore()
        attempt = _fake_attempt(step_attempt_id="sa-1")
        store.add_attempt(attempt)
        svc = _build_service(store)
        svc.report_heartbeat("sa-1")
        assert len(store.updates) == 1
        said, _kwargs = store.updates[0]
        assert said == "sa-1"
