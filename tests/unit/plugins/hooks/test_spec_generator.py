"""Tests for SpecGenerator — template-based spec generation."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
from hermit.plugins.builtin.hooks.decompose.spec_generator import (
    SpecGenerator,
    _extract_constraints,
    _extract_file_plan,
    _make_spec_id,
)
from hermit.plugins.builtin.hooks.research.models import ResearchReport


class TestMakeSpecId:
    def test_produces_kebab_slug_with_hash(self) -> None:
        result = _make_spec_id("Add user authentication")
        assert "add-user-authentication" in result
        assert len(result.split("-")[-1]) == 6  # hash suffix

    def test_truncates_long_goals(self) -> None:
        long_goal = "a" * 200
        result = _make_spec_id(long_goal)
        # slug part is at most 40 chars + dash + 6 hash
        assert len(result) <= 47

    def test_deterministic(self) -> None:
        assert _make_spec_id("foo") == _make_spec_id("foo")

    def test_different_goals_different_ids(self) -> None:
        assert _make_spec_id("foo") != _make_spec_id("bar")


class TestExtractFilePlan:
    def test_extracts_create_pattern(self) -> None:
        goal = "create src/hermit/foo.py for the new module"
        result = _extract_file_plan(goal)
        assert len(result) == 1
        assert result[0]["path"] == "src/hermit/foo.py"
        assert result[0]["action"] == "create"

    def test_extracts_modify_pattern(self) -> None:
        goal = "modify src/bar.py to add feature"
        result = _extract_file_plan(goal)
        assert len(result) == 1
        assert result[0]["action"] == "modify"

    def test_extracts_multiple(self) -> None:
        goal = "create src/a.py and modify src/b.py"
        result = _extract_file_plan(goal)
        assert len(result) == 2

    def test_no_patterns_returns_empty(self) -> None:
        result = _extract_file_plan("implement the feature")
        assert result == ()


class TestExtractConstraints:
    def test_extracts_must_not(self) -> None:
        goal = "Do the thing\n- Must not break existing API"
        result = _extract_constraints(goal)
        assert len(result) == 1
        assert "Must not break" in result[0]

    def test_extracts_never(self) -> None:
        goal = "- Never delete production data"
        result = _extract_constraints(goal)
        assert len(result) == 1

    def test_no_constraints_returns_empty(self) -> None:
        result = _extract_constraints("Just build the feature")
        assert result == ()


class TestSpecGenerator:
    def test_generate_basic(self) -> None:
        gen = SpecGenerator()
        spec = gen.generate(goal="Add logging to the system")
        assert isinstance(spec, GeneratedSpec)
        assert spec.spec_id
        assert spec.title == "Add logging to the system"
        assert spec.goal == "Add logging to the system"
        assert spec.trust_zone == "normal"
        assert len(spec.acceptance_criteria) >= 2

    def test_generate_with_file_plan_in_goal(self) -> None:
        gen = SpecGenerator()
        spec = gen.generate(goal="create src/hermit/logger.py for structured logging")
        assert len(spec.file_plan) == 1
        assert spec.file_plan[0]["path"] == "src/hermit/logger.py"

    def test_generate_with_explicit_constraints(self) -> None:
        gen = SpecGenerator()
        spec = gen.generate(
            goal="Add feature",
            constraints=("No breaking changes",),
        )
        assert "No breaking changes" in spec.constraints

    def test_generate_with_research_report(self) -> None:
        gen = SpecGenerator()
        report = ResearchReport(
            goal="research",
            suggested_approach="Use structlog for logging",
        )
        spec = gen.generate(goal="Add logging", research_report=report)
        assert spec.research_ref
        assert any("structlog" in c for c in spec.acceptance_criteria)

    def test_frozen_dataclass(self) -> None:
        gen = SpecGenerator()
        spec = gen.generate(goal="Test immutability")
        try:
            spec.title = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass
