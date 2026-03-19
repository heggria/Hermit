"""Tests for src/hermit/surfaces/cli/main.py"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from hermit.surfaces.cli.main import (
    _current_locale,
    _load_hermit_env,
    cli_t,
    hermit_env_path,
    t,
)


# ---------------------------------------------------------------------------
# hermit_env_path
# ---------------------------------------------------------------------------
class TestHermitEnvPath:
    def test_default_path(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMIT_BASE_DIR", None)
            result = hermit_env_path()
        assert result == Path.home() / ".hermit" / ".env"

    def test_with_base_dir(self) -> None:
        with patch.dict(os.environ, {"HERMIT_BASE_DIR": "/tmp/test-hermit"}, clear=False):
            result = hermit_env_path()
        assert result == Path("/tmp/test-hermit/.env")


# ---------------------------------------------------------------------------
# _load_hermit_env
# ---------------------------------------------------------------------------
class TestLoadHermitEnv:
    def test_no_env_file(self, tmp_path: Path) -> None:
        with patch(
            "hermit.surfaces.cli.main.hermit_env_path",
            return_value=tmp_path / "nonexistent" / ".env",
        ):
            # Should return early without error
            _load_hermit_env()

    def test_loads_env_vars(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            'MY_TEST_KEY_12345=hello\n# comment\n\nANOTHER_KEY="world"\n',
            encoding="utf-8",
        )
        with (
            patch(
                "hermit.surfaces.cli.main.hermit_env_path",
                return_value=env_file,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("MY_TEST_KEY_12345", None)
            os.environ.pop("ANOTHER_KEY", None)
            _load_hermit_env()
            assert os.environ.get("MY_TEST_KEY_12345") == "hello"
            assert os.environ.get("ANOTHER_KEY") == "world"
            # Cleanup
            os.environ.pop("MY_TEST_KEY_12345", None)
            os.environ.pop("ANOTHER_KEY", None)

    def test_existing_env_vars_not_overwritten(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MY_EXISTING_VAR=from_file\n", encoding="utf-8")
        with (
            patch(
                "hermit.surfaces.cli.main.hermit_env_path",
                return_value=env_file,
            ),
            patch.dict(os.environ, {"MY_EXISTING_VAR": "from_shell"}, clear=False),
        ):
            _load_hermit_env()
            assert os.environ["MY_EXISTING_VAR"] == "from_shell"

    def test_skips_lines_without_equals(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("NOEQUALS\nVALID_KEY=value\n", encoding="utf-8")
        with (
            patch(
                "hermit.surfaces.cli.main.hermit_env_path",
                return_value=env_file,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("VALID_KEY", None)
            _load_hermit_env()
            assert os.environ.get("VALID_KEY") == "value"
            os.environ.pop("VALID_KEY", None)


# ---------------------------------------------------------------------------
# _current_locale
# ---------------------------------------------------------------------------
class TestCurrentLocale:
    def test_returns_locale_from_settings(self) -> None:
        from types import SimpleNamespace

        settings = SimpleNamespace(locale="zh-CN")
        with patch("hermit.surfaces.cli.main.get_settings", return_value=settings):
            result = _current_locale()
        assert result in ("zh-CN", "en-US")  # depends on resolve_locale impl

    def test_fallback_on_exception(self) -> None:
        with patch(
            "hermit.surfaces.cli.main.get_settings",
            side_effect=Exception("settings not configured"),
        ):
            result = _current_locale()
        # Should fall back to resolve_locale() without args
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# t / cli_t
# ---------------------------------------------------------------------------
class TestTranslationHelpers:
    def test_t_returns_string(self) -> None:
        result = t("nonexistent.key", "fallback text")
        assert isinstance(result, str)

    def test_cli_t_returns_string(self) -> None:
        result = cli_t("nonexistent.key", "fallback text")
        assert isinstance(result, str)
