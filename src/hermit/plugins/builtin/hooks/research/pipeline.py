"""Research pipeline — orchestrates strategies and aggregates findings."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from hermit.plugins.builtin.hooks.research.models import ResearchFinding, ResearchReport

log = structlog.get_logger()


class ResearchPipeline:
    """Orchestrate multiple research strategies, deduplicate, and rank findings."""

    def __init__(
        self,
        strategies: list[Any],
        max_findings: int = 20,
    ) -> None:
        self._strategies = list(strategies)
        self._max_findings = max_findings

    async def run(self, goal: str, hints: list[str] | None = None) -> ResearchReport:
        """Run all strategies, aggregate and rank findings."""
        start = time.monotonic()
        hints = hints or []
        all_findings: list[ResearchFinding] = []
        query_count = 0

        # Run strategies concurrently (WebStrategy self-rate-limits internally)
        tasks = [strategy.research(goal, hints) for strategy in self._strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                strategy_name = type(self._strategies[i]).__name__
                log.warning("research_strategy_failed", strategy=strategy_name, error=str(result))
                continue
            findings = result
            all_findings.extend(findings)
            query_count += len(findings)

        # Deduplicate by title
        deduped = _deduplicate(all_findings)

        # Sort by relevance descending
        ranked = sorted(deduped, key=lambda f: f.relevance, reverse=True)
        trimmed = ranked[: self._max_findings]

        duration = time.monotonic() - start

        # Identify knowledge gaps
        sources_present = {f.source for f in trimmed}
        all_sources = {"codebase", "web", "doc", "git_history"}
        gaps = tuple(f"No {s} findings" for s in sorted(all_sources - sources_present))

        log.info(
            "research_complete",
            goal=goal[:80],
            total_findings=len(trimmed),
            sources=sorted(sources_present),
            duration=f"{duration:.1f}s",
        )

        return ResearchReport(
            goal=goal,
            findings=tuple(trimmed),
            knowledge_gaps=gaps,
            query_count=query_count,
            duration_seconds=round(duration, 2),
        )


def _deduplicate(findings: list[ResearchFinding]) -> list[ResearchFinding]:
    """Remove duplicate findings by title, keeping highest relevance."""
    best: dict[str, ResearchFinding] = {}
    for f in findings:
        key = f.title.lower().strip()
        existing = best.get(key)
        if existing is None or f.relevance > existing.relevance:
            best[key] = f
    return list(best.values())
