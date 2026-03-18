"""Tests for SignalStoreMixin — covers missing lines in store.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.signals.models import EvidenceSignal


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


class TestGetSignalNotFound:
    """Cover line 108: get_signal returns None for nonexistent signal."""

    def test_get_signal_returns_none_for_missing(self, store: KernelStore) -> None:
        result = store.get_signal("nonexistent_signal_id")
        assert result is None


class TestUpdateSignalDispositionWithoutActedAt:
    """Cover line 128: update disposition without acted_at."""

    def test_update_disposition_without_acted_at(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="evidence",
            source_ref="test",
            suggested_goal="goal",
        )
        store.create_signal(sig)
        store.update_signal_disposition(sig.signal_id, "suppressed")
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "suppressed"
        assert fetched.acted_at is None


class TestCheckCooldown:
    """Cover lines 142-146, 151: cooldown check logic."""

    def test_cooldown_not_active_returns_false(self, store: KernelStore) -> None:
        result = store.check_cooldown("nonexistent_key", 3600)
        assert result is False

    def test_cooldown_active_returns_true(self, store: KernelStore) -> None:
        sig = EvidenceSignal(
            source_kind="evidence",
            source_ref="test",
            cooldown_key="test_key",
            cooldown_seconds=3600,
        )
        store.create_signal(sig)
        result = store.check_cooldown("test_key", 3600)
        assert result is True


class TestSignalStats:
    """Cover line 178: signal_stats without since filter."""

    def test_signal_stats_without_since(self, store: KernelStore) -> None:
        sig1 = EvidenceSignal(source_kind="evidence", source_ref="a")
        sig2 = EvidenceSignal(source_kind="evidence", source_ref="b", disposition="acted")
        store.create_signal(sig1)
        store.create_signal(sig2)
        stats = store.signal_stats()
        assert stats.get("pending", 0) == 1
        assert stats.get("acted", 0) == 1


class TestListSignals:
    """Cover lines 185-188, 192: list_signals returns newest first."""

    def test_list_signals_returns_newest_first(self, store: KernelStore) -> None:
        sig1 = EvidenceSignal(
            source_kind="evidence",
            source_ref="a",
            created_at=1000.0,
        )
        sig2 = EvidenceSignal(
            source_kind="evidence",
            source_ref="b",
            created_at=2000.0,
        )
        store.create_signal(sig1)
        store.create_signal(sig2)
        signals = store.list_signals(limit=10)
        assert len(signals) == 2
        assert signals[0].created_at >= signals[1].created_at

    def test_list_signals_empty(self, store: KernelStore) -> None:
        signals = store.list_signals()
        assert signals == []
