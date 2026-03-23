"""Integration tests: report generation, formatting, and regression detection.

Validates the full reporting pipeline from trace -> report -> JSON/Markdown output,
plus cross-report regression comparisons.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

from hermit.kernel.verification.assurance.models import (
    AssuranceReport,
    AttributionCase,
    AttributionEdge,
    AttributionNode,
    ContractViolation,
    InvariantViolation,
    OracleSpec,
    ScenarioMetadata,
    ScenarioSpec,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.reporting import AssuranceReporter
from tests.assurance.conftest import make_envelope, make_governed_trace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _minimal_scenario(
    scenario_id: str = "report-pipeline-test",
    **kwargs: object,
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=scenario_id,
        metadata=ScenarioMetadata(name=scenario_id),
        **kwargs,
    )


def _make_contract_violation(
    *,
    contract_id: str = "approval.gating",
    severity: str = "high",
    task_id: str = "task-test",
    violation_id: str | None = None,
    evidence: dict | None = None,
    detected_at: float | None = None,
) -> ContractViolation:
    return ContractViolation(
        violation_id=violation_id or _uid("cv"),
        contract_id=contract_id,
        severity=severity,
        mode="runtime",
        task_id=task_id,
        evidence=evidence or {"reason": "test violation"},
        detected_at=detected_at or time.time(),
    )


def _make_invariant_violation(
    *,
    invariant_id: str = "governance.authority_chain_complete",
    severity: str = "blocker",
    task_id: str = "task-test",
    violation_id: str | None = None,
    evidence: dict | None = None,
    detected_at: float | None = None,
) -> InvariantViolation:
    return InvariantViolation(
        violation_id=violation_id or _uid("iv"),
        invariant_id=invariant_id,
        severity=severity,
        event_id=_uid("event"),
        task_id=task_id,
        evidence=evidence or {"reason": "test invariant violation"},
        detected_at=detected_at or time.time(),
    )


def _make_attribution() -> AttributionCase:
    node_root = AttributionNode(
        node_id=_uid("node"),
        node_type="step_attempt",
        ref="attempt-0",
        role="root_cause",
    )
    node_victim = AttributionNode(
        node_id=_uid("node"),
        node_type="tool_call",
        ref="tool-0",
        role="victim",
    )
    edge = AttributionEdge(
        source=node_root.node_id,
        target=node_victim.node_id,
        edge_type="caused_by",
    )
    return AttributionCase(
        case_id=_uid("case"),
        failure_signature="missing_grant_before_tool_call",
        first_divergence="step-0",
        root_cause_candidates=[node_root.node_id],
        selected_root_cause=node_root.node_id,
        propagation_chain=[node_root.node_id, node_victim.node_id],
        confidence=0.92,
        evidence_refs=["ref-a", "ref-b"],
        fix_hints=["Add grant before tool execution"],
        nodes=[node_root, node_victim],
        edges=[edge],
    )


def _build_clean_report(
    reporter: AssuranceReporter,
    envelopes: list[TraceEnvelope],
    scenario_id: str = "clean-scenario",
) -> AssuranceReport:
    """Build a report from a clean trace with no violations."""
    return reporter.build_report(
        run_id=envelopes[0].run_id if envelopes else "run-clean",
        scenario_id=scenario_id,
        invariant_violations=[],
        contract_violations=[],
        envelopes=envelopes,
    )


def _build_failing_report(
    reporter: AssuranceReporter,
    envelopes: list[TraceEnvelope],
    *,
    contract_violations: list[ContractViolation] | None = None,
    invariant_violations: list[InvariantViolation] | None = None,
    attribution: AttributionCase | None = None,
    oracle: OracleSpec | None = None,
    scenario_id: str = "fail-scenario",
) -> AssuranceReport:
    """Build a report with violations."""
    cv = contract_violations or [_make_contract_violation()]
    iv = invariant_violations or []
    return reporter.build_report(
        run_id=envelopes[0].run_id if envelopes else "run-fail",
        scenario_id=scenario_id,
        invariant_violations=iv,
        contract_violations=cv,
        envelopes=envelopes,
        attribution=attribution,
        oracle=oracle,
    )


# ---------------------------------------------------------------------------
# JSON report tests
# ---------------------------------------------------------------------------

_JSON_REQUIRED_KEYS = frozenset(
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


class TestJsonReport:
    """JSON report format validation."""

    def test_all_required_keys_present(self) -> None:
        """All 20 top-level keys from spec must exist."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        report = _build_clean_report(reporter, envelopes)
        json_out = reporter.emit_json(report)

        assert set(json_out.keys()) == _JSON_REQUIRED_KEYS
        assert len(json_out) == 20

    def test_pass_report_structure(self) -> None:
        """Clean trace produces status=pass, verdict=clean, first_violation=None."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=2)
        report = _build_clean_report(reporter, envelopes)
        json_out = reporter.emit_json(report)

        assert json_out["status"] == "pass"
        assert json_out["verdict"] == "clean"
        assert json_out["first_violation"] is None
        assert json_out["violations"] == []

    def test_fail_report_structure(self) -> None:
        """Bad trace produces status=fail, verdict describes violation, first_violation set."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        cv = _make_contract_violation(
            violation_id="cv-specific",
            severity="high",
            contract_id="approval.gating",
        )
        report = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv],
        )
        json_out = reporter.emit_json(report)

        assert json_out["status"] == "fail"
        assert "cv-specific" in json_out["verdict"]
        assert json_out["first_violation"] is not None
        assert json_out["first_violation"]["violation_id"] == "cv-specific"

    def test_violations_serialized_correctly(self) -> None:
        """Each violation has contract_id/invariant_id, severity, evidence."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        cv = _make_contract_violation(
            contract_id="side_effect.authorization",
            severity="blocker",
            evidence={"grant_ref": "missing"},
        )
        iv = _make_invariant_violation(
            invariant_id="governance.receipt_for_mutation",
            severity="high",
            evidence={"receipt_ref": "absent"},
        )

        report = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv],
            invariant_violations=[iv],
        )
        json_out = reporter.emit_json(report)

        assert len(json_out["violations"]) == 2

        # Find each violation in the list by its known fields
        contract_v = next(
            v for v in json_out["violations"] if "contract_id" in v
        )
        invariant_v = next(
            v for v in json_out["violations"] if "invariant_id" in v
        )

        assert contract_v["contract_id"] == "side_effect.authorization"
        assert contract_v["severity"] == "blocker"
        assert contract_v["evidence"] == {"grant_ref": "missing"}

        assert invariant_v["invariant_id"] == "governance.receipt_for_mutation"
        assert invariant_v["severity"] == "high"
        assert invariant_v["evidence"] == {"receipt_ref": "absent"}

    def test_attribution_included_when_violations_exist(self) -> None:
        """attribution field populated with nodes/edges/root_cause."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        attribution = _make_attribution()

        report = _build_failing_report(
            reporter,
            envelopes,
            attribution=attribution,
        )
        json_out = reporter.emit_json(report)

        attr = json_out["attribution"]
        assert attr is not None
        assert attr["case_id"] == attribution.case_id
        assert attr["selected_root_cause"] == attribution.selected_root_cause
        assert attr["confidence"] == 0.92
        assert len(attr["nodes"]) == 2
        assert len(attr["edges"]) == 1
        assert attr["fix_hints"] == ["Add grant before tool execution"]


