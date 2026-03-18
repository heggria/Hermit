from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EvaluationCriterion(Protocol):
    """Pluggable evaluation criterion protocol."""

    name: str

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        """Score a candidate workspace, returning 0.0-1.0."""
        ...

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        """Whether the candidate meets minimum standards."""
        ...


class TestPassCriterion:
    """Run ``uv run pytest`` and score based on exit code and pass ratio."""

    name: str = "tests_pass"

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        result = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=no"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if result.returncode == 0:
            return 1.0
        # Try to extract pass ratio from pytest output like "5 passed, 2 failed"
        stdout = result.stdout or ""
        try:
            passed = failed = 0
            for token in stdout.split():
                if token in ("passed,", "passed"):
                    passed = int(stdout.split("passed")[0].split()[-1])
                elif token == "failed," or token == "failed":
                    failed = int(stdout.split("failed")[0].split()[-1])
            total = passed + failed
            if total > 0:
                return passed / total
        except (ValueError, IndexError):
            pass
        return 0.0

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        result = subprocess.run(
            ["uv", "run", "pytest", "-q", "--tb=no"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        return result.returncode == 0


class LintCleanCriterion:
    """Run ``uv run ruff check`` and score based on violation count."""

    name: str = "lint_clean"

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "."],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode == 0:
            return 1.0
        lines = (result.stdout or "").strip().splitlines()
        violation_count = sum(1 for line in lines if line and not line.startswith("Found"))
        if violation_count == 0:
            return 1.0
        return max(0.0, 1.0 - violation_count * 0.05)

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "."],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        return result.returncode == 0


class TypeCheckCriterion:
    """Run ``uv run pyright`` and score based on exit code."""

    name: str = "type_check"

    def score(self, workspace_root: Path, context: dict[str, Any]) -> float:
        result = subprocess.run(
            ["uv", "run", "pyright"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if result.returncode == 0:
            return 1.0
        # Partial score: count errors in output
        stderr = result.stderr or result.stdout or ""
        error_count = stderr.count("error:")
        if error_count == 0:
            return 0.8
        return max(0.0, 1.0 - error_count * 0.02)

    def passed(self, workspace_root: Path, context: dict[str, Any]) -> bool:
        result = subprocess.run(
            ["uv", "run", "pyright"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        return result.returncode == 0


BUILTIN_CRITERIA: dict[str, type[EvaluationCriterion]] = {
    "tests_pass": TestPassCriterion,  # type: ignore[dict-item]
    "lint_clean": LintCleanCriterion,  # type: ignore[dict-item]
    "type_check": TypeCheckCriterion,  # type: ignore[dict-item]
}
