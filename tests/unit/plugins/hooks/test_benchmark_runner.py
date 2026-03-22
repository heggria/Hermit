"""Tests for BenchmarkRunner — quality checks and regression detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkErrorDetail, BenchmarkResult
from hermit.plugins.builtin.hooks.benchmark.runner import (
    BenchmarkRunner,
    _collect_error_details,
    _detect_regression,
    _parse_coverage,
    _parse_lint_errors,
    _parse_pytest,
    _parse_ruff,
    _parse_test_failures,
    _parse_typecheck_errors,
)


@pytest.fixture()
def store() -> MagicMock:
    s = MagicMock()
    s.list_tasks.return_value = []
    return s


@pytest.fixture()
def runner(store: MagicMock) -> BenchmarkRunner:
    return BenchmarkRunner(store=store, timeout=10)


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestParsePytest:
    def test_all_passed(self) -> None:
        assert _parse_pytest("42 passed in 3.5s") == (42, 42)

    def test_passed_and_failed(self) -> None:
        assert _parse_pytest("10 passed, 2 failed") == (12, 10)

    def test_no_match(self) -> None:
        assert _parse_pytest("no test output") == (0, 0)

    def test_case_insensitive(self) -> None:
        assert _parse_pytest("5 Passed") == (5, 5)


class TestParseCoverage:
    def test_valid(self) -> None:
        output = "TOTAL   500    50    90.0%"
        assert _parse_coverage(output) == 90.0

    def test_decimal(self) -> None:
        assert _parse_coverage("TOTAL   100    5    95.5%") == 95.5

    def test_no_match(self) -> None:
        assert _parse_coverage("nothing here") == 0.0


class TestParseRuff:
    def test_found_errors(self) -> None:
        assert _parse_ruff("Found 7 errors") == 7

    def test_no_errors(self) -> None:
        assert _parse_ruff("All checks passed") == 0


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestDetectRegression:
    def test_no_baseline(self) -> None:
        regression, compared = _detect_regression(10, 80.0, 0, None)
        assert regression is False
        assert compared == {}

    def test_no_regression(self) -> None:
        baseline = {"test_total": 10, "coverage": 80.0, "lint_violations": 0}
        regression, compared = _detect_regression(12, 85.0, 0, baseline)
        assert regression is False
        assert compared["test_total_delta"] == 2
        assert compared["coverage_delta"] == 5.0

    def test_test_count_drop(self) -> None:
        baseline = {"test_total": 20, "coverage": 80.0, "lint_violations": 0}
        regression, _ = _detect_regression(15, 80.0, 0, baseline)
        assert regression is True

    def test_coverage_drop(self) -> None:
        baseline = {"test_total": 10, "coverage": 85.0, "lint_violations": 0}
        regression, _ = _detect_regression(10, 84.0, 0, baseline)
        assert regression is True

    def test_lint_increase(self) -> None:
        baseline = {"test_total": 10, "coverage": 80.0, "lint_violations": 2}
        regression, _ = _detect_regression(10, 80.0, 5, baseline)
        assert regression is True

    def test_minor_coverage_change_ok(self) -> None:
        baseline = {"test_total": 10, "coverage": 80.0, "lint_violations": 0}
        # 0.3% drop is within the 0.5 tolerance
        regression, _ = _detect_regression(10, 79.7, 0, baseline)
        assert regression is False


# ---------------------------------------------------------------------------
# Runner integration (mocked subprocess)
# ---------------------------------------------------------------------------


class TestBenchmarkRunner:
    @pytest.mark.asyncio()
    async def test_successful_run(self, runner: BenchmarkRunner) -> None:
        output = "42 passed in 5.0s\nTOTAL   200    10    95.0%\n"
        with patch.object(runner, "_exec", return_value=(output, 0)):
            result = await runner.run("iter-1", "spec-1")

        assert isinstance(result, BenchmarkResult)
        assert result.check_passed is True
        assert result.test_total == 42
        assert result.test_passed == 42
        assert result.coverage == 95.0
        assert result.iteration_id == "iter-1"
        assert result.spec_id == "spec-1"

    @pytest.mark.asyncio()
    async def test_failed_run(self, runner: BenchmarkRunner) -> None:
        output = "5 passed, 3 failed\nFound 2 errors\n"
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-2", "spec-2")

        assert result.check_passed is False
        assert result.test_total == 8
        assert result.test_passed == 5
        assert result.lint_violations == 2

    @pytest.mark.asyncio()
    async def test_timeout_returns_failure(self, runner: BenchmarkRunner) -> None:
        async def slow_exec(cmd: str, cwd: str) -> tuple[str, int]:
            return "", 1

        with patch.object(runner, "_exec", side_effect=slow_exec):
            result = await runner.run("iter-3", "spec-3")
        assert result.check_passed is False
        assert result.test_total == 0

    @pytest.mark.asyncio()
    async def test_result_is_frozen(self, runner: BenchmarkRunner) -> None:
        with patch.object(runner, "_exec", return_value=("1 passed", 0)):
            result = await runner.run("iter-4", "spec-4")
        with pytest.raises(AttributeError):
            result.check_passed = False  # type: ignore[misc]

    @pytest.mark.asyncio()
    async def test_successful_run_has_empty_error_details(self, runner: BenchmarkRunner) -> None:
        output = "42 passed in 5.0s\nTOTAL   200    10    95.0%\n"
        with patch.object(runner, "_exec", return_value=(output, 0)):
            result = await runner.run("iter-5", "spec-5")

        assert result.check_passed is True
        assert result.error_details == ()
        assert result.raw_output == ""

    @pytest.mark.asyncio()
    async def test_failed_run_populates_error_details(self, runner: BenchmarkRunner) -> None:
        output = (
            "src/foo.py:10:5: E501 line too long\n"
            "Found 1 error\n"
            "5 passed, 3 failed\n"
            "FAILED tests/test_a.py::test_one\n"
            "FAILED tests/test_b.py::test_two\n"
        )
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-6", "spec-6")

        assert result.check_passed is False
        assert len(result.error_details) >= 1
        categories = {d.category for d in result.error_details}
        assert "lint" in categories
        assert "test_failure" in categories
        assert len(result.raw_output) > 0

    @pytest.mark.asyncio()
    async def test_raw_output_truncated(self, runner: BenchmarkRunner) -> None:
        output = "x" * 5000 + "\nFound 1 error\n"
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-7", "spec-7")

        assert result.check_passed is False
        assert len(result.raw_output) == 2000


# ---------------------------------------------------------------------------
# Structured error detail parsers
# ---------------------------------------------------------------------------


class TestParseTypecheckErrors:
    def test_pyright_errors(self) -> None:
        output = (
            'src/foo.py:10:5: error: Type "int" is not assignable\n'
            "src/bar.py:20:10: error: Argument missing\n"
        )
        detail = _parse_typecheck_errors(output)
        assert detail is not None
        assert detail.category == "typecheck"
        assert detail.count == 2
        assert "src/foo.py" in detail.file_paths
        assert "src/bar.py" in detail.file_paths

    def test_mypy_errors(self) -> None:
        output = "src/baz.py:5: error: Incompatible types in assignment\n"
        detail = _parse_typecheck_errors(output)
        assert detail is not None
        assert detail.category == "typecheck"
        assert detail.count == 1
        assert "src/baz.py" in detail.file_paths

    def test_summary_only(self) -> None:
        output = "3 errors found in 2 files"
        detail = _parse_typecheck_errors(output)
        assert detail is not None
        assert detail.count == 3
        assert detail.file_paths == ()

    def test_no_typecheck_errors(self) -> None:
        output = "All checks passed"
        assert _parse_typecheck_errors(output) is None

    def test_deduplicates_file_paths(self) -> None:
        output = "src/foo.py:10:5: error: Type mismatch\nsrc/foo.py:20:5: error: Another error\n"
        detail = _parse_typecheck_errors(output)
        assert detail is not None
        assert detail.file_paths == ("src/foo.py",)
        assert detail.count == 2


class TestParseTestFailures:
    def test_failed_lines(self) -> None:
        output = (
            "FAILED tests/test_a.py::test_one - AssertionError\n"
            "FAILED tests/test_b.py::test_two - ValueError\n"
        )
        detail = _parse_test_failures(output)
        assert detail is not None
        assert detail.category == "test_failure"
        assert detail.count == 2
        assert "tests/test_a.py::test_one" in detail.file_paths
        assert "tests/test_b.py::test_two" in detail.file_paths

    def test_no_failures(self) -> None:
        output = "42 passed in 5.0s"
        assert _parse_test_failures(output) is None

    def test_summary_trimmed_to_500(self) -> None:
        lines = [f"FAILED tests/test_{i}.py::test_fn" for i in range(50)]
        output = "\n".join(lines)
        detail = _parse_test_failures(output)
        assert detail is not None
        # Summary capped at 500 chars, but up to 10 lines collected
        assert len(detail.summary) <= 500


class TestParseLintErrors:
    def test_ruff_violations(self) -> None:
        output = (
            "src/foo.py:10:5: E501 line too long\n"
            "src/bar.py:20:1: F401 unused import\n"
            "Found 2 errors\n"
        )
        detail = _parse_lint_errors(output)
        assert detail is not None
        assert detail.category == "lint"
        assert detail.count == 2
        assert "src/foo.py" in detail.file_paths
        assert "src/bar.py" in detail.file_paths

    def test_no_lint_errors(self) -> None:
        output = "All checks passed"
        assert _parse_lint_errors(output) is None

    def test_zero_errors_returns_none(self) -> None:
        output = "Found 0 errors"
        assert _parse_lint_errors(output) is None


class TestCollectErrorDetails:
    def test_multiple_categories(self) -> None:
        output = (
            "src/mod.py:5:1: error: Type mismatch\n"
            "FAILED tests/test_x.py::test_y\n"
            "src/mod.py:10:5: E501 line too long\n"
            "Found 1 error\n"
        )
        details = _collect_error_details(output, lint_violations=1)
        categories = {d.category for d in details}
        assert "typecheck" in categories
        assert "test_failure" in categories
        assert "lint" in categories

    def test_empty_output(self) -> None:
        details = _collect_error_details("", lint_violations=0)
        assert details == ()

    def test_returns_tuple(self) -> None:
        details = _collect_error_details("no errors", lint_violations=0)
        assert isinstance(details, tuple)


class TestBenchmarkErrorDetailModel:
    def test_frozen(self) -> None:
        detail = BenchmarkErrorDetail(category="lint", count=1, summary="test")
        with pytest.raises(AttributeError):
            detail.count = 2  # type: ignore[misc]

    def test_default_file_paths(self) -> None:
        detail = BenchmarkErrorDetail(category="other", count=0, summary="")
        assert detail.file_paths == ()
