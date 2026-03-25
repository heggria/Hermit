"""Calibration models and rendering for review council evaluators.

Few-shot calibration examples allow operators to anchor reviewer judgment
by providing concrete examples of past reviews with expected outcomes.
This addresses the evaluator leniency problem identified in GAN-inspired
multi-agent harness research — evaluators tend to "talk themselves into"
approving mediocre work unless explicitly calibrated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CalibrationExample:
    """A single calibration example for a reviewer perspective."""

    input_summary: str  # Brief description of the code change under review
    expected_findings: tuple[dict[str, str], ...]  # Expected findings structure
    expected_pass: bool  # Whether the review should pass or fail
    reasoning: str  # Why this verdict is expected
    source: str = "manual"  # "manual" | "lesson_derived"


@dataclass(frozen=True)
class CalibrationSet:
    """A collection of calibration examples for a specific reviewer role."""

    perspective_role: str  # Matches ReviewPerspective.role
    examples: tuple[CalibrationExample, ...] = ()


def render_calibration_section(examples: tuple[CalibrationExample, ...]) -> str:
    """Render calibration examples into a prompt section for LLM injection.

    Returns empty string if no examples are provided.
    """
    if not examples:
        return ""

    lines: list[str] = [
        "\n## Calibration Examples\n",
        "Below are examples of past reviews with the expected verdict. Use these",
        "to calibrate your severity thresholds. Be as rigorous as these examples",
        "demonstrate — do NOT talk yourself into approving work that matches a",
        "failure pattern below.\n",
    ]

    for i, ex in enumerate(examples, 1):
        lines.append(f"### Example {i}")
        lines.append(f"**Change:** {ex.input_summary}")
        if ex.expected_findings:
            lines.append("**Expected findings:**")
            for finding in ex.expected_findings:
                severity = finding.get("severity", "unknown")
                message = finding.get("message", "")
                lines.append(f"  - [{severity}] {message}")
        verdict = "PASS" if ex.expected_pass else "FAIL"
        lines.append(f"**Expected verdict:** {verdict}")
        lines.append(f"**Reasoning:** {ex.reasoning}\n")

    return "\n".join(lines)


def calibration_examples_from_dicts(
    raw: list[dict[str, Any]],
) -> tuple[CalibrationExample, ...]:
    """Deserialize calibration examples from stored dict format."""
    result: list[CalibrationExample] = []
    for item in raw:
        findings_raw = item.get("expected_findings", [])
        findings = tuple(dict(f) for f in findings_raw) if findings_raw else ()
        result.append(
            CalibrationExample(
                input_summary=str(item.get("input_summary", "")),
                expected_findings=findings,
                expected_pass=bool(item.get("expected_pass", True)),
                reasoning=str(item.get("reasoning", "")),
                source=str(item.get("source", "manual")),
            )
        )
    return tuple(result)
