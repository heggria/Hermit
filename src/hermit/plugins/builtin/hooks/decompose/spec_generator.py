"""SpecGenerator — template-based spec generation from goals and research."""

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


def _extract_file_plan(goal: str) -> tuple[dict[str, str], ...]:
    """Extract file plan entries from goal text heuristics.

    Looks for patterns like 'create src/foo.py' or 'modify bar.py'.
    Returns an empty tuple when no patterns are found.
    """
    entries: list[dict[str, str]] = []
    pattern = re.compile(
        r"\b(create|modify|delete)\s+([\w/._-]+\.py)\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(goal):
        entries.append(
            {
                "path": match.group(2),
                "action": match.group(1).lower(),
                "reason": f"Extracted from goal: {match.group(0)}",
            }
        )
    return tuple(entries)


def _extract_constraints(goal: str) -> tuple[str, ...]:
    """Extract constraint-like sentences from the goal."""
    constraints: list[str] = []
    for line in goal.splitlines():
        stripped = line.strip().lstrip("- ")
        lower = stripped.lower()
        if any(kw in lower for kw in ("must not", "do not", "never", "禁止", "avoid")):
            constraints.append(stripped)
    return tuple(constraints)


class SpecGenerator:
    """Generates a structured spec from a goal and optional research report.

    v0.3: Template-based generation with structured field extraction.
    No LLM calls — deterministic parsing of goal text.
    """

    def generate(
        self,
        goal: str,
        research_report: ResearchReport | None = None,
        constraints: tuple[str, ...] | None = None,
    ) -> GeneratedSpec:
        """Produce a GeneratedSpec from the given goal.

        Args:
            goal: The high-level task description.
            research_report: Optional D1 research output for context.
            constraints: Optional explicit constraints to include.
        """
        spec_id = _make_spec_id(goal)
        title = goal.split("\n")[0][:80]

        extracted_constraints = _extract_constraints(goal)
        all_constraints = (constraints or ()) + extracted_constraints

        file_plan = _extract_file_plan(goal)

        acceptance_criteria = (
            "`make check` passes",
            "All new files have corresponding tests",
        )

        research_ref = ""
        if research_report is not None:
            research_ref = f"research:{spec_id}"
            if research_report.suggested_approach:
                acceptance_criteria = (
                    *acceptance_criteria,
                    f"Approach validated: {research_report.suggested_approach[:100]}",
                )

        return GeneratedSpec(
            spec_id=spec_id,
            title=title,
            goal=goal,
            constraints=all_constraints,
            acceptance_criteria=acceptance_criteria,
            file_plan=file_plan,
            research_ref=research_ref,
            trust_zone="normal",
        )
