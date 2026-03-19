"""Global test fixtures for Hermit test suite."""

from __future__ import annotations

import fcntl
import os
import weakref
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Lazy KernelStore tracking — deferred to avoid importing the entire kernel
# tree during collection.
# ---------------------------------------------------------------------------
_all_stores: list[weakref.ref] = []
_patched = False


def _ensure_patched() -> None:
    global _patched
    if _patched:
        return
    from hermit.kernel.ledger.journal.store import KernelStore

    _original_init = KernelStore.__init__

    def _tracking_init(self: KernelStore, *args: object, **kwargs: object) -> None:
        _original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        _all_stores.append(weakref.ref(self))

    KernelStore.__init__ = _tracking_init  # type: ignore[assignment]
    _patched = True


@pytest.fixture(autouse=True)
def _auto_close_kernel_stores() -> None:
    """Close all KernelStore connections opened during a test."""
    _ensure_patched()
    yield
    for ref in _all_stores:
        store = ref()
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
    _all_stores.clear()


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
    _ensure_patched()
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
# Auto-marking hook — apply markers based on test directory.
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = str(item.path)
        if "/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
