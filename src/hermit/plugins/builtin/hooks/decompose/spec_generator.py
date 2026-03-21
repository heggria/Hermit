"""SpecGenerator — research-aware spec generation from goals and findings."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec

if TYPE_CHECKING:
    from hermit.plugins.builtin.hooks.research.models import ResearchReport


def _make_spec_id(goal: str) -> str:
    """Derive a deterministic kebab-case spec ID from the goal text."""
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:40]
    suffix = hashlib.sha256(goal.encode()).hexdigest()[:6]
    return f"{slug}-{suffix}" if slug else suffix


def _extract_file_plan_from_goal(goal: str) -> list[dict[str, str]]:
    """Extract file plan entries from explicit patterns in goal text."""
    entries: list[dict[str, str]] = []
    pattern = re.compile(
        r"\b(create|modify|delete|add|update|fix|refactor)\s+([\w/._-]+\.\w+)\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(goal):
        action = match.group(1).lower()
        if action in ("add", "create"):
            action = "create"
        elif action in ("update", "fix", "refactor", "modify"):
            action = "modify"
        entries.append(
            {
                "path": match.group(2),
                "action": action,
                "reason": f"Extracted from goal: {match.group(0)}",
            }
        )
    return entries


def _extract_file_plan_from_research(
    findings: tuple[object, ...],
) -> list[dict[str, str]]:
    """Extract file plan entries from research findings.

    Files with higher relevance scores are more likely targets for modification.
    """
    entries: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for finding in findings:
        file_path = getattr(finding, "file_path", "")
        raw_relevance = getattr(finding, "relevance", None)
        relevance = raw_relevance if isinstance(raw_relevance, (int, float)) else 0.0
        if not file_path or file_path in seen_paths:
            continue
        if relevance < 0.15:
            continue
        seen_paths.add(file_path)
        entries.append(
            {
                "path": file_path,
                "action": "modify",
                "reason": f"Research finding (relevance={relevance:.2f})",
            }
        )
    return entries


def _extract_constraints(goal: str) -> list[str]:
    """Extract constraint-like sentences from the goal."""
    constraints: list[str] = []
    for line in goal.splitlines():
        stripped = line.strip().lstrip("- ")
        lower = stripped.lower()
        if any(kw in lower for kw in ("must not", "do not", "never", "禁止", "avoid")):
            constraints.append(stripped)
    return constraints


def _derive_constraints_from_research(
    findings: tuple[object, ...],
) -> list[str]:
    """Derive implicit constraints from research findings."""
    constraints: list[str] = []
    has_tests = False
    has_existing_api = False
    modules_touched: set[str] = set()

    for finding in findings:
        file_path = getattr(finding, "file_path", "")
        content = getattr(finding, "content", "")

        if "test" in file_path.lower():
            has_tests = True
        if file_path:
            parts = file_path.split("/")
            for idx, part in enumerate(parts):
                if part in ("kernel", "runtime", "plugins", "infra"):
                    if idx + 1 < len(parts):
                        modules_touched.add(f"{part}/{parts[idx + 1]}")
                    break

        if any(
            line.lstrip().startswith(("def ", "async def "))
            for line in content.splitlines()
            if not line.lstrip().startswith("#")
        ):
            has_existing_api = True

    if has_tests:
        constraints.append("Existing tests must continue to pass without modification")
    if has_existing_api:
        constraints.append("Preserve backward compatibility of existing public APIs")
    if len(modules_touched) > 2:
        constraints.append(
            f"Changes span {len(modules_touched)} modules — minimize cross-module coupling"
        )

    return constraints


def _generate_acceptance_criteria(
    goal: str,
    research_report: ResearchReport | None,
    file_plan: tuple[dict[str, str], ...],
) -> tuple[str, ...]:
    """Generate task-specific acceptance criteria from goal and research."""
    criteria: list[str] = ["`make check` passes"]

    # Goal-derived criteria (independent checks — compound goals accumulate)
    goal_lower = goal.lower()
    if any(kw in goal_lower for kw in ("test", "coverage", "spec")):
        criteria.append("New tests achieve >= 80% coverage of changed code")
    if any(kw in goal_lower for kw in ("fix", "bug", "regression")):
        criteria.append("Regression test added that reproduces the original issue")
    if any(kw in goal_lower for kw in ("implement", "add", "create")):
        criteria.append("All new files have corresponding unit tests")
    if any(kw in goal_lower for kw in ("refactor", "optimize", "improve")):
        criteria.append("Existing test suite passes without modification")

    # File-plan-derived criteria
    new_files = [f for f in file_plan if f.get("action") == "create"]
    if new_files:
        paths = ", ".join(f["path"] for f in new_files[:3])
        criteria.append(f"New files created and importable: {paths}")

    modified_files = [f for f in file_plan if f.get("action") == "modify"]
    if modified_files:
        paths = ", ".join(f["path"] for f in modified_files[:3])
        suffix = f" (+{len(modified_files) - 3} more)" if len(modified_files) > 3 else ""
        criteria.append(f"Modified files validated: {paths}{suffix}")

    # Research-derived criteria
    if research_report is not None:
        if research_report.suggested_approach:
            approach = research_report.suggested_approach
            display = approach[:100] + ("..." if len(approach) > 100 else "")
            criteria.append(f"Implementation follows validated approach: {display}")
        if research_report.knowledge_gaps:
            real_gaps = [
                g for g in research_report.knowledge_gaps if not g.lower().startswith("no ")
            ]
            if real_gaps:
                criteria.append(f"Knowledge gaps addressed: {'; '.join(real_gaps[:2])}")

    return tuple(criteria)


class SpecGenerator:
    """Generates a structured spec from a goal and optional research report.

    v0.3.1: Research-aware generation — extracts file_plan from research
    findings, derives constraints from codebase context, and generates
    task-specific acceptance criteria. No LLM calls — deterministic but
    research-informed.
    """

    def generate(
        self,
        goal: str,
        research_report: ResearchReport | None = None,
        constraints: tuple[str, ...] | None = None,
    ) -> GeneratedSpec:
        """Produce a GeneratedSpec from the given goal and research context."""
        spec_id = _make_spec_id(goal)
        title = goal.split("\n")[0][:80]

        # --- Constraints: goal-extracted + research-derived + explicit ---
        all_constraints = list(constraints) if constraints else []
        all_constraints.extend(_extract_constraints(goal))
        if research_report is not None:
            all_constraints.extend(_derive_constraints_from_research(research_report.findings))

        # --- File plan: goal-extracted + research-derived (deduplicated) ---
        file_entries = _extract_file_plan_from_goal(goal)
        seen_paths = {e["path"] for e in file_entries}
        if research_report is not None:
            for entry in _extract_file_plan_from_research(research_report.findings):
                if entry["path"] not in seen_paths:
                    file_entries.append(entry)
                    seen_paths.add(entry["path"])
        file_plan = tuple(file_entries)

        # --- Acceptance criteria: task-specific ---
        acceptance_criteria = _generate_acceptance_criteria(goal, research_report, file_plan)

        # --- Research ref ---
        research_ref = ""
        if research_report is not None:
            research_ref = f"research:{spec_id}"

        return GeneratedSpec(
            spec_id=spec_id,
            title=title,
            goal=goal,
            constraints=tuple(all_constraints),
            acceptance_criteria=acceptance_criteria,
            file_plan=file_plan,
            research_ref=research_ref,
            trust_zone="normal",
        )
