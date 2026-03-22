"""Tests for TaskDecomposer and LLMTaskDecomposer — spec decomposition."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.plugins.builtin.hooks.decompose.llm_task_decomposer import LLMTaskDecomposer
from hermit.plugins.builtin.hooks.decompose.models import (
    DecompositionPlan,
    GeneratedSpec,
)
from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer
from hermit.runtime.provider_host.shared.contracts import (
    ProviderFeatures,
    ProviderResponse,
    UsageMetrics,
)


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
        # Should have: 1 implement_goal step + 1 review step (for criterion) + 1 final check
        assert len(plan.steps) == 3
        assert plan.steps[0]["key"] == "implement_goal"
        assert plan.steps[0]["kind"] == "execute"
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


# ---------------------------------------------------------------------------
# Helpers for LLMTaskDecomposer tests
# ---------------------------------------------------------------------------

_VALID_DAG_JSON: dict[str, Any] = {
    "steps": [
        {
            "key": "implement_cache",
            "kind": "code",
            "title": "Implement cache module",
            "depends_on": [],
            "description": "Create the cache module.",
        },
        {
            "key": "test_cache",
            "kind": "test",
            "title": "Test cache module",
            "depends_on": ["implement_cache"],
            "description": "Add unit tests.",
        },
        {
            "key": "final_check",
            "kind": "review",
            "title": "Run final verification",
            "depends_on": ["implement_cache", "test_cache"],
        },
    ],
    "estimated_duration_minutes": 15,
    "rationale": "Simple create-test-review pipeline.",
}


def _make_dag_response(data: dict[str, Any]) -> ProviderResponse:
    return ProviderResponse(
        content=[{"type": "text", "text": json.dumps(data)}],
        stop_reason="end_turn",
        usage=UsageMetrics(),
    )


def _make_fake_provider(response: ProviderResponse) -> SimpleNamespace:
    return SimpleNamespace(
        name="fake",
        features=ProviderFeatures(),
        generate=lambda request: response,
        stream=lambda request: iter([]),
        clone=lambda **kw: None,
    )


# ---------------------------------------------------------------------------
# LLMTaskDecomposer tests
# ---------------------------------------------------------------------------


class TestLLMTaskDecomposer:
    def test_llm_decompose_with_mock_provider(self) -> None:
        """Mock returns valid DAG -> DecompositionPlan."""
        response = _make_dag_response(_VALID_DAG_JSON)
        provider = _make_fake_provider(response)

        decomposer = LLMTaskDecomposer(provider, model="test-model")
        plan = decomposer.decompose(_make_spec())

        assert isinstance(plan, DecompositionPlan)
        assert plan.spec_id == "test-spec-abc123"
        assert len(plan.steps) == 3
        assert plan.steps[0]["key"] == "implement_cache"
        assert plan.steps[-1]["key"] == "final_check"
        assert plan.estimated_duration_minutes == 15
        assert "implement_cache" in plan.dependency_graph
        assert plan.dependency_graph["test_cache"] == ["implement_cache"]

    def test_llm_decompose_fallback_on_failure(self) -> None:
        """Provider raises -> falls back to deterministic TaskDecomposer."""

        def _exploding_generate(request: Any) -> ProviderResponse:
            raise RuntimeError("LLM is down")

        provider = SimpleNamespace(
            name="broken",
            features=ProviderFeatures(),
            generate=_exploding_generate,
            stream=lambda request: iter([]),
            clone=lambda **kw: None,
        )

        decomposer = LLMTaskDecomposer(provider, model="test-model")
        plan = decomposer.decompose(_make_spec())

        # Should fall back to deterministic decomposer and still produce a plan
        assert isinstance(plan, DecompositionPlan)
        assert plan.spec_id == "test-spec-abc123"
        assert len(plan.steps) >= 2
        assert plan.steps[-1]["key"] == "final_check"


class TestValidateDecompositionOutput:
    """Tests for LLMTaskDecomposer._validate_decomposition_output."""

    def test_validate_decomposition_duplicate_keys(self) -> None:
        """Duplicate step keys must raise ValueError."""
        data = {
            "steps": [
                {"key": "step_a", "kind": "code", "title": "A", "depends_on": []},
                {"key": "step_a", "kind": "test", "title": "B", "depends_on": []},
                {"key": "final_check", "kind": "review", "title": "Final", "depends_on": []},
            ],
        }
        with pytest.raises(ValueError, match="duplicate step key"):
            LLMTaskDecomposer._validate_decomposition_output(data)

    def test_validate_decomposition_invalid_deps(self) -> None:
        """depends_on referencing a non-existent or future step must raise."""
        data = {
            "steps": [
                {
                    "key": "step_a",
                    "kind": "code",
                    "title": "A",
                    "depends_on": ["nonexistent"],
                },
                {"key": "final_check", "kind": "review", "title": "Final", "depends_on": []},
            ],
        }
        with pytest.raises(ValueError, match="not a prior step"):
            LLMTaskDecomposer._validate_decomposition_output(data)

    def test_validate_decomposition_missing_final_check(self) -> None:
        """Last step must have key 'final_check'."""
        data = {
            "steps": [
                {"key": "step_a", "kind": "code", "title": "A", "depends_on": []},
                {"key": "step_b", "kind": "review", "title": "B", "depends_on": ["step_a"]},
            ],
        }
        with pytest.raises(ValueError, match="final_check"):
            LLMTaskDecomposer._validate_decomposition_output(data)

    def test_validate_decomposition_empty_steps(self) -> None:
        """Empty steps list must raise."""
        with pytest.raises(ValueError, match="non-empty list"):
            LLMTaskDecomposer._validate_decomposition_output({"steps": []})

    def test_validate_decomposition_invalid_kind(self) -> None:
        """Step with invalid kind must raise."""
        data = {
            "steps": [
                {"key": "step_a", "kind": "deploy", "title": "A", "depends_on": []},
                {"key": "final_check", "kind": "review", "title": "Final", "depends_on": []},
            ],
        }
        with pytest.raises(ValueError, match="invalid kind"):
            LLMTaskDecomposer._validate_decomposition_output(data)
