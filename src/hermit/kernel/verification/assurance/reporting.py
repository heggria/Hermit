"""Assurance report builder and emitter.

Assembles AssuranceReport from violation lists, trace envelopes, attribution,
and oracle specs.  Emits canonical JSON dicts and human-readable Markdown.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog

from .models import (
    AssuranceReport,
    AttributionCase,
    ContractViolation,
    InvariantViolation,
    OracleSpec,
    TraceEnvelope,
    _id,
)

log = structlog.get_logger()

# Severity ordering — lower index means more severe.
_SEVERITY_RANK: dict[str, int] = {
    "blocker": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

_BLOCKING_SEVERITIES = frozenset({"blocker", "high"})


def _severity_key(sev: str) -> int:
    return _SEVERITY_RANK.get(sev, 99)


def _violation_detected_at(v: ContractViolation | InvariantViolation) -> float:
    return v.detected_at


def _violation_severity(v: ContractViolation | InvariantViolation) -> str:
    return v.severity


def _violation_id(v: ContractViolation | InvariantViolation) -> str:
    return v.violation_id


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class AssuranceReporter:
    """Builds, serialises, and compares assurance reports."""

    # -- build ---------------------------------------------------------------

    def build_report(
        self,
        *,
        run_id: str,
        scenario_id: str,
        invariant_violations: list[InvariantViolation],
        contract_violations: list[ContractViolation],
        envelopes: list[TraceEnvelope],
        attribution: AttributionCase | None = None,
        oracle: OracleSpec | None = None,
    ) -> AssuranceReport:
        """Assemble an :class:`AssuranceReport` from raw inputs.

        *status* is ``"pass"`` when no blocker/high violations exist **and**
        the oracle criteria are satisfied; ``"fail"`` otherwise.
        """
        all_violations: list[ContractViolation | InvariantViolation] = [
            *invariant_violations,
            *contract_violations,
        ]

        first_violation = self._find_first_violation(all_violations)
        has_blocking = any(v.severity in _BLOCKING_SEVERITIES for v in all_violations)
        oracle_pass = self._check_oracle(oracle, contract_violations, all_violations)
        status = "pass" if (not has_blocking and oracle_pass) else "fail"
        verdict = self._derive_verdict(status, all_violations)

        timelines = self._build_timelines(envelopes)
        evidence_refs = self._collect_evidence_refs(envelopes, all_violations)

        report = AssuranceReport(
            report_id=_id("rpt"),
            scenario_id=scenario_id,
            run_id=run_id,
            status=status,
            verdict=verdict,
            first_violation=first_violation,
            timelines=timelines,
            violations=all_violations,
            attribution=attribution,
            evidence_refs=evidence_refs,
        )
        log.info(
            "assurance_report_built",
            report_id=report.report_id,
            status=status,
            violation_count=len(all_violations),
        )
        return report

    # -- JSON ----------------------------------------------------------------

    def emit_json(self, report: AssuranceReport) -> dict[str, Any]:
        """Return a canonical JSON-serialisable dict."""
        first_v: dict[str, Any] | None = None
        if report.first_violation is not None:
            first_v = asdict(report.first_violation)

        violations_dicts: list[dict[str, Any]] = [asdict(v) for v in report.violations]
        attribution_dict: dict[str, Any] | None = (
            asdict(report.attribution) if report.attribution else None
        )

        return {
            "report_id": report.report_id,
            "scenario_id": report.scenario_id,
            "run_id": report.run_id,
            "status": report.status,
            "verdict": report.verdict,
            "first_violation": first_v,
            "timelines": report.timelines,
            "violations": violations_dicts,
            "attribution": attribution_dict,
            "fault_impact_graph": report.fault_impact_graph,
            "recovery": report.recovery,
            "duplicates": report.duplicates,
            "stuck_orphans": report.stuck_orphans,
            "side_effect_audit": report.side_effect_audit,
            "approval_bottlenecks": report.approval_bottlenecks,
            "adversarial": report.adversarial,
            "regression_comparison": report.regression_comparison,
            "replay_diff": report.replay_diff,
            "evidence_refs": report.evidence_refs,
            "created_at": report.created_at,
        }

    # -- Markdown ------------------------------------------------------------

    def emit_markdown(self, report: AssuranceReport) -> str:
        """Return a human-readable Markdown string with fixed sections."""
        lines: list[str] = []

        # Header
        lines.append(f"# Assurance Report: {report.scenario_id}")
        lines.append("")

        # Executive Summary
        lines.append("## Executive Summary")
        lines.append(f"- **Report ID**: {report.report_id}")
        lines.append(f"- **Run ID**: {report.run_id}")
        lines.append(f"- **Status**: {report.status}")
        lines.append(f"- **Verdict**: {report.verdict}")
        lines.append(f"- **Total Violations**: {len(report.violations)}")
        lines.append("")

        # Timeline
        lines.append("## Timeline")
        if report.timelines:
            start = report.timelines.get("start")
            end = report.timelines.get("end")
            count = report.timelines.get("event_count", 0)
            lines.append(f"- **Event count**: {count}")
            if start is not None:
                lines.append(f"- **Start**: {start}")
            if end is not None:
                lines.append(f"- **End**: {end}")
        else:
            lines.append("No timeline data.")
        lines.append("")

        # First Violation
        lines.append("## First Violation")
        if report.first_violation is not None:
            fv = report.first_violation
            lines.append(f"- **Violation ID**: {fv.violation_id}")
            lines.append(f"- **Severity**: {fv.severity}")
            lines.append(f"- **Detected at**: {fv.detected_at}")
            if isinstance(fv, ContractViolation):
                lines.append(f"- **Contract**: {fv.contract_id}")
            elif isinstance(fv, InvariantViolation):
                lines.append(f"- **Invariant**: {fv.invariant_id}")
        else:
            lines.append("No violations detected.")
        lines.append("")

        # Attribution
        lines.append("## Attribution")
        if report.attribution is not None:
            attr = report.attribution
            lines.append(f"- **Case ID**: {attr.case_id}")
            lines.append(f"- **Failure Signature**: {attr.failure_signature}")
            lines.append(f"- **Root Cause**: {attr.selected_root_cause}")
            lines.append(f"- **Confidence**: {attr.confidence}")
            if attr.fix_hints:
                lines.append(f"- **Fix Hints**: {', '.join(attr.fix_hints)}")
        else:
            lines.append("No attribution data.")
        lines.append("")

        # Recovery and Rollback
        lines.append("## Recovery and Rollback")
        if report.recovery:
            for key, val in report.recovery.items():
                lines.append(f"- **{key}**: {val}")
        else:
            lines.append("No recovery data.")
        lines.append("")

        # Side Effect Audit
        lines.append("## Side Effect Audit")
        if report.side_effect_audit:
            for key, val in report.side_effect_audit.items():
                lines.append(f"- **{key}**: {val}")
        else:
            lines.append("No side effects recorded.")
        lines.append("")

        # Approval Bottlenecks
        lines.append("## Approval Bottlenecks")
        if report.approval_bottlenecks:
            for key, val in report.approval_bottlenecks.items():
                lines.append(f"- **{key}**: {val}")
        else:
            lines.append("No approval bottlenecks detected.")
        lines.append("")

        # Adversarial Summary
        lines.append("## Adversarial Summary")
        if report.adversarial:
            for key, val in report.adversarial.items():
                lines.append(f"- **{key}**: {val}")
        else:
            lines.append("No adversarial perturbations applied.")
        lines.append("")

        # Replay Diff
        lines.append("## Replay Diff")
        if report.replay_diff:
            for key, val in report.replay_diff.items():
                lines.append(f"- **{key}**: {val}")
        else:
            lines.append("No replay diff available.")
        lines.append("")

        # Evidence Appendix
        lines.append("## Evidence Appendix")
        if report.evidence_refs:
            for ref in report.evidence_refs:
                lines.append(f"- `{ref}`")
        else:
            lines.append("No evidence references.")
        lines.append("")

        return "\n".join(lines)

    # -- compare -------------------------------------------------------------

    def compare_reports(
        self,
        baseline: AssuranceReport,
        current: AssuranceReport,
    ) -> dict[str, Any]:
        """Compare *current* against *baseline* for regression detection.

        Returns a dict with ``new_violations``, ``resolved_violations``,
        and ``changed_severity`` lists.
        """
        baseline_ids = {_violation_id(v) for v in baseline.violations}
        current_ids = {_violation_id(v) for v in current.violations}

        # Build severity maps keyed on violation_id.
        baseline_sev: dict[str, str] = {
            _violation_id(v): _violation_severity(v) for v in baseline.violations
        }
        current_sev: dict[str, str] = {
            _violation_id(v): _violation_severity(v) for v in current.violations
        }

        new_violations = sorted(current_ids - baseline_ids)
        resolved_violations = sorted(baseline_ids - current_ids)

        changed_severity: list[dict[str, str]] = []
        for vid in sorted(baseline_ids & current_ids):
            old_sev = baseline_sev[vid]
            new_sev = current_sev[vid]
            if old_sev != new_sev:
                changed_severity.append(
                    {"violation_id": vid, "old_severity": old_sev, "new_severity": new_sev}
                )

        result: dict[str, Any] = {
            "new_violations": new_violations,
            "resolved_violations": resolved_violations,
            "changed_severity": changed_severity,
            "baseline_report_id": baseline.report_id,
            "current_report_id": current.report_id,
            "baseline_status": baseline.status,
            "current_status": current.status,
        }
        log.info(
            "assurance_reports_compared",
            new=len(new_violations),
            resolved=len(resolved_violations),
            changed=len(changed_severity),
        )
        return result

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _find_first_violation(
        violations: list[ContractViolation | InvariantViolation],
    ) -> ContractViolation | InvariantViolation | None:
        """Return the violation with the earliest ``detected_at``."""
        if not violations:
            return None
        return min(violations, key=_violation_detected_at)

    @staticmethod
    def _check_oracle(
        oracle: OracleSpec | None,
        contract_violations: list[ContractViolation],
        all_violations: list[ContractViolation | InvariantViolation],
    ) -> bool:
        """Evaluate oracle criteria.  Returns ``True`` when no oracle is set."""
        if oracle is None:
            return True

        # must_pass_contracts: none of these contract IDs may appear violated.
        if oracle.must_pass_contracts:
            violated_contracts = {v.contract_id for v in contract_violations}
            for cid in oracle.must_pass_contracts:
                if cid in violated_contracts:
                    return False

        # max_unresolved_violations
        return len(all_violations) <= oracle.max_unresolved_violations

    @staticmethod
    def _derive_verdict(
        status: str,
        violations: list[ContractViolation | InvariantViolation],
    ) -> str:
        """Describe the most severe violation, or ``'clean'``."""
        if status == "pass":
            return "clean"
        if not violations:
            return "fail: oracle criteria not met"
        worst = min(violations, key=lambda v: _severity_key(v.severity))
        return f"{worst.severity}: {worst.violation_id}"

    @staticmethod
    def _build_timelines(envelopes: list[TraceEnvelope]) -> dict[str, Any]:
        """Build a lightweight timeline summary from envelopes."""
        if not envelopes:
            return {}
        wallclocks = [e.wallclock_at for e in envelopes]
        return {
            "start": min(wallclocks),
            "end": max(wallclocks),
            "event_count": len(envelopes),
        }

    @staticmethod
    def _collect_evidence_refs(
        envelopes: list[TraceEnvelope],
        violations: list[ContractViolation | InvariantViolation],
    ) -> list[str]:
        """Gather unique evidence refs from envelopes and violations."""
        refs: set[str] = set()
        for env in envelopes:
            refs.update(env.artifact_refs)
            for attr in ("approval_ref", "decision_ref", "grant_ref", "lease_ref", "receipt_ref"):
                val = getattr(env, attr, None)
                if val is not None:
                    refs.add(val)
        for v in violations:
            for val in v.evidence.values():
                if isinstance(val, str):
                    refs.add(val)
        return sorted(refs)
