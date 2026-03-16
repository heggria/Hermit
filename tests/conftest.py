"""Global test fixtures for Hermit test suite."""

from __future__ import annotations

import os
import weakref
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Module-level KernelStore tracking (patched once, not per-test)
# ---------------------------------------------------------------------------
_all_stores: list[weakref.ref[KernelStore]] = []
_original_init = KernelStore.__init__


def _tracking_init(self: KernelStore, *args: object, **kwargs: object) -> None:
    _original_init(self, *args, **kwargs)  # type: ignore[arg-type]
    _all_stores.append(weakref.ref(self))


KernelStore.__init__ = _tracking_init  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _auto_close_kernel_stores() -> None:  # noqa: PT004
    """Close all KernelStore connections opened during a test."""
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
def _default_locale() -> None:  # noqa: PT004
    os.environ["HERMIT_LOCALE"] = "en-US"
    yield  # type: ignore[misc]
    os.environ.pop("HERMIT_LOCALE", None)


# ---------------------------------------------------------------------------
# Convenience fixture — in-memory KernelStore for fast tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def kernel_store() -> KernelStore:
    """Provide a fresh in-memory KernelStore that is auto-closed after the test."""
    return KernelStore(Path(":memory:"))