# ---------------------------------------------------------------------------
# Markdown report tests
# ---------------------------------------------------------------------------

_MARKDOWN_REQUIRED_SECTIONS = [
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


class TestMarkdownReport:
    """Markdown report formatting."""

    def test_all_sections_present(self) -> None:
        """Header, Executive Summary, Timeline, First Violation, Attribution,
        Recovery and Rollback, Side Effect Audit, Approval Bottlenecks,
        Adversarial Summary, Replay Diff, Evidence Appendix."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=2)
        attribution = _make_attribution()
        report = _build_failing_report(
            reporter,
            envelopes,
            attribution=attribution,
        )
        md = reporter.emit_markdown(report)

        for section_header in _MARKDOWN_REQUIRED_SECTIONS:
            assert section_header in md, f"Missing section: {section_header}"

    def test_first_violation_section_content(self) -> None:
        """Section shows contract_id, severity, evidence."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        cv = _make_contract_violation(
            violation_id="cv-md-check",
            contract_id="approval.gating.v1",
            severity="blocker",
        )
        report = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv],
        )
        md = reporter.emit_markdown(report)

        assert "cv-md-check" in md
        assert "blocker" in md
        assert "approval.gating.v1" in md

    def test_clean_report_says_clean(self) -> None:
        """Pass report says 'clean' or 'no violations'."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        report = _build_clean_report(reporter, envelopes)
        md = reporter.emit_markdown(report)

        # Verdict in executive summary should say "clean"
        assert "clean" in md.lower()
        # First Violation section should indicate no violations
        assert "No violations detected" in md

    def test_executive_summary_includes_counts(self) -> None:
        """Summary mentions violation count."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        cv1 = _make_contract_violation(violation_id="cv-count-1")
        cv2 = _make_contract_violation(violation_id="cv-count-2")
        report = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv1, cv2],
        )
        md = reporter.emit_markdown(report)

        # The executive summary should include "Total Violations: 2"
        assert "**Total Violations**: 2" in md


