"""Tests for StalenessGuard."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from hermit.kernel.task.services.staleness_guard import StalenessGuard
from hermit.kernel.task.state.enums import TaskState

# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str,
    status: str,
    updated_at: float,
) -> SimpleNamespace:
    return SimpleNamespace(task_id=task_id, status=status, updated_at=updated_at)


class FakeStore:
    """Minimal store that records status updates for assertions."""

    def __init__(self, tasks: list[SimpleNamespace] | None = None) -> None:
        self._tasks: dict[str, SimpleNamespace] = {}
        self.status_updates: list[tuple[str, str, dict]] = []
        for t in tasks or []:
            self._tasks[t.task_id] = t

    def list_tasks(self, *, status: str | None = None, limit: int = 500) -> list[SimpleNamespace]:
        return [t for t in self._tasks.values() if status is None or t.status == status]

    def get_task(self, task_id: str) -> SimpleNamespace | None:
        return self._tasks.get(task_id)

    def update_task_status(self, task_id: str, status: str, *, payload: dict | None = None) -> None:
        self.status_updates.append((task_id, status, payload or {}))
        task = self._tasks.get(task_id)
        if task is not None:
            task.status = status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def now() -> float:
    return time.time()


@pytest.fixture()
def ttl() -> int:
    return 3600  # 1 hour for test speed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStalenessGuardSweep:
    def test_sweep_no_tasks(self, now: float, ttl: int) -> None:
        store = FakeStore()
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == []

    def test_sweep_skips_fresh_tasks(self, now: float, ttl: int) -> None:
        store = FakeStore(
            [
                _make_task("t1", TaskState.BLOCKED, now - 100),
                _make_task("t2", TaskState.PLANNING_READY, now - 200),
            ]
        )
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == []
        assert store.status_updates == []

    def test_sweep_fails_stale_blocked_task(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.BLOCKED, now - ttl - 100)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == ["t1"]
        assert len(store.status_updates) == 1
        task_id, status, payload = store.status_updates[0]
        assert task_id == "t1"
        assert status == "failed"
        assert payload["reason"] == "state_timeout_exceeded"
        assert payload["original_status"] == TaskState.BLOCKED

    def test_sweep_fails_stale_planning_ready_task(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.PLANNING_READY, now - ttl - 1)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == ["t1"]
        _, status, _ = store.status_updates[0]
        assert status == "failed"

    def test_sweep_fails_stale_needs_attention_task(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.NEEDS_ATTENTION, now - ttl - 1)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == ["t1"]
        _, status, _ = store.status_updates[0]
        assert status == "failed"

    def test_sweep_fails_stale_reconciling_task(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.RECONCILING, now - ttl - 1)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == ["t1"]
        _, status, payload = store.status_updates[0]
        assert status == "failed"
        assert payload["reason"] == "state_timeout_exceeded"
        assert payload["original_status"] == TaskState.RECONCILING

    def test_sweep_cancels_stale_paused_task(self, now: float, ttl: int) -> None:
        """PAUSED -> FAILED is not valid in the state machine; guard uses CANCELLED."""
        store = FakeStore([_make_task("t1", TaskState.PAUSED, now - ttl - 1)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == ["t1"]
        _, status, payload = store.status_updates[0]
        assert status == "cancelled"
        assert payload["reason"] == "state_timeout_exceeded"

    def test_sweep_multiple_stale_tasks(self, now: float, ttl: int) -> None:
        store = FakeStore(
            [
                _make_task("t1", TaskState.BLOCKED, now - ttl - 10),
                _make_task("t2", TaskState.NEEDS_ATTENTION, now - ttl - 20),
                _make_task("t3", TaskState.PLANNING_READY, now - 10),  # fresh
            ]
        )
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert set(affected) == {"t1", "t2"}
        assert len(store.status_updates) == 2

    def test_sweep_ignores_non_watchable_states(self, now: float, ttl: int) -> None:
        store = FakeStore(
            [
                _make_task("t1", TaskState.RUNNING, now - ttl - 100),
                _make_task("t2", TaskState.COMPLETED, now - ttl - 100),
                _make_task("t3", TaskState.QUEUED, now - ttl - 100),
            ]
        )
        guard = StalenessGuard(store, ttl_seconds=ttl)
        affected = guard.sweep()
        assert affected == []

    def test_sweep_records_stale_seconds_in_payload(self, now: float) -> None:
        ttl_val = 60
        store = FakeStore([_make_task("t1", TaskState.BLOCKED, now - 200)])
        guard = StalenessGuard(store, ttl_seconds=ttl_val)
        guard.sweep()
        _, _, payload = store.status_updates[0]
        assert payload["stale_seconds"] >= 200


class TestStalenessGuardCheckTask:
    def test_check_task_stale(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.BLOCKED, now - ttl - 100)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        assert guard.check_task("t1") is True

    def test_check_task_not_stale(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.BLOCKED, now - 100)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        assert guard.check_task("t1") is False

    def test_check_task_not_found(self, ttl: int) -> None:
        store = FakeStore()
        guard = StalenessGuard(store, ttl_seconds=ttl)
        assert guard.check_task("nonexistent") is False

    def test_check_task_non_watchable_state(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.RUNNING, now - ttl - 100)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        assert guard.check_task("t1") is False

    def test_check_task_completed_not_stale(self, now: float, ttl: int) -> None:
        store = FakeStore([_make_task("t1", TaskState.COMPLETED, now - ttl - 100)])
        guard = StalenessGuard(store, ttl_seconds=ttl)
        assert guard.check_task("t1") is False


class TestStalenessGuardDefaults:
    def test_default_ttl(self) -> None:
        store = FakeStore()
        guard = StalenessGuard(store)
        assert guard.ttl == 7 * 24 * 3600

    def test_custom_ttl(self) -> None:
        store = FakeStore()
        guard = StalenessGuard(store, ttl_seconds=300)
        assert guard.ttl == 300

    def test_watchable_states_are_correct(self) -> None:
        expected = frozenset(
            {"planning_ready", "paused", "needs_attention", "blocked", "reconciling"}
        )
        assert expected == StalenessGuard.WATCHABLE_STATES
