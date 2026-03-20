"""Tests for GovernedReviewer — thin wrapper over PatrolEngine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.quality.models import (
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
)
from hermit.plugins.builtin.hooks.quality.reviewer import (
    GovernedReviewer,
    _check_imports,
    _check_init_files,
    _check_naming,
)


class TestCheckImports:
    def test_valid_import_no_findings(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        (pkg / "utils.py").write_text("x = 1\n")
        source = pkg / "main.py"
        source.write_text("from .utils import x\n")
        findings = _check_imports(str(source), str(tmp_path))
        assert findings == []

    def test_unresolved_relative_import(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        source = pkg / "main.py"
        source.write_text("from .nonexistent import foo\n")
        findings = _check_imports(str(source), str(tmp_path))
        assert len(findings) == 1
        assert findings[0].category == "import"
        assert findings[0].severity == FindingSeverity.WARNING

    def test_syntax_error_no_crash(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def foo(\n")
        findings = _check_imports(str(source), str(tmp_path))
        assert findings == []


class TestCheckNaming:
    def test_snake_case_function_ok(self, tmp_path: Path) -> None:
        source = tmp_path / "good.py"
        source.write_text("def my_function():\n    pass\n")
        findings = _check_naming(str(source))
        assert findings == []

    def test_non_snake_case_function(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def MyFunction():\n    pass\n")
        findings = _check_naming(str(source))
        assert len(findings) == 1
        assert findings[0].category == "naming"
        assert "snake_case" in findings[0].message

    def test_lowercase_class_name(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.py"
        source.write_text("class myClass:\n    pass\n")
        findings = _check_naming(str(source))
        assert len(findings) == 1
        assert "uppercase" in findings[0].message

    def test_private_names_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "ok.py"
        source.write_text("def _helper():\n    pass\nclass _Internal:\n    pass\n")
        findings = _check_naming(str(source))
        assert findings == []


class TestCheckInitFiles:
    def test_missing_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        source = pkg / "module.py"
        source.write_text("x = 1\n")
        findings = _check_init_files([str(source)])
        assert len(findings) == 1
        assert findings[0].category == "init"

    def test_init_present(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        source = pkg / "module.py"
        source.write_text("x = 1\n")
        findings = _check_init_files([str(source)])
        assert findings == []


class TestGovernedReviewer:
    @pytest.mark.asyncio()
    async def test_review_empty_files(self) -> None:
        reviewer = GovernedReviewer()
        report = await reviewer.review([])
        assert isinstance(report, ReviewReport)
        assert report.passed is True
        assert report.findings == ()

    @pytest.mark.asyncio()
    async def test_review_with_naming_issue(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def BadName():\n    pass\n")
        reviewer = GovernedReviewer(workspace_root=str(tmp_path))
        with patch("hermit.plugins.builtin.hooks.quality.reviewer.PatrolEngine") as mock_patrol:
            mock_report = MagicMock()
            mock_report.checks = []
            mock_patrol.return_value.run_patrol.return_value = mock_report
            report = await reviewer.review([str(source)])
        assert len(report.findings) >= 1
        naming_findings = [f for f in report.findings if f.category == "naming"]
        assert len(naming_findings) == 1

    def test_report_immutable(self) -> None:
        report = ReviewReport(findings=(), passed=True, duration_seconds=0.1)
        try:
            report.passed = False  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_finding_immutable(self) -> None:
        finding = ReviewFinding(
            severity=FindingSeverity.WARNING,
            category="test",
            message="msg",
        )
        try:
            finding.message = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass
