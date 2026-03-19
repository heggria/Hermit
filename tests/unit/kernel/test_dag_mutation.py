"""Unit tests for DAG topology mutation: add_step, skip_step, rewire_dependency,
conditional predicates, cycle detection, and event sourcing."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import (
    ConditionalStepNode,
    StepDAGBuilder,
    StepNode,
)
from hermit.kernel.task.services.dag_execution import DAGExecutionService


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


class TestAddStep:
    def test_add_step_to_running_dag(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Can add a new step to a running DAG."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        builder.build_and_materialize(task_id, nodes)
        new_node = StepNode(key="c", kind="execute", title="C", depends_on=["b"])
        step_id = builder.add_step(task_id, new_node)
        step = store.get_step(step_id)
        assert step is not None
        assert step.status == "waiting"
        assert step.node_key == "c"

    def test_add_step_no_deps_is_ready(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """A step with no dependencies should be immediately ready."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        new_node = StepNode(key="b", kind="execute", title="B")
        step_id = builder.add_step(task_id, new_node)
        step = store.get_step(step_id)
        assert step is not None
        assert step.status == "ready"

    def test_add_step_cycle_detection(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Adding a step that creates a cycle should raise ValueError."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        builder.build_and_materialize(task_id, nodes)
        # Add c depending on b
        builder.add_step(task_id, StepNode(key="c", kind="execute", title="C", depends_on=["b"]))
        # Now try to rewire a to depend on c => cycle a->b->c->a
        with pytest.raises(ValueError, match="Cycle"):
            builder.rewire_dependency(task_id, "a", ["c"])

    def test_add_step_emits_topology_changed_event(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Adding a step should emit a dag.topology_changed event."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        builder.add_step(
            task_id,
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        )
        events = store.list_events(task_id=task_id, event_type="dag.topology_changed")
        assert len(events) >= 1
        last_evt = events[-1]
        assert last_evt["payload"]["mutation"] == "add_step"
        assert last_evt["payload"]["node_key"] == "b"

    def test_add_step_duplicate_key_raises(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Adding a step with an existing key should raise ValueError."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="already exists"):
            builder.add_step(task_id, StepNode(key="a", kind="execute", title="A2"))

    def test_add_step_unknown_dependency_raises(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Adding a step depending on non-existent key should raise ValueError."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="unknown"):
            builder.add_step(
                task_id,
                StepNode(key="b", kind="execute", title="B", depends_on=["z"]),
            )

    def test_add_step_creates_attempt(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Adding a step should also create a step_attempt."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        step_id = builder.add_step(task_id, StepNode(key="b", kind="execute", title="B"))
        attempts = store.list_step_attempts(step_id=step_id, limit=10)
        assert len(attempts) == 1
        assert attempts[0].status == "ready"

    def test_add_step_with_predicate_stores_in_metadata(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Predicate should be stored in the step attempt context."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        step_id = builder.add_step(
            task_id,
            StepNode(
                key="b",
                kind="execute",
                title="B",
                depends_on=["a"],
                predicate="score > 80",
            ),
        )
        attempts = store.list_step_attempts(step_id=step_id, limit=1)
        ctx = attempts[0].context or {}
        meta = ctx.get("ingress_metadata", {})
        node_meta = meta.get("dag_node_metadata", {})
        assert node_meta.get("predicate") == "score > 80"


class TestSkipStep:
    def test_skip_step_marks_skipped(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Skipping a step sets its status to 'skipped'."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        # Complete a so b becomes ready
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.activate_waiting_dependents(task_id, key_map["a"])
        # Skip b
        builder.skip_step(task_id, "b", reason="not needed")
        step_b = store.get_step(key_map["b"])
        assert step_b is not None
        assert step_b.status == "skipped"

    def test_skip_step_activates_downstream(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Skipping a step should activate its downstream dependents."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.activate_waiting_dependents(task_id, key_map["a"])
        builder.skip_step(task_id, "b", reason="not needed")
        step_c = store.get_step(key_map["c"])
        assert step_c is not None
        assert step_c.status == "ready"

    def test_skip_step_emits_event(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Skipping a step should emit a dag.topology_changed event."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        _dag, _key_map = builder.build_and_materialize(task_id, nodes)
        builder.skip_step(task_id, "a", reason="testing")
        events = store.list_events(task_id=task_id, event_type="dag.topology_changed")
        assert len(events) >= 1
        assert events[-1]["payload"]["mutation"] == "skip_step"

    def test_skip_nonexistent_step_raises(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Skipping a nonexistent step should raise ValueError."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="not found"):
            builder.skip_step(task_id, "z", reason="nope")

    def test_skip_step_closes_attempts(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Skipping should also mark step_attempts as skipped."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        builder.skip_step(task_id, "a", reason="testing")
        attempts = store.list_step_attempts(step_id=key_map["a"], limit=10)
        assert all(a.status == "skipped" for a in attempts)


class TestRewireDependency:
    def test_rewire_changes_deps(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Rewiring dependencies updates the step's depends_on."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        builder.rewire_dependency(task_id, "c", ["b"])
        step_c = store.get_step(key_map["c"])
        assert step_c is not None
        assert key_map["b"] in step_c.depends_on
        assert key_map["a"] not in step_c.depends_on

    def test_rewire_to_empty_deps_makes_ready(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Rewiring to empty deps should make a waiting step ready."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        assert store.get_step(key_map["b"]).status == "waiting"
        builder.rewire_dependency(task_id, "b", [])
        step_b = store.get_step(key_map["b"])
        assert step_b.status == "ready"

    def test_rewire_cycle_detection(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Rewiring that creates a cycle should raise ValueError."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="Cycle"):
            builder.rewire_dependency(task_id, "a", ["b"])

    def test_rewire_emits_event(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Rewiring should emit a dag.topology_changed event."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, _key_map = builder.build_and_materialize(task_id, nodes)
        builder.rewire_dependency(task_id, "b", [])
        events = store.list_events(task_id=task_id, event_type="dag.topology_changed")
        assert len(events) >= 1
        assert events[-1]["payload"]["mutation"] == "rewire_dependency"

    def test_rewire_nonexistent_step_raises(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="not found"):
            builder.rewire_dependency(task_id, "z", [])

    def test_rewire_unknown_dep_raises(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        builder.build_and_materialize(task_id, nodes)
        with pytest.raises(ValueError, match="unknown"):
            builder.rewire_dependency(task_id, "b", ["nonexistent"])

    def test_rewire_updates_step_attempts(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Rewiring to empty deps should also update step_attempt status."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        # b is waiting
        attempts_before = store.list_step_attempts(step_id=key_map["b"], limit=1)
        assert attempts_before[0].status == "waiting"
        # Rewire to no deps => ready
        builder.rewire_dependency(task_id, "b", [])
        attempts_after = store.list_step_attempts(step_id=key_map["b"], limit=1)
        assert attempts_after[0].status == "ready"


class TestConditionalPredicate:
    def test_evaluate_predicate_true(self) -> None:
        assert (
            StepDAGBuilder.evaluate_predicate("status == 'succeeded'", {"status": "succeeded"})
            is True
        )

    def test_evaluate_predicate_false(self) -> None:
        assert (
            StepDAGBuilder.evaluate_predicate("status == 'failed'", {"status": "succeeded"})
            is False
        )

    def test_evaluate_predicate_complex(self) -> None:
        outputs = {"research": "succeeded", "score": 85}
        assert StepDAGBuilder.evaluate_predicate("score > 80", outputs) is True
        assert StepDAGBuilder.evaluate_predicate("score < 50", outputs) is False

    def test_evaluate_predicate_error_returns_false(self) -> None:
        assert StepDAGBuilder.evaluate_predicate("undefined_var > 0", {}) is False

    def test_evaluate_predicate_with_none(self) -> None:
        assert StepDAGBuilder.evaluate_predicate("", {}) is False
        assert StepDAGBuilder.evaluate_predicate(None, {}) is False

    def test_predicate_stored_in_node_metadata(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """StepNode predicate should be stored in metadata during materialize."""
        task_id = _make_task(store)
        node = StepNode(
            key="a",
            kind="execute",
            title="A",
            predicate="score > 80",
        )
        dag = builder.validate([node])
        key_map = builder.materialize(task_id, dag)
        attempts = store.list_step_attempts(step_id=key_map["a"], limit=1)
        assert len(attempts) == 1
        ctx = attempts[0].context or {}
        meta = ctx.get("ingress_metadata", {})
        node_meta = meta.get("dag_node_metadata", {})
        assert node_meta.get("predicate") == "score > 80"

    def test_conditional_step_node_inherits_predicate(self) -> None:
        """ConditionalStepNode should work via inherited predicate field."""
        node = ConditionalStepNode(
            key="a",
            kind="execute",
            title="A",
            predicate="x > 5",
        )
        assert node.predicate == "x > 5"
        assert isinstance(node, StepNode)


class TestDAGExecutionWithSkipped:
    def test_skipped_step_counted_as_success_for_downstream(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Skipped steps should be treated as success for dependency resolution."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        builder.skip_step(task_id, "a", reason="testing")
        step_b = store.get_step(key_map["b"])
        assert step_b.status == "ready"

    def test_compute_task_status_all_skipped(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """Task with all steps skipped should be completed."""
        dag_exec = DAGExecutionService(store)
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        builder.build_and_materialize(task_id, nodes)
        builder.skip_step(task_id, "a", reason="testing")
        status = dag_exec.compute_task_status(task_id=task_id, step_status="skipped")
        assert status == "completed"


class TestExistingDAGUnbroken:
    def test_static_dag_still_works(self, builder: StepDAGBuilder, store: KernelStore) -> None:
        """Existing static DAG execution path should not be affected."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        assert store.get_step(key_map["a"]).status == "ready"
        assert store.get_step(key_map["b"]).status == "waiting"
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        activated = store.activate_waiting_dependents(task_id, key_map["a"])
        assert key_map["b"] in activated
        assert store.get_step(key_map["b"]).status == "ready"

    def test_build_and_materialize_backward_compat(
        self, builder: StepDAGBuilder, store: KernelStore
    ) -> None:
        """build_and_materialize should still return (DAGDefinition, key_map)."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        dag, key_map = builder.build_and_materialize(task_id, nodes)
        assert dag.roots == ["a"]
        assert "a" in key_map
