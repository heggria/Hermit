"""GovernedReviewer — thin incremental checks on top of PatrolEngine."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import structlog

from hermit.plugins.builtin.hooks.patrol.engine import PatrolEngine
from hermit.plugins.builtin.hooks.quality.models import FindingSeverity, ReviewFinding, ReviewReport

log = structlog.get_logger()


def _parse_file(file_path: str) -> ast.Module | None:
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        return ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return None


def _check_syntax(file_path: str) -> list[ReviewFinding]:
    """Check that a Python file can be parsed without syntax errors.

    Produces a BLOCKING finding when ast.parse fails — this indicates
    code that cannot be imported or executed at all.
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        line = exc.lineno or 0
        detail = exc.msg if exc.msg else "invalid syntax"
        return [
            ReviewFinding(
                severity=FindingSeverity.BLOCKING,
                category="syntax",
                message=f"Syntax error: {detail}",
                file_path=file_path,
                line=line,
            )
        ]
    return []


def _check_imports(file_path: str, workspace_root: str) -> list[ReviewFinding]:
    """Check that relative imports resolve to existing files.

    Produces BLOCKING findings for unresolved relative imports to
    files that don't exist on disk — these will cause ImportError
    at runtime.
    """
    tree = _parse_file(file_path)
    if tree is None:
        return []
    findings: list[ReviewFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
            parts = node.module.split(".")
            base = Path(file_path).parent
            for _ in range(node.level - 1):
                base = base.parent
            candidate = base / "/".join(parts)
            if not (candidate.with_suffix(".py").exists() or (candidate / "__init__.py").exists()):
                findings.append(
                    ReviewFinding(
                        severity=FindingSeverity.BLOCKING,
                        category="import",
                        message=f"Unresolved relative import '{node.module}' — "
                        f"target does not exist on disk",
                        file_path=file_path,
                        line=node.lineno,
                    )
                )
    return findings


def _check_naming(file_path: str) -> list[ReviewFinding]:
    """Check that public functions/classes follow naming conventions."""
    tree = _parse_file(file_path)
    if tree is None:
        return []
    findings: list[ReviewFinding] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            if node.name != node.name.lower():
                findings.append(
                    ReviewFinding(
                        severity=FindingSeverity.INFO,
                        category="naming",
                        message=f"Function '{node.name}' is not snake_case",
                        file_path=file_path,
                        line=node.lineno,
                    )
                )
        elif (
            isinstance(node, ast.ClassDef)
            and not node.name.startswith("_")
            and node.name[0].islower()
        ):
            findings.append(
                ReviewFinding(
                    severity=FindingSeverity.INFO,
                    category="naming",
                    message=f"Class '{node.name}' should start with uppercase",
                    file_path=file_path,
                    line=node.lineno,
                )
            )
    return findings


def _check_init_files(changed_files: list[str]) -> list[ReviewFinding]:
    """Check that directories of changed files contain __init__.py."""
    findings: list[ReviewFinding] = []
    seen_dirs: set[str] = set()
    for fp in changed_files:
        parent = str(Path(fp).parent)
        if parent in seen_dirs:
            continue
        seen_dirs.add(parent)
        if Path(parent).is_dir() and not (Path(parent) / "__init__.py").exists():
            findings.append(
                ReviewFinding(
                    severity=FindingSeverity.WARNING,
                    category="init",
                    message=f"Directory '{parent}' is missing __init__.py",
                    file_path=parent,
                )
            )
    return findings


class GovernedReviewer:
    """Code reviewer that wraps PatrolEngine and adds incremental checks.

    Delegates lint/test/todo checks to PatrolEngine. Adds only:
    - Import validation (relative imports resolve)
    - Naming consistency (snake_case functions, PascalCase classes)
    - Missing __init__.py for new directories
    """

    def __init__(self, workspace_root: str = "") -> None:
        self._workspace_root = workspace_root

    async def review(self, changed_files: list[str]) -> ReviewReport:
        """Review changed files and return a ReviewReport."""
        import asyncio

        return await asyncio.to_thread(self._review_sync, changed_files)

    def _review_sync(self, changed_files: list[str]) -> ReviewReport:
        start = time.monotonic()
        findings: list[ReviewFinding] = []

        # Delegate to PatrolEngine for lint + todo checks
        patrol = PatrolEngine(
            enabled_checks="lint,todo_scan",
            workspace_root=self._workspace_root,
        )
        patrol_report = patrol.run_patrol()
        for check in patrol_report.checks:
            for issue in check.issues:
                issue_file = str(issue.get("file", ""))
                if not issue_file or issue_file not in changed_files:
                    continue
                if check.check_name == "lint":
                    # E9xx errors (syntax errors) are fatal in ruff — treat as BLOCKING
                    code = str(issue.get("code", ""))
                    severity = (
                        FindingSeverity.BLOCKING
                        if code.startswith("E9")
                        else FindingSeverity.WARNING
                    )
                else:
                    severity = FindingSeverity.INFO
                findings.append(
                    ReviewFinding(
                        severity=severity,
                        category=check.check_name,
                        message=str(issue.get("message") or issue.get("text", "")),
                        file_path=issue_file,
                        line=int(issue.get("line", 0)),
                    )
                )

        # Incremental checks on changed .py files
        py_files = [f for f in changed_files if f.endswith(".py") and Path(f).is_file()]
        for fp in py_files:
            findings.extend(_check_syntax(fp))
            findings.extend(_check_imports(fp, self._workspace_root))
            findings.extend(_check_naming(fp))

        findings.extend(_check_init_files(changed_files))

        duration = time.monotonic() - start
        has_blocking = any(f.severity == FindingSeverity.BLOCKING for f in findings)

        return ReviewReport(
            findings=tuple(findings),
            passed=not has_blocking,
            duration_seconds=round(duration, 3),
        )
