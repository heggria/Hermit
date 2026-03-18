"""Tests for SignalProtocol — covers missing lines 25, 40, 48."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.signals.models import EvidenceSignal
from hermit.kernel.signals.protocol import SignalProtocol


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def protocol(store: KernelStore) -> SignalProtocol:
    return SignalProtocol(store)


class TestEmitCooldownBlock:
    """Cover line 25: emit returns None when cooldown is active."""

    def test_emit_returns_none_when_cooldown_active(self, protocol: SignalProtocol) -> None:
        sig1 = EvidenceSignal(
            source_kind="patrol",
            source_ref="test",
            suggested_goal="first",
            cooldown_key="patrol:check1",
            cooldown_seconds=3600,
        )
        result1 = protocol.emit(sig1)
        assert result1 is not None

        sig2 = EvidenceSignal(
            source_kind="patrol",
            source_ref="test",
            suggested_goal="second",
            cooldown_key="patrol:check1",
            cooldown_seconds=3600,
        )
        result2 = protocol.emit(sig2)
        assert result2 is None


class TestSuppressSignal:
    """Cover line 40: suppress marks signal as suppressed."""

    def test_suppress_updates_disposition(
        self, protocol: SignalProtocol, store: KernelStore
    ) -> None:
        sig = EvidenceSignal(
            source_kind="evidence",
            source_ref="test",
            suggested_goal="some goal",
        )
        protocol.emit(sig)
        protocol.suppress(sig.signal_id, reason="not relevant")
        fetched = store.get_signal(sig.signal_id)
        assert fetched is not None
        assert fetched.disposition == "suppressed"


class TestSignalStats:
    """Cover line 48: stats returns disposition distribution."""

    def test_stats_returns_distribution(self, protocol: SignalProtocol) -> None:
        sig1 = EvidenceSignal(source_kind="evidence", source_ref="a", suggested_goal="g1")
        sig2 = EvidenceSignal(source_kind="evidence", source_ref="b", suggested_goal="g2")
        protocol.emit(sig1)
        protocol.emit(sig2)
        protocol.suppress(sig2.signal_id, reason="dup")

        stats = protocol.stats()
        assert stats.get("pending", 0) == 1
        assert stats.get("suppressed", 0) == 1

    def test_stats_with_since_filter(self, protocol: SignalProtocol) -> None:
        import time

        sig = EvidenceSignal(source_kind="evidence", source_ref="a", suggested_goal="g1")
        protocol.emit(sig)

        # With a future timestamp, no signals should match
        future = time.time() + 10000
        stats = protocol.stats(since=future)
        assert stats == {}
