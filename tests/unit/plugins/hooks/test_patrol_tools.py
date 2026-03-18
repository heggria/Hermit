"""Tests for patrol plugin tools — patrol_run and patrol_status handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.patrol import tools as tools_mod
from hermit.plugins.builtin.hooks.patrol.models import PatrolCheckResult, PatrolReport
from hermit.plugins.builtin.hooks.patrol.tools import (
    _handle_patrol_run,
    _handle_patrol_status,
    register,
    set_engine,
)


class TestSetEngine:
    def setup_method(self) -> None:
        tools_mod._engine = None

    def test_set_engine_stores_reference(self) -> None:
        engine = MagicMock()
        set_engine(engine)
        assert tools_mod._engine is engine


class TestHandlePatrolRun:
    def setup_method(self) -> None:
        tools_mod._engine = None

    def test_no_engine_returns_message(self) -> None:
        result = _handle_patrol_run({})
        assert result == "Patrol engine is not running."

    def test_clean_report(self) -> None:
        engine = MagicMock()
        engine.run_patrol.return_value = PatrolReport(
            checks=[PatrolCheckResult(check_name="lint", status="clean", summary="ok")],
            total_issues=0,
        )
        tools_mod._engine = engine
        result = _handle_patrol_run({})
        assert "0 issue(s) found" in result

    def test_issues_with_message_key(self) -> None:
        issues: list[dict[str, Any]] = [
            {"file": "a.py", "line": 1, "message": "Line too long"},
            {"file": "b.py", "line": 2, "message": "Unused import"},
        ]
        engine = MagicMock()
        engine.run_patrol.return_value = PatrolReport(
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
        tools_mod._engine = engine
        result = _handle_patrol_run({})
        assert "2 issue(s) found" in result
        assert "Line too long" in result

    def test_issues_with_text_key(self) -> None:
        issues: list[dict[str, Any]] = [
            {"file": "a.py", "line": 1, "text": "# TODO: fix this"},
        ]
        engine = MagicMock()
        engine.run_patrol.return_value = PatrolReport(
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
        tools_mod._engine = engine
        result = _handle_patrol_run({})
        assert "# TODO: fix this" in result

    def test_issues_truncated_beyond_five(self) -> None:
        issues: list[dict[str, Any]] = [{"message": f"Issue {i}"} for i in range(8)]
        engine = MagicMock()
        engine.run_patrol.return_value = PatrolReport(
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
        tools_mod._engine = engine
        result = _handle_patrol_run({})
        assert "... and 3 more" in result

    def test_issues_fallback_to_str(self) -> None:
        issues: list[dict[str, Any]] = [{"code": "E501", "line": 10}]
        engine = MagicMock()
        engine.run_patrol.return_value = PatrolReport(
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
        tools_mod._engine = engine
        result = _handle_patrol_run({})
        assert "E501" in result


class TestHandlePatrolStatus:
    def setup_method(self) -> None:
        tools_mod._engine = None

    def test_no_engine_returns_message(self) -> None:
        result = _handle_patrol_status({})
        assert result == "Patrol engine is not running."

    def test_no_report_yet(self) -> None:
        engine = MagicMock()
        engine.last_report = None
        tools_mod._engine = engine
        result = _handle_patrol_status({})
        assert result == "No patrol report available yet."

    def test_with_report(self) -> None:
        report = PatrolReport(
            checks=[
                PatrolCheckResult(
                    check_name="lint", status="issues_found", summary="3", issue_count=3
                ),
                PatrolCheckResult(check_name="test", status="clean", summary="ok", issue_count=0),
            ],
            total_issues=3,
            started_at=1000.0,
            finished_at=1002.5,
        )
        engine = MagicMock()
        engine.last_report = report
        tools_mod._engine = engine
        result = _handle_patrol_status({})
        assert "3 issue(s)" in result
        assert "2 check(s) run" in result


class TestRegister:
    def test_register_adds_two_tools(self) -> None:
        ctx = MagicMock()
        register(ctx)
        assert ctx.add_tool.call_count == 2
        names = {call.args[0].name for call in ctx.add_tool.call_args_list}
        assert names == {"patrol_run", "patrol_status"}
