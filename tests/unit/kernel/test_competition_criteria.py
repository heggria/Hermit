from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from hermit.kernel.execution.competition.criteria import (
    BUILTIN_CRITERIA,
    EvaluationCriterion,
    LintCleanCriterion,
    TestPassCriterion,
    TypeCheckCriterion,
)


def _fake_run_ok(*args: Any, **kwargs: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(returncode=0, stdout="5 passed\n", stderr="")


def _fake_run_fail(*args: Any, **kwargs: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(returncode=1, stdout="3 passed, 2 failed\n", stderr="")


def test_builtin_criteria_registry() -> None:
    assert "tests_pass" in BUILTIN_CRITERIA
    assert "lint_clean" in BUILTIN_CRITERIA
    assert "type_check" in BUILTIN_CRITERIA


def test_criterion_protocol_compliance() -> None:
    for cls in BUILTIN_CRITERIA.values():
        instance = cls()
        assert isinstance(instance, EvaluationCriterion)
        assert hasattr(instance, "name")
        assert hasattr(instance, "score")
        assert hasattr(instance, "passed")


def test_test_pass_criterion_passed(tmp_path: Path) -> None:
    criterion = TestPassCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", _fake_run_ok):
        assert criterion.passed(tmp_path, {}) is True
        assert criterion.score(tmp_path, {}) == 1.0


def test_test_pass_criterion_failed(tmp_path: Path) -> None:
    criterion = TestPassCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", _fake_run_fail):
        assert criterion.passed(tmp_path, {}) is False


def test_lint_clean_criterion_passed(tmp_path: Path) -> None:
    criterion = LintCleanCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", _fake_run_ok):
        assert criterion.passed(tmp_path, {}) is True
        assert criterion.score(tmp_path, {}) == 1.0


def test_lint_clean_criterion_with_violations(tmp_path: Path) -> None:
    from types import SimpleNamespace

    def fake_run(*a: Any, **kw: Any) -> Any:
        return SimpleNamespace(
            returncode=1,
            stdout="file.py:1:1: E501 line too long\nfile.py:2:1: F401 unused import\nFound 2 errors.\n",
            stderr="",
        )

    criterion = LintCleanCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", fake_run):
        assert criterion.passed(tmp_path, {}) is False
        score = criterion.score(tmp_path, {})
        assert 0.0 < score < 1.0


def test_test_pass_criterion_score_partial_ratio(tmp_path: Path) -> None:
    """Lines 40-50: parse pass ratio from pytest output when tests fail."""
    from types import SimpleNamespace

    criterion = TestPassCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="3 passed, 2 failed\n", stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 3 / 5


def test_test_pass_criterion_score_no_parseable_output(tmp_path: Path) -> None:
    """Lines 51-53: unparseable stdout falls back to 0.0."""
    from types import SimpleNamespace

    criterion = TestPassCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="some garbage output\n", stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 0.0


def test_test_pass_criterion_score_empty_stdout(tmp_path: Path) -> None:
    """Lines 40, 53: empty/None stdout falls back to 0.0."""
    from types import SimpleNamespace

    criterion = TestPassCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout=None, stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 0.0


def test_test_pass_criterion_score_value_error(tmp_path: Path) -> None:
    """Lines 51-52: ValueError during int parsing falls back to 0.0."""
    from types import SimpleNamespace

    # "passed" token is present but preceding token is not a number
    criterion = TestPassCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="abc passed, def failed\n", stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 0.0


def test_lint_clean_criterion_score_only_found_lines(tmp_path: Path) -> None:
    """Line 86: returncode != 0 but all lines start with 'Found' → 1.0."""
    from types import SimpleNamespace

    criterion = LintCleanCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="Found 0 errors.\n", stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 1.0


def test_type_check_criterion_passed(tmp_path: Path) -> None:
    criterion = TypeCheckCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", _fake_run_ok):
        assert criterion.passed(tmp_path, {}) is True
        assert criterion.score(tmp_path, {}) == 1.0


def test_type_check_criterion_score_no_errors(tmp_path: Path) -> None:
    """Lines 118-121: returncode != 0 but no 'error:' in output → 0.8."""
    from types import SimpleNamespace

    criterion = TypeCheckCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="some warnings\n", stderr=""),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 0.8


def test_type_check_criterion_score_with_errors(tmp_path: Path) -> None:
    """Lines 118-122: returncode != 0 with error count → partial score."""
    from types import SimpleNamespace

    criterion = TypeCheckCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="file.py:1: error: bad type\nfile.py:2: error: missing arg\n",
        ),
    ):
        score = criterion.score(tmp_path, {})
        # 2 errors: 1.0 - 2*0.02 = 0.96
        assert score == 0.96


def test_type_check_criterion_score_many_errors_clamps_to_zero(tmp_path: Path) -> None:
    """Line 122: many errors clamp score to 0.0 via max()."""
    from types import SimpleNamespace

    errors = "\n".join(f"file.py:{i}: error: bad" for i in range(100))
    criterion = TypeCheckCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="", stderr=errors),
    ):
        score = criterion.score(tmp_path, {})
        assert score == 0.0


def test_type_check_criterion_score_stderr_none_falls_to_stdout(tmp_path: Path) -> None:
    """Line 118: stderr is None/empty, falls back to stdout for error counting."""
    from types import SimpleNamespace

    criterion = TypeCheckCriterion()
    with patch(
        "hermit.kernel.execution.competition.criteria.subprocess.run",
        return_value=SimpleNamespace(
            returncode=1,
            stdout="file.py:1: error: bad type\n",
            stderr=None,
        ),
    ):
        score = criterion.score(tmp_path, {})
        # 1 error via stdout fallback: 1.0 - 0.02 = 0.98
        assert score == 0.98