# ---------------------------------------------------------------------------
# Regression comparison tests
# ---------------------------------------------------------------------------


class TestRegressionComparison:
    """Compare two reports for regressions."""

    def test_new_violations_detected(self) -> None:
        """baseline has 0 violations, current has 2 -> new_violations=[2 items]."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        baseline = _build_clean_report(reporter, envelopes)

        cv1 = _make_contract_violation(violation_id="cv-new-1")
        cv2 = _make_contract_violation(violation_id="cv-new-2")
        current = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv1, cv2],
        )

        comparison = reporter.compare_reports(baseline, current)

        assert len(comparison["new_violations"]) == 2
        assert "cv-new-1" in comparison["new_violations"]
        assert "cv-new-2" in comparison["new_violations"]
        assert comparison["resolved_violations"] == []
        assert comparison["changed_severity"] == []
        assert comparison["baseline_status"] == "pass"
        assert comparison["current_status"] == "fail"

    def test_resolved_violations_detected(self) -> None:
        """baseline has 2, current has 0 -> resolved_violations=[2 items]."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        cv1 = _make_contract_violation(violation_id="cv-resolved-1")
        cv2 = _make_contract_violation(violation_id="cv-resolved-2")
        baseline = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv1, cv2],
        )

        current = _build_clean_report(reporter, envelopes)

        comparison = reporter.compare_reports(baseline, current)

        assert len(comparison["resolved_violations"]) == 2
        assert "cv-resolved-1" in comparison["resolved_violations"]
        assert "cv-resolved-2" in comparison["resolved_violations"]
        assert comparison["new_violations"] == []
        assert comparison["baseline_status"] == "fail"
        assert comparison["current_status"] == "pass"

    def test_no_changes_detected(self) -> None:
        """Same violations -> empty new/resolved lists."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        shared_violations = [
            _make_contract_violation(violation_id="cv-same-1", severity="high"),
            _make_contract_violation(violation_id="cv-same-2", severity="high"),
        ]

        baseline = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=shared_violations,
        )
        current = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=shared_violations,
        )

        comparison = reporter.compare_reports(baseline, current)

        assert comparison["new_violations"] == []
        assert comparison["resolved_violations"] == []
        assert comparison["changed_severity"] == []

    def test_severity_change_detected(self) -> None:
        """Same contract_id but severity changed."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        baseline_cv = _make_contract_violation(
            violation_id="cv-sev-change",
            severity="high",
        )
        current_cv = _make_contract_violation(
            violation_id="cv-sev-change",
            severity="blocker",
        )

        baseline = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[baseline_cv],
        )
        current = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[current_cv],
        )

        comparison = reporter.compare_reports(baseline, current)

        assert comparison["new_violations"] == []
        assert comparison["resolved_violations"] == []
        assert len(comparison["changed_severity"]) == 1
        change = comparison["changed_severity"][0]
        assert change["violation_id"] == "cv-sev-change"
        assert change["old_severity"] == "high"
        assert change["new_severity"] == "blocker"


# ---------------------------------------------------------------------------
# Oracle validation tests
# ---------------------------------------------------------------------------


