"""Review council arbiter — synthesizes multiple reviewer findings into a single verdict.

CouncilArbiter aggregates findings produced by parallel LLM reviewers
(each examining code from a different perspective such as correctness,
security, or maintainability) and consolidates them into a single
accept / revise / reject verdict.

The arbitration algorithm works as follows:

1. Each finding carries a severity level (critical, high, medium, low, info).
2. Severities are mapped to numeric weights via ``_SEVERITY_SCORES``.
3. The weighted sum is compared against ``_REVISE_THRESHOLD`` and
   ``_REJECT_THRESHOLD`` to determine the overall verdict.
4. When the verdict is *revise*, concrete ``RevisionDirective`` objects are
   produced so downstream automation knows exactly what to fix.

Typical usage::

    arbiter = CouncilArbiter()
    verdict = arbiter.arbitrate(findings)

See Also:
    ``hermit.plugins.builtin.hooks.quality.models``: Data models used by
    the arbiter (CouncilVerdict, ReviewerFinding, RevisionDirective).
"""

from __future__ import annotations

import time

import structlog

from hermit.plugins.builtin.hooks.quality.models import (
    CouncilVerdict,
    ReviewerFinding,
    ReviewPerspective,
    RevisionDirective,
)

log = structlog.get_logger()

# Thresholds
_REJECT_THRESHOLD = 3.0  # weighted score above this -> reject
_REVISE_THRESHOLD = 1.0  # weighted score above this -> revise

# Severity scores for weighted calculation
_SEVERITY_SCORES: dict[str, float] = {
    "critical": 1.0,
    "high": 0.7,
    "medium": 0.3,
    "low": 0.1,
    "info": 0.0,
}

# Consensus boost when multiple reviewers flag the same file
_CONSENSUS_BOOST = 1.5


