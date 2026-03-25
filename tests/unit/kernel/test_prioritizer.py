"""Tests for TaskPrioritizer (D1: Smart Dispatch)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.execution.coordination.prioritizer import TaskPrioritizer
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store() -> KernelStore:
    return KernelStore(Path(":memory:"))


@pytest.fixture()
def prioritizer(store: KernelStore) -> TaskPrioritizer:
    return TaskPrioritizer(store)


def _ensure_conv(store: KernelStore, conv_id: str = "conv-1") -> str:
    store.ensure_conversation(conv_id, source_channel="test")
    return conv_id


def _create_task(
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


class TestPriorityScoreComputation:
    def test_score_nonexistent_task_returns_none(self, prioritizer: TaskPrioritizer) -> None:
        assert prioritizer.score_task("nonexistent") is None

    def test_basic_score_for_running_task(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id)
        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.task_id == task_id
        assert score.risk_penalty == 5  # "default" policy_profile

    def test_risk_penalty_critical(self, store: KernelStore, prioritizer: TaskPrioritizer) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="critical")
        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.risk_penalty == 20

    def test_risk_penalty_elevated(self, store: KernelStore, prioritizer: TaskPrioritizer) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="elevated")
        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.risk_penalty == 10

    def test_risk_penalty_unknown_profile_is_zero(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="custom")
        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.risk_penalty == 0

    def test_age_bonus_increases_with_older_tasks(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="custom")

        # Backdate created_at by 3 hours
        three_hours_ago = time.time() - 3 * 3600
        with store._get_conn():
            store._get_conn().execute(
                "UPDATE tasks SET created_at = ? WHERE task_id = ?",
                (three_hours_ago, task_id),
            )

        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.age_bonus >= 3

    def test_age_bonus_capped_at_ten(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="custom")

        # Backdate created_at by 100 hours
        old = time.time() - 100 * 3600
        with store._get_conn():
            store._get_conn().execute(
                "UPDATE tasks SET created_at = ? WHERE task_id = ?",
                (old, task_id),
            )

        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.age_bonus == 10

    def test_blocked_bonus_for_previously_blocked_task(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, status="blocked", policy_profile="custom")

        # Record a blocked event then set status back to running
        store.update_task_status(task_id, "blocked")
        store.update_task_status(task_id, "running")

        score = prioritizer.score_task(task_id)
        assert score is not None
        assert score.blocked_bonus == 10

    def test_final_score_formula(self, store: KernelStore, prioritizer: TaskPrioritizer) -> None:
        conv_id = _ensure_conv(store)
        task_id = _create_task(store, conv_id, policy_profile="custom")
        score = prioritizer.score_task(task_id)
        assert score is not None
        expected = score.raw_score - score.risk_penalty + score.age_bonus + score.blocked_bonus
        assert score.final_score == expected


class TestBestCandidateAfterPark:
    def test_two_tasks_park_one_selects_other(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A")
        task_b = _create_task(store, conv_id, title="Task B")
        store.set_conversation_focus(conv_id, task_id=task_a, reason="initial")

        # Park task A (make it blocked)
        store.update_task_status(task_a, "blocked")

        best = prioritizer.best_candidate_after_park(task_a, conv_id)
        assert best == task_b

    def test_no_candidates_returns_none(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A")
        store.update_task_status(task_a, "blocked")

        best = prioritizer.best_candidate_after_park(task_a, conv_id)
        assert best is None

    def test_excludes_parked_task_from_candidates(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A")
        # task_a is still "running" but should be excluded since it's the parked task
        best = prioritizer.best_candidate_after_park(task_a, conv_id)
        assert best is None


class TestRecalculatePriorities:
    def test_returns_sorted_scores(self, store: KernelStore, prioritizer: TaskPrioritizer) -> None:
        conv_id = _ensure_conv(store)
        _create_task(store, conv_id, title="Task 1", policy_profile="custom")
        _create_task(store, conv_id, title="Task 2", policy_profile="critical")

        scores = prioritizer.recalculate_priorities(conv_id)
        assert len(scores) == 2
        # First should have higher final_score (custom has 0 penalty vs critical 20)
        assert scores[0].final_score >= scores[1].final_score

    def test_recalculate_without_conversation_filter(
        self, store: KernelStore, prioritizer: TaskPrioritizer
    ) -> None:
        conv_id = _ensure_conv(store)
        _create_task(store, conv_id, title="Task 1")
        scores = prioritizer.recalculate_priorities()
        assert len(scores) >= 1
