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
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


class TestActivateWaitingDependents:
    def test_activate_on_dependency_completion(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        assert store.get_step(key_map["b"]).status == "waiting"
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["b"] in activated
        assert store.get_step(key_map["b"]).status == "ready"

    def test_no_activation_when_dependency_still_running(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(key="c", kind="execute", title="C", depends_on=["a", "b"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["c"] not in activated
        assert store.get_step(key_map["c"]).status == "waiting"

    def test_diamond_activation(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert set(activated) == {key_map["b"], key_map["c"]}
        assert store.get_step(key_map["d"]).status == "waiting"

        store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
        activated2 = store.activate_waiting_dependents(task_id, key_map["b"])
        assert key_map["d"] not in activated2

        store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
        activated3 = store.activate_waiting_dependents(task_id, key_map["c"])
        assert key_map["d"] in activated3
        assert store.get_step(key_map["d"]).status == "ready"


class TestFailurePropagation:
    def test_cascade_failure_all_required(
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
        assert store.get_step(key_map["b"]).status == "failed"
        assert store.get_step(key_map["c"]).status == "failed"

    def test_any_sufficient_no_cascade_on_single_failure(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
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
        assert store.get_step(key_map["c"]).status == "waiting"

    def test_has_non_terminal_steps(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        assert store.has_non_terminal_steps(task_id) is True
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        assert store.has_non_terminal_steps(task_id) is True
        store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
        assert store.has_non_terminal_steps(task_id) is False


class TestCycleDetection:
    def test_dag_builder_rejects_cycle(self, store: KernelStore) -> None:
        """Cycle detection is validated at the DAGBuilder level."""
        from hermit.kernel.task.services.dag_builder import StepDAGBuilder

        builder = StepDAGBuilder(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A", depends_on=["c"]),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        with pytest.raises(ValueError, match="Cycle"):
            builder.validate(nodes)

    def test_create_step_linear_chain_is_valid(self, store: KernelStore) -> None:
        """A→B→C is a valid linear chain, not a cycle."""
        task_id = _make_task(store)
        a = store.create_step(task_id=task_id, kind="execute", status="ready")
        b = store.create_step(
            task_id=task_id, kind="execute", status="waiting", depends_on=[a.step_id]
        )
        c = store.create_step(
            task_id=task_id, kind="execute", status="waiting", depends_on=[b.step_id]
        )
        assert c.status == "waiting"
        assert b.step_id in c.depends_on
