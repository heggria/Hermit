from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import (
    StepDAGBuilder,
    StepNode,
)


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


def _make_task(store: KernelStore) -> str:
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


class TestValidate:
    def test_single_node(self, builder: StepDAGBuilder) -> None:
        nodes = [StepNode(key="a", kind="execute", title="Step A")]
        dag = builder.validate(nodes)
        assert dag.roots == ["a"]
        assert dag.leaves == ["a"]
        assert dag.topological_order == ["a"]

    def test_linear_chain(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        dag = builder.validate(nodes)
        assert dag.roots == ["a"]
        assert dag.leaves == ["c"]
        assert dag.topological_order == ["a", "b", "c"]

    def test_diamond(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        dag = builder.validate(nodes)
        assert dag.roots == ["a"]
        assert dag.leaves == ["d"]
        assert dag.topological_order[0] == "a"
        assert dag.topological_order[-1] == "d"
        assert set(dag.topological_order[1:3]) == {"b", "c"}

    def test_cycle_detected(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A", depends_on=["c"]),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        with pytest.raises(ValueError, match="Cycle"):
            builder.validate(nodes)

    def test_duplicate_key(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="a", kind="execute", title="A2"),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            builder.validate(nodes)

    def test_unknown_dependency(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A", depends_on=["z"]),
        ]
        with pytest.raises(ValueError, match="unknown step 'z'"):
            builder.validate(nodes)

    def test_empty(self, builder: StepDAGBuilder) -> None:
        with pytest.raises(ValueError, match="at least one"):
            builder.validate([])

    def test_disconnected_graph(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
        ]
        with pytest.raises(ValueError, match="not weakly connected"):
            builder.validate(nodes)

    def test_wide_fan_out(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="root", kind="execute", title="Root"),
            StepNode(key="b", kind="execute", title="B", depends_on=["root"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["root"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["root"]),
            StepNode(key="e", kind="execute", title="E", depends_on=["root"]),
        ]
        dag = builder.validate(nodes)
        assert dag.roots == ["root"]
        assert set(dag.leaves) == {"b", "c", "d", "e"}

    def test_complex_dag(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
            StepNode(key="e", kind="execute", title="E", depends_on=["c"]),
            StepNode(key="f", kind="execute", title="F", depends_on=["d"]),
        ]
        dag = builder.validate(nodes)
        assert dag.roots == ["a"]
        order = dag.topological_order
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")
        assert order.index("c") < order.index("e")
        assert order.index("d") < order.index("f")


class TestMaterialize:
    def test_root_is_ready_others_waiting(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        store.ensure_conversation("conv_1", source_channel="test")
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        dag = builder.validate(nodes)
        key_map = builder.materialize(task_id, dag)

        step_a = store.get_step(key_map["a"])
        step_b = store.get_step(key_map["b"])
        assert step_a is not None
        assert step_b is not None
        assert step_a.status == "ready"
        assert step_b.status == "waiting"

    def test_diamond_materialization(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        store.ensure_conversation("conv_1", source_channel="test")
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        assert store.get_step(key_map["a"]).status == "ready"
        assert store.get_step(key_map["b"]).status == "waiting"
        assert store.get_step(key_map["c"]).status == "waiting"
        assert store.get_step(key_map["d"]).status == "waiting"

        step_b = store.get_step(key_map["b"])
        assert key_map["a"] in step_b.depends_on

    def test_join_strategy_preserved(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        store.ensure_conversation("conv_1", source_channel="test")
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(
                key="b",
                kind="execute",
                title="B",
                depends_on=["a"],
                join_strategy="any_sufficient",
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        step_b = store.get_step(key_map["b"])
        assert step_b.join_strategy == "any_sufficient"
