"""Integration tests for DAG task lifecycle: create → parallel execute → join → complete."""

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
        title="dag_test",
        goal="test DAG lifecycle",
        source_channel="test",
    )
    return task.task_id


def test_diamond_full_lifecycle(store: KernelStore, builder: StepDAGBuilder) -> None:
    """Diamond: A → {B, C} → D — full lifecycle from creation to completion."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="research", title="Research"),
        StepNode(key="b", kind="code", title="Frontend", depends_on=["a"]),
        StepNode(key="c", kind="code", title="Backend", depends_on=["a"]),
        StepNode(key="d", kind="review", title="Review", depends_on=["b", "c"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    assert store.get_step(key_map["a"]).status == "ready"
    assert store.get_step(key_map["b"]).status == "waiting"
    assert store.get_step(key_map["c"]).status == "waiting"
    assert store.get_step(key_map["d"]).status == "waiting"

    # Step A executes and succeeds
    store.update_step(key_map["a"], status="running")
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(task_id, key_map["a"])
    assert set(activated) == {key_map["b"], key_map["c"]}
    assert store.get_step(key_map["b"]).status == "ready"
    assert store.get_step(key_map["c"]).status == "ready"
    assert store.get_step(key_map["d"]).status == "waiting"

    # Steps B and C execute in parallel and succeed
    store.update_step(key_map["b"], status="running")
    store.update_step(key_map["c"], status="running")
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(task_id, key_map["b"])
    assert key_map["d"] not in activated

    store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(task_id, key_map["c"])
    assert key_map["d"] in activated
    assert store.get_step(key_map["d"]).status == "ready"

    # Step D executes and succeeds
    store.update_step(key_map["d"], status="running")
    store.update_step(key_map["d"], status="succeeded", finished_at=time.time())
    assert not store.has_non_terminal_steps(task_id)


def test_linear_lifecycle(store: KernelStore, builder: StepDAGBuilder) -> None:
    """Linear: A → B → C — degenerates to sequential execution."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    assert store.get_step(key_map["a"]).status == "ready"
    assert store.get_step(key_map["b"]).status == "waiting"
    assert store.get_step(key_map["c"]).status == "waiting"

    # A → succeeded
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(task_id, key_map["a"])
    assert store.get_step(key_map["b"]).status == "ready"
    assert store.get_step(key_map["c"]).status == "waiting"

    # B → succeeded
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(task_id, key_map["b"])
    assert store.get_step(key_map["c"]).status == "ready"

    # C → succeeded
    store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
    assert not store.has_non_terminal_steps(task_id)


def test_wide_fan_out_lifecycle(store: KernelStore, builder: StepDAGBuilder) -> None:
    """A → {B, C, D, E} — all children activate simultaneously."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
        StepNode(key="d", kind="execute", title="D", depends_on=["a"]),
        StepNode(key="e", kind="execute", title="E", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(task_id, key_map["a"])
    assert len(activated) == 4
    for k in ["b", "c", "d", "e"]:
        assert store.get_step(key_map[k]).status == "ready"


def test_claim_respects_dependencies(store: KernelStore, builder: StepDAGBuilder) -> None:
    """claim_next_ready_step_attempt should not claim waiting steps."""
    task_id = _make_task(store)
    store.update_task_status(task_id, "queued")
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Only A should be claimable
    attempt = store.claim_next_ready_step_attempt()
    assert attempt is not None
    assert attempt.step_id == key_map["a"]

    # No more claimable (B is waiting)
    attempt2 = store.claim_next_ready_step_attempt()
    assert attempt2 is None


def test_backward_compat_no_deps(store: KernelStore) -> None:
    """Steps without depends_on continue to work as before."""
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1", title="test", goal="test", source_channel="test"
    )
    step = store.create_step(task_id=task.task_id, kind="execute", status="running")
    assert step.depends_on == []
    assert step.join_strategy == "all_required"
    assert step.input_bindings == {}
    assert step.status == "running"
