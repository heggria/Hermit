"""Integration tests for DAG failure strategies and cascade behavior."""

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


class TestAllRequiredFailure:
    def test_single_failure_cascades_downstream(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        cascaded = store.propagate_step_failure(task_id, key_map["a"])
        assert key_map["b"] in cascaded
        assert key_map["c"] in cascaded

    def test_diamond_failure_at_branch(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # A succeeds, B and C activate
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.activate_waiting_dependents(task_id, key_map["a"])

        # B fails — D (all_required) should cascade fail
        store.update_step(key_map["b"], status="failed", finished_at=time.time())
        cascaded = store.propagate_step_failure(task_id, key_map["b"])
        assert key_map["d"] in cascaded
        assert store.get_step(key_map["d"]).status == "failed"

        # C is still ready (independent branch)
        assert store.get_step(key_map["c"]).status == "ready"


class TestAnySufficientFailure:
    def test_single_success_activates(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(
                key="c",
                kind="execute",
                title="C",
                depends_on=["a", "b"],
                join_strategy="any_sufficient",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["c"] in activated

    def test_single_failure_no_cascade(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(
                key="c",
                kind="execute",
                title="C",
                depends_on=["a", "b"],
                join_strategy="any_sufficient",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        cascaded = store.propagate_step_failure(task_id, key_map["a"])
        assert key_map["c"] not in cascaded


class TestBestEffortFailure:
    def test_all_terminal_activates(self, store: KernelStore, builder: StepDAGBuilder) -> None:
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

        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["c"] not in activated  # b still pending

        store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["b"])
        assert key_map["c"] in activated


class TestMajorityFailure:
    def test_majority_succeeded(self, store: KernelStore, builder: StepDAGBuilder) -> None:
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
                join_strategy="majority",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["d"] not in activated

        store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["b"])
        assert key_map["d"] in activated  # 2/3 > 50%
