"""Tests for BenchmarkRunner — quality checks and regression detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkErrorDetail, BenchmarkResult
from hermit.plugins.builtin.hooks.benchmark.runner import (
    BenchmarkRunner,
    _collect_error_details,
    _count_lint_violations,
    _count_typecheck_errors,
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


# ---------------------------------------------------------------------------
# Delta comparison helpers
# ---------------------------------------------------------------------------


class TestCountTypecheckErrors:
    def test_summary_line(self) -> None:
        output = "some output\n1337 errors found in 200 files\nmore output"
        assert _count_typecheck_errors(output) == 1337

    def test_summary_line_singular(self) -> None:
        output = "1 error found in 1 file"
        assert _count_typecheck_errors(output) == 1

    def test_fallback_pyright_lines(self) -> None:
        output = "src/foo.py:10:5: error: Type mismatch\nsrc/bar.py:20:3: error: Missing arg\n"
        assert _count_typecheck_errors(output) == 2

    def test_no_errors(self) -> None:
        output = "All checks passed"
        assert _count_typecheck_errors(output) == 0


class TestCountLintViolations:
    def test_found_errors(self) -> None:
        output = "Found 7 errors"
        assert _count_lint_violations(output) == 7

    def test_no_errors(self) -> None:
        output = "All checks passed"
        assert _count_lint_violations(output) == 0


# ---------------------------------------------------------------------------
# Delta comparison in BenchmarkRunner
# ---------------------------------------------------------------------------


class TestBenchmarkRunnerDeltaComparison:
    @pytest.mark.asyncio()
    async def test_delta_passes_with_preexisting_errors(self, runner: BenchmarkRunner) -> None:
        """Pre-existing errors should not cause failure when baseline is provided."""
        output = "42 passed in 5.0s\nTOTAL   200    10    95.0%\n1337 errors found in 200 files\n"
        baseline = {"typecheck_errors": 1337, "lint_violations": 0}
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d1", "spec-d1", baseline_metrics=baseline)

        assert result.check_passed is True
        assert result.delta_info["new_typecheck"] == 0
        assert result.delta_info["baseline_typecheck"] == 1337
        assert result.delta_info["current_typecheck"] == 1337

    @pytest.mark.asyncio()
    async def test_delta_fails_when_new_errors_introduced(self, runner: BenchmarkRunner) -> None:
        """New errors beyond the baseline should cause failure."""
        output = "42 passed in 5.0s\n1340 errors found in 200 files\n"
        baseline = {"typecheck_errors": 1337, "lint_violations": 0}
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d2", "spec-d2", baseline_metrics=baseline)

        assert result.check_passed is False
        assert result.delta_info["new_typecheck"] == 3

    @pytest.mark.asyncio()
    async def test_delta_fails_on_new_lint_violations(self, runner: BenchmarkRunner) -> None:
        """New lint violations beyond baseline should cause failure."""
        output = "42 passed in 5.0s\nFound 5 errors\n"
        baseline = {"typecheck_errors": 0, "lint_violations": 3}
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d3", "spec-d3", baseline_metrics=baseline)

        assert result.check_passed is False
        assert result.delta_info["new_lint"] == 2

    @pytest.mark.asyncio()
    async def test_delta_fails_on_test_failures(self, runner: BenchmarkRunner) -> None:
        """Test failures should always cause failure even with baseline."""
        output = "5 passed, 3 failed\n"
        baseline = {"typecheck_errors": 0, "lint_violations": 0}
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d4", "spec-d4", baseline_metrics=baseline)

        assert result.check_passed is False
        assert result.delta_info["test_passed"] == 5
        assert result.delta_info["test_total"] == 8

    @pytest.mark.asyncio()
    async def test_no_baseline_uses_absolute_check(self, runner: BenchmarkRunner) -> None:
        """Without baseline_metrics, fall back to absolute returncode check."""
        output = "42 passed in 5.0s\n1337 errors found\n"
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d5", "spec-d5")

        assert result.check_passed is False
        assert result.delta_info == {}

    @pytest.mark.asyncio()
    async def test_delta_tolerates_fewer_errors(self, runner: BenchmarkRunner) -> None:
        """If errors decreased from baseline, that is a pass."""
        output = "42 passed in 5.0s\n1330 errors found in 200 files\n"
        baseline = {"typecheck_errors": 1337, "lint_violations": 0}
        with patch.object(runner, "_exec", return_value=(output, 1)):
            result = await runner.run("iter-d6", "spec-d6", baseline_metrics=baseline)

        assert result.check_passed is True
        assert result.delta_info["new_typecheck"] == 0
        assert result.delta_info["current_typecheck"] == 1330

    @pytest.mark.asyncio()
    async def test_delta_info_stored_in_result(self, runner: BenchmarkRunner) -> None:
        """delta_info should be populated in the result."""
        output = "42 passed in 5.0s\n"
        baseline = {"typecheck_errors": 10, "lint_violations": 5}
        with patch.object(runner, "_exec", return_value=(output, 0)):
            result = await runner.run("iter-d7", "spec-d7", baseline_metrics=baseline)

        assert "baseline_typecheck" in result.delta_info
        assert "baseline_lint" in result.delta_info
        assert result.delta_info["baseline_typecheck"] == 10
        assert result.delta_info["baseline_lint"] == 5


# ---------------------------------------------------------------------------
# Tiered benchmark strategy
# ---------------------------------------------------------------------------


class TestBenchmarkRunnerTieredStrategy:
    @pytest.fixture(autouse=True)
    def _fake_file_exists(self) -> None:
        """Changed files are synthetic in tests — skip the existence check."""
        with patch("hermit.plugins.builtin.hooks.benchmark.runner.os.path.exists", return_value=True):
            yield

    @pytest.mark.asyncio()
    async def test_tiered_all_tiers_pass(self, runner: BenchmarkRunner) -> None:
        """When all tiers pass, tier_reached should be tier3_full."""
        calls: list[str] = []

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            calls.append(cmd)
            if "ruff" in cmd:
                return "All checks passed!\n", 0
            if "pytest" in cmd and "tests/unit/" in cmd:
                return "100 passed in 10.0s\n", 0
            if "pytest" in cmd:
                return "5 passed in 1.0s\n", 0
            if cmd == "make test":
                return "200 passed in 30.0s\n", 0
            return "", 0

        changed = ["src/hermit/foo.py", "tests/unit/test_foo.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t1", "spec-t1", changed_files=changed)

        assert result.check_passed is True
        assert result.tier_reached == "tier3_full"
        assert result.strategy_used == "tiered"
        assert any("ruff" in c for c in calls)
        assert any("make test" in c for c in calls)

    @pytest.mark.asyncio()
    async def test_tiered_fails_at_tier1_lint(self, runner: BenchmarkRunner) -> None:
        """Lint failure on changed files stops at tier1."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if "ruff" in cmd:
                return "src/hermit/foo.py:10:5: E501 line too long\nFound 1 error\n", 1
            return "", 0

        changed = ["src/hermit/foo.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t2", "spec-t2", changed_files=changed)

        assert result.check_passed is False
        assert result.tier_reached == "tier1_lint"
        assert result.strategy_used == "tiered"

    @pytest.mark.asyncio()
    async def test_tiered_fails_at_tier1_test(self, runner: BenchmarkRunner) -> None:
        """Test failure on changed test files stops at tier1."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if "ruff" in cmd:
                return "", 0
            if "pytest" in cmd and "test_bar" in cmd:
                return "1 passed, 1 failed\nFAILED tests/test_bar.py::test_x\n", 1
            return "", 0

        changed = ["src/hermit/bar.py", "tests/test_bar.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t3", "spec-t3", changed_files=changed)

        assert result.check_passed is False
        assert result.tier_reached == "tier1_test"

    @pytest.mark.asyncio()
    async def test_tiered_fails_at_tier2_unit(self, runner: BenchmarkRunner) -> None:
        """Unit test failure stops at tier2."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if "tests/unit/" in cmd:
                return "50 passed, 2 failed\n", 1
            return "", 0

        changed = ["src/hermit/baz.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t4", "spec-t4", changed_files=changed)

        assert result.check_passed is False
        assert result.tier_reached == "tier2_unit"

    @pytest.mark.asyncio()
    async def test_tiered_fails_at_tier3_full(self, runner: BenchmarkRunner) -> None:
        """Full test suite failure at tier3."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if cmd == "make test":
                return "190 passed, 5 failed\n", 1
            return "", 0

        changed = ["src/hermit/qux.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t5", "spec-t5", changed_files=changed)

        assert result.check_passed is False
        assert result.tier_reached == "tier3_full"

    @pytest.mark.asyncio()
    async def test_no_changed_files_falls_back_to_full(self, runner: BenchmarkRunner) -> None:
        """Without changed_files, strategy falls back to full make check."""
        with patch.object(runner, "_exec", return_value=("42 passed\n", 0)):
            result = await runner.run("iter-t6", "spec-t6")

        assert result.strategy_used == "full"
        assert result.tier_reached == "full"

    @pytest.mark.asyncio()
    async def test_full_strategy_ignores_changed_files(self, runner: BenchmarkRunner) -> None:
        """Explicit full strategy runs make check even with changed_files."""
        with patch.object(runner, "_exec", return_value=("42 passed\n", 0)):
            result = await runner.run(
                "iter-t7",
                "spec-t7",
                changed_files=["src/hermit/foo.py"],
                strategy="full",
            )

        assert result.strategy_used == "full"
        assert result.tier_reached == "full"

    @pytest.mark.asyncio()
    async def test_quick_strategy_runs_tier1_only(self, runner: BenchmarkRunner) -> None:
        """Quick strategy runs only tier 1 and stops."""
        calls: list[str] = []

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            calls.append(cmd)
            return "1 passed\n", 0

        changed = ["src/hermit/foo.py", "tests/test_foo.py"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run(
                "iter-t8",
                "spec-t8",
                changed_files=changed,
                strategy="quick",
            )

        assert result.check_passed is True
        assert result.tier_reached == "tier1_quick"
        assert result.strategy_used == "quick"
        # Should NOT have run make test or unit tests
        assert not any("make test" in c for c in calls)
        assert not any("tests/unit/" in c for c in calls)

    @pytest.mark.asyncio()
    async def test_tiered_skips_non_python_files(self, runner: BenchmarkRunner) -> None:
        """Non-.py changed files are ignored for lint/test but tiered still runs."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if "tests/unit/" in cmd:
                return "10 passed\n", 0
            if cmd == "make test":
                return "20 passed\n", 0
            return "", 0

        changed = ["README.md", "config.toml"]
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run("iter-t9", "spec-t9", changed_files=changed)

        assert result.check_passed is True
        assert result.tier_reached == "tier3_full"

    @pytest.mark.asyncio()
    async def test_tiered_with_delta_baseline(self, runner: BenchmarkRunner) -> None:
        """Tiered strategy works together with delta comparison."""

        async def fake_exec(cmd: str, cwd: str) -> tuple[str, int]:
            if cmd == "make test":
                return "42 passed in 5.0s\n1337 errors found in 200 files\n", 1
            return "", 0

        changed = ["src/hermit/mod.py"]
        baseline = {"typecheck_errors": 1337, "lint_violations": 0}
        with patch.object(runner, "_exec", side_effect=fake_exec):
            result = await runner.run(
                "iter-t10",
                "spec-t10",
                changed_files=changed,
                baseline_metrics=baseline,
            )

        # Delta comparison should pass (no new errors)
        assert result.check_passed is True
        assert result.tier_reached == "tier3_full"
        assert result.delta_info["new_typecheck"] == 0
