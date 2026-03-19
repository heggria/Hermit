"""Tests for best_effort join strategy when all upstream dependencies fail.

Covers the edge case fixed in propagate_step_failure (Fix 5): after cascading
failures, activate_waiting_dependents is called so that best_effort steps
whose barriers are fully satisfied (all deps terminal, even if all failed)
get activated instead of remaining stuck in 'waiting' forever.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1", title="test", goal="test", source_channel="test"
    )
    return task.task_id


class TestBestEffortAllFailed:
    """best_effort step activates even when every upstream dependency failed."""

    def test_activated_when_all_deps_failed(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """Two deps both fail → best_effort downstream should become ready."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(
                key="c",
                kind="execute",
                title="C",
                depends_on=["a", "b"],
                join_strategy="best_effort",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # First dep fails — c should stay waiting (b still pending)
        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["a"])
        assert store.get_step(key_map["c"]).status == "waiting"

        # Second dep fails — c should now activate (all deps terminal)
        store.update_step(key_map["b"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["b"])
        assert store.get_step(key_map["c"]).status == "ready"

    def test_activated_when_mixed_terminal(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """One dep succeeds, one fails → best_effort downstream should activate."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(
                key="c",
                kind="execute",
                title="C",
                depends_on=["a", "b"],
                join_strategy="best_effort",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # a succeeds — c stays waiting (b not terminal yet)
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["c"] not in activated

        # b fails — c should now activate via propagate_step_failure
        store.update_step(key_map["b"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["b"])
        assert store.get_step(key_map["c"]).status == "ready"

    def test_not_activated_while_deps_still_running(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """best_effort step must NOT activate while any dep is non-terminal."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(key="c", kind="execute", title="C"),
            StepNode(
                key="d",
                kind="execute",
                title="D",
                depends_on=["a", "b", "c"],
                join_strategy="best_effort",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # a fails — d should stay waiting (b, c still pending)
        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["a"])
        assert store.get_step(key_map["d"]).status == "waiting"

        # b fails — d should still stay waiting (c still pending)
        store.update_step(key_map["b"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["b"])
        assert store.get_step(key_map["d"]).status == "waiting"

        # c fails — NOW d should activate (all 3 deps terminal)
        store.update_step(key_map["c"], status="failed", finished_at=time.time())
        store.propagate_step_failure(task_id, key_map["c"])
        assert store.get_step(key_map["d"]).status == "ready"

    def test_all_required_not_activated_when_all_failed(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """Contrast: all_required step should cascade-fail, NOT activate."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(
                key="c",
                kind="execute",
                title="C",
                depends_on=["a", "b"],
                join_strategy="all_required",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        cascaded = store.propagate_step_failure(task_id, key_map["a"])
        # all_required: first failure cascades immediately
        assert key_map["c"] in cascaded
        assert store.get_step(key_map["c"]).status == "failed"
