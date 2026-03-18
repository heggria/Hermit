"""Tests for patrol tools — agent-facing manual patrol triggering and status."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.patrol import tools as patrol_tools
from hermit.plugins.builtin.hooks.patrol.models import PatrolCheckResult, PatrolReport
from hermit.plugins.builtin.hooks.patrol.tools import (
    _handle_patrol_run,
    _handle_patrol_status,
    register,
    set_engine,
)


class TestSetEngine:
    def setup_method(self) -> None:
        patrol_tools._engine = None

    def test_set_engine(self) -> None:
        mock_engine = MagicMock()
        set_engine(mock_engine)
        assert patrol_tools._engine is mock_engine
        patrol_tools._engine = None


class TestHandlePatrolRun:
    def setup_method(self) -> None:
        patrol_tools._engine = None

    def teardown_method(self) -> None:
        patrol_tools._engine = None

    def test_no_engine(self) -> None:
        result = _handle_patrol_run({})
        assert result == "Patrol engine is not running."

    def test_clean_report(self) -> None:
        report = PatrolReport(
            checks=[
                PatrolCheckResult(check_name="lint", status="clean", summary="ok", issue_count=0),
            ],
            total_issues=0,
        )
        mock_engine = MagicMock()
        mock_engine.run_patrol.return_value = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_run({})
        assert "0 issue(s) found" in result
        assert "lint: clean" in result

    def test_issues_with_messages(self) -> None:
        issues: list[dict[str, Any]] = [
            {"message": "Line too long"},
            {"message": "Unused import"},
        ]
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="lint",
                    status="issues_found",
                    summary="2 issues",
                    issue_count=2,
                    issues=issues,
                ),
            ],
            total_issues=2,
        )
        mock_engine = MagicMock()
        mock_engine.run_patrol.return_value = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_run({})
        assert "2 issue(s) found" in result
        assert "Line too long" in result
        assert "Unused import" in result

    def test_issues_with_text_fallback(self) -> None:
        issues: list[dict[str, Any]] = [
            {"text": "# TODO: fix me"},
        ]
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="todo_scan",
                    status="issues_found",
                    summary="1 marker",
                    issue_count=1,
                    issues=issues,
                ),
            ],
            total_issues=1,
        )
        mock_engine = MagicMock()
        mock_engine.run_patrol.return_value = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_run({})
        assert "TODO: fix me" in result

    def test_issues_truncated_beyond_five(self) -> None:
        issues: list[dict[str, Any]] = [{"message": f"issue {i}"} for i in range(8)]
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="lint",
                    status="issues_found",
                    summary="8 issues",
                    issue_count=8,
                    issues=issues,
                ),
            ],
            total_issues=8,
        )
        mock_engine = MagicMock()
        mock_engine.run_patrol.return_value = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_run({})
        assert "... and 3 more" in result

    def test_issue_str_fallback(self) -> None:
        """When neither 'message' nor 'text' is present, str(issue) is used."""
        issues: list[dict[str, Any]] = [{"code": "E501"}]
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="lint",
                    status="issues_found",
                    summary="1 issue",
                    issue_count=1,
                    issues=issues,
                ),
            ],
            total_issues=1,
        )
        mock_engine = MagicMock()
        mock_engine.run_patrol.return_value = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_run({})
        assert "E501" in result


class TestHandlePatrolStatus:
    def setup_method(self) -> None:
        patrol_tools._engine = None

    def teardown_method(self) -> None:
        patrol_tools._engine = None

    def test_no_engine(self) -> None:
        result = _handle_patrol_status({})
        assert result == "Patrol engine is not running."

    def test_no_report_yet(self) -> None:
        mock_engine = MagicMock()
        mock_engine.last_report = None
        patrol_tools._engine = mock_engine

        result = _handle_patrol_status({})
        assert result == "No patrol report available yet."

    def test_with_report(self) -> None:
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="lint",
                    status="issues_found",
                    summary="2 issues",
                    issue_count=2,
                ),
                PatrolCheckResult(
                    check_name="test",
                    status="clean",
                    summary="ok",
                    issue_count=0,
                ),
            ],
            total_issues=2,
            started_at=1000.0,
            finished_at=1005.5,
        )
        mock_engine = MagicMock()
        mock_engine.last_report = report
        patrol_tools._engine = mock_engine

        result = _handle_patrol_status({})
        assert "2 issue(s)" in result
        assert "2 check(s)" in result
        assert "5.5s" in result
        assert "lint: issues_found" in result
        assert "test: clean" in result


class TestRegister:
    def test_registers_two_tools(self) -> None:
        ctx = MagicMock()
        register(ctx)
        assert ctx.add_tool.call_count == 2
        tool_names = [call.args[0].name for call in ctx.add_tool.call_args_list]
        assert "patrol_run" in tool_names
        assert "patrol_status" in tool_names
