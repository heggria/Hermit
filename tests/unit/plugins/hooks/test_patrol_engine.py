"""Tests for the Patrol Engine plugin."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.patrol.checks import (
    BUILTIN_CHECKS,
    LintCheck,
    TodoScanCheck,
)
from hermit.plugins.builtin.hooks.patrol.engine import PatrolEngine
from hermit.plugins.builtin.hooks.patrol.models import PatrolCheckResult, PatrolReport

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestPatrolCheckResult:
    def test_defaults(self) -> None:
        r = PatrolCheckResult(check_name="lint", status="clean", summary="ok")
        assert r.check_name == "lint"
        assert r.status == "clean"
        assert r.summary == "ok"
        assert r.issue_count == 0
        assert r.issues == []
        assert r.disposition == "report_only"

    def test_with_issues(self) -> None:
        issues: list[dict[str, Any]] = [{"file": "a.py", "line": 1}]
        r = PatrolCheckResult(
            check_name="todo_scan",
            status="issues_found",
            summary="1 marker",
            issue_count=1,
            issues=issues,
        )
        assert r.issue_count == 1
        assert len(r.issues) == 1


class TestPatrolReport:
    def test_empty_report(self) -> None:
        r = PatrolReport()
        assert r.checks == []
        assert r.total_issues == 0
        assert r.started_at == 0.0
        assert r.finished_at == 0.0
        assert r.workspace_root == ""

    def test_with_checks(self) -> None:
        c1 = PatrolCheckResult(check_name="lint", status="clean", summary="ok")
        c2 = PatrolCheckResult(
            check_name="test", status="issues_found", summary="1 fail", issue_count=1
        )
        r = PatrolReport(checks=[c1, c2], total_issues=1, workspace_root="/tmp")
        assert len(r.checks) == 2
        assert r.total_issues == 1


# ---------------------------------------------------------------------------
# Check tests (with mocks)
# ---------------------------------------------------------------------------


class TestLintCheck:
    def test_clean(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "[]"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = LintCheck().run("/workspace")
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_issues_found(self) -> None:
        import json

        issues_json = json.dumps(
            [
                {
                    "filename": "a.py",
                    "location": {"row": 10},
                    "code": "E501",
                    "message": "Line too long",
                },
                {
                    "filename": "b.py",
                    "location": {"row": 5},
                    "code": "F401",
                    "message": "Unused import",
                },
            ]
        )
        mock_result = MagicMock()
        mock_result.stdout = issues_json
        mock_result.stderr = ""
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = LintCheck().run("/workspace")
        assert result.status == "issues_found"
        assert result.issue_count == 2

    def test_ruff_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("ruff")):
            result = LintCheck().run("/workspace")
        assert result.status == "error"
        assert "ruff not found" in result.summary


class TestTodoScanCheck:
    def test_finds_todos(self, tmp_path: Path) -> None:
        py_file = tmp_path / "example.py"
        py_file.write_text(
            textwrap.dedent("""\
            def foo():
                # TODO: implement this
                pass

            # FIXME: broken logic
            x = 1

            # HACK: workaround
            y = 2
            """)
        )
        result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "issues_found"
        assert result.issue_count == 3
        tags = {i["tag"] for i in result.issues}
        assert tags == {"TODO", "FIXME", "HACK"}

    def test_clean_directory(self, tmp_path: Path) -> None:
        py_file = tmp_path / "clean.py"
        py_file.write_text("def clean():\n    return True\n")
        result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "clean"
        assert result.issue_count == 0

    def test_nonexistent_directory(self) -> None:
        result = TodoScanCheck().run("/nonexistent/path/xyzzy")
        assert result.status == "error"

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("# TODO: secret\n")
        (tmp_path / "visible.py").write_text("# clean code\n")
        result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "clean"

    def test_skips_pycache(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "cached.py").write_text("# TODO: cached\n")
        (tmp_path / "main.py").write_text("x = 1\n")
        result = TodoScanCheck().run(str(tmp_path))
        assert result.status == "clean"


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


class TestPatrolEngine:
    def test_run_patrol_with_mock_checks(self) -> None:
        """run_patrol should aggregate results from enabled checks."""
        mock_result = PatrolCheckResult(check_name="lint", status="clean", summary="ok")
        mock_check_cls = MagicMock()
        mock_check_cls.return_value.run.return_value = mock_result

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check_cls}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            report = engine.run_patrol()

        assert len(report.checks) == 1
        assert report.checks[0].status == "clean"
        assert report.total_issues == 0
        assert report.started_at > 0
        assert report.finished_at >= report.started_at

    def test_only_runs_enabled_checks(self) -> None:
        """Engine should skip checks not in the enabled list."""
        mock_lint = MagicMock()
        mock_lint.return_value.run.return_value = PatrolCheckResult(
            check_name="lint", status="clean", summary="ok"
        )
        mock_test = MagicMock()
        mock_test.return_value.run.return_value = PatrolCheckResult(
            check_name="test", status="clean", summary="ok"
        )

        with patch.dict(
            BUILTIN_CHECKS,
            {"lint": mock_lint, "test": mock_test},
            clear=True,
        ):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            report = engine.run_patrol()

        assert len(report.checks) == 1
        assert report.checks[0].check_name == "lint"
        mock_test.return_value.run.assert_not_called()

    def test_unknown_check_skipped(self) -> None:
        """Unknown check names should be silently skipped."""
        engine = PatrolEngine(enabled_checks="nonexistent_check", workspace_root="/workspace")
        report = engine.run_patrol()
        assert len(report.checks) == 0

    def test_aggregates_issue_count(self) -> None:
        mock_lint = MagicMock()
        mock_lint.return_value.run.return_value = PatrolCheckResult(
            check_name="lint",
            status="issues_found",
            summary="2 issues",
            issue_count=2,
        )
        mock_test = MagicMock()
        mock_test.return_value.run.return_value = PatrolCheckResult(
            check_name="test",
            status="issues_found",
            summary="3 failures",
            issue_count=3,
        )

        with patch.dict(
            BUILTIN_CHECKS,
            {"lint": mock_lint, "test": mock_test},
            clear=True,
        ):
            engine = PatrolEngine(enabled_checks="lint,test", workspace_root="/workspace")
            report = engine.run_patrol()

        assert report.total_issues == 5

    def test_last_report(self) -> None:
        """last_report should be None before first run, then populated."""
        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint", status="clean", summary="ok"
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            assert engine.last_report is None
            engine.run_patrol()
            assert engine.last_report is not None
            assert engine.last_report.total_issues == 0

    def test_start_stop_lifecycle(self) -> None:
        """Engine should start and stop its daemon thread cleanly."""
        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint", status="clean", summary="ok"
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(
                interval_minutes=1,
                enabled_checks="lint",
                workspace_root="/workspace",
            )
            engine.start()
            assert engine._thread is not None
            assert engine._thread.is_alive()
            engine.stop()
            assert not engine._thread.is_alive()

    def test_check_exception_handled(self) -> None:
        """If a check raises an exception, it should be caught and not crash."""
        mock_check = MagicMock()
        mock_check.return_value.run.side_effect = RuntimeError("boom")

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            # Should not raise
            report = engine.run_patrol()
            # The check raised, so it's not appended
            assert len(report.checks) == 0


# ---------------------------------------------------------------------------
# BUILTIN_CHECKS registry
# ---------------------------------------------------------------------------


class TestBuiltinChecks:
    def test_all_checks_registered(self) -> None:
        expected = {"lint", "test", "todo_scan", "coverage", "security"}
        assert set(BUILTIN_CHECKS.keys()) == expected

    def test_all_checks_have_name(self) -> None:
        for name, cls in BUILTIN_CHECKS.items():
            assert cls.name == name  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Signal emission tests (D3→D5 wiring)
# ---------------------------------------------------------------------------


class TestPatrolSignalEmission:
    def test_emit_signals_creates_evidence_signal(self) -> None:
        """Patrol should emit EvidenceSignal via store.create_signal when issues found."""
        created_signals: list[Any] = []
        store = MagicMock()
        store.check_cooldown.return_value = False
        store.create_signal.side_effect = lambda s: created_signals.append(s)

        runner = MagicMock()
        runner.task_controller.store = store

        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint",
            status="issues_found",
            summary="2 lint errors",
            issue_count=2,
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            engine.set_runner(runner)
            engine.run_patrol()

        assert len(created_signals) == 1
        sig = created_signals[0]
        assert sig.source_kind == "lint_violation"
        assert sig.risk_level == "low"
        assert sig.cooldown_key == "patrol:lint"

    def test_emit_signals_respects_cooldown(self) -> None:
        """If cooldown is active, no signal should be emitted."""
        store = MagicMock()
        store.check_cooldown.return_value = True

        runner = MagicMock()
        runner.task_controller.store = store

        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint",
            status="issues_found",
            summary="2 lint errors",
            issue_count=2,
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            engine.set_runner(runner)
            engine.run_patrol()

        store.create_signal.assert_not_called()

    def test_emit_signals_skips_clean_checks(self) -> None:
        """Clean checks should not emit any signals."""
        store = MagicMock()
        runner = MagicMock()
        runner.task_controller.store = store

        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint", status="clean", summary="ok"
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            engine.set_runner(runner)
            engine.run_patrol()

        store.create_signal.assert_not_called()
        store.check_cooldown.assert_not_called()

    def test_security_vuln_gets_critical_risk(self) -> None:
        """Security checks should produce critical risk signals."""
        created_signals: list[Any] = []
        store = MagicMock()
        store.check_cooldown.return_value = False
        store.create_signal.side_effect = lambda s: created_signals.append(s)

        runner = MagicMock()
        runner.task_controller.store = store

        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="security",
            status="issues_found",
            summary="CVE found",
            issue_count=1,
        )

        with patch.dict(BUILTIN_CHECKS, {"security": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="security", workspace_root="/workspace")
            engine.set_runner(runner)
            engine.run_patrol()

        assert len(created_signals) == 1
        assert created_signals[0].risk_level == "critical"
        assert created_signals[0].suggested_policy_profile == "default"

    def test_no_runner_skips_emission(self) -> None:
        """Without a runner, signal emission is silently skipped."""
        mock_check = MagicMock()
        mock_check.return_value.run.return_value = PatrolCheckResult(
            check_name="lint",
            status="issues_found",
            summary="2 lint errors",
            issue_count=2,
        )

        with patch.dict(BUILTIN_CHECKS, {"lint": mock_check}, clear=True):
            engine = PatrolEngine(enabled_checks="lint", workspace_root="/workspace")
            # No set_runner call — should not raise
            report = engine.run_patrol()

        assert report.total_issues == 2
