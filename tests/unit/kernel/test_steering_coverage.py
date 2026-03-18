"""Tests for SteeringProtocol — covers missing lines 31, 41, 51, 117-118."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.signals.models import SteeringDirective
from hermit.kernel.signals.steering import SteeringProtocol


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def protocol(store: KernelStore) -> SteeringProtocol:
    return SteeringProtocol(store)


def _make_directive(**overrides: object) -> SteeringDirective:
    defaults: dict = dict(
        task_id="task_001",
        steering_type="scope",
        directive="Focus on error handling first",
        evidence_refs=["artifact://review/pr-42"],
        issued_by="operator",
    )
    defaults.update(overrides)
    return SteeringDirective(**defaults)


class TestAcknowledgeNonexistent:
    """Cover line 31: acknowledge returns early when signal not found."""

    def test_acknowledge_nonexistent_is_noop(self, protocol: SteeringProtocol) -> None:
        # Should not raise, just return silently
        protocol.acknowledge("nonexistent_id")


class TestApplyNonexistent:
    """Cover line 41: apply returns early when signal not found."""

    def test_apply_nonexistent_is_noop(self, protocol: SteeringProtocol) -> None:
        protocol.apply("nonexistent_id")


class TestRejectNonexistent:
    """Cover line 51: reject returns early when signal not found."""

    def test_reject_nonexistent_is_noop(self, protocol: SteeringProtocol) -> None:
        protocol.reject("nonexistent_id", reason="doesn't exist")


class TestMarkInputDirtyException:
    """Cover lines 117-118: _mark_input_dirty swallows exceptions."""

    def test_mark_input_dirty_exception_swallowed(self, store: KernelStore) -> None:
        protocol = SteeringProtocol(store)

        # Patch list_step_attempts to raise an exception
        with patch.object(store, "list_step_attempts", side_effect=RuntimeError("db error")):
            sd = _make_directive()
            # issue() calls _mark_input_dirty, which should swallow the exception
            result = protocol.issue(sd)
            assert result.directive_id == sd.directive_id

            # Verify the directive was still created despite _mark_input_dirty failure
            fetched = store.get_signal(sd.directive_id)
            assert fetched is not None
