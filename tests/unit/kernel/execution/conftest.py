"""Shared fixtures for kernel/execution tests.

A module-scoped KernelStore avoids repeated SQLite schema initialization
across test files in this directory.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture(scope="module")
def shared_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[KernelStore]:
    """Single KernelStore per test module — schema init runs once.

    The store is removed from the global ``_current_test_stores`` deque
    immediately after creation so that the function-scoped auto-close
    fixture does not close it prematurely.  Cleanup happens here at
    module teardown instead.
    """
    from tests.conftest import _current_test_stores

    db_path = tmp_path_factory.mktemp("exec") / "state.db"
    store = KernelStore(db_path)
    # Prevent the function-scoped _auto_close_kernel_stores from closing
    # this module-scoped store after the first test.
    try:
        _current_test_stores.remove(store)
    except ValueError:
        pass
    yield store
    store.close()


@pytest.fixture()
def conv_id(shared_store: KernelStore) -> str:
    """Unique conversation ID registered in the shared store."""
    cid = f"conv-{uuid.uuid4().hex[:8]}"
    shared_store.ensure_conversation(cid, source_channel="chat")
    return cid
