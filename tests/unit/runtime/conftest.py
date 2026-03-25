"""Shared fixtures for runtime unit tests.

Provides reusable, pre-initialized instances of expensive objects
(KernelStore, CommandSandbox, ArtifactStore) to avoid repeated
schema creation and object construction overhead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.runtime.provider_host.execution.sandbox import CommandSandbox


@pytest.fixture
def mem_store() -> KernelStore:
    """In-memory KernelStore — one per test for full isolation."""
    return KernelStore(Path(":memory:"))


@pytest.fixture
def sandbox_l0() -> CommandSandbox:
    """Lightweight CommandSandbox in l0 mode with no cwd.

    Suitable for pure unit tests that only exercise method logic
    without running real subprocesses.
    """
    return CommandSandbox(mode="l0")


@pytest.fixture
def artifact_store(tmp_path: Path) -> ArtifactStore:
    """ArtifactStore backed by a temporary directory."""
    return ArtifactStore(tmp_path / "artifacts")
