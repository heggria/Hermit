"""E2E: Deliberation gating across the dispatch path.

Exercises the full deliberation gating pipeline:
  task + step + attempt creation → risk/kind evaluation →
  DeliberationService.check_deliberation_needed() →
  KernelDispatchService._check_deliberation_needed() →
  ledger event recording + status transitions.

Uses real KernelStore (file-backed) with tmp_path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.competition.deliberation_service import DeliberationService
from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _make_runner(store: KernelStore) -> Any:
    """Build a minimal fake runner with a real KernelStore."""
    return SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=MagicMock(),
    )


def _create_attempt(
    store: KernelStore,
    *,
    step_kind: str,
    risk_band: str,
) -> tuple[str, str, str]:
    """Create task + step + step_attempt and return (task_id, step_id, step_attempt_id)."""
    task = store.create_task(
        conversation_id="conv_delib_e2e",
        title="Deliberation E2E test",
        goal="Test deliberation gating",
        source_channel="test",
        status="running",
    )
    step = store.create_step(
        task_id=task.task_id,
        kind=step_kind,
        status="running",
    )
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        context={
            "risk_band": risk_band,
            "ingress_metadata": {"dispatch_mode": "async"},
        },
    )
    return task.task_id, step.step_id, attempt.step_attempt_id


# ---------------------------------------------------------------------------
# Test 9: High risk triggers deliberation_pending
# ---------------------------------------------------------------------------


class TestHighRiskTriggersDeliberationPending:
    """Test 9: High-risk action_class='write_local' triggers deliberation gating."""

    def test_static_check_returns_true(self) -> None:
        """DeliberationService.check_deliberation_needed() should return True
        for risk_level='high' with a mutation action_class."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="high", action_class="write_local"
            )
            is True
        )

    def test_dispatch_gates_high_risk_attempt(self, store: KernelStore) -> None:
        """Full dispatch path: high-risk attempt is moved to deliberation_pending."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, step_id, step_attempt_id = _create_attempt(
            store, step_kind="write_local", risk_band="high"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is True

        # Verify: attempt moved to deliberation_pending
        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"
        assert updated_attempt.waiting_reason == "deliberation_required"

        # Verify: context enriched with deliberation metadata
        ctx = updated_attempt.context or {}
        assert ctx.get("deliberation_risk_band") == "high"
        assert ctx.get("deliberation_step_kind") == "write_local"
        assert "deliberation_pending_at" in ctx

        # Verify: step also moved to deliberation_pending
        updated_step = store.get_step(step_id)
        assert updated_step is not None
        assert updated_step.status == "deliberation_pending"

        # Verify: ledger event "dispatch.deliberation_required" recorded
        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 1


# ---------------------------------------------------------------------------
# Test 10: Critical risk triggers deliberation
# ---------------------------------------------------------------------------


class TestCriticalRiskTriggersDeliberation:
    """Test 10: Critical-risk action_class='execute_command' triggers deliberation gating."""

    def test_static_check_returns_true(self) -> None:
        """DeliberationService.check_deliberation_needed() should return True
        for risk_level='critical' with a mutation action_class."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="critical", action_class="execute_command"
            )
            is True
        )

    def test_dispatch_gates_critical_risk_attempt(self, store: KernelStore) -> None:
        """Full dispatch path: critical-risk attempt is moved to deliberation_pending."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, step_id, step_attempt_id = _create_attempt(
            store, step_kind="execute_command", risk_band="critical"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is True

        # Verify: attempt moved to deliberation_pending
        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"
        assert updated_attempt.waiting_reason == "deliberation_required"

        # Verify: context enriched with deliberation metadata
        ctx = updated_attempt.context or {}
        assert ctx.get("deliberation_risk_band") == "critical"
        assert ctx.get("deliberation_step_kind") == "execute_command"

        # Verify: step also moved to deliberation_pending
        updated_step = store.get_step(step_id)
        assert updated_step is not None
        assert updated_step.status == "deliberation_pending"

        # Verify: ledger event recorded
        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 1


# ---------------------------------------------------------------------------
# Test 11: Low risk bypasses deliberation — direct dispatch
# ---------------------------------------------------------------------------


class TestLowRiskBypassesDeliberationDirectDispatch:
    """Test 11: Low-risk action_class='read_local' bypasses deliberation entirely."""

    def test_static_check_returns_false(self) -> None:
        """DeliberationService.check_deliberation_needed() should return False
        for risk_level='low' with any action_class."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="low", action_class="execute_command"
            )
            is False
        )

    def test_dispatch_bypasses_low_risk_attempt(self, store: KernelStore) -> None:
        """Full dispatch path: low-risk attempt proceeds directly (no deliberation_pending)."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, step_id, step_attempt_id = _create_attempt(
            store, step_kind="execute", risk_band="low"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is False

        # Verify: attempt status NOT changed — still running (ready for dispatch)
        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "running"

        # Verify: step status NOT changed
        updated_step = store.get_step(step_id)
        assert updated_step is not None
        assert updated_step.status == "running"

        # Verify: no ledger event recorded
        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 0


# ---------------------------------------------------------------------------
# Test 12: Medium risk — kind-dependent deliberation matrix
# ---------------------------------------------------------------------------


class TestMediumRiskKindDependentDeliberation:
    """Test 12: Medium-risk deliberation is determined by action_class.

    The deliberation matrix for medium risk uses action_class values
    from _MEDIUM_RISK_DELIBERATION_ACTIONS:
      - write_local     -> deliberation triggered
      - read_local      -> deliberation bypassed (readonly)
      - patch_file      -> deliberation triggered
      - network_read    -> deliberation bypassed (readonly)
    """

    # -- Static checks via DeliberationService.check_deliberation_needed() ---

    def test_medium_write_local_triggers_deliberation(self) -> None:
        """medium + write_local -> True."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="medium", action_class="write_local"
            )
            is True
        )

    def test_medium_read_local_bypasses_deliberation(self) -> None:
        """medium + read_local -> False (readonly action)."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="medium", action_class="read_local"
            )
            is False
        )

    def test_medium_patch_file_triggers_deliberation(self) -> None:
        """medium + patch_file -> True."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="medium", action_class="patch_file"
            )
            is True
        )

    def test_medium_network_read_bypasses_deliberation(self) -> None:
        """medium + network_read -> False (readonly action)."""
        assert (
            DeliberationService.check_deliberation_needed(
                risk_level="medium", action_class="network_read"
            )
            is False
        )

    # -- Full dispatch path verification -------------------------------------

    def test_dispatch_medium_write_local_triggers(self, store: KernelStore) -> None:
        """Dispatch path: medium + write_local -> deliberation_pending."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = _create_attempt(
            store, step_kind="write_local", risk_band="medium"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is True

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"

        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 1

    def test_dispatch_medium_read_local_bypasses(self, store: KernelStore) -> None:
        """Dispatch path: medium + read_local -> no deliberation, stays running."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = _create_attempt(
            store, step_kind="read_local", risk_band="medium"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is False

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "running"

        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 0

    def test_dispatch_medium_patch_file_triggers(self, store: KernelStore) -> None:
        """Dispatch path: medium + patch_file -> deliberation_pending."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = _create_attempt(
            store, step_kind="patch_file", risk_band="medium"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is True

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"
        ctx = updated_attempt.context or {}
        assert ctx.get("deliberation_risk_band") == "medium"
        assert ctx.get("deliberation_step_kind") == "patch_file"

        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 1

    def test_dispatch_medium_network_read_bypasses(self, store: KernelStore) -> None:
        """Dispatch path: medium + network_read -> no deliberation, stays running."""
        runner = _make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = _create_attempt(
            store, step_kind="network_read", risk_band="medium"
        )

        result = dispatch._check_deliberation_needed(step_attempt_id)
        assert result is False

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "running"

        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 0
