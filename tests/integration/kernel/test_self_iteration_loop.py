"""Integration tests for the self-iteration closed loop.

Covers the full path: signal emit → consume → task create →
competition spawn → evaluate → promote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hermit.kernel.execution.competition.evaluator import CompetitionEvaluator
from hermit.kernel.execution.competition.service import CompetitionService
from hermit.kernel.signals.consumer import SignalConsumer
from hermit.kernel.signals.models import EvidenceSignal
from hermit.kernel.signals.protocol import SignalProtocol

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def task_controller(store: KernelStore) -> TaskController:
    return TaskController(store)


@pytest.fixture
def signal_protocol(store: KernelStore) -> SignalProtocol:
    return SignalProtocol(store)


@pytest.fixture
def competition_service(store: KernelStore, task_controller: TaskController) -> CompetitionService:
    return CompetitionService(
        store,
        task_controller=task_controller,
        workspace_manager=None,
        evaluator=CompetitionEvaluator(),
    )


@pytest.fixture
def consumer(
    signal_protocol: SignalProtocol,
    task_controller: TaskController,
    competition_service: CompetitionService,
) -> SignalConsumer:
    return SignalConsumer(
        signal_protocol,
        task_controller,
        competition_service=competition_service,
    )


class TestSignalToTaskLoop:
    """Signal emit → consume → task creation."""

    def test_signal_consumed_produces_task(
        self,
        signal_protocol: SignalProtocol,
        consumer: SignalConsumer,
        store: KernelStore,
    ) -> None:
        signal = EvidenceSignal(
            source_kind="test_failure",
            source_ref="test://test_foo",
            summary="test_foo failed due to assertion error",
            suggested_goal="Fix the failing assertion in test_foo",
            risk_level="low",
            suggested_policy_profile="default",
        )
        signal_protocol.emit(signal)

        # Signal should be actionable
        actionable = signal_protocol.actionable()
        assert len(actionable) == 1
        assert actionable[0].signal_id == signal.signal_id

        # Consume should create a task
        consumed = consumer.consume_once()
        assert consumed == 1

        # Signal should now be acted
        actionable_after = signal_protocol.actionable()
        assert len(actionable_after) == 0

        # Task should exist
        tasks = store.list_tasks(limit=10)
        followup_tasks = [t for t in tasks if t.source_channel == "signal"]
        assert len(followup_tasks) == 1
        assert "Fix the failing assertion" in followup_tasks[0].goal

    def test_signal_without_goal_is_skipped(
        self,
        signal_protocol: SignalProtocol,
        consumer: SignalConsumer,
    ) -> None:
        signal = EvidenceSignal(
            source_kind="info",
            source_ref="test://info",
            summary="",
            suggested_goal="",
            risk_level="low",
        )
        signal_protocol.emit(signal)

        consumed = consumer.consume_once()
        assert consumed == 0

    def test_high_risk_signal_spawns_competition(
        self,
        signal_protocol: SignalProtocol,
        consumer: SignalConsumer,
        store: KernelStore,
    ) -> None:
        signal = EvidenceSignal(
            source_kind="security_vuln",
            source_ref="scan://cve-2024-xxx",
            summary="Critical vulnerability in auth module",
            suggested_goal="Patch critical auth vulnerability",
            risk_level="critical",
        )
        signal_protocol.emit(signal)

        consumed = consumer.consume_once()
        assert consumed == 1

        # Should have created a competition, not a plain task
        tasks = store.list_tasks(limit=20)
        competition_tasks = [t for t in tasks if "Competition:" in t.title]
        assert len(competition_tasks) >= 1


class TestCompetitionLifecycle:
    """Competition create → spawn → evaluate → promote."""

    def test_competition_full_lifecycle(
        self,
        competition_service: CompetitionService,
        store: KernelStore,
    ) -> None:
        store.ensure_conversation("test-conv", source_channel="test")

        competition = competition_service.create_competition(
            conversation_id="test-conv",
            goal="Improve error handling",
            candidate_count=2,
            source_channel="competition",
        )
        assert competition.status == "draft"

        candidate_ids = competition_service.spawn_candidates(competition.competition_id)
        assert len(candidate_ids) == 2

        # Verify competition is now running
        comp = store.get_competition(competition.competition_id)
        assert comp is not None
        assert comp.status == "running"

        # Simulate candidates completing
        candidates = store.list_candidates(competition.competition_id)
        for candidate in candidates:
            # Mark the candidate's task as completed
            store.update_task_status(candidate.task_id, "completed")
            competition_service.on_candidate_task_completed(candidate.task_id)

        # Competition should be decided (or cancelled if no criteria pass)
        final = store.get_competition(competition.competition_id)
        assert final is not None
        assert final.status in ("decided", "cancelled")

    def test_competition_cancel_on_all_failed(
        self,
        competition_service: CompetitionService,
        store: KernelStore,
    ) -> None:
        store.ensure_conversation("test-conv-2", source_channel="test")

        competition = competition_service.create_competition(
            conversation_id="test-conv-2",
            goal="Test cancellation",
            candidate_count=2,
            source_channel="competition",
        )
        competition_service.spawn_candidates(competition.competition_id)

        # Simulate all candidates failing
        candidates = store.list_candidates(competition.competition_id)
        for candidate in candidates:
            store.update_task_status(candidate.task_id, "failed")
            competition_service.on_candidate_task_completed(candidate.task_id)

        final = store.get_competition(competition.competition_id)
        assert final is not None
        assert final.status == "cancelled"


class TestEndToEndLoop:
    """Full loop: signal → consume → competition → evaluate."""

    def test_signal_to_competition_to_evaluation(
        self,
        signal_protocol: SignalProtocol,
        consumer: SignalConsumer,
        store: KernelStore,
    ) -> None:
        # 1. Emit a high-risk signal
        signal = EvidenceSignal(
            source_kind="coverage_drop",
            source_ref="ci://coverage-report",
            summary="Coverage dropped below 80%",
            suggested_goal="Increase test coverage back above 80%",
            risk_level="high",
        )
        signal_protocol.emit(signal)

        # 2. Consumer picks it up and creates competition
        consumed = consumer.consume_once()
        assert consumed == 1

        # 3. Verify competition was created
        tasks = store.list_tasks(limit=20)
        competition_tasks = [t for t in tasks if "Competition:" in t.title]
        assert len(competition_tasks) >= 1
        parent_task = competition_tasks[0]

        # 4. Verify signal is now marked as acted
        assert len(signal_protocol.actionable()) == 0

        # 5. Verify candidates were spawned
        competition = store.find_competition_by_parent_task(parent_task.task_id)
        assert competition is not None
        assert competition.status == "running"

        candidates = store.list_candidates(competition.competition_id)
        assert len(candidates) == 2

    def test_multiple_signals_processed_in_batch(
        self,
        signal_protocol: SignalProtocol,
        consumer: SignalConsumer,
        store: KernelStore,
    ) -> None:
        for i in range(3):
            signal = EvidenceSignal(
                source_kind="lint_violation",
                source_ref=f"lint://rule_{i}",
                summary=f"Lint violation #{i}",
                suggested_goal=f"Fix lint violation #{i}",
                risk_level="low",
            )
            signal_protocol.emit(signal)

        consumed = consumer.consume_once(limit=10)
        assert consumed == 3

        # All 3 should produce separate tasks (low risk → no competition)
        tasks = store.list_tasks(limit=20)
        signal_tasks = [t for t in tasks if t.source_channel == "signal"]
        assert len(signal_tasks) == 3
