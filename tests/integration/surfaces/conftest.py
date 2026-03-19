"""Shared fixtures for CLI surface tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _force_cli_locale(monkeypatch):
    from hermit.runtime.assembly.config import get_settings

    # Remove any HERMIT_ vars leaked by _load_hermit_env() (main.py) into
    # os.environ at module-import time.  Each test re-adds what it needs
    # via monkeypatch.
    for key in [k for k in os.environ if k.startswith("HERMIT_") and k != "HERMIT_LOCALE"]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
