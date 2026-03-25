"""Additional coverage tests for src/hermit/runtime/control/lifecycle/budgets.py

Targets the ~6 missed statements: configure_runtime_budget, get_runtime_budget,
and edge cases on Deadline.
"""

from __future__ import annotations

import time

import pytest

from hermit.runtime.control.lifecycle.budgets import (
    Deadline,
    ExecutionBudget,
    configure_runtime_budget,
    get_runtime_budget,
)

# ---------------------------------------------------------------------------
# Deadline
# ---------------------------------------------------------------------------


class TestDeadline:
    def test_start_creates_valid_deadline(self) -> None:
        d = Deadline.start(soft_seconds=10.0, hard_seconds=30.0)
        assert d.soft_at > d.started_at
        assert d.hard_at >= d.soft_at

    def test_hard_at_least_soft(self) -> None:
        d = Deadline.start(soft_seconds=30.0, hard_seconds=10.0)
        assert d.hard_at >= d.soft_at

    def test_negative_seconds_clamped_to_zero(self) -> None:
        d = Deadline.start(soft_seconds=-5.0, hard_seconds=-10.0)
        assert d.soft_at == d.started_at
        assert d.hard_at == d.started_at

    def test_soft_remaining_with_now(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now, soft_at=now + 10.0, hard_at=now + 20.0)
        remaining = d.soft_remaining(now=now)
        assert abs(remaining - 10.0) < 0.1

    def test_soft_remaining_expired(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now - 20.0, soft_at=now - 10.0, hard_at=now + 5.0)
        assert d.soft_remaining(now=now) == 0.0

    def test_hard_remaining_with_now(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now, soft_at=now + 5.0, hard_at=now + 15.0)
        remaining = d.hard_remaining(now=now)
        assert abs(remaining - 15.0) < 0.1

    def test_hard_remaining_expired(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now - 20.0, soft_at=now - 15.0, hard_at=now - 5.0)
        assert d.hard_remaining(now=now) == 0.0

    def test_soft_exceeded(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now - 20.0, soft_at=now - 1.0, hard_at=now + 10.0)
        assert d.soft_exceeded(now=now) is True

    def test_soft_not_exceeded(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now, soft_at=now + 100.0, hard_at=now + 200.0)
        assert d.soft_exceeded(now=now) is False

    def test_hard_exceeded(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now - 30.0, soft_at=now - 20.0, hard_at=now - 1.0)
        assert d.hard_exceeded(now=now) is True

    def test_hard_not_exceeded(self) -> None:
        now = time.monotonic()
        d = Deadline(started_at=now, soft_at=now + 5.0, hard_at=now + 100.0)
        assert d.hard_exceeded(now=now) is False

    def test_remaining_without_now_uses_monotonic(self) -> None:
        d = Deadline.start(soft_seconds=1000.0, hard_seconds=2000.0)
        assert d.soft_remaining() > 0
        assert d.hard_remaining() > 0

    def test_exceeded_without_now_uses_monotonic(self) -> None:
        d = Deadline.start(soft_seconds=1000.0, hard_seconds=2000.0)
        assert d.soft_exceeded() is False
        assert d.hard_exceeded() is False


# ---------------------------------------------------------------------------
# ExecutionBudget
# ---------------------------------------------------------------------------


class TestExecutionBudget:
    def test_default_values(self) -> None:
        b = ExecutionBudget()
        assert b.tool_soft_deadline == 30.0
        assert b.tool_hard_deadline == 600.0
        assert b.ingress_ack_deadline == 5.0

    def test_custom_values(self) -> None:
        b = ExecutionBudget(tool_soft_deadline=10.0, tool_hard_deadline=60.0)
        assert b.tool_soft_deadline == 10.0

    def test_tool_deadline_returns_deadline(self) -> None:
        b = ExecutionBudget(tool_soft_deadline=5.0, tool_hard_deadline=15.0)
        d = b.tool_deadline()
        assert isinstance(d, Deadline)
        assert d.soft_remaining() > 0
        assert d.hard_remaining() > 0


# ---------------------------------------------------------------------------
# configure_runtime_budget / get_runtime_budget
# ---------------------------------------------------------------------------


class TestRuntimeBudgetGlobals:
    @pytest.fixture(autouse=True)
    def _restore_runtime_budget(self, monkeypatch):
        """Ensure _runtime_budget is restored even if a test fails."""
        import hermit.runtime.control.lifecycle.budgets as _mod

        original = _mod._runtime_budget
        yield
        _mod._runtime_budget = original

    def test_get_default_budget(self) -> None:
        configure_runtime_budget(None)
        budget = get_runtime_budget()
        assert isinstance(budget, ExecutionBudget)
        assert budget.tool_soft_deadline == 30.0

    def test_configure_custom_budget(self) -> None:
        custom = ExecutionBudget(tool_soft_deadline=99.0)
        configure_runtime_budget(custom)
        budget = get_runtime_budget()
        assert budget.tool_soft_deadline == 99.0

    def test_configure_none_resets_to_default(self) -> None:
        custom = ExecutionBudget(tool_soft_deadline=99.0)
        configure_runtime_budget(custom)
        configure_runtime_budget(None)
        budget = get_runtime_budget()
        assert budget.tool_soft_deadline == 30.0
