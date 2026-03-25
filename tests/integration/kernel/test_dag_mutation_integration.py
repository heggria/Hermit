"""Integration tests for DAG topology mutation: full lifecycle with add, skip,
rewire, conditional predicates, and event sourcing audit trail."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode
from hermit.kernel.task.services.dag_execution import DAGExecutionService


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


@pytest.fixture
def dag_exec(store: KernelStore) -> DAGExecutionService:
    return DAGExecutionService(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


def test_add_step_then_execute_full_lifecycle(
    store: KernelStore,
    builder: StepDAGBuilder,
    dag_exec: DAGExecutionService,
) -> None:
    """Full lifecycle: create DAG, add step dynamically, execute all."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Dynamically add step c depending on b
    new_step_id = builder.add_step(
        task_id,
        StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
    )
    key_map["c"] = new_step_id

    # Execute a
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    dag_exec.advance(
        task_id=task_id,
        step_id=key_map["a"],
        step_attempt_id="",
        status="succeeded",
    )
    assert store.get_step(key_map["b"]).status == "ready"

    # Execute b
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    dag_exec.advance(
        task_id=task_id,
        step_id=key_map["b"],
        step_attempt_id="",
        status="succeeded",
    )
    assert store.get_step(key_map["c"]).status == "ready"

    # Execute c
    store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
    assert not store.has_non_terminal_steps(task_id)


def test_skip_middle_step_lifecycle(
    store: KernelStore,
    builder: StepDAGBuilder,
    dag_exec: DAGExecutionService,
) -> None:
    """Skip a middle step and verify downstream still executes."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Execute a
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    dag_exec.advance(
        task_id=task_id,
        step_id=key_map["a"],
        step_attempt_id="",
        status="succeeded",
    )

    # Skip b
    builder.skip_step(task_id, "b", reason="not needed")
    # c should be ready since b is skipped (counts as success)
    step_c = store.get_step(key_map["c"])
    assert step_c.status == "ready"

    # Execute c
    store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
    assert not store.has_non_terminal_steps(task_id)
    status = dag_exec.compute_task_status(task_id=task_id, step_status="succeeded")
    assert status == "completed"


def test_rewire_then_execute(
    store: KernelStore,
    builder: StepDAGBuilder,
    dag_exec: DAGExecutionService,
) -> None:
    """Rewire deps then execute the modified DAG."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B"),
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Rewire c to depend on b instead of a
    builder.rewire_dependency(task_id, "c", ["b"])

    # Execute b (c should become ready, not a)
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(task_id, key_map["b"])
    assert key_map["c"] in activated


def test_event_sourcing_complete_audit_trail(
    store: KernelStore,
    builder: StepDAGBuilder,
) -> None:
    """All mutations should be traceable through events."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    builder.build_and_materialize(task_id, nodes)

    # Add step
    builder.add_step(task_id, StepNode(key="c", kind="execute", title="C"))
    # Skip step
    builder.skip_step(task_id, "c", reason="not needed")
    # Rewire
    builder.rewire_dependency(task_id, "b", [])

    topology_events = store.list_events(task_id=task_id, event_type="dag.topology_changed")
    mutations = [e["payload"]["mutation"] for e in topology_events]
    assert "add_step" in mutations
    assert "skip_step" in mutations
    assert "rewire_dependency" in mutations


def test_multiple_mutations_preserve_dag_integrity(
    store: KernelStore,
    builder: StepDAGBuilder,
) -> None:
    """Multiple mutations should maintain a valid DAG."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    builder.build_and_materialize(task_id, nodes)

    # Add c depending on a
    builder.add_step(
        task_id,
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
    )
    # Rewire b to also depend on c
    builder.rewire_dependency(task_id, "b", ["a", "c"])
    # Verify b depends on both a and c
    key_map = store.get_key_to_step_id(task_id)
    step_b = store.get_step(key_map["b"])
    assert key_map["a"] in step_b.depends_on
    assert key_map["c"] in step_b.depends_on


def test_conditional_predicate_skips_on_false(
    store: KernelStore,
    builder: StepDAGBuilder,
    dag_exec: DAGExecutionService,
) -> None:
    """Steps with predicates that evaluate to False should be auto-skipped."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(
            key="b",
            kind="execute",
            title="B",
            depends_on=["a"],
            predicate="a == 'succeeded'",
        ),
        StepNode(
            key="c",
            kind="execute",
            title="C",
            depends_on=["a"],
            predicate="a == 'nonexistent_status'",
        ),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Execute a
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    dag_exec.advance(
        task_id=task_id,
        step_id=key_map["a"],
        step_attempt_id="",
        status="succeeded",
    )

    # b should be ready (predicate is True: a == 'succeeded')
    step_b = store.get_step(key_map["b"])
    assert step_b.status == "ready"

    # c should be skipped (predicate is False: a == 'nonexistent_status')
    step_c = store.get_step(key_map["c"])
    assert step_c.status == "skipped"


def test_skip_root_then_downstream_chain(
    store: KernelStore,
    builder: StepDAGBuilder,
    dag_exec: DAGExecutionService,
) -> None:
    """Skipping a root step should activate the entire downstream chain."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Skip a => b should become ready
    builder.skip_step(task_id, "a", reason="fast path")
    assert store.get_step(key_map["b"]).status == "ready"

    # Skip b => c should become ready
    builder.skip_step(task_id, "b", reason="fast path")
    assert store.get_step(key_map["c"]).status == "ready"

    # All steps terminal
    builder.skip_step(task_id, "c", reason="fast path")
    assert not store.has_non_terminal_steps(task_id)
    status = dag_exec.compute_task_status(task_id=task_id, step_status="skipped")
    assert status == "completed"
