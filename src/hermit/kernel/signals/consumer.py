"""SignalConsumer — consumes actionable signals and creates follow-up tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from hermit.kernel.execution.competition.service import CompetitionService
    from hermit.kernel.signals.protocol import SignalProtocol
    from hermit.kernel.task.services.controller import TaskController

log = structlog.get_logger()


class SignalConsumer:
    """Consumes actionable signals and creates follow-up tasks."""

    def __init__(
        self,
        signal_protocol: SignalProtocol,
        task_controller: TaskController,
        competition_service: CompetitionService | None = None,
    ) -> None:
        self._protocol = signal_protocol
        self._task_controller = task_controller
        self._competition = competition_service

    def consume_once(self, limit: int = 50) -> int:
        """Fetch actionable signals, create tasks, return count consumed."""
        signals = self._protocol.actionable(limit=limit)
        consumed = 0
        for signal in signals:
            if not signal.suggested_goal:
                continue
            try:
                task_id = self._create_task(signal)
                if task_id:
                    self._protocol.consume(signal.signal_id, task_id)
                    consumed += 1
            except Exception:
                log.exception("signal_consume_failed", signal_id=signal.signal_id)
        return consumed

    def _create_task(self, signal: object) -> str | None:
        from hermit.kernel.signals.models import EvidenceSignal

        # Reject anything that isn't a concrete EvidenceSignal early.
        if not isinstance(signal, EvidenceSignal):
            return None

        conv_id = signal.conversation_id or f"signal_{signal.signal_id}"
        self._task_controller.store.ensure_conversation(conv_id, source_channel="signal")

        use_competition = (
            signal.risk_level in ("high", "critical") and self._competition is not None
        )

        if use_competition:
            # use_competition already implies self._competition is not None.
            comp = self._competition.create_competition(  # type: ignore[union-attr]
                conversation_id=conv_id,
                goal=signal.suggested_goal,
                candidate_count=2,
                source_channel="signal",
            )
            self._competition.spawn_candidates(comp.competition_id)  # type: ignore[union-attr]
            comp_record = self._task_controller.store.get_competition(comp.competition_id)
            return comp_record.parent_task_id if comp_record else None

        ctx = self._task_controller.start_task(
            conversation_id=conv_id,
            goal=signal.suggested_goal,
            source_channel="signal",
            kind="execute",
        )
        return ctx.task_id
