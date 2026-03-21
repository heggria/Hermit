"""Tests for SpecGenerator — research-aware spec generation."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
from hermit.plugins.builtin.hooks.decompose.spec_generator import (
    SpecGenerator,
    _derive_constraints_from_research,
    _extract_constraints,
    _extract_file_plan_from_goal,
    _extract_file_plan_from_research,
    _generate_acceptance_criteria,
    _make_spec_id,
)
from hermit.plugins.builtin.hooks.research.models import ResearchFinding, ResearchReport


class TestMakeSpecId:
    def test_produces_kebab_slug_with_hash(self) -> None:
        result = _make_spec_id("Add user authentication")
        assert "add-user-authentication" in result

    def test_deterministic(self) -> None:
        assert _make_spec_id("foo") == _make_spec_id("foo")

    def test_different_goals_different_ids(self) -> None:
        assert _make_spec_id("foo") != _make_spec_id("bar")


class TestExtractFilePlanFromGoal:
    def test_extracts_create_pattern(self) -> None:
        result = _extract_file_plan_from_goal("create src/hermit/foo.py for the new module")
        assert len(result) == 1
        assert result[0]["path"] == "src/hermit/foo.py"
        assert result[0]["action"] == "create"

    def test_no_patterns_returns_empty(self) -> None:
        assert _extract_file_plan_from_goal("implement the feature") == []

    def test_normalizes_action_verbs(self) -> None:
        result = _extract_file_plan_from_goal("fix src/bug.py and add src/new.py")
        actions = {e["action"] for e in result}
        assert "modify" in actions and "create" in actions


class TestExtractFilePlanFromResearch:
    def test_extracts_from_findings(self) -> None:
        findings = (
            ResearchFinding(
                source="codebase",
                title="f",
                content="x",
                relevance=0.25,
                file_path="src/hermit/kernel/foo.py",
            ),
        )
        assert len(_extract_file_plan_from_research(findings)) == 1

    def test_skips_low_relevance(self) -> None:
        findings = (
            ResearchFinding(
                source="codebase",
                title="f",
                content="",
                relevance=0.05,
                file_path="src/hermit/unrelated.py",
            ),
        )
        assert len(_extract_file_plan_from_research(findings)) == 0

    def test_deduplicates_paths(self) -> None:
        findings = (
            ResearchFinding(
                source="codebase", title="a", content="", relevance=0.3, file_path="src/a.py"
            ),
            ResearchFinding(
                source="codebase", title="b", content="", relevance=0.2, file_path="src/a.py"
            ),
        )
        assert len(_extract_file_plan_from_research(findings)) == 1


class TestExtractConstraints:
    def test_extracts_must_not(self) -> None:
        result = _extract_constraints("Do the thing\n- Must not break existing API")
        assert len(result) == 1

    def test_no_constraints_returns_empty(self) -> None:
        assert _extract_constraints("Just build the feature") == []


class TestDeriveConstraintsFromResearch:
    def test_detects_existing_tests(self) -> None:
        findings = (
            ResearchFinding(
                source="codebase",
                title="t",
                content="def test_foo():",
                relevance=0.3,
                file_path="tests/unit/test_foo.py",
            ),
        )
        assert any("Existing tests" in c for c in _derive_constraints_from_research(findings))

    def test_detects_existing_api(self) -> None:
        findings = (
            ResearchFinding(
                source="codebase",
                title="m",
                content="def public_method(self):\n    pass",
                relevance=0.3,
                file_path="src/hermit/module.py",
            ),
        )
        assert any(
            "backward compatibility" in c for c in _derive_constraints_from_research(findings)
        )


class TestGenerateAcceptanceCriteria:
    def test_always_includes_make_check(self) -> None:
        assert any(
            "make check" in c for c in _generate_acceptance_criteria("do something", None, ())
        )

    def test_fix_adds_regression_test(self) -> None:
        assert any(
            "regression" in c.lower()
            for c in _generate_acceptance_criteria("fix the login bug", None, ())
        )

    def test_implement_adds_unit_tests(self) -> None:
        assert any(
            "unit tests" in c.lower()
            for c in _generate_acceptance_criteria("implement new feature", None, ())
        )

    def test_research_approach_included(self) -> None:
        report = ResearchReport(goal="test", suggested_approach="Use LRU caching")
        assert any(
            "LRU caching" in c for c in _generate_acceptance_criteria("optimize", report, ())
        )


class TestSpecGenerator:
    def test_generate_basic(self) -> None:
        spec = SpecGenerator().generate(goal="Add logging to the system")
        assert isinstance(spec, GeneratedSpec)
        assert spec.spec_id and spec.title == "Add logging to the system"
        assert len(spec.acceptance_criteria) >= 1

    def test_generate_with_research_report(self) -> None:
        report = ResearchReport(
            goal="research",
            findings=(
                ResearchFinding(
                    source="codebase",
                    title="m",
                    content="def setup(): pass",
                    relevance=0.3,
                    file_path="src/hermit/kernel/context/memory/retrieval.py",
                ),
            ),
            suggested_approach="Use structlog for logging",
        )
        spec = SpecGenerator().generate(goal="Add logging", research_report=report)
        assert spec.research_ref
        assert any("structlog" in c for c in spec.acceptance_criteria)
        assert len(spec.file_plan) >= 1

    def test_frozen_dataclass(self) -> None:
        spec = SpecGenerator().generate(goal="Test immutability")
        try:
            spec.title = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass
