"""Shared fixtures for CLI surface tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_cli_locale(monkeypatch):
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