class TestOracleValidation:
    """Oracle acceptance criteria checking."""

    def test_must_pass_contracts_all_pass(self) -> None:
        """When must_pass_contracts all pass, report status is pass."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        oracle = OracleSpec(
            final_state="completed",
            must_pass_contracts=["approval.gating", "side_effect.authorization"],
            max_unresolved_violations=0,
        )

        # No violations -> all must_pass_contracts are clean
        report = reporter.build_report(
            run_id="run-oracle-pass",
            scenario_id="oracle-all-pass",
            invariant_violations=[],
            contract_violations=[],
            envelopes=envelopes,
            oracle=oracle,
        )

        assert report.status == "pass"
        assert report.verdict == "clean"

    def test_must_pass_contracts_one_fails(self) -> None:
        """When a must_pass contract is violated, report status is fail."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)

        oracle = OracleSpec(
            final_state="completed",
            must_pass_contracts=["approval.gating"],
            max_unresolved_violations=10,  # high limit, but must_pass overrides
        )

        cv = _make_contract_violation(
            contract_id="approval.gating",
            severity="medium",  # low severity, but it's a must_pass contract
        )

        report = reporter.build_report(
            run_id="run-oracle-fail",
            scenario_id="oracle-must-pass-fail",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
            oracle=oracle,
        )

        # Must fail because a must_pass contract has a violation, even though
        # severity is medium (not blocking) and max_unresolved is high
        assert report.status == "fail"

    def test_allowed_failures_excluded(self) -> None:
        """Violations against allowed_failures contracts should not cause
        oracle failure when using the lab's check_oracle method."""
        from hermit.kernel.verification.assurance.lab import AssuranceLab

        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        lab = AssuranceLab()

        # Use final_state="failed" because the report will have status="fail"
        # (the violation has "high" severity which is blocking).
        # The key check here is that the allowed_failures filter prevents
        # the violation from counting against max_unresolved_violations.
        oracle = OracleSpec(
            final_state="failed",
            must_pass_contracts=[],
            allowed_failures=["flaky.contract"],
            max_unresolved_violations=0,  # strict: zero tolerance
        )

        cv = _make_contract_violation(
            contract_id="flaky.contract",
            severity="high",
        )

        report = reporter.build_report(
            run_id="run-allowed",
            scenario_id="oracle-allowed-failures",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )

        # Lab's check_oracle filters allowed_failures from unresolved count.
        # Without allowed_failures, this high-severity violation would exceed
        # max_unresolved_violations=0 and fail.
        result = lab.check_oracle(report, oracle)
        assert result is True

        # Verify that without the allowed_failures entry, it would fail
        oracle_strict = OracleSpec(
            final_state="failed",
            must_pass_contracts=[],
            allowed_failures=[],
            max_unresolved_violations=0,
        )
        result_strict = lab.check_oracle(report, oracle_strict)
        assert result_strict is False

    def test_max_duplicate_side_effects_enforced(self) -> None:
        """Exceeding max_duplicate_side_effects causes oracle failure."""
        from hermit.kernel.verification.assurance.lab import AssuranceLab

        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        lab = AssuranceLab()

        oracle = OracleSpec(
            final_state="completed",
            max_duplicate_side_effects=1,
        )

        # Build a clean report but inject duplicate side effect data
        report = _build_clean_report(reporter, envelopes, scenario_id="dup-test")
        report_with_dups = replace(report, duplicates={"count": 3})

        result = lab.check_oracle(report_with_dups, oracle)
        assert result is False

        # Below limit should pass
        report_within_limit = replace(report, duplicates={"count": 1})
        assert lab.check_oracle(report_within_limit, oracle) is True

    def test_max_unresolved_violations_enforced(self) -> None:
        """Exceeding max_unresolved_violations causes oracle failure."""
        from hermit.kernel.verification.assurance.lab import AssuranceLab

        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=1)
        lab = AssuranceLab()

        # Use final_state="failed" because reports with high-severity
        # violations will have status="fail".
        oracle = OracleSpec(
            final_state="failed",
            max_unresolved_violations=1,
        )

        cv1 = _make_contract_violation(
            violation_id="cv-unresolved-1",
            severity="high",
        )
        cv2 = _make_contract_violation(
            violation_id="cv-unresolved-2",
            severity="high",
        )

        report = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv1, cv2],
        )

        # 2 high-severity violations exceed the limit of 1
        result = lab.check_oracle(report, oracle)
        assert result is False

        # With only 1 violation, should pass (1 <= max of 1)
        report_one = _build_failing_report(
            reporter,
            envelopes,
            contract_violations=[cv1],
        )
        result_one = lab.check_oracle(report_one, oracle)
        assert result_one is True


# ---------------------------------------------------------------------------
# Multi-step report tests
# ---------------------------------------------------------------------------


