"""Tests for KernelStore.get_schema_version() and schema_version()."""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.ledger.journal.store import _SCHEMA_VERSION, KernelStore


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


# ── schema_version() ────────────────────────────────────────────────


def test_schema_version_returns_current(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.schema_version() == _SCHEMA_VERSION


def test_schema_version_matches_constant(tmp_path: Path) -> None:
    """The value stored in the DB should equal the module-level constant."""
    store = _make_store(tmp_path)
    assert store.schema_version() == "18"


# ── get_schema_version() ────────────────────────────────────────────


def test_get_schema_version_returns_current(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.get_schema_version() == _SCHEMA_VERSION


def test_get_schema_version_is_alias(tmp_path: Path) -> None:
    """get_schema_version() must return the same value as schema_version()."""
    store = _make_store(tmp_path)
    assert store.get_schema_version() == store.schema_version()


def test_get_schema_version_return_type(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    result = store.get_schema_version()
    assert isinstance(result, str)
    assert result != ""
