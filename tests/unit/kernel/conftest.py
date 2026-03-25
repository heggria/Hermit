"""Shared fixtures for kernel unit tests.

The ``fast_store`` fixture is a local alias for the root-level ``kernel_store``
fixture (see tests/conftest.py) which provides a function-scoped in-memory
KernelStore.  This avoids file-backed SQLite schema creation (~300 ms each)
and gives ~15x speedup per test that uses it.
"""

from __future__ import annotations

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def fast_store(kernel_store: KernelStore) -> KernelStore:
    """Alias for ``kernel_store`` — an in-memory KernelStore.

    Kept as a convenience name for kernel tests that were migrated from
    explicit ``KernelStore(tmp_path / "state.db")`` calls.
    """
    return kernel_store
