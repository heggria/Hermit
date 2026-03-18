"""Tests for AutoParkService (D1: Smart Dispatch & Auto-Park)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.coordination.auto_park import AutoParkService
from hermit.kernel.execution.coordination.prioritizer import TaskPrioritizer
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store() -> KernelStore:
    return KernelStore(Path(":memory:"))


@pytest.fixture()
def prioritizer(store: KernelStore) -> TaskPrioritizer:
    return TaskPrioritizer(store)


@pytest.fixture()
def auto_park(store: KernelStore, prioritizer: TaskPrioritizer) -> AutoParkService:
    return AutoParkService(store, prioritizer)


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


class TestOnTaskParked:
    def test_park_switches_focus_to_candidate(
        self, store: KernelStore, auto_park: AutoParkService
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A")
        task_b = _create_task(store, conv_id, title="Task B")
        store.set_conversation_focus(conv_id, task_id=task_a, reason="initial")

        # Park task A
        store.update_task_status(task_a, "blocked")
        result = auto_park.on_task_parked(conv_id, task_a)

        assert result == task_b
        conv = store.get_conversation(conv_id)
        assert conv is not None
        assert conv.focus_task_id == task_b
        assert conv.focus_reason == "auto_park"

    def test_park_no_candidate_focus_unchanged(
        self, store: KernelStore, auto_park: AutoParkService
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A")
        store.set_conversation_focus(conv_id, task_id=task_a, reason="initial")

        # Block the only task
        store.update_task_status(task_a, "blocked")
        result = auto_park.on_task_parked(conv_id, task_a)

        assert result is None
        # Focus was not changed by auto_park (still has old value from before)
        conv = store.get_conversation(conv_id)
        assert conv is not None
        # Focus is still task_a since auto_park didn't change it
        assert conv.focus_task_id == task_a


class TestOnTaskUnparked:
    def test_unpark_highest_priority_gets_focus(
        self, store: KernelStore, auto_park: AutoParkService
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A", policy_profile="custom")
        task_b = _create_task(store, conv_id, title="Task B", policy_profile="critical")
        store.set_conversation_focus(conv_id, task_id=task_b, reason="initial")

        # Unpark task_a (which has lower risk penalty, so higher score)
        auto_park.on_task_unparked(conv_id, task_a)

        conv = store.get_conversation(conv_id)
        assert conv is not None
        # task_a should win since custom (0 penalty) beats critical (-20)
        assert conv.focus_task_id == task_a

    def test_unpark_lower_priority_keeps_current_focus(
        self, store: KernelStore, auto_park: AutoParkService
    ) -> None:
        conv_id = _ensure_conv(store)
        task_a = _create_task(store, conv_id, title="Task A", policy_profile="custom")
        task_b = _create_task(store, conv_id, title="Task B", policy_profile="critical")
        store.set_conversation_focus(conv_id, task_id=task_a, reason="initial")

        # Unpark task_b (critical = high penalty = lower score)
        auto_park.on_task_unparked(conv_id, task_b)

        conv = store.get_conversation(conv_id)
        assert conv is not None
        # task_a should keep focus since it has higher score
        assert conv.focus_task_id == task_a
