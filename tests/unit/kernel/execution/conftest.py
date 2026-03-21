"""Shared fixtures for kernel/execution tests.

A module-scoped KernelStore avoids repeated SQLite schema initialization
across test files in this directory.
"""

from __future__ import annotations

import uuid

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture(scope="module")
def shared_store(tmp_path_factory: pytest.TempPathFactory) -> KernelStore:
    """Single KernelStore per test module — schema init runs once."""
    db_path = tmp_path_factory.mktemp("exec") / "state.db"
    return KernelStore(db_path)


@pytest.fixture()
def conv_id(shared_store: KernelStore) -> str:
    """Unique conversation ID registered in the shared store."""
    cid = f"conv-{uuid.uuid4().hex[:8]}"
    shared_store.ensure_conversation(cid, source_channel="chat")
    return cid