class TestMultiStepReport:
    """Reports for traces with multiple steps."""

    def test_five_step_clean_trace(self) -> None:
        """5-step governed trace -> all pass."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=5)
        report = _build_clean_report(reporter, envelopes, scenario_id="five-step")
        json_out = reporter.emit_json(report)

        assert json_out["status"] == "pass"
        assert json_out["verdict"] == "clean"
        assert json_out["violations"] == []
        assert json_out["first_violation"] is None

        # Timeline should cover all envelopes
        # 5 steps * 4 events each + 1 task.created + 1 task.completed = 22
        assert json_out["timelines"]["event_count"] == 22
        assert json_out["timelines"]["start"] <= json_out["timelines"]["end"]

    def test_violation_in_step_3_only(self) -> None:
        """Steps 1-2 clean, step 3 missing grant, steps 4-5 clean.
        Report should identify step 3 violation specifically."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=5)

        # The step-2 tool_call.start envelope is the one that would be missing
        # a grant in step 3 (0-indexed). Find the tool_call.start for step-2.
        step_2_tool_call = next(
            env
            for env in envelopes
            if env.event_type == "tool_call.start" and env.step_id == "step-2"
        )

        # Create a contract violation specific to step 3 (step_id="step-2" in 0-index)
        cv = _make_contract_violation(
            violation_id="cv-step-3-grant",
            contract_id="side_effect.authorization",
            severity="blocker",
            task_id=step_2_tool_call.task_id,
            evidence={
                "step_id": "step-2",
                "step_attempt_id": step_2_tool_call.step_attempt_id,
                "reason": "tool_call.start without grant_ref",
            },
        )

        report = reporter.build_report(
            run_id=envelopes[0].run_id,
            scenario_id="step-3-violation",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )

        assert report.status == "fail"
        assert report.first_violation is not None
        assert report.first_violation.violation_id == "cv-step-3-grant"
        assert len(report.violations) == 1

        # Verify the violation is associated with step-2 via evidence
        assert isinstance(report.first_violation, ContractViolation)
        assert report.first_violation.evidence["step_id"] == "step-2"
        assert report.first_violation.contract_id == "side_effect.authorization"

        # JSON output should have the violation correctly serialised
        json_out = reporter.emit_json(report)
        fv = json_out["first_violation"]
        assert fv["contract_id"] == "side_effect.authorization"
        assert fv["evidence"]["step_id"] == "step-2"

        # Markdown should mention the violation
        md = reporter.emit_markdown(report)
        assert "cv-step-3-grant" in md
        assert "side_effect.authorization" in md

    def test_evidence_refs_populated(self) -> None:
        """Evidence refs should contain relevant artifact/approval/receipt refs."""
        reporter = AssuranceReporter()
        envelopes = make_governed_trace(num_steps=3)

        # Add artifact refs to a specific envelope to verify they appear
        artifact_ref = _uid("artifact")
        envelopes[0] = replace(
            envelopes[0],
            artifact_refs=[artifact_ref],
        )

        # Collect expected refs from envelopes
        expected_refs: set[str] = set()
        for env in envelopes:
            expected_refs.update(env.artifact_refs)
            for attr in ("approval_ref", "decision_ref", "grant_ref", "lease_ref", "receipt_ref"):
                val = getattr(env, attr, None)
                if val is not None:
                    expected_refs.add(val)

        # Add a violation with evidence to verify its refs also appear
        violation_evidence_ref = _uid("evidence")
        cv = _make_contract_violation(
            evidence={"grant_ref": violation_evidence_ref},
            severity="medium",  # non-blocking so status can still be pass
        )

        report = reporter.build_report(
            run_id=envelopes[0].run_id,
            scenario_id="evidence-refs-test",
            invariant_violations=[],
            contract_violations=[cv],
            envelopes=envelopes,
        )

        # All envelope refs should be present
        for ref in expected_refs:
            assert ref in report.evidence_refs, f"Missing ref from envelopes: {ref}"

        # Violation evidence ref should also be present
        assert violation_evidence_ref in report.evidence_refs

        # The custom artifact ref should be present
        assert artifact_ref in report.evidence_refs

        # Evidence refs should be sorted
        assert report.evidence_refs == sorted(report.evidence_refs)

        # JSON output should match
        json_out = reporter.emit_json(report)
        assert json_out["evidence_refs"] == report.evidence_refs

        # Markdown evidence appendix should list each ref
        md = reporter.emit_markdown(report)
        assert artifact_ref in md
        assert violation_evidence_ref in md
