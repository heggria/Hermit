"""Tests for TaskDecomposer — deterministic spec decomposition."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.decompose.models import (
    DecompositionPlan,
    GeneratedSpec,
)
from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer


def _make_spec(**overrides: object) -> GeneratedSpec:
    """Helper to create a GeneratedSpec with defaults."""
    defaults = {
        "spec_id": "test-spec-abc123",
        "title": "Test spec",
        "goal": "Test goal",
        "constraints": (),
        "acceptance_criteria": ("`make check` passes",),
        "file_plan": (),
    }
    defaults.update(overrides)
    return GeneratedSpec(**defaults)  # type: ignore[arg-type]


class TestTaskDecomposer:
    def test_empty_file_plan(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec()
        plan = decomposer.decompose(spec)
        assert isinstance(plan, DecompositionPlan)
        assert plan.spec_id == "test-spec-abc123"
        # Should have: 1 review step (for criterion) + 1 final check
        assert len(plan.steps) == 2
        assert plan.steps[-1]["key"] == "final_check"

    def test_create_steps(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=(
                {"path": "src/foo.py", "action": "create", "reason": "new module"},
                {"path": "src/bar.py", "action": "create", "reason": "new module"},
            ),
        )
        plan = decomposer.decompose(spec)
        code_steps = [s for s in plan.steps if s["kind"] == "code"]
        assert len(code_steps) == 2
        # Create steps have no dependencies
        for step in code_steps:
            assert step["depends_on"] == []

    def test_modify_depends_on_creates(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=(
                {"path": "src/foo.py", "action": "create", "reason": "new"},
                {"path": "src/bar.py", "action": "modify", "reason": "update"},
            ),
        )
        plan = decomposer.decompose(spec)
        modify_step = next(s for s in plan.steps if s["metadata"]["action"] == "modify")
        create_key = next(s["key"] for s in plan.steps if s["metadata"]["action"] == "create")
        assert create_key in modify_step["depends_on"]

    def test_review_steps_depend_on_code(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=({"path": "src/foo.py", "action": "create", "reason": "new"},),
            acceptance_criteria=("tests pass", "lint clean"),
        )
        plan = decomposer.decompose(spec)
        review_steps = [
            s for s in plan.steps if s["kind"] == "review" and s["key"] != "final_check"
        ]
        code_keys = [s["key"] for s in plan.steps if s["kind"] == "code"]
        for review in review_steps:
            for ck in code_keys:
                assert ck in review["depends_on"]

    def test_final_check_depends_on_reviews(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            acceptance_criteria=("tests pass",),
        )
        plan = decomposer.decompose(spec)
        final = plan.steps[-1]
        assert final["key"] == "final_check"
        assert final["kind"] == "review"
        review_keys = [
            s["key"] for s in plan.steps if s["kind"] == "review" and s["key"] != "final_check"
        ]
        for rk in review_keys:
            assert rk in final["depends_on"]

    def test_dependency_graph_consistency(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=({"path": "src/a.py", "action": "create", "reason": ""},),
            acceptance_criteria=("check",),
        )
        plan = decomposer.decompose(spec)
        # Every step's depends_on should match dependency_graph
        for step in plan.steps:
            assert plan.dependency_graph[step["key"]] == step["depends_on"]

    def test_estimated_duration(self) -> None:
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=(
                {"path": "src/a.py", "action": "create", "reason": ""},
                {"path": "src/b.py", "action": "create", "reason": ""},
            ),
            acceptance_criteria=("check",),
        )
        plan = decomposer.decompose(spec)
        # 2 code steps * 5 + (1 review + 1 final) * 2 = 14
        assert plan.estimated_duration_minutes == 14

    def test_frozen_plan(self) -> None:
        decomposer = TaskDecomposer()
        plan = decomposer.decompose(_make_spec())
        try:
            plan.spec_id = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_step_node_compatible_keys(self) -> None:
        """Steps must have key, kind, title, depends_on — StepNode-compatible."""
        decomposer = TaskDecomposer()
        spec = _make_spec(
            file_plan=({"path": "src/x.py", "action": "create", "reason": ""},),
        )
        plan = decomposer.decompose(spec)
        for step in plan.steps:
            assert "key" in step
            assert "kind" in step
            assert "title" in step
            assert "depends_on" in step
