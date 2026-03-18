"""SignalProtocol — lifecycle management for evidence signals."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hermit.kernel.signals.models import EvidenceSignal

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore


class SignalProtocol:
    """Manages the lifecycle of evidence signals in the store."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def emit(self, signal: EvidenceSignal) -> EvidenceSignal | None:
        """Persist a signal. Returns None if cooldown is active."""
        if signal.cooldown_key and self._store.check_cooldown(
            signal.cooldown_key, signal.cooldown_seconds
        ):
            return None
        self._store.create_signal(signal)
        return signal

    def consume(self, signal_id: str, produced_task_id: str) -> None:
        """Mark signal as acted and link to produced task."""
        self._store.update_signal_disposition(
            signal_id,
            "acted",
            acted_at=time.time(),
            produced_task_id=produced_task_id,
        )

    def suppress(self, signal_id: str, reason: str = "") -> None:
        """Mark signal as suppressed."""
        self._store.update_signal_disposition(signal_id, "suppressed")

    def actionable(self, limit: int = 50) -> list[EvidenceSignal]:
        """Return pending, non-expired signals (excluding steering signals)."""
        return self._store.actionable_signals(limit=limit)

    def stats(self, since: float | None = None) -> dict[str, int]:
        """Return disposition distribution."""
        return self._store.signal_stats(since=since)
