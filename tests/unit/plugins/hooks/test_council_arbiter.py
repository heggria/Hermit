"""Tests for CouncilArbiter — review council verdict synthesis."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.quality.council_arbiter import CouncilArbiter
from hermit.plugins.builtin.hooks.quality.models import (
    CouncilVerdict,
    ReviewerFinding,
    ReviewPerspective,
    RevisionDirective,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PERSPECTIVES: tuple[ReviewPerspective, ...] = (
    ReviewPerspective(
        role="security",
        system_prompt_template="Review for security issues.",
        severity_weight=1.5,
        required=True,
        timeout_seconds=30.0,
    ),
    ReviewPerspective(
        role="logic",
        system_prompt_template="Review for logic issues.",
        severity_weight=1.0,
        required=False,
        timeout_seconds=30.0,
    ),
)


def _finding(
    role: str = "security",
    severity: str = "medium",
    file_path: str = "src/foo.py",
    message: str = "issue found",
    suggested_fix: str = "",
    line_start: int = 0,
) -> ReviewerFinding:
    return ReviewerFinding(
        reviewer_role=role,
        category="code",
        severity=severity,
        file_path=file_path,
        message=message,
        suggested_fix=suggested_fix,
        line_start=line_start,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCouncilArbiter:
    def test_accept_when_no_findings(self) -> None:
        """No findings from any reviewer -> accept."""
        arbiter = CouncilArbiter()
        verdict = arbiter.synthesize(
            council_id="c-001",
            findings_by_role={"security": [], "logic": []},
            perspectives=_PERSPECTIVES,
            lint_passed=True,
        )
        assert isinstance(verdict, CouncilVerdict)
        assert verdict.verdict == "accept"
        assert verdict.finding_count == 0
        assert verdict.critical_count == 0
        assert verdict.lint_passed is True

    def test_reject_on_critical_from_required_reviewer(self) -> None:
        """A critical finding from a required reviewer -> reject."""
        arbiter = CouncilArbiter()
        verdict = arbiter.synthesize(
            council_id="c-002",
            findings_by_role={
                "security": [_finding(role="security", severity="critical")],
                "logic": [],
            },
            perspectives=_PERSPECTIVES,
            lint_passed=True,
        )
        assert verdict.verdict == "reject"
        assert verdict.critical_count == 1

    def test_revise_on_high_findings(self) -> None:
        """High-severity findings (below reject threshold) -> revise."""
        arbiter = CouncilArbiter()
        verdict = arbiter.synthesize(
            council_id="c-003",
            findings_by_role={
                "security": [],
                "logic": [_finding(role="logic", severity="high")],
            },
            perspectives=_PERSPECTIVES,
            lint_passed=True,
            revision_cycle=0,
            max_revision_cycles=3,
        )
        assert verdict.verdict == "revise"
        assert verdict.high_count == 1
        assert verdict.revision_directive  # non-empty directive

    def test_revise_respects_max_revision_cycles(self) -> None:
        """At max revision cycles, high findings lead to accept (not revise)."""
        arbiter = CouncilArbiter()
        verdict = arbiter.synthesize(
            council_id="c-004",
            findings_by_role={
                "security": [],
                "logic": [_finding(role="logic", severity="high")],
            },
            perspectives=_PERSPECTIVES,
            lint_passed=True,
            revision_cycle=3,
            max_revision_cycles=3,
        )
        # Cannot revise when revision_cycle >= max_revision_cycles,
        # and weighted score of a single "high" from non-required reviewer
        # is 0.7 * 1.0 = 0.7, which is below _REJECT_THRESHOLD (3.0).
        # So the decision falls through to "accept".
        assert verdict.verdict == "accept"

    def test_consensus_boost(self) -> None:
        """Same file flagged by 2+ reviewers gets a consensus boost."""
        arbiter = CouncilArbiter()
        shared_file = "src/hermit/kernel/core.py"
        verdict = arbiter.synthesize(
            council_id="c-005",
            findings_by_role={
                "security": [_finding(role="security", severity="medium", file_path=shared_file)],
                "logic": [_finding(role="logic", severity="medium", file_path=shared_file)],
            },
            perspectives=_PERSPECTIVES,
            lint_passed=True,
            revision_cycle=0,
            max_revision_cycles=3,
        )
        assert verdict.consensus_score > 0.0
        # The consensus boost raises the weighted score enough to trigger revise.
        # base: security medium = 0.3 * 1.5 = 0.45
        #        logic medium  = 0.3 * 1.0 = 0.30   -> total = 0.75
        # consensus boost: each finding's base * weight * 0.5 added again
        #   = 0.45 * 0.5 + 0.30 * 0.5 = 0.225 + 0.15 = 0.375
        # grand total = 0.75 + 0.375 = 1.125  -> above _REVISE_THRESHOLD (1.0)
        assert verdict.verdict == "revise"

    def test_build_revision_directive_groups_by_file(self) -> None:
        """build_revision_directive groups findings by file in priority order."""
        arbiter = CouncilArbiter()
        findings = [
            _finding(
                role="security",
                severity="high",
                file_path="src/auth.py",
                message="SQL injection risk",
                suggested_fix="Use parameterized queries",
                line_start=42,
            ),
            _finding(
                role="logic",
                severity="medium",
                file_path="src/cache.py",
                message="Cache miss not handled",
                line_start=10,
            ),
            _finding(
                role="security",
                severity="critical",
                file_path="src/auth.py",
                message="Hardcoded secret",
                line_start=5,
            ),
        ]

        directive = arbiter.build_revision_directive(
            spec_id="spec-001",
            council_id="c-006",
            findings=findings,
            revision_cycle=0,
            max_cycles=3,
        )

        assert isinstance(directive, RevisionDirective)
        assert directive.spec_id == "spec-001"
        assert directive.council_id == "c-006"
        assert directive.revision_cycle == 0
        assert len(directive.findings_to_fix) == 3  # all are actionable
        # auth.py has critical -> should come first in priority_order
        assert directive.priority_order[0] == "src/auth.py"
        assert "src/cache.py" in directive.priority_order
        # Narrative should contain file headers
        assert "## src/auth.py" in directive.narrative
        assert "## src/cache.py" in directive.narrative
        assert "SQL injection risk" in directive.narrative
        assert "Fix: Use parameterized queries" in directive.narrative

    def test_build_revision_directive_no_actionable(self) -> None:
        """When all findings are low/info, findings_to_fix is empty."""
        arbiter = CouncilArbiter()
        findings = [
            _finding(role="logic", severity="low", file_path="src/a.py"),
            _finding(role="logic", severity="info", file_path="src/b.py"),
        ]
        directive = arbiter.build_revision_directive(
            spec_id="spec-002",
            council_id="c-007",
            findings=findings,
            revision_cycle=1,
            max_cycles=3,
        )
        assert len(directive.findings_to_fix) == 0
        assert "No actionable findings" in directive.narrative

    def test_reject_on_high_weighted_score(self) -> None:
        """Many medium findings from weighted reviewer can push score above reject threshold."""
        arbiter = CouncilArbiter()
        # 11 medium findings from security (weight 1.5): 11 * 0.3 * 1.5 = 4.95 > 3.0
        many_findings = [
            _finding(role="security", severity="medium", file_path=f"src/f{i}.py")
            for i in range(11)
        ]
        verdict = arbiter.synthesize(
            council_id="c-008",
            findings_by_role={"security": many_findings, "logic": []},
            perspectives=_PERSPECTIVES,
            lint_passed=True,
        )
        assert verdict.verdict == "reject"
