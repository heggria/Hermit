from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.coordination.join_barrier import (
    JoinBarrierService,
    JoinStrategy,
    _evaluate_strategy,
)
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def service(store: KernelStore) -> JoinBarrierService:
    return JoinBarrierService(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


class TestEvaluateStrategy:
    def test_all_required_all_succeeded(self) -> None:
        assert _evaluate_strategy(JoinStrategy.ALL_REQUIRED, 3, 3, 0) is True

    def test_all_required_partial(self) -> None:
        assert _evaluate_strategy(JoinStrategy.ALL_REQUIRED, 3, 2, 0) is False

    def test_all_required_one_failed(self) -> None:
        assert _evaluate_strategy(JoinStrategy.ALL_REQUIRED, 3, 2, 1) is False

    def test_any_sufficient_one_succeeded(self) -> None:
        assert _evaluate_strategy(JoinStrategy.ANY_SUFFICIENT, 3, 1, 2) is True

    def test_any_sufficient_none_succeeded(self) -> None:
        assert _evaluate_strategy(JoinStrategy.ANY_SUFFICIENT, 3, 0, 3) is False

    def test_majority_over_half(self) -> None:
        assert _evaluate_strategy(JoinStrategy.MAJORITY, 4, 3, 1) is True

    def test_majority_exactly_half(self) -> None:
        assert _evaluate_strategy(JoinStrategy.MAJORITY, 4, 2, 2) is False

    def test_best_effort_all_terminal(self) -> None:
        assert _evaluate_strategy(JoinStrategy.BEST_EFFORT, 3, 1, 2) is True

    def test_best_effort_some_pending(self) -> None:
        assert _evaluate_strategy(JoinStrategy.BEST_EFFORT, 3, 1, 1) is False


class TestJoinBarrierService:
    def test_evaluate_no_deps(self, service: JoinBarrierService, store: KernelStore) -> None:
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="ready")
        result = service.evaluate(task_id, step.step_id)
        assert result.satisfied is True
        assert result.total == 0

    def test_evaluate_all_required_satisfied(
        self, service: JoinBarrierService, store: KernelStore
    ) -> None:
        task_id = _make_task(store)
        a = store.create_step(task_id=task_id, kind="execute", status="running")
        b = store.create_step(
            task_id=task_id,
            kind="execute",
            status="waiting",
            depends_on=[a.step_id],
        )
        import time

        store.update_step(a.step_id, status="succeeded", finished_at=time.time())
        result = service.evaluate(task_id, b.step_id)
        assert result.satisfied is True
        assert result.succeeded == 1

    def test_evaluate_all_required_not_satisfied(
        self, service: JoinBarrierService, store: KernelStore
    ) -> None:
        task_id = _make_task(store)
        a = store.create_step(task_id=task_id, kind="execute", status="running")
        b = store.create_step(
            task_id=task_id,
            kind="execute",
            status="waiting",
            depends_on=[a.step_id],
        )
        result = service.evaluate(task_id, b.step_id)
        assert result.satisfied is False
        assert result.pending == 1

    def test_evaluate_nonexistent_step(
        self, service: JoinBarrierService, store: KernelStore
    ) -> None:
        result = service.evaluate("task_xxx", "step_xxx")
        assert result.satisfied is False
