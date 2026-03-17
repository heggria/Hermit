from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_hermit_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for Hermit state."""
    hermit_dir = tmp_path / ".hermit"
    hermit_dir.mkdir()
    return hermit_dir


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Provide a temporary SQLite database path."""
    return tmp_path / "hermit.db"


@pytest.fixture
def tmp_json_path(tmp_path: Path) -> Path:
    """Provide a temporary JSON file path."""
    return tmp_path / "test.json"
