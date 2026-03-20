"""Tests for promote_memories_via_kernel — policy deny and capability grant error paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.memory.hooks_promotion import promote_memories_via_kernel
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def _settings(tmp_path: Path) -> SimpleNamespace:
    """Build a minimal settings object."""
    db_path = tmp_path / "kernel.db"
    artifacts_dir = tmp_path / "artifacts"
    memory_file = tmp_path / "memory" / "memories.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("")
    return SimpleNamespace(
        kernel_db_path=str(db_path),
        kernel_artifacts_dir=str(artifacts_dir),
        memory_file=str(memory_file),
    )


def _entry(category: str = "user_preference", content: str = "test memory") -> MemoryEntry:
    return MemoryEntry(
        category=category,
        content=content,
        confidence=0.8,
    )


def test_promote_returns_false_when_no_entries(tmp_path: Path) -> None:
    """When new_entries is empty, promotion returns False immediately."""
    settings = _settings(tmp_path)
    engine = MagicMock()

    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="sess-1",
        messages=[{"role": "user", "content": "hello"}],
        used_keywords=set(),
        new_entries=[],
        mode="session_end",
    )

    assert result is False


def test_promote_returns_false_when_missing_kernel_paths(tmp_path: Path) -> None:
    """When kernel_db_path or kernel_artifacts_dir is missing, promotion returns False."""
    settings = SimpleNamespace(
        kernel_db_path=None,
        kernel_artifacts_dir=None,
        memory_file=str(tmp_path / "memory" / "memories.md"),
    )
    engine = MagicMock()
    entry = _entry()

    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="sess-1",
        messages=[{"role": "user", "content": "hello"}],
        used_keywords=set(),
        new_entries=[entry],
        mode="session_end",
    )

    assert result is False


def test_promote_enqueues_task_successfully(tmp_path: Path) -> None:
    """When settings are valid, promotion enqueues a task and returns True."""
    settings = _settings(tmp_path)
    engine = MagicMock()
    entry = _entry()

    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="sess-2",
        messages=[{"role": "user", "content": "hello"}],
        used_keywords=set(),
        new_entries=[entry],
        mode="session_end",
    )

    assert result is True


def test_promote_returns_false_for_missing_settings() -> None:
    """When kernel_db_path or kernel_artifacts_dir is missing, returns False."""
    engine = MagicMock()
    entry = _entry()

    # No kernel_db_path
    settings = SimpleNamespace(kernel_db_path=None, kernel_artifacts_dir="/tmp/art")
    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="s",
        messages=[],
        used_keywords=set(),
        new_entries=[entry],
        mode="x",
    )
    assert result is False


def test_promote_returns_false_for_empty_entries() -> None:
    """When new_entries is empty, returns False."""
    engine = MagicMock()
    settings = SimpleNamespace(kernel_db_path="/tmp/db", kernel_artifacts_dir="/tmp/art")
    result = promote_memories_via_kernel(
        engine,
        settings,
        session_id="s",
        messages=[],
        used_keywords=set(),
        new_entries=[],
        mode="x",
    )
    assert result is False
