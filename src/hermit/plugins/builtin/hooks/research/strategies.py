"""Research strategies — codebase, web, doc, and git history."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import structlog

from hermit.plugins.builtin.hooks.research.models import ResearchFinding

log = structlog.get_logger()


class CodebaseStrategy:
    """Search local codebase using glob + file read."""

    def __init__(self, workspace: str = "") -> None:
        self._workspace = workspace

    async def research(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        """Glob for relevant files based on goal keywords."""
        if not self._workspace:
            return []

        return await asyncio.to_thread(self._research_sync, goal, hints)

    def _research_sync(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        workspace_root = Path(self._workspace).resolve()
        if not workspace_root.is_dir():
            return []

        keywords = _extract_keywords(goal, hints or [])
        findings: list[ResearchFinding] = []

        # Search common source patterns
        patterns = [
            "**/*.py",
            "**/*.ts",
            "**/*.js",
            "**/*.md",
            "**/*.toml",
            "**/*.yaml",
            "**/*.yml",
        ]
        seen_paths: set[str] = set()

        for pattern in patterns:
            try:
                matches = list(workspace_root.glob(pattern))
            except (ValueError, OSError):
                continue

            for match in matches[:100]:  # cap per pattern
                if not match.is_file():
                    continue
                relative = str(match.relative_to(workspace_root))
                if relative in seen_paths:
                    continue

                # Check if filename/path matches any keyword
                path_lower = relative.lower()
                relevance = _score_path(path_lower, keywords)
                if relevance < 0.1:
                    continue

                seen_paths.add(relative)
                try:
                    size = match.stat().st_size
                    if size > 50_000:
                        continue
                    content = match.read_text(encoding="utf-8", errors="replace")[:2000]
                except OSError:
                    continue

                # Boost relevance if content contains keywords
                content_relevance = _score_content(content.lower(), keywords)
                combined = min(1.0, relevance + content_relevance * 0.5)

                findings.append(
                    ResearchFinding(
                        source="codebase",
                        title=relative,
                        content=content[:500],
                        relevance=combined,
                        file_path=relative,
                    )
                )

        findings.sort(key=lambda f: f.relevance, reverse=True)
        return findings[:10]


class WebStrategy:
    """Search the web via DuckDuckGo (serial, rate-limited)."""

    _DELAY_SECONDS = 2.0
    _MAX_QUERIES = 5

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def research(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        """Run web searches serially with rate limiting."""
        if not self._enabled:
            return []

        from hermit.plugins.builtin.tools.web_tools.search import handle_search

        queries = _build_web_queries(goal, hints or [])
        queries = queries[: self._MAX_QUERIES]
        findings: list[ResearchFinding] = []

        for i, query in enumerate(queries):
            if i > 0:
                await asyncio.sleep(self._DELAY_SECONDS)

            try:
                result = handle_search({"query": query, "max_results": 5})
                if result and "No results" not in result:
                    findings.append(
                        ResearchFinding(
                            source="web",
                            title=f"Web search: {query}",
                            content=result[:1000],
                            relevance=0.6 - (i * 0.05),
                            metadata={"query": query},
                        )
                    )
            except Exception:
                log.debug("web_search_failed", query=query)

        return findings


class DocStrategy:
    """Fetch documentation from URLs found in hints or common doc sites."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def research(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        """Fetch documentation URLs provided in hints."""
        if not self._enabled:
            return []

        from hermit.plugins.builtin.tools.web_tools.fetch import handle_fetch

        urls = [h for h in (hints or []) if h.startswith(("http://", "https://"))]
        if not urls:
            return []

        findings: list[ResearchFinding] = []
        for url in urls[:5]:
            try:
                content = handle_fetch({"url": url, "max_length": 10000})
                if content and "Error" not in content[:20]:
                    findings.append(
                        ResearchFinding(
                            source="doc",
                            title=f"Documentation: {url}",
                            content=content[:1000],
                            relevance=0.7,
                            url=url,
                        )
                    )
            except Exception:
                log.debug("doc_fetch_failed", url=url)

        return findings


class GitHistoryStrategy:
    """Parse git log for change patterns relevant to the goal."""

    def __init__(self, workspace: str = "") -> None:
        self._workspace = workspace

    async def research(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        """Search git log for commits matching goal keywords."""
        if not self._workspace:
            return []

        return await asyncio.to_thread(self._research_sync, goal, hints)

    def _research_sync(self, goal: str, hints: list[str] | None = None) -> list[ResearchFinding]:
        workspace_root = Path(self._workspace).resolve()
        if not (workspace_root / ".git").is_dir():
            return []

        keywords = _extract_keywords(goal, hints or [])
        if not keywords:
            return []

        search_term = " ".join(keywords[:3])
        findings: list[ResearchFinding] = []

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--all", f"--grep={search_term}", "-n", "20"],
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().splitlines()
                findings.append(
                    ResearchFinding(
                        source="git_history",
                        title=f"Git commits matching '{search_term}'",
                        content="\n".join(lines[:15]),
                        relevance=0.5,
                        metadata={"match_count": str(len(lines))},
                    )
                )
        except (subprocess.TimeoutExpired, OSError):
            log.debug("git_log_failed", workspace=self._workspace)

        # Also check recently changed files
        try:
            result = subprocess.run(
                ["git", "log", "--name-only", "--pretty=format:", "-n", "10"],
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                changed_files = [f for f in result.stdout.strip().splitlines() if f.strip()]
                relevant = [f for f in changed_files if _score_path(f.lower(), keywords) > 0.1]
                if relevant:
                    findings.append(
                        ResearchFinding(
                            source="git_history",
                            title="Recently changed relevant files",
                            content="\n".join(sorted(set(relevant))[:10]),
                            relevance=0.4,
                            metadata={"file_count": str(len(relevant))},
                        )
                    )
        except (subprocess.TimeoutExpired, OSError):
            pass

        return findings


# --- Helpers ---


def _extract_keywords(goal: str, hints: list[str]) -> list[str]:
    """Extract meaningful keywords from goal and hints."""
    stop_words = frozenset(
        {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "shall",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "about",
            "how",
            "what",
            "which",
            "who",
            "when",
            "where",
            "why",
            "and",
            "or",
            "not",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
        }
    )
    words: list[str] = []
    for text in [goal] + [h for h in hints if not h.startswith("http")]:
        for word in text.lower().split():
            cleaned = word.strip(".,;:!?\"'()[]{}").replace("-", "_")
            if len(cleaned) > 2 and cleaned not in stop_words:
                words.append(cleaned)
    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:10]


def _score_path(path_lower: str, keywords: list[str]) -> float:
    """Score a file path by keyword match density."""
    if not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in path_lower)
    return min(1.0, hits / max(len(keywords), 1))


def _score_content(content_lower: str, keywords: list[str]) -> float:
    """Score content by keyword presence."""
    if not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in content_lower)
    return min(1.0, hits / max(len(keywords), 1))


def _build_web_queries(goal: str, hints: list[str]) -> list[str]:
    """Build web search queries from goal and hints."""
    queries = [goal]
    non_url_hints = [h for h in hints if not h.startswith("http")]
    for hint in non_url_hints[:2]:
        queries.append(f"{goal} {hint}")
    return queries
