"""Patrol check implementations — Protocol-based pluggable checks."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from hermit.plugins.builtin.hooks.patrol.models import PatrolCheckResult

_TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


@runtime_checkable
class PatrolCheck(Protocol):
    """Interface for a patrol check."""

    name: str

    def run(self, workspace_root: str) -> PatrolCheckResult: ...


class LintCheck:
    """Run ruff check and report lint issues."""

    name = "lint"

    def run(self, workspace_root: str) -> PatrolCheckResult:
        try:
            result = subprocess.run(
                ["ruff", "check", workspace_root, "--output-format=json"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            issues: list[dict[str, object]] = []
            if result.stdout.strip():
                try:
                    raw: list[dict[str, Any]] = json.loads(result.stdout)
                    for entry in raw:
                        loc: dict[str, Any] = entry.get("location") or {}
                        issues.append(
                            {
                                "file": str(entry.get("filename", "")),
                                "line": int(loc.get("row", 0)),
                                "code": str(entry.get("code", "")),
                                "message": str(entry.get("message", "")),
                            }
                        )
                except json.JSONDecodeError:
                    pass
            count = len(issues)
            if count == 0:
                return PatrolCheckResult(
                    check_name=self.name,
                    status="clean",
                    summary="No lint issues found",
                )
            return PatrolCheckResult(
                check_name=self.name,
                status="issues_found",
                summary=f"{count} lint issue(s) found",
                issue_count=count,
                issues=issues,  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            return PatrolCheckResult(
                check_name=self.name,
                status="error",
                summary="ruff not found — install with: uv pip install ruff",
            )
        except Exception as exc:
            return PatrolCheckResult(check_name=self.name, status="error", summary=str(exc))


class TestCheck:
    """Run pytest and report test results."""

    name = "test"

    def run(self, workspace_root: str) -> PatrolCheckResult:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=no", "-q", workspace_root],
                capture_output=True,
                text=True,
                timeout=300,
            )
            output = result.stdout + result.stderr
            # Parse pytest summary line like "5 passed, 2 failed"
            failed = 0
            passed = 0
            for match in re.finditer(r"(\d+)\s+passed", output):
                passed = int(match.group(1))
            for match in re.finditer(r"(\d+)\s+failed", output):
                failed = int(match.group(1))

            if result.returncode == 0:
                return PatrolCheckResult(
                    check_name=self.name,
                    status="clean",
                    summary=f"All {passed} tests passed",
                )
            return PatrolCheckResult(
                check_name=self.name,
                status="issues_found",
                summary=f"{failed} test(s) failed, {passed} passed",
                issue_count=failed,
            )
        except FileNotFoundError:
            return PatrolCheckResult(
                check_name=self.name,
                status="error",
                summary="pytest not found",
            )
        except Exception as exc:
            return PatrolCheckResult(check_name=self.name, status="error", summary=str(exc))


class TodoScanCheck:
    """Scan Python files for TODO/FIXME/HACK/XXX comments."""

    name = "todo_scan"

    def run(self, workspace_root: str) -> PatrolCheckResult:
        try:
            issues: list[dict[str, object]] = []
            root = Path(workspace_root)
            if not root.is_dir():
                return PatrolCheckResult(
                    check_name=self.name,
                    status="error",
                    summary=f"Workspace root is not a directory: {workspace_root}",
                )
            for dirpath, _dirnames, filenames in os.walk(root):
                # Skip hidden dirs and common non-source dirs
                parts = Path(dirpath).parts
                if any(
                    p.startswith(".") or p in ("__pycache__", "node_modules", ".git") for p in parts
                ):
                    continue
                for fname in filenames:
                    if not fname.endswith(".py"):
                        continue
                    fpath = Path(dirpath) / fname
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    for lineno, line in enumerate(text.splitlines(), start=1):
                        match = _TODO_PATTERN.search(line)
                        if match:
                            issues.append(
                                {
                                    "file": str(fpath),
                                    "line": lineno,
                                    "tag": match.group(1).upper(),
                                    "text": line.strip(),
                                }
                            )
            count = len(issues)
            if count == 0:
                return PatrolCheckResult(
                    check_name=self.name,
                    status="clean",
                    summary="No TODO/FIXME/HACK/XXX markers found",
                )
            return PatrolCheckResult(
                check_name=self.name,
                status="issues_found",
                summary=f"{count} TODO/FIXME marker(s) found",
                issue_count=count,
                issues=issues,  # type: ignore[arg-type]
            )
        except Exception as exc:
            return PatrolCheckResult(check_name=self.name, status="error", summary=str(exc))


class CoverageCheck:
    """Run pytest with coverage and report results."""

    name = "coverage"

    def run(self, workspace_root: str) -> PatrolCheckResult:
        try:
            result = subprocess.run(
                [
                    "python",
                    "-m",
                    "pytest",
                    "--cov",
                    workspace_root,
                    "--cov-report=term-missing",
                    "-q",
                    "--tb=no",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            output = result.stdout + result.stderr
            # Look for TOTAL line like "TOTAL    1234    567    54%"
            total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if total_match:
                coverage_pct = int(total_match.group(1))
                return PatrolCheckResult(
                    check_name=self.name,
                    status="clean" if coverage_pct >= 80 else "issues_found",
                    summary=f"Coverage: {coverage_pct}%",
                    issue_count=0 if coverage_pct >= 80 else 1,
                )
            return PatrolCheckResult(
                check_name=self.name,
                status="error",
                summary="Could not parse coverage output",
            )
        except FileNotFoundError:
            return PatrolCheckResult(
                check_name=self.name,
                status="error",
                summary="pytest-cov not configured — install with: uv pip install pytest-cov",
            )
        except Exception as exc:
            return PatrolCheckResult(check_name=self.name, status="error", summary=str(exc))


class SecurityCheck:
    """Run pip-audit and report vulnerabilities."""

    name = "security"

    def run(self, workspace_root: str) -> PatrolCheckResult:
        try:
            result = subprocess.run(
                ["pip-audit", "--format=json"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=workspace_root,
            )
            vulns: list[dict[str, object]] = []
            if result.stdout.strip():
                try:
                    parsed: dict[str, Any] = json.loads(result.stdout)
                    deps: list[dict[str, Any]] = parsed.get("dependencies", [])
                    for dep_dict in deps:
                        vuln_list: list[dict[str, Any]] = dep_dict.get("vulns", []) or []
                        for v_dict in vuln_list:
                            vulns.append(
                                {
                                    "package": str(dep_dict.get("name", "")),
                                    "version": str(dep_dict.get("version", "")),
                                    "vuln_id": str(v_dict.get("id", "")),
                                    "fix_versions": str(v_dict.get("fix_versions", [])),
                                }
                            )
                except json.JSONDecodeError:
                    pass
            count = len(vulns)
            if count == 0:
                return PatrolCheckResult(
                    check_name=self.name,
                    status="clean",
                    summary="No known vulnerabilities found",
                )
            return PatrolCheckResult(
                check_name=self.name,
                status="issues_found",
                summary=f"{count} vulnerability(ies) found",
                issue_count=count,
                issues=vulns,  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            return PatrolCheckResult(
                check_name=self.name,
                status="error",
                summary="pip-audit not found — install with: uv pip install pip-audit",
            )
        except Exception as exc:
            return PatrolCheckResult(check_name=self.name, status="error", summary=str(exc))


BUILTIN_CHECKS: dict[str, type[PatrolCheck]] = {
    "lint": LintCheck,  # type: ignore[dict-item]
    "test": TestCheck,  # type: ignore[dict-item]
    "todo_scan": TodoScanCheck,  # type: ignore[dict-item]
    "coverage": CoverageCheck,  # type: ignore[dict-item]
    "security": SecurityCheck,  # type: ignore[dict-item]
}
