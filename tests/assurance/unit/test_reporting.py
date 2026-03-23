"""Unit tests for AssuranceReporter."""

from __future__ import annotations

import time

import pytest

from hermit.kernel.verification.assurance.models import (
    AttributionCase,
    ContractViolation,
    InvariantViolation,
    OracleSpec,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.reporting import AssuranceReporter
from tests.assurance.conftest import make_governed_trace


def _uid(prefix: str = "test") -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_invariant_violation(
    *,
    severity: str = "blocker",
    detected_at: float | None = None,
    violation_id: str | None = None,
) -> InvariantViolation:
    return InvariantViolation(
        violation_id=violation_id or _uid("iv"),
        invariant_id="state.task_transition_legality",
        severity=severity,
        event_id=_uid("evt"),
        task_id="task-test",
        evidence={"detail": "illegal transition"},
        detected_at=detected_at if detected_at is not None else time.time(),
    )


def _make_contract_violation(
    *,
    severity: str = "high",
    detected_at: float | None = None,
    violation_id: str | None = None,
    contract_id: str = "approval.gating",
) -> ContractViolation:
    return ContractViolation(
        violation_id=violation_id or _uid("cv"),
        contract_id=contract_id,
        severity=severity,
        mode="runtime",
        task_id="task-test",
        evidence={"ref": _uid("evidence")},
        detected_at=detected_at if detected_at is not None else time.time(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def reporter() -> AssuranceReporter:
    return AssuranceReporter()


@pytest.fixture()
def envelopes() -> list[TraceEnvelope]:
    return make_governed_trace(num_steps=2)


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    """Tests for AssuranceReporter.build_report."""

    def test_pass_with_no_violations(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-1",
            scenario_id="scn-1",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        assert report.status == "pass"
        assert report.verdict == "clean"
        assert report.first_violation is None
        assert len(report.violations) == 0

    def test_fail_with_blocker_invariant_violation(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        iv = _make_invariant_violation(severity="blocker")
        report = reporter.build_report(
            run_id="run-2",
            scenario_id="scn-2",
            invariant_violations=[iv],
            contract_violations=[],
            envelopes=envelopes,
        )
        assert report.status == "fail"
        assert report.first_violation is iv
        assert len(report.violations) == 1

    def test_fail_with_high_contract_violation(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high")
        report = reporter.build_report(
            run_id="run-3",
            scenario_id="scn-3",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        assert report.status == "fail"
        assert report.first_violation is cv

    def test_pass_with_low_severity_violations(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="low")
        iv = _make_invariant_violation(severity="info")
        report = reporter.build_report(
            run_id="run-4",
            scenario_id="scn-4",
            invariant_violations=[iv],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        # low/info are not blocking
        assert report.status == "pass"
        assert report.verdict == "clean"
        # first_violation still populated (the earliest)
        assert report.first_violation is not None

    def test_timelines_populated(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-5",
            scenario_id="scn-5",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        assert "start" in report.timelines
        assert "end" in report.timelines
        assert report.timelines["event_count"] == len(envelopes)

    def test_empty_envelopes_empty_timelines(self, reporter: AssuranceReporter) -> None:
        report = reporter.build_report(
            run_id="run-6",
            scenario_id="scn-6",
            invariant_violations=[],
            contract_violations=[],
            envelopes=[],
        )
        assert report.timelines == {}

    def test_attribution_stored(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        attr = AttributionCase(
            case_id="attr-1",
            failure_signature="state_corruption",
            selected_root_cause="tool_failure",
            confidence=0.85,
        )
        report = reporter.build_report(
            run_id="run-7",
            scenario_id="scn-7",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            attribution=attr,
        )
        assert report.attribution is attr


# ---------------------------------------------------------------------------
# Oracle checking
# ---------------------------------------------------------------------------


class TestOracleChecking:
    """Tests for oracle-driven pass/fail determination."""

    def test_oracle_must_pass_contracts_fail(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        oracle = OracleSpec(must_pass_contracts=["approval.gating"])
        cv = _make_contract_violation(severity="low", contract_id="approval.gating")
        report = reporter.build_report(
            run_id="run-o1",
            scenario_id="scn-o1",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
            oracle=oracle,
        )
        # Even though severity is low, oracle says this contract must pass
        assert report.status == "fail"

    def test_oracle_max_unresolved_violations(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        oracle = OracleSpec(max_unresolved_violations=0)
        cv = _make_contract_violation(severity="low")
        report = reporter.build_report(
            run_id="run-o2",
            scenario_id="scn-o2",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
            oracle=oracle,
        )
        assert report.status == "fail"

    def test_oracle_no_criteria_passes(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        oracle = OracleSpec()
        report = reporter.build_report(
            run_id="run-o3",
            scenario_id="scn-o3",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            oracle=oracle,
        )
        assert report.status == "pass"

    def test_no_oracle_passes(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-o4",
            scenario_id="scn-o4",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            oracle=None,
        )
        assert report.status == "pass"


# ---------------------------------------------------------------------------
# First violation ordering
# ---------------------------------------------------------------------------


class TestFirstViolation:
    """Tests for first_violation selection by detected_at."""

    def test_earliest_invariant_selected(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        now = time.time()
        early = _make_invariant_violation(severity="blocker", detected_at=now - 10)
        late = _make_contract_violation(severity="high", detected_at=now - 1)
        report = reporter.build_report(
            run_id="run-fv1",
            scenario_id="scn-fv1",
            invariant_violations=[early],
            contract_violations=[late],
            envelopes=envelopes,
        )
        assert report.first_violation is early

    def test_earliest_contract_selected(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        now = time.time()
        late_inv = _make_invariant_violation(severity="blocker", detected_at=now - 1)
        early_cv = _make_contract_violation(severity="high", detected_at=now - 20)
        report = reporter.build_report(
            run_id="run-fv2",
            scenario_id="scn-fv2",
            invariant_violations=[late_inv],
            contract_violations=[early_cv],
            envelopes=envelopes,
        )
        assert report.first_violation is early_cv

    def test_single_violation_is_first(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high")
        report = reporter.build_report(
            run_id="run-fv3",
            scenario_id="scn-fv3",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        assert report.first_violation is cv


# ---------------------------------------------------------------------------
# emit_json
# ---------------------------------------------------------------------------

_EXPECTED_JSON_KEYS = frozenset(
    {
        "report_id",
        "scenario_id",
        "run_id",
        "status",
        "verdict",
        "first_violation",
        "timelines",
        "violations",
        "attribution",
        "fault_impact_graph",
        "recovery",
        "duplicates",
        "stuck_orphans",
        "side_effect_audit",
        "approval_bottlenecks",
        "adversarial",
        "regression_comparison",
        "replay_diff",
        "evidence_refs",
        "created_at",
    }
)


class TestEmitJson:
    """Tests for emit_json output."""

    def test_all_top_level_keys_present(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-j1",
            scenario_id="scn-j1",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        result = reporter.emit_json(report)
        assert set(result.keys()) == _EXPECTED_JSON_KEYS

    def test_json_pass_status(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-j2",
            scenario_id="scn-j2",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        result = reporter.emit_json(report)
        assert result["status"] == "pass"
        assert result["first_violation"] is None
        assert result["violations"] == []

    def test_json_fail_with_violation(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high")
        report = reporter.build_report(
            run_id="run-j3",
            scenario_id="scn-j3",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        result = reporter.emit_json(report)
        assert result["status"] == "fail"
        assert result["first_violation"] is not None
        assert result["first_violation"]["violation_id"] == cv.violation_id
        assert len(result["violations"]) == 1

    def test_json_attribution_included(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        attr = AttributionCase(case_id="attr-j1", confidence=0.9)
        report = reporter.build_report(
            run_id="run-j4",
            scenario_id="scn-j4",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            attribution=attr,
        )
        result = reporter.emit_json(report)
        assert result["attribution"]["case_id"] == "attr-j1"

    def test_json_no_attribution(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-j5",
            scenario_id="scn-j5",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        result = reporter.emit_json(report)
        assert result["attribution"] is None


# ---------------------------------------------------------------------------
# emit_markdown
# ---------------------------------------------------------------------------

_EXPECTED_MD_SECTIONS = [
    "# Assurance Report:",
    "## Executive Summary",
    "## Timeline",
    "## First Violation",
    "## Attribution",
    "## Recovery and Rollback",
    "## Side Effect Audit",
    "## Approval Bottlenecks",
    "## Adversarial Summary",
    "## Replay Diff",
    "## Evidence Appendix",
]


class TestEmitMarkdown:
    """Tests for emit_markdown output."""

    def test_all_sections_present(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-m1",
            scenario_id="scn-m1",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        md = reporter.emit_markdown(report)
        for section in _EXPECTED_MD_SECTIONS:
            assert section in md, f"Missing section: {section}"

    def test_pass_report_shows_clean(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-m2",
            scenario_id="scn-m2",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        md = reporter.emit_markdown(report)
        assert "clean" in md
        assert "No violations detected." in md

    def test_fail_report_shows_violation(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high", violation_id="cv-visible")
        report = reporter.build_report(
            run_id="run-m3",
            scenario_id="scn-m3",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        md = reporter.emit_markdown(report)
        assert "cv-visible" in md
        assert "fail" in md

    def test_attribution_section_populated(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        attr = AttributionCase(
            case_id="attr-md1",
            failure_signature="state_corruption",
            selected_root_cause="tool_failure",
            confidence=0.75,
            fix_hints=["retry with backoff"],
        )
        report = reporter.build_report(
            run_id="run-m4",
            scenario_id="scn-m4",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            attribution=attr,
        )
        md = reporter.emit_markdown(report)
        assert "attr-md1" in md
        assert "state_corruption" in md
        assert "retry with backoff" in md

    def test_evidence_appendix_lists_refs(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        report = reporter.build_report(
            run_id="run-m5",
            scenario_id="scn-m5",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        md = reporter.emit_markdown(report)
        # Evidence refs come from envelopes; governed trace has approval/grant/lease/receipt refs
        if report.evidence_refs:
            for ref in report.evidence_refs[:3]:
                assert ref in md


# ---------------------------------------------------------------------------
# compare_reports
# ---------------------------------------------------------------------------


class TestCompareReports:
    """Tests for compare_reports regression detection."""

    def test_new_violations_detected(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        baseline = reporter.build_report(
            run_id="run-c1a",
            scenario_id="scn-c1",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        cv = _make_contract_violation(severity="high", violation_id="new-cv-1")
        current = reporter.build_report(
            run_id="run-c1b",
            scenario_id="scn-c1",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        diff = reporter.compare_reports(baseline, current)
        assert "new-cv-1" in diff["new_violations"]
        assert diff["resolved_violations"] == []

    def test_resolved_violations_detected(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high", violation_id="old-cv-1")
        baseline = reporter.build_report(
            run_id="run-c2a",
            scenario_id="scn-c2",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        current = reporter.build_report(
            run_id="run-c2b",
            scenario_id="scn-c2",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        diff = reporter.compare_reports(baseline, current)
        assert "old-cv-1" in diff["resolved_violations"]
        assert diff["new_violations"] == []

    def test_changed_severity_detected(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv_old = _make_contract_violation(
            severity="high", violation_id="cv-sev", detected_at=100.0
        )
        baseline = reporter.build_report(
            run_id="run-c3a",
            scenario_id="scn-c3",
            invariant_violations=[],
            contract_violations=[cv_old],
            envelopes=envelopes,
        )
        cv_new = _make_contract_violation(
            severity="blocker", violation_id="cv-sev", detected_at=100.0
        )
        current = reporter.build_report(
            run_id="run-c3b",
            scenario_id="scn-c3",
            invariant_violations=[],
            contract_violations=[cv_new],
            envelopes=envelopes,
        )
        diff = reporter.compare_reports(baseline, current)
        assert len(diff["changed_severity"]) == 1
        assert diff["changed_severity"][0]["violation_id"] == "cv-sev"
        assert diff["changed_severity"][0]["old_severity"] == "high"
        assert diff["changed_severity"][0]["new_severity"] == "blocker"

    def test_no_changes_empty_diff(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        cv = _make_contract_violation(severity="high", violation_id="cv-stable")
        baseline = reporter.build_report(
            run_id="run-c4a",
            scenario_id="scn-c4",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        # Use the same violation object in current
        current = reporter.build_report(
            run_id="run-c4b",
            scenario_id="scn-c4",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )
        diff = reporter.compare_reports(baseline, current)
        assert diff["new_violations"] == []
        assert diff["resolved_violations"] == []
        assert diff["changed_severity"] == []

    def test_compare_includes_report_ids(
        self, reporter: AssuranceReporter, envelopes: list[TraceEnvelope]
    ) -> None:
        baseline = reporter.build_report(
            run_id="run-c5a",
            scenario_id="scn-c5",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        current = reporter.build_report(
            run_id="run-c5b",
            scenario_id="scn-c5",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
        )
        diff = reporter.compare_reports(baseline, current)
        assert diff["baseline_report_id"] == baseline.report_id
        assert diff["current_report_id"] == current.report_id
        assert diff["baseline_status"] == "pass"
        assert diff["current_status"] == "pass"
