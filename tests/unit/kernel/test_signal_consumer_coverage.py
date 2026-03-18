"""Tests for SignalConsumer — covers missing lines 42-43, 51."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.signals.consumer import SignalConsumer
from hermit.kernel.signals.models import EvidenceSignal


def _make_protocol(signals: list[EvidenceSignal] | None = None) -> MagicMock:
    proto = MagicMock()
    proto.actionable.return_value = signals or []
    return proto


def _make_task_controller(task_id: str = "task_001") -> MagicMock:
    ctrl = MagicMock()
    ctx = SimpleNamespace(task_id=task_id)
    ctrl.start_task.return_value = ctx
    return ctrl


class TestSignalConsumerExceptionHandling:
    """Cover lines 42-43: exception during _create_task logs and continues."""

    def test_consume_once_logs_exception_and_continues(self) -> None:
        sig1 = EvidenceSignal(
            signal_id="sig_a",
            source_kind="evidence",
            suggested_goal="Do something",
        )
        sig2 = EvidenceSignal(
            signal_id="sig_b",
            source_kind="evidence",
            suggested_goal="Do another thing",
        )
        proto = _make_protocol([sig1, sig2])
        ctrl = _make_task_controller()

        # First call to start_task raises, second succeeds
        ctrl.start_task.side_effect = [RuntimeError("boom"), SimpleNamespace(task_id="t2")]

        consumer = SignalConsumer(proto, ctrl)
        consumed = consumer.consume_once()

        # Only the second signal should succeed
        assert consumed == 1
        assert proto.consume.call_count == 1
        proto.consume.assert_called_once_with("sig_b", "t2")


class TestSignalConsumerNonEvidenceSignal:
    """Cover line 51: signal that is not an EvidenceSignal returns None from _create_task."""

    def test_non_evidence_signal_is_skipped(self) -> None:
        # A plain object with suggested_goal but not an EvidenceSignal instance
        fake_signal = SimpleNamespace(
            signal_id="sig_fake",
            suggested_goal="Do stuff",
        )
        proto = MagicMock()
        proto.actionable.return_value = [fake_signal]
        ctrl = _make_task_controller()

        consumer = SignalConsumer(proto, ctrl)
        consumed = consumer.consume_once()

        # _create_task returns None for non-EvidenceSignal, so consume is not called
        assert consumed == 0
        proto.consume.assert_not_called()