class CouncilArbiter:
    """Synthesizes review findings into a verdict.

    Unlike DeliberationService.arbitrate() which selects a winner from
    competing proposals, the arbiter aggregates multiple review perspectives
    into a single accept/revise/reject decision.
    """

    def synthesize(
        self,
        council_id: str,
        findings_by_role: dict[str, list[ReviewerFinding]],
        perspectives: tuple[ReviewPerspective, ...],
        lint_passed: bool,
        *,
        revision_cycle: int = 0,
        max_revision_cycles: int = 3,
    ) -> CouncilVerdict:
        """Synthesize findings from multiple reviewers into a single verdict.

        Args:
            council_id: Unique identifier for this council session.
            findings_by_role: Mapping of reviewer role -> list of findings.
            perspectives: The reviewer perspectives used in this council.
            lint_passed: Whether the lint check passed.
            revision_cycle: Current revision cycle (0 = first review).
            max_revision_cycles: Maximum allowed revision cycles.

        Returns:
            A CouncilVerdict with the synthesized decision and metrics.
        """
        start = time.monotonic()

        # Build weight lookup from perspectives
        weight_by_role: dict[str, float] = {p.role: p.severity_weight for p in perspectives}
        required_roles: set[str] = {p.role for p in perspectives if p.required}

        # 1. Flatten all findings into one list
        all_findings: list[ReviewerFinding] = []
        for role_findings in findings_by_role.values():
            all_findings.extend(role_findings)

        # 2. Compute weighted score
        weighted_score = 0.0
        for finding in all_findings:
            base_score = _SEVERITY_SCORES.get(finding.severity, 0.0)
            role_weight = weight_by_role.get(finding.reviewer_role, 1.0)
            weighted_score += base_score * role_weight

        # 3. Count findings by severity
        critical_count = sum(1 for f in all_findings if f.severity == "critical")
        high_count = sum(1 for f in all_findings if f.severity == "high")
        medium_count = sum(1 for f in all_findings if f.severity == "medium")

        # 4. Compute consensus: if 2+ reviewers flag the same file, boost severity
        file_flagged_by: dict[str, set[str]] = {}
        for finding in all_findings:
            if finding.file_path:
                flagged_roles = file_flagged_by.setdefault(finding.file_path, set())
                flagged_roles.add(finding.reviewer_role)

        consensus_files: set[str] = set()
        for file_path, roles in file_flagged_by.items():
            if len(roles) >= 2:
                consensus_files.add(file_path)

        # Apply consensus boost to weighted score for consensus files
        for finding in all_findings:
            if finding.file_path in consensus_files:
                base_score = _SEVERITY_SCORES.get(finding.severity, 0.0)
                role_weight = weight_by_role.get(finding.reviewer_role, 1.0)
                boost = base_score * role_weight * (_CONSENSUS_BOOST - 1.0)
                weighted_score += boost

        # Consensus score: fraction of flagged files with multi-reviewer agreement
        total_flagged_files = len(file_flagged_by)
        consensus_score = (
            len(consensus_files) / total_flagged_files if total_flagged_files > 0 else 0.0
        )

        # 5. Decision rules
        has_required_critical = any(
            f.severity == "critical"
            for role in required_roles
            for f in findings_by_role.get(role, [])
        )
        lint_has_critical = not lint_passed and critical_count > 0

        if has_required_critical or weighted_score > _REJECT_THRESHOLD or lint_has_critical:
            verdict = "reject"
        elif (
            high_count > 0 or weighted_score > _REVISE_THRESHOLD
        ) and revision_cycle < max_revision_cycles:
            verdict = "revise"
        else:
            verdict = "accept"

        # 6. Build revision directive narrative if verdict == "revise"
        revision_directive = ""
        if verdict == "revise":
            actionable = [f for f in all_findings if f.severity in ("critical", "high", "medium")]
            lines: list[str] = []
            lines.append(
                f"Revision cycle {revision_cycle + 1}/{max_revision_cycles} — "
                f"{len(actionable)} issue(s) to address:"
            )
            for finding in actionable:
                location = finding.file_path
                if finding.line_start:
                    location = f"{location}:{finding.line_start}"
                fix_hint = f" | fix: {finding.suggested_fix}" if finding.suggested_fix else ""
                lines.append(
                    f"  [{finding.severity.upper()}] {location} — {finding.message}{fix_hint}"
                )
            revision_directive = "\n".join(lines)

        duration = time.monotonic() - start

        council_verdict = CouncilVerdict(
            verdict=verdict,
            council_id=council_id,
            reviewer_count=len(findings_by_role),
            finding_count=len(all_findings),
            critical_count=critical_count,
            high_count=high_count,
            findings=tuple(all_findings),
            lint_passed=lint_passed,
            consensus_score=round(consensus_score, 3),
            revision_directive=revision_directive,
            duration_seconds=round(duration, 6),
            decided_at=time.time(),
        )

        log.info(
            "council_arbiter.verdict",
            council_id=council_id,
            verdict=verdict,
            weighted_score=round(weighted_score, 3),
            critical=critical_count,
            high=high_count,
            medium=medium_count,
            consensus_files=len(consensus_files),
            revision_cycle=revision_cycle,
        )

        return council_verdict

    def build_revision_directive(
        self,
        spec_id: str,
        council_id: str,
        findings: list[ReviewerFinding],
        revision_cycle: int,
        max_cycles: int,
    ) -> RevisionDirective:
        """Build a structured RevisionDirective from findings.

        Filters to actionable findings (critical/high/medium), determines
        a priority order based on severity, and produces a human-readable
        narrative suitable for an LLM implementer.

        Args:
            spec_id: The spec being reviewed.
            council_id: The council session that produced these findings.
            findings: All findings from the council.
            revision_cycle: Current revision cycle number.
            max_cycles: Maximum allowed revision cycles.

        Returns:
            A RevisionDirective with prioritized findings and narrative.
        """
        actionable = [f for f in findings if f.severity in ("critical", "high", "medium")]

        # Priority order: critical files first, then high, then medium
        severity_rank = {"critical": 0, "high": 1, "medium": 2}
        file_max_severity: dict[str, int] = {}
        for finding in actionable:
            if finding.file_path:
                current = file_max_severity.get(finding.file_path, 999)
                rank = severity_rank.get(finding.severity, 999)
                file_max_severity[finding.file_path] = min(current, rank)

        priority_order = tuple(
            fp for fp, _ in sorted(file_max_severity.items(), key=lambda x: x[1])
        )

        # Build narrative
        lines: list[str] = []
        lines.append(
            f"Revision directive for spec {spec_id} (cycle {revision_cycle + 1}/{max_cycles}):"
        )
        lines.append("")

        if not actionable:
            lines.append("No actionable findings — consider accepting.")
        else:
            # Group by file for readability
            by_file: dict[str, list[ReviewerFinding]] = {}
            for finding in actionable:
                file_findings = by_file.setdefault(finding.file_path or "(unknown)", [])
                file_findings.append(finding)

            for file_path in priority_order:
                file_findings = by_file.get(file_path, [])
                lines.append(f"## {file_path}")
                for finding in file_findings:
                    loc = f"L{finding.line_start}" if finding.line_start else ""
                    if finding.line_end and finding.line_end != finding.line_start:
                        loc = f"L{finding.line_start}-{finding.line_end}"
                    reviewer = f"[{finding.reviewer_role}]"
                    lines.append(
                        f"  - {reviewer} {finding.severity.upper()} {loc}: {finding.message}"
                    )
                    if finding.suggested_fix:
                        lines.append(f"    Fix: {finding.suggested_fix}")
                lines.append("")

            # Include findings without a file path
            no_file = by_file.get("(unknown)", [])
            if no_file:
                lines.append("## General")
                for finding in no_file:
                    lines.append(
                        f"  - [{finding.reviewer_role}] {finding.severity.upper()}: "
                        f"{finding.message}"
                    )
                    if finding.suggested_fix:
                        lines.append(f"    Fix: {finding.suggested_fix}")
                lines.append("")

        narrative = "\n".join(lines)

        return RevisionDirective(
            spec_id=spec_id,
            council_id=council_id,
            revision_cycle=revision_cycle,
            findings_to_fix=tuple(actionable),
            priority_order=priority_order,
            max_revision_cycles=max_cycles,
            narrative=narrative,
        )
