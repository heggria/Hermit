"""Tests for the `hermit overnight` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.surfaces.cli.main import app

runner = CliRunner()


def test_overnight_no_db(tmp_path: Path) -> None:
    """When kernel DB does not exist, should exit with error."""
    settings = SimpleNamespace(base_dir=str(tmp_path))
    with patch("hermit.runtime.assembly.config.get_settings", return_value=settings):
        result = runner.invoke(app, ["overnight"])
    assert result.exit_code == 1
    assert "No kernel database found" in result.output


def test_overnight_markdown_output(tmp_path: Path) -> None:
    """With valid DB, should produce markdown output."""
    db_dir = tmp_path / "kernel"
    db_dir.mkdir()
    store = KernelStore(db_dir / "state.db")
    store.close()

    settings = SimpleNamespace(base_dir=str(tmp_path))
    with patch("hermit.runtime.assembly.config.get_settings", return_value=settings):
        result = runner.invoke(app, ["overnight"])
    assert result.exit_code == 0
    assert "Overnight Report" in result.output


def test_overnight_json_output(tmp_path: Path) -> None:
    """With --json flag, should produce JSON output."""
    db_dir = tmp_path / "kernel"
    db_dir.mkdir()
    store = KernelStore(db_dir / "state.db")
    store.close()

    settings = SimpleNamespace(base_dir=str(tmp_path))
    with patch("hermit.runtime.assembly.config.get_settings", return_value=settings):
        result = runner.invoke(app, ["overnight", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "tasks_completed" in data


def test_overnight_custom_lookback(tmp_path: Path) -> None:
    """--lookback parameter should be passed through."""
    db_dir = tmp_path / "kernel"
    db_dir.mkdir()
    store = KernelStore(db_dir / "state.db")
    store.close()

    settings = SimpleNamespace(base_dir=str(tmp_path))
    with patch("hermit.runtime.assembly.config.get_settings", return_value=settings):
        result = runner.invoke(app, ["overnight", "--lookback", "24"])
    assert result.exit_code == 0
    assert "24h" in result.output
