"""Tests for evidence signals store, protocol, and steering coverage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.signals.models import EvidenceSignal, SteeringDirective
from hermit.kernel.signals.protocol import SignalProtocol
from hermit.kernel.signals.steering import SteeringProtocol


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


# ---------------------------------------------------------------------------
# SignalStoreMixin coverage
# ---------------------------------------------------------------------------


class TestSignalStoreCheckCooldown:
    def test_cooldown_active_when_recent_signal(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="test://ref",
            summary="test",
            suggested_goal="fix",
            risk_level="low",
            cooldown_key="test:key",
            cooldown_seconds=3600,
        )
        store.create_signal(sig)
        assert store.check_cooldown("test:key", 3600) is True

    def test_cooldown_not_active_when_no_signals(self, store: KernelStore) -> None:
        assert store.check_cooldown("test:key", 3600) is False

    def test_cooldown_not_active_outside_window(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="test://ref",
            summary="test",
            suggested_goal="fix",
            risk_level="low",
            cooldown_key="test:key",
            cooldown_seconds=1,
            created_at=time.time() - 100,
        )
        store.create_signal(sig)
        assert store.check_cooldown("test:key", 1) is False


class TestSignalStoreStats:
    def test_signal_stats_with_since(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        store.create_signal(sig)
        stats = store.signal_stats(since=0.0)
        assert stats.get("pending", 0) == 1

    def test_signal_stats_without_since(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        store.create_signal(sig)
        stats = store.signal_stats()
        assert stats.get("pending", 0) == 1


class TestSignalStoreListSignals:
    def test_list_signals_empty(self, store: KernelStore) -> None:
        assert store.list_signals() == []

    def test_list_signals_ordered_newest_first(self, store: KernelStore) -> None:
        s1 = EvidenceSignal(
            source_kind="a",
            source_ref="r",
            summary="first",
            suggested_goal="g",
            risk_level="low",
            created_at=100.0,
        )
        s2 = EvidenceSignal(
            source_kind="b",
            source_ref="r",
            summary="second",
            suggested_goal="g",
            risk_level="low",
            created_at=200.0,
        )
        store.create_signal(s1)
        store.create_signal(s2)
        signals = store.list_signals(limit=10)
        assert len(signals) == 2
        assert signals[0].summary == "second"
        assert signals[1].summary == "first"


class TestSignalStoreUpdateDisposition:
    def test_update_disposition_with_acted_at(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        store.create_signal(sig)
        store.update_signal_disposition(sig.signal_id, "acted", acted_at=123.0)
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "acted"
        assert fetched.acted_at == 123.0

    def test_update_disposition_without_acted_at(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        store.create_signal(sig)
        store.update_signal_disposition(sig.signal_id, "suppressed")
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "suppressed"

    def test_update_disposition_with_produced_task_id(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        store.create_signal(sig)
        store.update_signal_disposition(sig.signal_id, "acted", produced_task_id="task-123")
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.produced_task_id == "task-123"


# ---------------------------------------------------------------------------
# SignalProtocol coverage
# ---------------------------------------------------------------------------


class TestSignalProtocolEmit:
    def test_emit_returns_signal(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        result = proto.emit(sig)
        assert result is not None
        assert result.signal_id == sig.signal_id

    def test_emit_returns_none_when_cooldown_active(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig1 = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="first",
            suggested_goal="g",
            risk_level="low",
            cooldown_key="dup_key",
            cooldown_seconds=3600,
        )
        sig2 = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="second",
            suggested_goal="g",
            risk_level="low",
            cooldown_key="dup_key",
            cooldown_seconds=3600,
        )
        proto.emit(sig1)
        result = proto.emit(sig2)
        assert result is None

    def test_consume(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        proto.emit(sig)
        proto.consume(sig.signal_id, produced_task_id="t1")
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "acted"
        assert fetched.produced_task_id == "t1"

    def test_suppress(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        proto.emit(sig)
        proto.suppress(sig.signal_id)
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "suppressed"

    def test_actionable(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        proto.emit(sig)
        actionable = proto.actionable()
        assert len(actionable) == 1

    def test_stats(self, store: KernelStore) -> None:
        proto = SignalProtocol(store)
        sig = EvidenceSignal(
            source_kind="test",
            source_ref="r",
            summary="s",
            suggested_goal="g",
            risk_level="low",
        )
        proto.emit(sig)
        stats = proto.stats()
        assert stats.get("pending", 0) == 1


# ---------------------------------------------------------------------------
# SteeringProtocol coverage
# ---------------------------------------------------------------------------


class TestSteeringStore:
    def test_create_and_list_steerings(self, store: KernelStore) -> None:
        d = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Focus on performance",
        )
        store.create_steering(d)
        steerings = store.list_steerings_for_task("task-1")
        assert len(steerings) == 1
        assert steerings[0].directive == "Focus on performance"

    def test_list_steerings_for_task(self, store: KernelStore) -> None:
        d1 = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Do A",
            disposition="pending",
        )
        d2 = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Do B",
            disposition="acknowledged",
        )
        store.create_steering(d1)
        store.create_steering(d2)
        pending = store.list_steerings_for_task("task-1", disposition="pending")
        assert len(pending) == 1
        all_steerings = store.list_steerings_for_task("task-1")
        assert len(all_steerings) == 2

    def test_update_steering_disposition_with_applied_at(self, store: KernelStore) -> None:
        d = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Do A",
        )
        store.create_steering(d)
        store.update_steering_disposition(d.directive_id, "applied", applied_at=123.0)
        steerings = store.list_steerings_for_task("task-1")
        assert steerings[0].disposition == "applied"


class TestSteeringProtocolNotFound:
    def test_apply_nonexistent(self, store: KernelStore) -> None:
        proto = SteeringProtocol(store)
        proto.apply("nonexistent-id")

    def test_reject_nonexistent(self, store: KernelStore) -> None:
        proto = SteeringProtocol(store)
        proto.reject("nonexistent-id")

    def test_supersede(self, store: KernelStore) -> None:
        proto = SteeringProtocol(store)
        d1 = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Old",
        )
        d2 = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="New",
        )
        proto.issue(d1)
        proto.supersede(d1.directive_id, d2)
        actives = proto.active_for_task("task-1")
        active_ids = [a.directive_id for a in actives]
        assert d2.directive_id in active_ids


class TestSteeringMarkInputDirtyException:
    def test_mark_input_dirty_swallows_exception(self, store: KernelStore) -> None:
        proto = SteeringProtocol(store)
        d = SteeringDirective(
            task_id="task-1",
            steering_type="focus",
            directive="Test",
        )
        proto.issue(d)
        # Acknowledge should try to mark input dirty but task doesn't exist
        # Should not raise
        proto.acknowledge(d.directive_id)
