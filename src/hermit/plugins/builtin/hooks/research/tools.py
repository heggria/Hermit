"""Agent-facing tool for research context gathering."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from hermit.plugins.builtin.hooks.research.pipeline import ResearchPipeline
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec

_pipeline_lock = threading.Lock()
_pipeline: ResearchPipeline | None = None


def set_pipeline(pipeline: ResearchPipeline | None) -> None:
    global _pipeline
    with _pipeline_lock:
        _pipeline = pipeline


def get_pipeline() -> ResearchPipeline | None:
    with _pipeline_lock:
        return _pipeline


def _handle_research_context(payload: dict[str, Any]) -> str:
    """Run the research pipeline and return a structured report."""
    pipeline = get_pipeline()
    if pipeline is None:
        return "Research pipeline is not initialized."

    goal = str(payload.get("goal", "")).strip()
    if not goal:
        return "Error: 'goal' is required."

    hints = payload.get("hints", [])
    if isinstance(hints, str):
        hints = [h.strip() for h in hints.split(",") if h.strip()]

    # Run the async pipeline from sync context
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already in an async context — schedule as a task
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, pipeline.run(goal, hints))
            report = future.result(timeout=120)
    else:
        report = asyncio.run(pipeline.run(goal, hints))

    # Format as structured text
    lines = [
        f"## Research Report: {report.goal}",
        f"Duration: {report.duration_seconds}s | Findings: {len(report.findings)} | Queries: {report.query_count}",
        "",
    ]

    for i, finding in enumerate(report.findings, 1):
        lines.append(f"### {i}. [{finding.source}] {finding.title}")
        if finding.file_path:
            lines.append(f"File: {finding.file_path}")
        if finding.url:
            lines.append(f"URL: {finding.url}")
        lines.append(f"Relevance: {finding.relevance:.2f}")
        lines.append(finding.content[:500])
        lines.append("")

    if report.knowledge_gaps:
        lines.append("### Knowledge Gaps")
        for gap in report.knowledge_gaps:
            lines.append(f"- {gap}")
        lines.append("")

    if report.suggested_approach:
        lines.append(f"### Suggested Approach\n{report.suggested_approach}")

    return "\n".join(lines)


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="research_context",
            description=(
                "Research a topic across codebase, web, documentation, and git history. "
                "Provide a goal describing what you need to learn, and optional hints "
                "(keywords, URLs) to guide the search. Returns a structured report with "
                "ranked findings from multiple sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What you want to research or understand.",
                    },
                    "hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional keywords or documentation URLs to guide research.",
                    },
                },
                "required": ["goal"],
            },
            handler=_handle_research_context,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
