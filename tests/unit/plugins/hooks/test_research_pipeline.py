"""Tests for the Research Pipeline plugin."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.research.models import ResearchFinding, ResearchReport
from hermit.plugins.builtin.hooks.research.pipeline import ResearchPipeline, _deduplicate
from hermit.plugins.builtin.hooks.research.strategies import (
    CodebaseStrategy,
    DocStrategy,
    GitHistoryStrategy,
    WebStrategy,
    _extract_keywords,
    _score_content,
    _score_path,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestResearchFinding:
    def test_frozen(self) -> None:
        f = ResearchFinding(source="codebase", title="test.py", content="hello", relevance=0.8)
        with pytest.raises(AttributeError):
            f.source = "web"  # type: ignore[misc]

    def test_defaults(self) -> None:
        f = ResearchFinding(source="web", title="result", content="body", relevance=0.5)
        assert f.url == ""
        assert f.file_path == ""
        assert f.metadata == {}

    def test_with_all_fields(self) -> None:
        f = ResearchFinding(
            source="doc",
            title="API docs",
            content="usage guide",
            relevance=0.9,
            url="https://example.com",
            file_path="docs/api.md",
            metadata={"version": "1.0"},
        )
        assert f.source == "doc"
        assert f.url == "https://example.com"
        assert f.metadata == {"version": "1.0"}


class TestResearchReport:
    def test_defaults(self) -> None:
        r = ResearchReport(goal="test goal")
        assert r.findings == ()
        assert r.suggested_approach == ""
        assert r.knowledge_gaps == ()
        assert r.query_count == 0
        assert r.duration_seconds == 0.0

    def test_frozen(self) -> None:
        r = ResearchReport(goal="test")
        with pytest.raises(AttributeError):
            r.goal = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_basic(self) -> None:
        keywords = _extract_keywords("implement authentication flow", [])
        # "implement" is now a stop word (action verb)
        assert "authentication" in keywords
        assert "flow" in keywords

    def test_filters_stop_words(self) -> None:
        keywords = _extract_keywords("how to implement the feature", [])
        assert "how" not in keywords
        assert "the" not in keywords
        assert "implement" not in keywords  # action verb stop word
        assert "feature" in keywords

    def test_includes_hints(self) -> None:
        keywords = _extract_keywords("auth", ["oauth2", "jwt"])
        assert "auth" in keywords
        assert "oauth2" in keywords
        assert "jwt" in keywords

    def test_excludes_urls(self) -> None:
        keywords = _extract_keywords("auth", ["https://example.com"])
        assert "https://example.com" not in keywords

    def test_max_10_keywords(self) -> None:
        long_goal = " ".join(f"word{i}" for i in range(20))
        keywords = _extract_keywords(long_goal, [])
        assert len(keywords) <= 10

    def test_deduplicates(self) -> None:
        keywords = _extract_keywords("auth auth auth", ["auth"])
        assert keywords.count("auth") == 1


class TestScorePath:
    def test_no_keywords(self) -> None:
        assert _score_path("src/main.py", []) == 0.0

    def test_full_match(self) -> None:
        assert _score_path("auth/login.py", ["auth", "login"]) == 1.0

    def test_partial_match(self) -> None:
        score = _score_path("auth/views.py", ["auth", "login"])
        assert 0.0 < score < 1.0

    def test_no_match(self) -> None:
        assert _score_path("utils/helpers.py", ["auth", "login"]) == 0.0


class TestScoreContent:
    def test_no_keywords(self) -> None:
        assert _score_content("some content", []) == 0.0

    def test_matches(self) -> None:
        score = _score_content("implement auth flow", ["auth", "flow"])
        assert score > 0.0


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------


class TestCodebaseStrategy:
    def test_empty_workspace(self) -> None:
        strategy = CodebaseStrategy(workspace="")
        result = asyncio.run(strategy.research("find auth code"))
        assert result == []

    def test_nonexistent_workspace(self) -> None:
        strategy = CodebaseStrategy(workspace="/nonexistent/path")
        result = asyncio.run(strategy.research("test"))
        assert result == []

    def test_finds_matching_files(self, tmp_path: Path) -> None:
        # Create test files
        (tmp_path / "auth.py").write_text("def authenticate(user): pass")
        (tmp_path / "utils.py").write_text("def helper(): pass")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "login.py").write_text("from auth import authenticate")

        strategy = CodebaseStrategy(workspace=str(tmp_path))
        findings = asyncio.run(strategy.research("authentication login"))

        assert len(findings) > 0
        sources = {f.source for f in findings}
        assert sources == {"codebase"}
        # auth.py and login.py should be found
        paths = {f.file_path for f in findings}
        assert "auth.py" in paths or "src/login.py" in paths

    def test_skips_large_files(self, tmp_path: Path) -> None:
        large_file = tmp_path / "big.py"
        large_file.write_text("x" * 60_000)  # > 50KB

        strategy = CodebaseStrategy(workspace=str(tmp_path))
        findings = asyncio.run(strategy.research("big"))
        # large file should be skipped
        paths = {f.file_path for f in findings}
        assert "big.py" not in paths


class TestWebStrategy:
    def test_disabled(self) -> None:
        strategy = WebStrategy(enabled=False)
        result = asyncio.run(strategy.research("test query"))
        assert result == []

    def test_search_called(self) -> None:
        strategy = WebStrategy(enabled=True)
        with patch(
            "hermit.plugins.builtin.tools.web_tools.search.handle_search",
            return_value="### 1. Result\nSome content\nURL: https://example.com",
        ) as mock_search:
            findings = asyncio.run(strategy.research("test query"))
            assert mock_search.called
            assert len(findings) > 0
            assert findings[0].source == "web"

    def test_handles_search_failure(self) -> None:
        strategy = WebStrategy(enabled=True)
        with patch(
            "hermit.plugins.builtin.tools.web_tools.search.handle_search",
            side_effect=Exception("network error"),
        ):
            findings = asyncio.run(strategy.research("test"))
            assert findings == []


class TestDocStrategy:
    def test_disabled(self) -> None:
        strategy = DocStrategy(enabled=False)
        result = asyncio.run(strategy.research("test", ["https://example.com"]))
        assert result == []

    def test_no_urls_in_hints(self) -> None:
        strategy = DocStrategy(enabled=True)
        result = asyncio.run(strategy.research("test", ["keyword1", "keyword2"]))
        assert result == []

    def test_fetches_urls(self) -> None:
        strategy = DocStrategy(enabled=True)
        with patch(
            "hermit.plugins.builtin.tools.web_tools.fetch.handle_fetch",
            return_value="Documentation content here",
        ) as mock_fetch:
            findings = asyncio.run(strategy.research("api usage", ["https://docs.example.com"]))
            assert mock_fetch.called
            assert len(findings) == 1
            assert findings[0].source == "doc"
            assert findings[0].url == "https://docs.example.com"


class TestGitHistoryStrategy:
    def test_empty_workspace(self) -> None:
        strategy = GitHistoryStrategy(workspace="")
        result = asyncio.run(strategy.research("test"))
        assert result == []

    def test_no_git_repo(self, tmp_path: Path) -> None:
        strategy = GitHistoryStrategy(workspace=str(tmp_path))
        result = asyncio.run(strategy.research("test"))
        assert result == []

    def test_with_git_repo(self, tmp_path: Path) -> None:
        # Initialize a git repo with a commit
        os.system(
            f"cd {tmp_path} && git init && git config user.email 't@t' && git config user.name 't'"
        )
        (tmp_path / "auth.py").write_text("pass")
        os.system(f"cd {tmp_path} && git add . && git commit -m 'add auth module'")

        strategy = GitHistoryStrategy(workspace=str(tmp_path))
        findings = asyncio.run(strategy.research("auth module"))

        assert len(findings) > 0
        assert all(f.source == "git_history" for f in findings)


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_removes_duplicates(self) -> None:
        findings = [
            ResearchFinding(source="web", title="Auth Guide", content="a", relevance=0.5),
            ResearchFinding(source="doc", title="auth guide", content="b", relevance=0.8),
        ]
        result = _deduplicate(findings)
        assert len(result) == 1
        assert result[0].relevance == 0.8  # keeps highest

    def test_preserves_unique(self) -> None:
        findings = [
            ResearchFinding(source="web", title="Guide A", content="a", relevance=0.5),
            ResearchFinding(source="doc", title="Guide B", content="b", relevance=0.8),
        ]
        result = _deduplicate(findings)
        assert len(result) == 2


class TestResearchPipeline:
    def test_empty_strategies(self) -> None:
        pipeline = ResearchPipeline(strategies=[], max_findings=10)
        report = asyncio.run(pipeline.run("test goal"))
        assert report.goal == "test goal"
        assert report.findings == ()
        assert len(report.knowledge_gaps) > 0

    def test_aggregates_findings(self) -> None:
        class MockStrategy:
            async def research(
                self, goal: str, hints: list[str] | None = None
            ) -> list[ResearchFinding]:
                return [
                    ResearchFinding(
                        source="codebase", title="found.py", content="content", relevance=0.9
                    )
                ]

        pipeline = ResearchPipeline(strategies=[MockStrategy()], max_findings=10)
        report = asyncio.run(pipeline.run("find code"))
        assert len(report.findings) == 1
        assert report.findings[0].title == "found.py"
        assert report.duration_seconds >= 0.0

    def test_max_findings_limit(self) -> None:
        class BulkStrategy:
            async def research(
                self, goal: str, hints: list[str] | None = None
            ) -> list[ResearchFinding]:
                return [
                    ResearchFinding(
                        source="codebase", title=f"file{i}.py", content="c", relevance=0.5
                    )
                    for i in range(30)
                ]

        pipeline = ResearchPipeline(strategies=[BulkStrategy()], max_findings=5)
        report = asyncio.run(pipeline.run("test"))
        assert len(report.findings) == 5

    def test_handles_strategy_exception(self) -> None:
        class FailingStrategy:
            async def research(
                self, goal: str, hints: list[str] | None = None
            ) -> list[ResearchFinding]:
                raise RuntimeError("boom")

        class OkStrategy:
            async def research(
                self, goal: str, hints: list[str] | None = None
            ) -> list[ResearchFinding]:
                return [ResearchFinding(source="web", title="ok", content="c", relevance=0.5)]

        pipeline = ResearchPipeline(strategies=[FailingStrategy(), OkStrategy()])
        report = asyncio.run(pipeline.run("test"))
        assert len(report.findings) == 1

    def test_sorts_by_relevance(self) -> None:
        class MultiStrategy:
            async def research(
                self, goal: str, hints: list[str] | None = None
            ) -> list[ResearchFinding]:
                return [
                    ResearchFinding(source="web", title="low", content="c", relevance=0.2),
                    ResearchFinding(source="codebase", title="high", content="c", relevance=0.9),
                    ResearchFinding(source="doc", title="mid", content="c", relevance=0.5),
                ]

        pipeline = ResearchPipeline(strategies=[MultiStrategy()])
        report = asyncio.run(pipeline.run("test"))
        relevances = [f.relevance for f in report.findings]
        assert relevances == sorted(relevances, reverse=True)


# ---------------------------------------------------------------------------
# Hooks registration test
# ---------------------------------------------------------------------------


class TestHooksRegistration:
    def test_register_adds_hooks(self) -> None:
        from hermit.plugins.builtin.hooks.research.hooks import register
        from hermit.runtime.capability.contracts.base import HookEvent

        mock_ctx = MagicMock()
        register(mock_ctx)

        assert mock_ctx.add_hook.call_count == 2
        events = [call.args[0] for call in mock_ctx.add_hook.call_args_list]
        assert HookEvent.SERVE_START in events
        assert HookEvent.SERVE_STOP in events


class TestToolsRegistration:
    def test_register_adds_tool(self) -> None:
        from hermit.plugins.builtin.hooks.research.tools import register

        mock_ctx = MagicMock()
        register(mock_ctx)

        assert mock_ctx.add_tool.call_count == 1
        tool_spec = mock_ctx.add_tool.call_args[0][0]
        assert tool_spec.name == "research_context"
        assert tool_spec.readonly is True
        assert tool_spec.requires_receipt is False
