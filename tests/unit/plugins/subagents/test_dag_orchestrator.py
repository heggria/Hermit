"""Tests for DAG orchestrator plan creation and dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.task.services.dag_builder import StepNode
from hermit.plugins.builtin.subagents.orchestrator.dag_orchestrator import (
    DAGOrchestrator,
    DAGPlan,
)

# ── DAGPlan ──


def test_dag_plan_is_frozen_dataclass() -> None:
    plan = DAGPlan(goal="test goal", nodes=[], rationale="because")
    assert plan.goal == "test goal"
    assert plan.nodes == []
    assert plan.rationale == "because"


def test_dag_plan_default_rationale() -> None:
    plan = DAGPlan(goal="g", nodes=[])
    assert plan.rationale == ""


# ── DAGOrchestrator.plan_from_nodes ──


def test_plan_from_nodes_minimal() -> None:
    store = MagicMock()
    builder = MagicMock()
    orch = DAGOrchestrator(store, builder)

    nodes = [{"key": "step1"}]
    plan = orch.plan_from_nodes("my goal", nodes)

    assert isinstance(plan, DAGPlan)
    assert plan.goal == "my goal"
    assert len(plan.nodes) == 1
    node = plan.nodes[0]
    assert node.key == "step1"
    assert node.kind == "execute"
    assert node.title == "step1"
    assert node.depends_on == []
    assert node.join_strategy == "all_required"
    assert node.input_bindings == {}
    assert node.max_attempts == 1
    assert node.metadata == {}


def test_plan_from_nodes_full_options() -> None:
    store = MagicMock()
    builder = MagicMock()
    orch = DAGOrchestrator(store, builder)

    nodes = [
        {
            "key": "research",
            "kind": "research",
            "title": "Research Phase",
            "depends_on": [],
            "join_strategy": "any_sufficient",
            "input_bindings": {"data": "source.output"},
            "max_attempts": 3,
            "metadata": {"priority": "high"},
        },
        {
            "key": "implement",
            "kind": "code",
            "title": "Implementation",
            "depends_on": ["research"],
            "join_strategy": "all_required",
        },
    ]
    plan = orch.plan_from_nodes("build feature", nodes, rationale="decomposed")

    assert plan.rationale == "decomposed"
    assert len(plan.nodes) == 2

    r = plan.nodes[0]
    assert r.key == "research"
    assert r.kind == "research"
    assert r.title == "Research Phase"
    assert r.join_strategy == "any_sufficient"
    assert r.input_bindings == {"data": "source.output"}
    assert r.max_attempts == 3
    assert r.metadata == {"priority": "high"}

    impl = plan.nodes[1]
    assert impl.depends_on == ["research"]


# ── DAGOrchestrator.materialize_and_dispatch ──


def test_materialize_and_dispatch_calls_builder() -> None:
    store = MagicMock()
    builder = MagicMock()
    builder.build_and_materialize.return_value = (MagicMock(), {"step1": "sid-1", "step2": "sid-2"})

    orch = DAGOrchestrator(store, builder)
    plan = DAGPlan(
        goal="g",
        nodes=[
            StepNode(key="step1", kind="execute", title="Step 1"),
            StepNode(key="step2", kind="execute", title="Step 2", depends_on=["step1"]),
        ],
    )

    result = orch.materialize_and_dispatch("task-123", plan, queue_priority=5)

    builder.build_and_materialize.assert_called_once_with("task-123", plan.nodes, queue_priority=5)
    assert result == {"step1": "sid-1", "step2": "sid-2"}


# ── DAGOrchestrator.get_step_statuses ──


def test_get_step_statuses_returns_found_steps() -> None:
    store = MagicMock()
    builder = MagicMock()

    step1 = SimpleNamespace(status="running")
    step2 = SimpleNamespace(status="completed")
    store.get_step.side_effect = lambda sid: {"sid-1": step1, "sid-2": step2}.get(sid)

    orch = DAGOrchestrator(store, builder)
    result = orch.get_step_statuses("task-1", ["sid-1", "sid-2"])

    assert result == {"sid-1": "running", "sid-2": "completed"}


def test_get_step_statuses_skips_missing() -> None:
    store = MagicMock()
    builder = MagicMock()

    store.get_step.return_value = None
    orch = DAGOrchestrator(store, builder)
    result = orch.get_step_statuses("task-1", ["missing-id"])

    assert result == {}
