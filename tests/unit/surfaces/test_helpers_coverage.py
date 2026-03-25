"""Additional coverage tests for src/hermit/surfaces/cli/_helpers.py

Targets the ~4 missed statements: format_epoch edge cases, caffeinate
missing binary, require_auth codex paths.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from hermit.surfaces.cli._helpers import caffeinate, format_epoch, require_auth

# ---------------------------------------------------------------------------
# format_epoch edge cases
# ---------------------------------------------------------------------------


class TestFormatEpochEdgeCases:
    def test_zero_timestamp(self) -> None:
        result = format_epoch(0.0)
        assert isinstance(result, str)
        # Should parse as valid ISO format
        parsed = datetime.fromisoformat(result)
        assert parsed.year == 1970

    def test_float_conversion(self) -> None:
        """Ensures the float() call in the implementation works."""
        result = format_epoch(1700000000)
        assert isinstance(result, str)
        parsed = datetime.fromisoformat(result)
        assert parsed is not None


# ---------------------------------------------------------------------------
# caffeinate edge cases
# ---------------------------------------------------------------------------


class TestCaffeinateEdgeCases:
    def test_no_caffeinate_binary(self) -> None:
        settings = SimpleNamespace(prevent_sleep=True)
        with (
            patch("hermit.surfaces.cli._helpers.sys") as mock_sys,
            patch("hermit.surfaces.cli._helpers.shutil") as mock_shutil,
        ):
            mock_sys.platform = "darwin"
            mock_shutil.which.return_value = None  # caffeinate not found
            with caffeinate(settings):
                pass  # Should be no-op


# ---------------------------------------------------------------------------
# require_auth edge cases
# ---------------------------------------------------------------------------


class TestRequireAuthEdgeCases:
    def test_codex_with_auth_file_includes_auth_mode(self) -> None:
        settings = SimpleNamespace(
            has_auth=False,
            provider="codex",
            codex_auth_file_exists=True,
            codex_auth_mode="chatgpt-login",
        )
        with pytest.raises(typer.BadParameter, match=r"chatgpt-login|local"):
            require_auth(settings)

    def test_codex_auth_mode_none(self) -> None:
        """When codex_auth_mode is None, should still produce a message."""
        settings = SimpleNamespace(
            has_auth=False,
            provider="codex",
            codex_auth_file_exists=True,
            codex_auth_mode=None,
        )
        with pytest.raises(typer.BadParameter):
            require_auth(settings)
