"""Shared fixtures for companion app tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_hermit_env(monkeypatch):
    from hermit.runtime.assembly.config import get_settings

    for key in [k for k in os.environ if k.startswith("HERMIT_") and k != "HERMIT_LOCALE"]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    # Post-clear removed: monkeypatch restores env vars automatically and the
    # next test's pre-clear ensures a fresh cache.  Skipping the redundant call
    # saves one lru_cache invalidation per test.
