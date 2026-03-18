"""Tests for the CLI overnight command."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import typer.testing

import hermit.plugins.builtin.hooks.overnight.report as _report_mod  # noqa: F401
import hermit.surfaces.cli._commands_overnight as _overnight_mod  # noqa: F401

# Import the main app *and* the overnight command module to register the command.
from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


class TestOvernightCommand:
    def test_no_database_exits_with_error(self, tmp_path: Path) -> None:
        fake_settings = SimpleNamespace(base_dir=tmp_path)
        with patch(
            "hermit.runtime.assembly.config.get_settings",
            return_value=fake_settings,
        ):
            result = runner.invoke(app, ["overnight"])

        assert result.exit_code == 1
        assert "No kernel database found" in result.output

    def test_text_output(self, tmp_path: Path) -> None:
        db_path = tmp_path / "kernel" / "state.db"
        db_path.parent.mkdir(parents=True)
        db_path.touch()

        fake_settings = SimpleNamespace(base_dir=tmp_path)
        fake_summary = SimpleNamespace()
        markdown_output = "# Overnight Report\nAll clear."

        with (
            patch(
                "hermit.runtime.assembly.config.get_settings",
                return_value=fake_settings,
            ),
            patch(
                "hermit.kernel.ledger.journal.store.KernelStore",
            ) as MockStore,
            patch(
                "hermit.plugins.builtin.hooks.overnight.report.OvernightReportService",
            ) as MockService,
        ):
            mock_store = MockStore.return_value
            svc = MockService.return_value
            svc.generate.return_value = fake_summary
            svc.format_markdown.return_value = markdown_output

            result = runner.invoke(app, ["overnight"])

        assert result.exit_code == 0
        assert "Overnight Report" in result.output
        svc.generate.assert_called_once_with(lookback_hours=12)
        mock_store.close.assert_called_once()

    def test_json_output(self, tmp_path: Path) -> None:
        db_path = tmp_path / "kernel" / "state.db"
        db_path.parent.mkdir(parents=True)
        db_path.touch()

        fake_settings = SimpleNamespace(base_dir=tmp_path)
        fake_summary = SimpleNamespace()
        dashboard_json = {"status": "ok", "tasks": 0}

        with (
            patch(
                "hermit.runtime.assembly.config.get_settings",
                return_value=fake_settings,
            ),
            patch(
                "hermit.kernel.ledger.journal.store.KernelStore",
            ) as MockStore,
            patch(
                "hermit.plugins.builtin.hooks.overnight.report.OvernightReportService",
            ) as MockService,
        ):
            mock_store = MockStore.return_value
            svc = MockService.return_value
            svc.generate.return_value = fake_summary
            svc.format_dashboard_json.return_value = dashboard_json

            result = runner.invoke(app, ["overnight", "--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed == dashboard_json
        mock_store.close.assert_called_once()

    def test_custom_lookback(self, tmp_path: Path) -> None:
        db_path = tmp_path / "kernel" / "state.db"
        db_path.parent.mkdir(parents=True)
        db_path.touch()

        fake_settings = SimpleNamespace(base_dir=tmp_path)

        with (
            patch(
                "hermit.runtime.assembly.config.get_settings",
                return_value=fake_settings,
            ),
            patch("hermit.kernel.ledger.journal.store.KernelStore"),
            patch(
                "hermit.plugins.builtin.hooks.overnight.report.OvernightReportService",
            ) as MockService,
        ):
            svc = MockService.return_value
            svc.generate.return_value = SimpleNamespace()
            svc.format_markdown.return_value = "report"

            result = runner.invoke(app, ["overnight", "--lookback", "24"])

        assert result.exit_code == 0
        svc.generate.assert_called_once_with(lookback_hours=24)
