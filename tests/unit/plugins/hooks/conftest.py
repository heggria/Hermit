"""Shared fixtures for hooks plugin tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture
def kernel_store() -> KernelStore:
    """In-memory KernelStore — avoids disk I/O per test."""
    store = KernelStore(Path(":memory:"))
    yield store
    store.close()
