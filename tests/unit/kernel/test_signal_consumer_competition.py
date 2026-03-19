"""Tests for SignalConsumer — covers competition path and additional edge cases."""

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


class TestSignalConsumerSkipNoGoal:
    """Covers the `if not signal.suggested_goal: continue` branch."""

    def test_signal_without_goal_skipped(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_nogoal",
            source_kind="evidence",
            suggested_goal="",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller()

        consumer = SignalConsumer(proto, ctrl)
        consumed = consumer.consume_once()

        assert consumed == 0
        proto.consume.assert_not_called()


class TestSignalConsumerCompetitionPath:
    """Covers lines 53-67: high/critical risk with competition service."""

    def test_high_risk_uses_competition(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_high",
            source_kind="evidence",
            suggested_goal="High risk task",
            risk_level="high",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller()
        competition = MagicMock()
        comp_obj = SimpleNamespace(competition_id="comp_1")
        competition.create_competition.return_value = comp_obj
        comp_record = SimpleNamespace(parent_task_id="parent_task_1")
        ctrl.store.get_competition.return_value = comp_record

        consumer = SignalConsumer(proto, ctrl, competition_service=competition)
        consumed = consumer.consume_once()

        assert consumed == 1
        competition.create_competition.assert_called_once()
        competition.spawn_candidates.assert_called_once_with("comp_1")
        proto.consume.assert_called_once_with("sig_high", "parent_task_1")

    def test_critical_risk_uses_competition(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_crit",
            source_kind="evidence",
            suggested_goal="Critical task",
            risk_level="critical",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller()
        competition = MagicMock()
        comp_obj = SimpleNamespace(competition_id="comp_2")
        competition.create_competition.return_value = comp_obj
        comp_record = SimpleNamespace(parent_task_id="parent_task_2")
        ctrl.store.get_competition.return_value = comp_record

        consumer = SignalConsumer(proto, ctrl, competition_service=competition)
        consumed = consumer.consume_once()

        assert consumed == 1
        proto.consume.assert_called_once_with("sig_crit", "parent_task_2")

    def test_high_risk_without_competition_service_uses_normal_path(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_high_no_comp",
            source_kind="evidence",
            suggested_goal="High risk but no competition",
            risk_level="high",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller("task_normal")

        consumer = SignalConsumer(proto, ctrl, competition_service=None)
        consumed = consumer.consume_once()

        assert consumed == 1
        ctrl.start_task.assert_called_once()
        proto.consume.assert_called_once_with("sig_high_no_comp", "task_normal")

    def test_competition_returns_none_record(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_none_rec",
            source_kind="evidence",
            suggested_goal="Task with None comp record",
            risk_level="high",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller()
        competition = MagicMock()
        comp_obj = SimpleNamespace(competition_id="comp_3")
        competition.create_competition.return_value = comp_obj
        ctrl.store.get_competition.return_value = None

        consumer = SignalConsumer(proto, ctrl, competition_service=competition)
        consumed = consumer.consume_once()

        # _create_task returns None, so consume is not called
        assert consumed == 0

    def test_low_risk_skips_competition(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_low",
            source_kind="evidence",
            suggested_goal="Low risk task",
            risk_level="low",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller("task_low")
        competition = MagicMock()

        consumer = SignalConsumer(proto, ctrl, competition_service=competition)
        consumed = consumer.consume_once()

        assert consumed == 1
        competition.create_competition.assert_not_called()
        ctrl.start_task.assert_called_once()

    def test_conversation_id_from_signal(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_conv",
            source_kind="evidence",
            suggested_goal="Task with conv id",
            conversation_id="conv_existing",
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller("task_conv")

        consumer = SignalConsumer(proto, ctrl)
        consumed = consumer.consume_once()

        assert consumed == 1
        ctrl.store.ensure_conversation.assert_called_once_with(
            "conv_existing", source_channel="signal"
        )

    def test_conversation_id_generated_from_signal_id(self) -> None:
        sig = EvidenceSignal(
            signal_id="sig_gen",
            source_kind="evidence",
            suggested_goal="Task without conv id",
            conversation_id=None,
        )
        proto = _make_protocol([sig])
        ctrl = _make_task_controller("task_gen")

        consumer = SignalConsumer(proto, ctrl)
        consumed = consumer.consume_once()

        assert consumed == 1
        ctrl.store.ensure_conversation.assert_called_once_with(
            "signal_sig_gen", source_channel="signal"
        )
