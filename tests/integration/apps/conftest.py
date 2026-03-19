"""Shared fixtures for companion app integration tests."""

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
    get_settings.cache_clear()
