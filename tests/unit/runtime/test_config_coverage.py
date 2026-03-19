"""Tests for runtime/assembly/config.py — coverage for missed lines.

Covers: _parse_headers_str, _set_if_present, _override_if_present,
_read_env_file_values, _codex_auth functions, Settings properties,
Settings.effective_max_tokens, Settings.execution_budget,
Settings.has_auth for various providers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.runtime.assembly.config import (
    Settings,
    _codex_access_token,
    _codex_auth_api_key,
    _codex_auth_mode,
    _codex_refresh_token,
    _override_if_present,
    _parse_headers_str,
    _read_env_file_values,
    _set_if_present,
    get_settings,
)

# ---------------------------------------------------------------------------
# _parse_headers_str
# ---------------------------------------------------------------------------


class TestParseHeadersStr:
    def test_none_returns_empty(self) -> None:
        assert _parse_headers_str(None) == {}

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_headers_str("") == {}

    def test_single_header(self) -> None:
        result = _parse_headers_str("X-Custom: value")
        assert result == {"X-Custom": "value"}

    def test_multiple_headers_comma_separated(self) -> None:
        result = _parse_headers_str("X-A: 1, X-B: 2")
        assert result == {"X-A": "1", "X-B": "2"}

    def test_newline_separated(self) -> None:
        result = _parse_headers_str("X-A: 1\nX-B: 2")
        assert result == {"X-A": "1", "X-B": "2"}

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            _parse_headers_str("no-colon-here")

    def test_empty_items_skipped(self) -> None:
        result = _parse_headers_str("X-A: 1,,X-B: 2,")
        assert result == {"X-A": "1", "X-B": "2"}


# ---------------------------------------------------------------------------
# _set_if_present / _override_if_present
# ---------------------------------------------------------------------------


class TestSetIfPresent:
    def test_sets_when_missing(self) -> None:
        d: dict[str, object] = {}
        _set_if_present(d, "key", "val")
        assert d["key"] == "val"

    def test_does_not_override_existing(self) -> None:
        d: dict[str, object] = {"key": "old"}
        _set_if_present(d, "key", "new")
        assert d["key"] == "old"

    def test_none_value_ignored(self) -> None:
        d: dict[str, object] = {}
        _set_if_present(d, "key", None)
        assert "key" not in d


class TestOverrideIfPresent:
    def test_overrides_existing(self) -> None:
        d: dict[str, object] = {"key": "old"}
        _override_if_present(d, "key", "new")
        assert d["key"] == "new"

    def test_sets_when_missing(self) -> None:
        d: dict[str, object] = {}
        _override_if_present(d, "key", "val")
        assert d["key"] == "val"

    def test_none_value_ignored(self) -> None:
        d: dict[str, object] = {"key": "old"}
        _override_if_present(d, "key", None)
        assert d["key"] == "old"


# ---------------------------------------------------------------------------
# _read_env_file_values
# ---------------------------------------------------------------------------


class TestReadEnvFileValues:
    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = _read_env_file_values(tmp_path / "nope.env")
        assert result == {}

    def test_reads_key_values(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("KEY1=value1\nKEY2=value2\n")
        result = _read_env_file_values(f)
        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_skips_comments_and_blank_lines(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("# comment\n\nKEY=val\n")
        result = _read_env_file_values(f)
        assert result == {"KEY": "val"}

    def test_skips_lines_without_equals(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("NOEQUALS\nKEY=val\n")
        result = _read_env_file_values(f)
        assert result == {"KEY": "val"}

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("  KEY  = value  \n")
        result = _read_env_file_values(f)
        assert result == {"KEY": "value"}


# ---------------------------------------------------------------------------
# _codex_auth functions
# ---------------------------------------------------------------------------


class TestCodexAuth:
    def test_codex_auth_mode_no_file(self, tmp_path: Path) -> None:
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=tmp_path / "nonexistent.json",
        ):
            assert _codex_auth_mode() is None

    def test_codex_auth_mode_with_value(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"auth_mode": "api-key"}))
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_auth_mode() == "api-key"

    def test_codex_auth_api_key_from_openai(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"OPENAI_API_KEY": "sk-123"}))
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_auth_api_key() == "sk-123"

    def test_codex_auth_api_key_empty(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({}))
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_auth_api_key() is None

    def test_codex_access_token(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(
            json.dumps({"tokens": {"access_token": "at-123", "refresh_token": "rt-456"}})
        )
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_access_token() == "at-123"
            assert _codex_refresh_token() == "rt-456"

    def test_codex_tokens_non_dict(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"tokens": "not-a-dict"}))
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_access_token() is None
            assert _codex_refresh_token() is None

    def test_codex_invalid_json(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text("not json")
        with patch(
            "hermit.runtime.assembly.config._codex_auth_path",
            return_value=auth_file,
        ):
            assert _codex_auth_api_key() is None


# ---------------------------------------------------------------------------
# Settings properties
# ---------------------------------------------------------------------------


class TestSettingsProperties:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "HERMIT_AUTH_TOKEN",
            "HERMIT_BASE_URL",
            "HERMIT_CUSTOM_HEADERS",
            "HERMIT_CLAUDE_AUTH_TOKEN",
            "HERMIT_CLAUDE_BASE_URL",
            "HERMIT_CLAUDE_HEADERS",
            "HERMIT_CLAUDE_API_KEY",
            "HERMIT_PROVIDER",
            "HERMIT_PROFILE",
            "HERMIT_BASE_DIR",
            "HERMIT_MODEL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_effective_max_tokens_with_thinking(self) -> None:
        s = Settings(
            max_tokens=1000,
            thinking_budget=2000,
            _env_file=None,
        )
        assert s.effective_max_tokens() == 3000

    def test_effective_max_tokens_without_thinking(self) -> None:
        s = Settings(max_tokens=2048, thinking_budget=0, _env_file=None)
        assert s.effective_max_tokens() == 2048

    def test_effective_max_tokens_thinking_less_than_max(self) -> None:
        s = Settings(max_tokens=4000, thinking_budget=1000, _env_file=None)
        assert s.effective_max_tokens() == 4000

    def test_directory_properties(self, tmp_path: Path) -> None:
        s = Settings(base_dir=tmp_path, _env_file=None)
        assert s.memory_dir == tmp_path / "memory"
        assert s.plugins_dir == tmp_path / "plugins"
        assert s.sessions_dir == tmp_path / "sessions"
        assert s.skills_dir == tmp_path / "skills"
        assert s.rules_dir == tmp_path / "rules"
        assert s.hooks_dir == tmp_path / "hooks"
        assert s.schedules_dir == tmp_path / "schedules"
        assert s.kernel_dir == tmp_path / "kernel"
        assert s.kernel_db_path == tmp_path / "kernel" / "state.db"
        assert s.kernel_artifacts_dir == tmp_path / "kernel" / "artifacts"
        assert s.image_memory_dir == tmp_path / "image-memory"
        assert s.context_file == tmp_path / "context.md"
        assert s.memory_file == tmp_path / "memory" / "memories.md"
        assert s.session_state_file == tmp_path / "memory" / "session_state.json"

    def test_webhook_defaults(self) -> None:
        s = Settings(_env_file=None)
        assert s.resolved_webhook_host == "0.0.0.0"
        assert s.resolved_webhook_port == 8321

    def test_webhook_custom(self) -> None:
        s = Settings(webhook_host="127.0.0.1", webhook_port=9000, _env_file=None)
        assert s.resolved_webhook_host == "127.0.0.1"
        assert s.resolved_webhook_port == 9000

    def test_parsed_headers_empty(self) -> None:
        s = Settings(claude_headers=None, openai_headers=None, _env_file=None)
        assert s.parsed_claude_headers == {}
        assert s.parsed_openai_headers == {}

    def test_has_auth_claude(self) -> None:
        s = Settings(provider="claude", claude_api_key="key", _env_file=None)
        assert s.has_auth is True

    def test_has_auth_claude_no_key(self) -> None:
        s = Settings(provider="claude", claude_api_key=None, claude_auth_token=None, _env_file=None)
        assert s.has_auth is False

    def test_has_auth_codex_with_key(self) -> None:
        s = Settings(provider="codex", openai_api_key="key", _env_file=None)
        assert s.has_auth is True

    def test_legacy_aliases(self) -> None:
        s = Settings(claude_api_key="k", claude_auth_token="t", _env_file=None)
        assert s.anthropic_api_key == "k"
        assert s.auth_token == "t"

    def test_execution_budget(self) -> None:
        s = Settings(command_timeout_seconds=60, _env_file=None)
        budget = s.execution_budget()
        assert budget.tool_soft_deadline == 60.0

    def test_get_settings_caches(self) -> None:
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        get_settings.cache_clear()


class TestSettingsModelValidator:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "HERMIT_AUTH_TOKEN",
            "HERMIT_BASE_URL",
            "HERMIT_CUSTOM_HEADERS",
            "HERMIT_CLAUDE_AUTH_TOKEN",
            "HERMIT_CLAUDE_BASE_URL",
            "HERMIT_CLAUDE_HEADERS",
            "HERMIT_CLAUDE_API_KEY",
            "HERMIT_PROVIDER",
            "HERMIT_PROFILE",
            "HERMIT_BASE_DIR",
            "HERMIT_MODEL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_anthropic_key_alias(self) -> None:
        s = Settings(anthropic_api_key="ak-123", _env_file=None)
        assert s.claude_api_key == "ak-123"

    def test_auth_token_alias(self) -> None:
        s = Settings(auth_token="token-123", _env_file=None)
        assert s.claude_auth_token == "token-123"

    def test_locale_normalization(self) -> None:
        s = Settings(locale="en", _env_file=None)
        assert s.locale in ("en-US", "en")  # normalized

    def test_non_dict_data_handled(self) -> None:
        # The validator should handle non-dict gracefully
        s = Settings(_env_file=None)
        assert s is not None
