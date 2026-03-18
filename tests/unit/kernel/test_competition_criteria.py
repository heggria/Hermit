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


def test_type_check_criterion_passed(tmp_path: Path) -> None:
    criterion = TypeCheckCriterion()
    with patch("hermit.kernel.execution.competition.criteria.subprocess.run", _fake_run_ok):
        assert criterion.passed(tmp_path, {}) is True
        assert criterion.score(tmp_path, {}) == 1.0
