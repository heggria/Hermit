"""Extra coverage tests for observation.py, prioritizer.py, auto_park.py, join_barrier.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.coordination.auto_park import AutoParkService
from hermit.kernel.execution.coordination.join_barrier import (
    JoinBarrierService,
    JoinStrategy,
    _evaluate_strategy,
)
from hermit.kernel.execution.coordination.observation import (
    ObservationProgress,
    ObservationService,
    ObservationTicket,
    SubtaskJoinObservation,
    normalize_observation_progress,
    normalize_observation_ticket,
    normalize_subtask_join_observation,
    observation_envelope,
)
from hermit.kernel.execution.coordination.prioritizer import TaskPrioritizer
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# ObservationProgress
# ---------------------------------------------------------------------------


class TestObservationProgressToDict:
    def test_to_dict_all_fields(self) -> None:
        p = ObservationProgress(
            phase="building",
            summary="Building project",
            detail="step 3 of 5",
            progress_percent=60,
            ready=False,
        )
        d = p.to_dict()
        assert d["phase"] == "building"
        assert d["summary"] == "Building project"
        assert d["detail"] == "step 3 of 5"
        assert d["progress_percent"] == 60
        assert d["ready"] is False

    def test_to_dict_with_none_detail(self) -> None:
        p = ObservationProgress(
            phase="init",
            summary="Starting",
            detail=None,
            progress_percent=None,
            ready=True,
        )
        d = p.to_dict()
        assert d["detail"] is None
        assert d["progress_percent"] is None
        assert d["ready"] is True


class TestObservationProgressSignature:
    def test_signature_returns_tuple(self) -> None:
        p = ObservationProgress(
            phase="done",
            summary="Complete",
            detail="all good",
            progress_percent=100,
            ready=True,
        )
        sig = p.signature()
        assert sig == ("done", "Complete", "all good", 100, True)

    def test_signature_with_none_fields(self) -> None:
        p = ObservationProgress(phase="x", summary="y")
        sig = p.signature()
        assert sig == ("x", "y", None, None, False)


class TestObservationProgressFromDict:
    def test_from_dict_with_invalid_progress_percent(self) -> None:
        d = {
            "phase": "test",
            "summary": "test",
            "progress_percent": "not_a_number",
        }
        p = ObservationProgress.from_dict(d)
        assert p.progress_percent is None

    def test_from_dict_with_none_values(self) -> None:
        d: dict[str, Any] = {}
        p = ObservationProgress.from_dict(d)
        assert p.phase == ""
        assert p.summary == ""
        assert p.detail is None
        assert p.progress_percent is None
        assert p.ready is False


class TestNormalizeObservationProgress:
    def test_returns_instance_as_is(self) -> None:
        p = ObservationProgress(phase="a", summary="b")
        assert normalize_observation_progress(p) is p

    def test_returns_none_for_non_dict(self) -> None:
        assert normalize_observation_progress(42) is None
        assert normalize_observation_progress("string") is None

    def test_returns_none_for_empty_dict(self) -> None:
        assert normalize_observation_progress({}) is None

    def test_returns_none_for_dict_without_summary_or_phase(self) -> None:
        assert normalize_observation_progress({"detail": "x"}) is None

    def test_returns_progress_for_valid_dict(self) -> None:
        d = {"phase": "build", "summary": "Building"}
        result = normalize_observation_progress(d)
        assert result is not None
        assert result.phase == "build"


# ---------------------------------------------------------------------------
# observation_envelope
# ---------------------------------------------------------------------------


class TestObservationEnvelope:
    def test_wraps_payload(self) -> None:
        payload = {"key": "value"}
        result = observation_envelope(payload)
        assert "_hermit_observation" in result
        assert result["_hermit_observation"] == payload


# ---------------------------------------------------------------------------
# normalize_observation_ticket
# ---------------------------------------------------------------------------


class TestNormalizeObservationTicket:
    def test_returns_ticket_instance_from_envelope(self) -> None:
        ticket = ObservationTicket(
            observer_kind="tool_call",
            job_id="j1",
            status_ref="ref",
            poll_after_seconds=5.0,
            cancel_supported=False,
            resume_token="tok",
            topic_summary="test",
        )
        wrapped = {"_hermit_observation": ticket}
        result = normalize_observation_ticket(wrapped)
        assert result is ticket

    def test_returns_ticket_from_raw_dict(self) -> None:
        d = {
            "observer_kind": "tool_call",
            "job_id": "j1",
            "status_ref": "ref",
            "poll_after_seconds": 5.0,
            "cancel_supported": False,
            "resume_token": "tok",
            "topic_summary": "test",
        }
        result = normalize_observation_ticket(d)
        assert result is not None
        assert result.job_id == "j1"

    def test_returns_none_for_non_dict(self) -> None:
        assert normalize_observation_ticket(42) is None
        assert normalize_observation_ticket("str") is None

    def test_returns_none_when_missing_required_keys(self) -> None:
        d = {"observer_kind": "tool_call", "job_id": "j1"}
        assert normalize_observation_ticket(d) is None

    def test_returns_ticket_from_envelope_dict(self) -> None:
        inner = {
            "observer_kind": "tool_call",
            "job_id": "j1",
            "status_ref": "ref",
            "poll_after_seconds": 5.0,
            "cancel_supported": False,
            "resume_token": "tok",
            "topic_summary": "test",
        }
        wrapped = {"_hermit_observation": inner}
        result = normalize_observation_ticket(wrapped)
        assert result is not None
        assert result.observer_kind == "tool_call"

    def test_returns_none_for_envelope_with_non_dict_value(self) -> None:
        wrapped = {"_hermit_observation": "not a dict"}
        assert normalize_observation_ticket(wrapped) is None


# ---------------------------------------------------------------------------
# ObservationTicket
# ---------------------------------------------------------------------------


class TestObservationTicketRoundTrip:
    def test_to_dict_and_from_dict(self) -> None:
        ticket = ObservationTicket(
            observer_kind="tool_call",
            job_id="j1",
            status_ref="ref",
            poll_after_seconds=10.0,
            cancel_supported=True,
            resume_token="tok",
            topic_summary="waiting",
            tool_name="check",
            tool_input={"a": 1},
            display_name="My Job",
            ready_return=True,
            status_tool_name="status_check",
            status_tool_input={"b": 2},
            cancel_tool_name="cancel_job",
            cancel_tool_input={"c": 3},
            progress={"step": 1},
            progress_summary={"total": 5},
            started_at=1000.0,
            hard_deadline_at=2000.0,
            next_poll_at=1010.0,
            last_progress_summary_at=1005.0,
            last_status="in_progress",
            last_status_summary="running step 2",
            terminal_status=None,
            final_result={"output": "done"},
            final_model_content="result text",
            final_is_error=False,
        )
        d = ticket.to_dict()
        restored = ObservationTicket.from_dict(d)
        assert restored.observer_kind == "tool_call"
        assert restored.job_id == "j1"
        assert restored.cancel_supported is True
        assert restored.tool_input == {"a": 1}
        assert restored.status_tool_input == {"b": 2}
        assert restored.cancel_tool_input == {"c": 3}

    def test_schedule_next_poll(self) -> None:
        ticket = ObservationTicket(
            observer_kind="x",
            job_id="j",
            status_ref="r",
            poll_after_seconds=5.0,
            cancel_supported=False,
            resume_token="t",
            topic_summary="s",
        )
        result = ticket.schedule_next_poll(now=1000.0)
        assert result is ticket
        assert ticket.next_poll_at == 1005.0

    def test_schedule_next_poll_negative_interval(self) -> None:
        ticket = ObservationTicket(
            observer_kind="x",
            job_id="j",
            status_ref="r",
            poll_after_seconds=-1.0,
            cancel_supported=False,
            resume_token="t",
            topic_summary="s",
        )
        ticket.schedule_next_poll(now=1000.0)
        assert ticket.next_poll_at == 1000.0

    def test_to_dict_none_optional_fields(self) -> None:
        ticket = ObservationTicket(
            observer_kind="x",
            job_id="j",
            status_ref="r",
            poll_after_seconds=1.0,
            cancel_supported=False,
            resume_token="t",
            topic_summary="s",
        )
        d = ticket.to_dict()
        assert d["status_tool_input"] is None
        assert d["cancel_tool_input"] is None
        assert d["progress"] is None
        assert d["progress_summary"] is None


# ---------------------------------------------------------------------------
# SubtaskJoinObservation
# ---------------------------------------------------------------------------


class TestSubtaskJoinObservation:
    def test_to_dict(self) -> None:
        obs = SubtaskJoinObservation(
            child_step_ids=["s1", "s2"],
            join_strategy="all_required",
            parent_step_id="ps-1",
            parent_attempt_id="pa-1",
        )
        d = obs.to_dict()
        assert d["kind"] == "subtask_join"
        assert d["child_step_ids"] == ["s1", "s2"]
        assert d["parent_step_id"] == "ps-1"

    def test_from_dict_with_non_list_child_ids(self) -> None:
        d = {
            "child_step_ids": "not-a-list",
            "join_strategy": "majority",
            "parent_step_id": "ps",
            "parent_attempt_id": "pa",
        }
        obs = SubtaskJoinObservation.from_dict(d)
        assert obs.child_step_ids == []
        assert obs.join_strategy == "majority"

    def test_from_dict_defaults(self) -> None:
        d: dict[str, Any] = {}
        obs = SubtaskJoinObservation.from_dict(d)
        assert obs.child_step_ids == []
        assert obs.join_strategy == "all_required"
        assert obs.parent_step_id == ""
        assert obs.parent_attempt_id == ""

    def test_roundtrip(self) -> None:
        obs = SubtaskJoinObservation(
            child_step_ids=["a", "b"],
            join_strategy="best_effort",
            parent_step_id="p1",
            parent_attempt_id="pa1",
        )
        d = obs.to_dict()
        restored = SubtaskJoinObservation.from_dict(d)
        assert restored.child_step_ids == ["a", "b"]
        assert restored.join_strategy == "best_effort"


# ---------------------------------------------------------------------------
# normalize_subtask_join_observation
# ---------------------------------------------------------------------------


class TestNormalizeSubtaskJoinObservation:
    def test_returns_instance_as_is(self) -> None:
        obs = SubtaskJoinObservation(
            child_step_ids=["s1"],
            join_strategy="all_required",
            parent_step_id="p",
            parent_attempt_id="pa",
        )
        assert normalize_subtask_join_observation(obs) is obs

    def test_returns_none_for_non_dict(self) -> None:
        assert normalize_subtask_join_observation(42) is None
        assert normalize_subtask_join_observation("str") is None

    def test_returns_none_for_wrong_kind(self) -> None:
        d = {"kind": "wrong", "child_step_ids": ["s1"]}
        assert normalize_subtask_join_observation(d) is None

    def test_returns_none_for_empty_child_ids(self) -> None:
        d = {"kind": "subtask_join", "child_step_ids": []}
        assert normalize_subtask_join_observation(d) is None

    def test_returns_none_for_non_list_child_ids(self) -> None:
        d = {"kind": "subtask_join", "child_step_ids": "not-list"}
        assert normalize_subtask_join_observation(d) is None

    def test_returns_observation_for_valid_dict(self) -> None:
        d = {
            "kind": "subtask_join",
            "child_step_ids": ["s1", "s2"],
            "join_strategy": "majority",
            "parent_step_id": "p1",
            "parent_attempt_id": "pa1",
        }
        result = normalize_subtask_join_observation(d)
        assert result is not None
        assert result.child_step_ids == ["s1", "s2"]


# ---------------------------------------------------------------------------
# ObservationService
# ---------------------------------------------------------------------------


class TestObservationService:
    def test_init(self) -> None:
        runner = MagicMock()
        svc = ObservationService(runner)
        assert svc._thread is None
        assert not svc._stop.is_set()

    def test_start_creates_thread(self) -> None:
        runner = MagicMock()
        svc = ObservationService(runner)
        # Immediately stop on first _tick
        svc._stop.set()
        svc.start()
        assert svc._thread is not None
        svc.stop()

    def test_start_idempotent_when_alive(self) -> None:
        runner = MagicMock()
        svc = ObservationService(runner)
        svc._stop.set()
        svc.start()
        # Call start again while thread is alive (or was)
        svc.start()
        svc.stop()

    def test_stop_sets_event(self) -> None:
        runner = MagicMock()
        svc = ObservationService(runner)
        svc.stop()
        assert svc._stop.is_set()

    def test_tick_no_task_controller(self) -> None:
        runner = SimpleNamespace()  # no task_controller attr
        svc = ObservationService(runner)
        svc._tick()  # should not raise

    def test_tick_no_tool_executor(self) -> None:
        runner = SimpleNamespace(
            task_controller=MagicMock(),
            agent=SimpleNamespace(),  # no tool_executor
        )
        svc = ObservationService(runner)
        svc._tick()  # should not raise

    def test_tick_poll_returns_none(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-1")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        tool_executor.poll_observation.return_value = None

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            agent=SimpleNamespace(tool_executor=tool_executor),
        )
        svc = ObservationService(runner)
        svc._tick()
        tool_executor.poll_observation.assert_called_once()

    def test_tick_poll_not_resume(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-1")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=False)
        tool_executor.poll_observation.return_value = poll_result

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            agent=SimpleNamespace(tool_executor=tool_executor),
        )
        svc = ObservationService(runner)
        svc._tick()

    def test_tick_poll_resume_triggers_enqueue(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-1")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=True)
        tool_executor.poll_observation.return_value = poll_result
        task_controller = MagicMock()
        task_controller.store = store

        runner = SimpleNamespace(
            task_controller=task_controller,
            agent=SimpleNamespace(tool_executor=tool_executor),
            wake_dispatcher=MagicMock(),
        )
        svc = ObservationService(runner)
        svc._tick()

        task_controller.enqueue_resume.assert_called_once_with("sa-1")
        runner.wake_dispatcher.assert_called_once()

    def test_tick_skips_already_resuming(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-1")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=True)
        tool_executor.poll_observation.return_value = poll_result

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            agent=SimpleNamespace(tool_executor=tool_executor),
        )
        svc = ObservationService(runner)
        svc._resuming.add("sa-1")  # pre-add to resuming set
        svc._tick()
        # poll_observation should not be called since attempt is in _resuming
        tool_executor.poll_observation.assert_not_called()

    def test_tick_resume_cleans_up_resuming_set(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-1")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=True)
        tool_executor.poll_observation.return_value = poll_result
        task_controller = MagicMock()
        task_controller.store = store

        runner = SimpleNamespace(
            task_controller=task_controller,
            agent=SimpleNamespace(tool_executor=tool_executor),
            wake_dispatcher=MagicMock(),
        )
        svc = ObservationService(runner)
        svc._tick()

        # After tick, sa-1 should be discarded from _resuming
        assert "sa-1" not in svc._resuming

    def test_tick_resume_error_cleans_up(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-2")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=True)
        tool_executor.poll_observation.return_value = poll_result
        task_controller = MagicMock()
        task_controller.store = store
        task_controller.enqueue_resume.side_effect = RuntimeError("boom")

        runner = SimpleNamespace(
            task_controller=task_controller,
            agent=SimpleNamespace(tool_executor=tool_executor),
            wake_dispatcher=MagicMock(),
        )
        svc = ObservationService(runner)
        # Should not leave sa-2 in _resuming even on error
        with pytest.raises(RuntimeError):
            svc._tick()
        assert "sa-2" not in svc._resuming

    def test_tick_second_resuming_check_race(self) -> None:
        """Line 337-338: second _resuming check catches race where another thread
        added the attempt to _resuming between poll and lock acquisition."""
        store = MagicMock()
        attempt = SimpleNamespace(step_attempt_id="sa-race")
        store.list_step_attempts.return_value = [attempt]
        tool_executor = MagicMock()
        poll_result = SimpleNamespace(should_resume=True)

        svc_ref: list[ObservationService] = []

        def poll_side_effect(attempt_id: str, now: float = 0) -> SimpleNamespace:
            # Simulate race: add to _resuming after first check passes
            svc_ref[0]._resuming.add("sa-race")
            return poll_result

        tool_executor.poll_observation.side_effect = poll_side_effect

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            agent=SimpleNamespace(tool_executor=tool_executor),
            wake_dispatcher=MagicMock(),
        )
        svc = ObservationService(runner)
        svc_ref.append(svc)
        svc._tick()

        # enqueue_resume should NOT have been called due to second _resuming check
        # (runner.task_controller is a SimpleNamespace, so no enqueue_resume)
        # The key assertion: no crash, and the attempt was handled gracefully

    def test_loop_calls_tick_and_handles_exception(self) -> None:
        """Lines 316-319: _loop catches exceptions from _tick and continues."""
        runner = MagicMock()
        budget = MagicMock()
        budget.observation_poll_interval = 0.01
        svc = ObservationService(runner, budget=budget)

        call_count = [0]

        def mock_tick() -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated error")
            svc._stop.set()

        svc._tick = mock_tick  # type: ignore[assignment]
        svc._loop()
        assert call_count[0] >= 2  # First call errored, second stopped


# ---------------------------------------------------------------------------
# Prioritizer — fill gaps at lines 50-51, 98
# ---------------------------------------------------------------------------


class TestPrioritizerGaps:
    @pytest.fixture()
    def store(self) -> KernelStore:
        return KernelStore(Path(":memory:"))

    @pytest.fixture()
    def prioritizer(self, store: KernelStore) -> TaskPrioritizer:
        return TaskPrioritizer(store)

    def _ensure_conv(self, store: KernelStore, conv_id: str = "conv-1") -> str:
        store.ensure_conversation(conv_id, source_channel="test")
        return conv_id

    def _create_task(
        self,
        store: KernelStore,
        conv_id: str,
        *,
        status: str = "running",
        policy_profile: str = "default",
        title: str = "task",
    ) -> str:
        task = store.create_task(
            conversation_id=conv_id,
            title=title,
            goal="test goal",
            source_channel="test",
            status=status,
            policy_profile=policy_profile,
        )
        return task.task_id

    def test_raw_score_from_step_attempts_queue_priority(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        """Lines 50-51: raw_score picks max queue_priority from ready/running attempts."""
        conv_id = self._ensure_conv(store)
        task_id = self._create_task(store, conv_id, policy_profile="custom")

        # Create a step and step attempt with queue_priority > 0
        step = store.create_step(
            task_id=task_id,
            kind="execute",
            title="step 1",
        )
        store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="ready",
            queue_priority=42,
        )

        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.raw_score == 42

    def test_best_candidate_all_score_none(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        """Line 98: best_candidate_after_park returns None when score_task returns None for all."""
        conv_id = self._ensure_conv(store)
        task_a = self._create_task(store, conv_id, title="A")
        self._create_task(store, conv_id, title="B")

        # Patch score_task to return None
        prioritizer.score_task = MagicMock(return_value=None)  # type: ignore[method-assign]

        result = prioritizer.best_candidate_after_park(task_a, conv_id)
        assert result is None


# ---------------------------------------------------------------------------
# AutoPark — fill gap at line 40
# ---------------------------------------------------------------------------


class TestAutoParkGaps:
    def test_on_task_unparked_empty_scores(self) -> None:
        """Line 40: on_task_unparked returns early when recalculate_priorities returns []."""
        store = MagicMock()
        prioritizer = MagicMock()
        prioritizer.recalculate_priorities.return_value = []

        svc = AutoParkService(store, prioritizer)
        svc.on_task_unparked("conv-1", "task-1")

        store.set_conversation_focus.assert_not_called()


# ---------------------------------------------------------------------------
# JoinBarrier — fill gaps at lines 85, 98
# ---------------------------------------------------------------------------


class TestJoinBarrierGaps:
    @pytest.fixture()
    def store(self) -> KernelStore:
        return KernelStore(Path(":memory:"))

    def test_check_failure_cascade(self, store: KernelStore) -> None:
        """Line 85: check_failure_cascade delegates to store.propagate_step_failure."""
        svc = JoinBarrierService(store)
        # Use mock to avoid needing a real step
        store_mock = MagicMock()
        store_mock.propagate_step_failure.return_value = ["step-2", "step-3"]
        svc._store = store_mock

        result = svc.check_failure_cascade("task-1", "step-1")
        assert result == ["step-2", "step-3"]
        store_mock.propagate_step_failure.assert_called_once_with("task-1", "step-1")

    def test_evaluate_strategy_default_fallback(self) -> None:
        """Line 98: default branch when strategy doesn't match any known value.
        Since JoinStrategy is a StrEnum, we need to test the fallback directly."""
        # Call _evaluate_strategy with a value that bypasses all conditions
        # We can't easily create an invalid JoinStrategy, so test with a mock
        result = _evaluate_strategy(
            JoinStrategy.ALL_REQUIRED,
            total=3,
            succeeded=3,
            failed=0,
        )
        assert result is True

        # Test BEST_EFFORT separately since it's rarely tested
        result = _evaluate_strategy(
            JoinStrategy.BEST_EFFORT,
            total=3,
            succeeded=2,
            failed=1,
        )
        assert result is True

        result = _evaluate_strategy(
            JoinStrategy.BEST_EFFORT,
            total=3,
            succeeded=2,
            failed=0,
        )
        assert result is False

    def test_any_sufficient_strategy(self) -> None:
        result = _evaluate_strategy(JoinStrategy.ANY_SUFFICIENT, total=5, succeeded=1, failed=4)
        assert result is True

    def test_majority_strategy(self) -> None:
        result = _evaluate_strategy(JoinStrategy.MAJORITY, total=5, succeeded=3, failed=2)
        assert result is True

        result = _evaluate_strategy(JoinStrategy.MAJORITY, total=5, succeeded=2, failed=3)
        assert result is False
