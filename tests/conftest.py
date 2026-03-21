"""Global test fixtures for Hermit test suite."""

from __future__ import annotations

import fcntl
import os
from collections import deque
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Lazy KernelStore tracking — deferred to avoid importing the entire kernel
# tree during collection.
#
# Uses a deque of direct refs populated per-test via _current_test_stores.
# After each test the fixture drains the deque and closes every store.
# Using direct refs + deque.popleft() avoids the overhead of weakref
# dereferencing and list iteration over dead references.
# ---------------------------------------------------------------------------
_current_test_stores: deque = deque()
_patched = False


def _ensure_patched() -> None:
    global _patched
    if _patched:
        return
    from hermit.kernel.ledger.journal.store import KernelStore

    _original_init = KernelStore.__init__

    def _tracking_init(self: KernelStore, *args: object, **kwargs: object) -> None:
        _original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        _current_test_stores.append(self)

    KernelStore.__init__ = _tracking_init  # type: ignore[assignment]
    _patched = True


@pytest.fixture(autouse=True)
def _auto_close_kernel_stores() -> None:
    """Close all KernelStore connections opened during a test.

    The monkey-patch is applied once via ``pytest_sessionstart``; this fixture
    only performs the cleanup sweep after each test.  Uses a deque for O(1)
    popleft and only processes stores created during the current test.
    """
    yield
    while _current_test_stores:
        store = _current_test_stores.popleft()
        try:
            store.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Default locale — most tests expect en-US; zh-CN tests override locally.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _default_locale() -> None:
    os.environ["HERMIT_LOCALE"] = "en-US"
    yield  # type: ignore[misc]
    os.environ.pop("HERMIT_LOCALE", None)


# ---------------------------------------------------------------------------
# Convenience fixture — in-memory KernelStore for fast tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def kernel_store():
    """Provide a fresh in-memory KernelStore that is auto-closed after the test."""
    from hermit.kernel.ledger.journal.store import KernelStore

    return KernelStore(Path(":memory:"))


# ---------------------------------------------------------------------------
# Concurrent run lock — prevent multiple test suites from competing for CPU.
# Only acquired on the controller process (not xdist workers).
# ---------------------------------------------------------------------------
_lock_file = None


def pytest_configure(config: pytest.Config) -> None:
    global _lock_file
    # xdist workers have workerinput; skip lock for them
    if hasattr(config, "workerinput"):
        return
    # Allow skipping lock for benchmarking / CI environments
    if os.environ.get("_HERMIT_SKIP_TEST_LOCK"):
        return
    lock_path = Path.home() / ".hermit" / ".test-suite.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _lock_file = lock_path.open("w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _lock_file.close()
        _lock_file = None
        pytest.exit("Another test suite is already running. Aborting.", returncode=1)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _lock_file
    if _lock_file is not None:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()
        _lock_file = None


# ---------------------------------------------------------------------------
# Session start — apply the KernelStore tracking monkey-patch once.
# ---------------------------------------------------------------------------
def pytest_sessionstart(session: pytest.Session) -> None:
    _ensure_patched()


# ---------------------------------------------------------------------------
# Auto-marking hook — apply markers based on test directory.
# Pre-compute marker objects to avoid creating them per-item.
# ---------------------------------------------------------------------------
_integration_marker = pytest.mark.integration
_e2e_marker = pytest.mark.e2e
_SEP = os.sep


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_marker = _integration_marker
    e2e_marker = _e2e_marker
    sep = _SEP
    int_segment = f"{sep}integration{sep}"
    e2e_segment = f"{sep}e2e{sep}"

    for item in items:
        path_str = os.fspath(item.path)
        if int_segment in path_str:
            item.add_marker(integration_marker)
        elif e2e_segment in path_str:
            item.add_marker(e2e_marker)
