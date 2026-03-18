"""Tests for patrol check implementations — LintCheck, TestCheck, TodoScanCheck, etc."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.patrol.checks import (
    CoverageCheck,
    LintCheck,
    SecurityCheck,
    TestCheck,
    TodoScanCheck,
)


class TestLintCheck:
    """Cover checks.py LintCheck lines 31-76."""

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_no_issues(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="[]", stderr="", returncode=0)
        check = LintCheck()
        result = check.run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_with_issues(self, mock_run: MagicMock) -> None:
        issues: list[dict[str, Any]] = [
            {
                "filename": "a.py",
                "location": {"row": 10, "column": 1},
                "code": "E501",
                "message": "Line too long",
            }
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(issues), stderr="", returncode=1)
        check = LintCheck()
        result = check.run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 1
        assert result.issues[0]["code"] == "E501"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_ruff_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("ruff not found")
        check = LintCheck()
        result = check.run("/workspace")
        assert result.status == "error"
        assert "ruff not found" in result.summary

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_malformed_json(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="not json", stderr="", returncode=1)
        check = LintCheck()
        result = check.run("/workspace")
        assert result.status == "clean"  # malformed json => no issues parsed

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_general_exception(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = RuntimeError("timeout")
        check = LintCheck()
        result = check.run("/workspace")
        assert result.status == "error"


class TestTestCheck:
    """Cover checks.py TestCheck lines 84-120."""

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_all_passed(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="5 passed\n", stderr="", returncode=0)
        check = TestCheck()
        result = check.run("/workspace")
        assert result.status == "clean"
        assert "5" in result.summary

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_some_failed(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="3 passed, 2 failed\n", stderr="", returncode=1)
        check = TestCheck()
        result = check.run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 2

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_pytest_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("pytest not found")
        check = TestCheck()
        result = check.run("/workspace")
        assert result.status == "error"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_general_exception(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = RuntimeError("boom")
        check = TestCheck()
        result = check.run("/workspace")
        assert result.status == "error"


class TestTodoScanCheck:
    """Cover checks.py TodoScanCheck lines 128-179."""

    def test_no_workspace(self, tmp_path: Path) -> None:
        check = TodoScanCheck()
        result = check.run(str(tmp_path / "nonexistent"))
        assert result.status == "error"

    def test_clean_workspace(self, tmp_path: Path) -> None:
        (tmp_path / "clean.py").write_text("x = 1\n")
        check = TodoScanCheck()
        result = check.run(str(tmp_path))
        assert result.status == "clean"

    def test_todo_found(self, tmp_path: Path) -> None:
        (tmp_path / "dirty.py").write_text("# TODO: fix this\nx = 1\n# FIXME: also this\n")
        check = TodoScanCheck()
        result = check.run(str(tmp_path))
        assert result.status == "issues_found"
        assert result.issue_count == 2

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("# TODO: hidden todo\n")
        check = TodoScanCheck()
        result = check.run(str(tmp_path))
        assert result.status == "clean"

    def test_skips_pycache(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "cached.py").write_text("# TODO: in cache\n")
        check = TodoScanCheck()
        result = check.run(str(tmp_path))
        assert result.status == "clean"


class TestCoverageCheck:
    """Cover checks.py CoverageCheck lines 187-227."""

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_high_coverage(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout="TOTAL    1000    100    90%\n", stderr="", returncode=0
        )
        check = CoverageCheck()
        result = check.run("/workspace")
        assert result.status == "clean"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_low_coverage(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout="TOTAL    1000    500    50%\n", stderr="", returncode=0
        )
        check = CoverageCheck()
        result = check.run("/workspace")
        assert result.status == "issues_found"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_unparseable_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="no coverage data\n", stderr="", returncode=0)
        check = CoverageCheck()
        result = check.run("/workspace")
        assert result.status == "error"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_pytest_cov_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        check = CoverageCheck()
        result = check.run("/workspace")
        assert result.status == "error"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_general_exception(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = RuntimeError("boom")
        check = CoverageCheck()
        result = check.run("/workspace")
        assert result.status == "error"


class TestSecurityCheck:
    """Cover checks.py SecurityCheck lines 235-283."""

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_no_vulnerabilities(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"dependencies": []}), stderr="", returncode=0
        )
        check = SecurityCheck()
        result = check.run("/workspace")
        assert result.status == "clean"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_with_vulnerabilities(self, mock_run: MagicMock) -> None:
        data = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.20.0",
                    "vulns": [{"id": "CVE-2023-1234", "fix_versions": ["2.31.0"]}],
                }
            ]
        }
        mock_run.return_value = MagicMock(stdout=json.dumps(data), stderr="", returncode=1)
        check = SecurityCheck()
        result = check.run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 1

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_pip_audit_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        check = SecurityCheck()
        result = check.run("/workspace")
        assert result.status == "error"

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_malformed_json(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="not valid json", stderr="", returncode=1)
        check = SecurityCheck()
        result = check.run("/workspace")
        assert result.status == "clean"  # no vulns parsed

    @patch("hermit.plugins.builtin.hooks.patrol.checks.subprocess.run")
    def test_general_exception(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = RuntimeError("boom")
        check = SecurityCheck()
        result = check.run("/workspace")
        assert result.status == "error"
