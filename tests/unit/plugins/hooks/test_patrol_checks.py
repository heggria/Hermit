"""Tests for patrol check implementations — covers missing lines in checks.py."""

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

# ---------------------------------------------------------------------------
# LintCheck — missing lines: 53-54 (JSONDecodeError), 75-76 (generic exception)
# ---------------------------------------------------------------------------


class TestLintCheckEdgeCases:
    def test_json_decode_error_returns_clean(self) -> None:
        """Non-JSON stdout should be swallowed; empty issues -> clean."""
        mock_result = MagicMock()
        mock_result.stdout = "not valid json at all"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = LintCheck().run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_generic_exception_returns_error(self) -> None:
        """Unexpected exceptions should be caught and returned as error."""
        with patch("subprocess.run", side_effect=OSError("disk error")):
            result = LintCheck().run("/workspace")
        assert result.status == "error"
        assert "disk error" in result.summary


# ---------------------------------------------------------------------------
# TestCheck — missing lines: 85-120
# ---------------------------------------------------------------------------


class TestTestCheck:
    def test_all_pass(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "5 passed\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = TestCheck().run("/workspace")
        assert result.status == "clean"
        assert "5" in result.summary

    def test_some_failures(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "3 passed, 2 failed\n"
        mock_result.stderr = ""
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = TestCheck().run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 2
        assert "2" in result.summary
        assert "3" in result.summary

    def test_pytest_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("pytest")):
            result = TestCheck().run("/workspace")
        assert result.status == "error"
        assert "pytest not found" in result.summary

    def test_generic_exception(self) -> None:
        with patch("subprocess.run", side_effect=RuntimeError("timeout expired")):
            result = TestCheck().run("/workspace")
        assert result.status == "error"
        assert "timeout expired" in result.summary


# ---------------------------------------------------------------------------
# TodoScanCheck — missing lines: 147 (non-.py skip), 151-152 (OSError)
# ---------------------------------------------------------------------------


class TestTodoScanCheckEdgeCases:
    def test_skips_non_python_files(self, tmp_path: Path) -> None:
        """Non-.py files should be skipped even if they contain TODO markers."""
        (tmp_path / "readme.md").write_text("# TODO: document this\n")
        (tmp_path / "clean.py").write_text("x = 1\n")
        result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_oserror_reading_file(self, tmp_path: Path) -> None:
        """Files that raise OSError on read should be silently skipped."""
        py_file = tmp_path / "unreadable.py"
        py_file.write_text("# TODO: something\n")

        original_read = Path.read_text

        def patched_read(self: Path, *a: Any, **kw: Any) -> str:
            if self.name == "unreadable.py":
                raise OSError("Permission denied")
            return original_read(self, *a, **kw)

        with patch.object(Path, "read_text", patched_read):
            result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "clean"

    def test_generic_exception_returns_error(self, tmp_path: Path) -> None:
        """Unexpected exception in TodoScanCheck -> error result."""
        with patch("os.walk", side_effect=RuntimeError("walk failed")):
            result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "error"
        assert "walk failed" in result.summary


# ---------------------------------------------------------------------------
# CoverageCheck — missing lines: 188-227
# ---------------------------------------------------------------------------


class TestCoverageCheck:
    def test_coverage_above_threshold(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "TOTAL    1234    100    92%\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = CoverageCheck().run("/workspace")
        assert result.status == "clean"
        assert "92%" in result.summary
        assert result.issue_count == 0

    def test_coverage_below_threshold(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "TOTAL    1234    800    35%\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = CoverageCheck().run("/workspace")
        assert result.status == "issues_found"
        assert "35%" in result.summary
        assert result.issue_count == 1

    def test_cannot_parse_coverage(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "some unrelated output\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = CoverageCheck().run("/workspace")
        assert result.status == "error"
        assert "Could not parse" in result.summary

    def test_pytest_cov_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("pytest")):
            result = CoverageCheck().run("/workspace")
        assert result.status == "error"
        assert "pytest-cov" in result.summary

    def test_generic_exception(self) -> None:
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            result = CoverageCheck().run("/workspace")
        assert result.status == "error"
        assert "boom" in result.summary


# ---------------------------------------------------------------------------
# SecurityCheck — missing lines: 236-283
# ---------------------------------------------------------------------------


class TestSecurityCheck:
    def test_no_vulnerabilities(self) -> None:
        audit_output = json.dumps(
            {"dependencies": [{"name": "requests", "version": "2.28.0", "vulns": []}]}
        )
        mock_result = MagicMock()
        mock_result.stdout = audit_output
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = SecurityCheck().run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_vulnerabilities_found(self) -> None:
        audit_output = json.dumps(
            {
                "dependencies": [
                    {
                        "name": "requests",
                        "version": "2.25.0",
                        "vulns": [
                            {"id": "CVE-2023-1234", "fix_versions": ["2.28.0"]},
                            {"id": "CVE-2023-5678", "fix_versions": ["2.28.0"]},
                        ],
                    }
                ]
            }
        )
        mock_result = MagicMock()
        mock_result.stdout = audit_output
        mock_result.stderr = ""
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = SecurityCheck().run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 2
        assert result.issues[0]["package"] == "requests"
        assert result.issues[0]["vuln_id"] == "CVE-2023-1234"

    def test_empty_stdout(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = SecurityCheck().run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_json_decode_error(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "not json {{"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = SecurityCheck().run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_pip_audit_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("pip-audit")):
            result = SecurityCheck().run("/workspace")
        assert result.status == "error"
        assert "pip-audit not found" in result.summary

    def test_generic_exception(self) -> None:
        with patch("subprocess.run", side_effect=OSError("network error")):
            result = SecurityCheck().run("/workspace")
        assert result.status == "error"
        assert "network error" in result.summary
