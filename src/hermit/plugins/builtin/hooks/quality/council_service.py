"""Review council service -- orchestrates parallel LLM reviews using worker pools."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.quality.models import (
    CouncilVerdict,
    FindingSeverity,
    ReviewerFinding,
    ReviewPerspective,
)

log = structlog.get_logger()

_MAX_FILE_LINES = 500  # Max lines per file sent to reviewer
_MAX_FILES = 20  # Max files per review session
_FUTURES_TIMEOUT_SECONDS = 120  # Global timeout for all reviewer futures
_DEFAULT_MAX_TOKENS = 4096


def _read_file_bounded(path: Path, max_lines: int = _MAX_FILE_LINES) -> str:
    """Read a file, truncating to *max_lines* lines.

    Returns an empty string if the file cannot be read.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    if len(lines) > max_lines:
        truncated = lines[:max_lines]
        truncated.append(f"\n... (truncated, {len(lines) - max_lines} lines omitted)")
        return "\n".join(truncated)
    return "\n".join(lines)


def _git_diff_for_files(workspace_root: str, files: list[str]) -> str:
    """Try to get git diff output for the listed files.

    Falls back to an empty string if git is unavailable or the workspace
    is not a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", *files],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _parse_reviewer_response(raw_text: str, reviewer_role: str) -> list[ReviewerFinding]:
    """Parse an LLM response into a list of ReviewerFinding objects.

    Expects the response to contain a JSON array (possibly wrapped in
    markdown fences).  Each element should have at least ``category``,
    ``severity``, ``file_path``, and ``message``.
    """
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1 :]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array within the text
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            log.warning(
                "council.parse_response_failed",
                reviewer_role=reviewer_role,
                raw_preview=raw_text[:200],
            )
            return []
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            log.warning(
                "council.parse_response_failed",
                reviewer_role=reviewer_role,
                raw_preview=raw_text[:200],
            )
            return []

    if not isinstance(parsed, list):
        parsed = [parsed] if isinstance(parsed, dict) else []

    findings: list[ReviewerFinding] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        findings.append(
            ReviewerFinding(
                reviewer_role=reviewer_role,
                category=str(item.get("category", "general")),
                severity=str(item.get("severity", "info")),
                file_path=str(item.get("file_path", "")),
                line_start=int(item.get("line_start", 0)),
                line_end=int(item.get("line_end", 0)),
                message=str(item.get("message", "")),
                suggested_fix=str(item.get("suggested_fix", "")),
                confidence=float(item.get("confidence", 0.0)),
                evidence_refs=tuple(str(r) for r in item.get("evidence_refs", ())),
            )
        )
    return findings


class ReviewCouncilService:
    """Orchestrates parallel LLM reviews.

    Lifecycle:
    1. convene() -- reads file contents, dispatches reviewers in parallel, waits
    2. Each reviewer runs an LLM call with a perspective-specific prompt
    3. GovernedReviewer runs lint in parallel (no LLM cost)
    4. Arbiter synthesizes all findings into a verdict
    """

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        workspace_root: str,
        perspectives: tuple[ReviewPerspective, ...] | None = None,
        *,
        default_model: str = "claude-haiku-4-5-20251001",
        max_workers: int = 5,
    ) -> None:
        self._provider_factory = provider_factory
        self._workspace_root = workspace_root
        self._perspectives = perspectives or ()
        self._default_model = default_model
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convene(
        self,
        spec_id: str,
        changed_files: list[str],
        spec_data: dict[str, Any],
        *,
        revision_cycle: int = 0,
        max_revision_cycles: int = 3,
        prior_findings: list[ReviewerFinding] | None = None,
    ) -> CouncilVerdict:
        """Run all reviewers in parallel and synthesize a verdict."""
        start = time.monotonic()
        council_id = f"council-{uuid.uuid4().hex[:12]}"

        # ----- Early exit: nothing to review -----
        if not changed_files:
            log.warning(
                "council.no_files_to_review",
                council_id=council_id,
                spec_id=spec_id,
            )
            return CouncilVerdict(
                verdict="revise",
                council_id=council_id,
                reviewer_count=0,
                finding_count=0,
                critical_count=0,
                high_count=0,
                findings=(),
                lint_passed=True,
                consensus_score=0.0,
                revision_directive="No changed files provided for review.",
                duration_seconds=round(time.monotonic() - start, 3),
                decided_at=time.time(),
            )

        log.info(
            "council.convene",
            council_id=council_id,
            spec_id=spec_id,
            file_count=len(changed_files),
            perspective_count=len(self._perspectives),
            revision_cycle=revision_cycle,
        )

        # ----- Validate perspective dependencies -----
        all_roles = {p.role for p in self._perspectives}
        for p in self._perspectives:
            unknown = set(p.requires_passed) - all_roles
            if unknown:
                log.warning(
                    "council.unknown_prerequisite",
                    role=p.role,
                    unknown_roles=unknown,
                )

        # Detect circular dependencies
        dep_graph: dict[str, tuple[str, ...]] = {
            p.role: p.requires_passed for p in self._perspectives if p.requires_passed
        }
        for start_role in dep_graph:
            visited: set[str] = set()
            current = start_role
            while current in dep_graph:
                if current in visited:
                    cycle = " -> ".join([start_role, *list(visited), current])
                    raise ValueError(f"Circular review dependency detected: {cycle}")
                visited.add(current)
                # Follow chain: pick first requires_passed that is also dependent
                next_roles = [r for r in dep_graph[current] if r in dep_graph]
                current = next_roles[0] if next_roles else ""

        # ----- Read file contents (bounded) -----
        bounded_files = changed_files[:_MAX_FILES]
        file_contents: dict[str, str] = {}
        for rel_path in bounded_files:
            full_path = Path(self._workspace_root) / rel_path
            content = _read_file_bounded(full_path)
            if content:
                file_contents[rel_path] = content

        diff_content = _git_diff_for_files(self._workspace_root, bounded_files)

        # ----- Split perspectives into dependency phases -----
        independent = [p for p in self._perspectives if not p.requires_passed]
        dependent = [p for p in self._perspectives if p.requires_passed]

        all_findings: list[ReviewerFinding] = []
        lint_passed = True
        passed_roles: set[str] = set()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
        ) as pool:
            # Submit lint reviewer early so it runs in parallel with phase 1
            lint_future = pool.submit(
                self._run_lint_review,
                bounded_files,
                self._workspace_root,
            )

            # Phase 1: independent perspectives
            phase1_findings, phase1_passed = self._dispatch_phase(
                pool,
                independent,
                council_id,
                file_contents,
                diff_content,
                spec_data,
                prior_findings,
            )
            all_findings.extend(phase1_findings)
            # A role "passed" if it completed and produced no critical/high findings
            roles_with_blocking = {
                f.reviewer_role for f in phase1_findings if f.severity in ("critical", "high")
            }
            passed_roles.update(phase1_passed - roles_with_blocking)

            # Phase 2: dependent perspectives (only if prerequisites passed)
            eligible = [
                p for p in dependent if all(req in passed_roles for req in p.requires_passed)
            ]
            skipped = [
                p for p in dependent if not all(req in passed_roles for req in p.requires_passed)
            ]
            for p in skipped:
                missing = [r for r in p.requires_passed if r not in passed_roles]
                log.info(
                    "council.reviewer_skipped",
                    council_id=council_id,
                    role=p.role,
                    missing_prerequisites=missing,
                )

            if eligible:
                phase2_findings, _ = self._dispatch_phase(
                    pool,
                    eligible,
                    council_id,
                    file_contents,
                    diff_content,
                    spec_data,
                    prior_findings,
                )
                all_findings.extend(phase2_findings)

            # Collect lint results
            try:
                lint_result = lint_future.result(timeout=30)
                lint_passed, lint_findings = lint_result
                all_findings.extend(lint_findings)
            except Exception:
                log.warning(
                    "council.lint_failed",
                    council_id=council_id,
                    exc_info=True,
                )

        # ----- Synthesize verdict -----
        verdict = self._synthesize_verdict(
            council_id=council_id,
            spec_id=spec_id,
            findings=all_findings,
            lint_passed=lint_passed,
            start_time=start,
            revision_cycle=revision_cycle,
            max_revision_cycles=max_revision_cycles,
        )

        log.info(
            "council.verdict",
            council_id=council_id,
            verdict=verdict.verdict,
            finding_count=verdict.finding_count,
            critical_count=verdict.critical_count,
            high_count=verdict.high_count,
            duration_seconds=verdict.duration_seconds,
        )

        return verdict

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _dispatch_phase(
        self,
        pool: concurrent.futures.ThreadPoolExecutor,
        perspectives: list[ReviewPerspective],
        council_id: str,
        file_contents: dict[str, str],
        diff_content: str,
        spec_data: dict[str, Any],
        prior_findings: list[ReviewerFinding] | None,
    ) -> tuple[list[ReviewerFinding], set[str]]:
        """Dispatch a batch of perspectives and collect results.

        Returns (findings, roles_that_completed_without_critical_or_high).
        """
        futures: dict[concurrent.futures.Future[list[ReviewerFinding]], str] = {}
        for perspective in perspectives:
            future = pool.submit(
                self._run_single_review,
                perspective,
                council_id,
                file_contents,
                diff_content,
                spec_data,
                prior_findings,
            )
            futures[future] = perspective.role

        findings: list[ReviewerFinding] = []
        completed_roles: set[str] = set()

        phase_timeout = max(
            (p.timeout_seconds for p in perspectives),
            default=_FUTURES_TIMEOUT_SECONDS,
        )

        done, not_done = concurrent.futures.wait(
            futures.keys(),
            timeout=phase_timeout,
        )

        for future in done:
            role = futures[future]
            try:
                role_findings = future.result()
                findings.extend(role_findings)
                completed_roles.add(role)
                log.debug(
                    "council.reviewer_done",
                    council_id=council_id,
                    role=role,
                    finding_count=len(role_findings),
                )
            except Exception:
                log.warning(
                    "council.reviewer_failed",
                    council_id=council_id,
                    role=role,
                    exc_info=True,
                )

        for future in not_done:
            role = futures[future]
            future.cancel()
            log.warning(
                "council.reviewer_timeout",
                council_id=council_id,
                role=role,
            )

        # If no reviewer completed successfully and some timed out, inject an
        # error finding so the verdict cannot silently auto-accept.
        if not completed_roles and not_done:
            timed_out_roles = [futures[f] for f in not_done]
            log.warning(
                "council.all_reviewers_failed",
                council_id=council_id,
                timed_out_roles=timed_out_roles,
            )
            findings.append(
                ReviewerFinding(
                    reviewer_role="council",
                    category="council_error",
                    severity="critical",
                    file_path="",
                    message=(
                        "All reviewers timed out or failed — "
                        "council could not complete review. "
                        f"Timed-out roles: {', '.join(timed_out_roles)}"
                    ),
                )
            )

        return findings, completed_roles

    def _run_single_review(
        self,
        perspective: ReviewPerspective,
        council_id: str,
        file_contents: dict[str, str],
        diff_content: str,
        spec_data: dict[str, Any],
        prior_findings: list[ReviewerFinding] | None,
    ) -> list[ReviewerFinding]:
        """Run a single LLM reviewer for one perspective."""
        try:
            provider = self._provider_factory()
        except Exception:
            log.warning(
                "council.provider_creation_failed",
                role=perspective.role,
                exc_info=True,
            )
            return []

        # Build user prompt
        files_section = ""
        for path, content in file_contents.items():
            files_section += f"\n### {path}\n```\n{content}\n```\n"

        diff_section = ""
        if diff_content:
            diff_section = f"\n### Git Diff\n```diff\n{diff_content}\n```\n"

        prior_section = ""
        if prior_findings:
            prior_json = json.dumps(
                [
                    {
                        "reviewer_role": f.reviewer_role,
                        "category": f.category,
                        "severity": f.severity,
                        "file_path": f.file_path,
                        "message": f.message,
                    }
                    for f in prior_findings
                ],
                indent=2,
            )
            prior_section = f"\n### Prior Review Findings\n```json\n{prior_json}\n```\n"

        spec_section = json.dumps(spec_data, indent=2, default=str)

        user_prompt = (
            f"## Review Council Session: {council_id}\n\n"
            f"### Spec\n```json\n{spec_section}\n```\n"
            f"{files_section}"
            f"{diff_section}"
            f"{prior_section}"
            "\nReturn your findings as a JSON array.  Each element must have:\n"
            '  "category", "severity" (critical|high|medium|low|info), '
            '"file_path", "line_start", "line_end", "message", '
            '"suggested_fix", "confidence" (0.0-1.0)\n'
        )

        system_prompt = perspective.system_prompt_template.replace("{council_id}", council_id)

        model = perspective.model or self._default_model

        from hermit.runtime.provider_host.shared.contracts import ProviderRequest

        request = ProviderRequest(
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        try:
            response = provider.generate(request)
        except Exception:
            log.warning(
                "council.llm_call_failed",
                role=perspective.role,
                exc_info=True,
            )
            return []

        # Extract text from response content blocks
        raw_text = ""
        for block in response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw_text += block.get("text", "")

        return _parse_reviewer_response(raw_text, perspective.role)

    def _run_lint_review(
        self,
        changed_files: list[str],
        workspace_root: str,
    ) -> tuple[bool, list[ReviewerFinding]]:
        """Run GovernedReviewer synchronously and convert to ReviewerFinding."""
        try:
            from hermit.plugins.builtin.hooks.quality.reviewer import GovernedReviewer

            reviewer = GovernedReviewer(workspace_root=workspace_root)
            report = reviewer._review_sync(changed_files)

            findings: list[ReviewerFinding] = []
            for finding in report.findings:
                # Map FindingSeverity to council severity strings
                severity_map: dict[FindingSeverity, str] = {
                    FindingSeverity.BLOCKING: "critical",
                    FindingSeverity.WARNING: "medium",
                    FindingSeverity.INFO: "info",
                }
                findings.append(
                    ReviewerFinding(
                        reviewer_role="lint",
                        category=finding.category,
                        severity=severity_map.get(finding.severity, "info"),
                        file_path=finding.file_path,
                        line_start=finding.line,
                        message=finding.message,
                    )
                )

            return (report.passed, findings)
        except Exception:
            log.warning("council.lint_review_failed", exc_info=True)
            return (True, [])

    def _synthesize_verdict(
        self,
        *,
        council_id: str,
        spec_id: str,
        findings: list[ReviewerFinding],
        lint_passed: bool,
        start_time: float,
        revision_cycle: int,
        max_revision_cycles: int,
    ) -> CouncilVerdict:
        """Synthesize all findings into a single verdict.

        Delegates to CouncilArbiter if available; otherwise falls back to
        a deterministic rule-based synthesis.
        """
        try:
            from hermit.plugins.builtin.hooks.quality.council_arbiter import (
                CouncilArbiter,
            )

            # Group findings by role for the arbiter
            findings_by_role: dict[str, list[ReviewerFinding]] = {}
            for f in findings:
                findings_by_role.setdefault(f.reviewer_role, []).append(f)

            return CouncilArbiter().synthesize(
                council_id=council_id,
                findings_by_role=findings_by_role,
                perspectives=self._perspectives,
                lint_passed=lint_passed,
                revision_cycle=revision_cycle,
                max_revision_cycles=max_revision_cycles,
            )
        except ImportError:
            pass

        # Fallback: deterministic rule-based synthesis
        duration = round(time.monotonic() - start_time, 3)
        critical_count = sum(1 for f in findings if f.severity == "critical")
        high_count = sum(1 for f in findings if f.severity == "high")

        # Determine verdict
        if critical_count > 0:
            verdict_str = "reject"
        elif (high_count > 0 and revision_cycle < max_revision_cycles) or not lint_passed:
            verdict_str = "revise"
        else:
            verdict_str = "accept"

        # Build revision directive for non-accept verdicts
        revision_directive = ""
        if verdict_str != "accept":
            actionable = [f for f in findings if f.severity in ("critical", "high")]
            if actionable:
                lines = [f"Revision cycle {revision_cycle + 1}/{max_revision_cycles}:"]
                for f in actionable:
                    loc = f"{f.file_path}:{f.line_start}" if f.line_start else f.file_path
                    lines.append(f"  - [{f.severity}] {loc}: {f.message}")
                revision_directive = "\n".join(lines)

        # Compute consensus score: fraction of reviewers that found no critical issues
        reviewer_roles = {f.reviewer_role for f in findings}
        if reviewer_roles:
            roles_with_critical = {f.reviewer_role for f in findings if f.severity == "critical"}
            consensus_score = round(1.0 - len(roles_with_critical) / len(reviewer_roles), 2)
        else:
            consensus_score = 1.0

        return CouncilVerdict(
            verdict=verdict_str,
            council_id=council_id,
            reviewer_count=len(reviewer_roles),
            finding_count=len(findings),
            critical_count=critical_count,
            high_count=high_count,
            findings=tuple(findings),
            lint_passed=lint_passed,
            consensus_score=consensus_score,
            revision_directive=revision_directive,
            duration_seconds=duration,
            decided_at=time.time(),
        )
