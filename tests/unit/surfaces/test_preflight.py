"""Tests for src/hermit/surfaces/cli/_preflight.py"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

# Import via main to avoid circular import issues
import hermit.surfaces.cli.main  # noqa: F401 — triggers full registration
from hermit.surfaces.cli._preflight import (
    _build_serve_preflight,
    _describe_env_source,
    _format_preflight_item,
    _PreflightItem,
    _read_env_file_keys,
    _resolve_env_key,
    _serve_exit_history_path,
    _serve_log_dir,
    _serve_status_path,
    iso_now,
    run_serve_preflight,
    write_serve_status,
)


# ---------------------------------------------------------------------------
# _PreflightItem
# ---------------------------------------------------------------------------
class TestPreflightItem:
    def test_frozen_dataclass(self) -> None:
        item = _PreflightItem(label="Test", ok=True, detail="all good")
        assert item.label == "Test"
        assert item.ok is True
        assert item.detail == "all good"


# ---------------------------------------------------------------------------
# _read_env_file_keys
# ---------------------------------------------------------------------------
class TestReadEnvFileKeys:
    def test_file_with_keys(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".hermit" / ".env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text(
            "API_KEY=secret\nMODEL=claude-3\n# comment\n\nINVALID_LINE\n",
            encoding="utf-8",
        )
        with patch("hermit.surfaces.cli._preflight.hermit_env_path", return_value=env_file):
            keys = _read_env_file_keys()
        assert "API_KEY" in keys
        assert "MODEL" in keys
        assert "INVALID_LINE" not in keys

    def test_file_not_exists(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        with patch("hermit.surfaces.cli._preflight.hermit_env_path", return_value=env_file):
            keys = _read_env_file_keys()
        assert keys == set()

    def test_file_with_comments_and_blanks(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\n  \n  # another\n", encoding="utf-8")
        with patch("hermit.surfaces.cli._preflight.hermit_env_path", return_value=env_file):
            keys = _read_env_file_keys()
        assert keys == set()


# ---------------------------------------------------------------------------
# _resolve_env_key
# ---------------------------------------------------------------------------
class TestResolveEnvKey:
    def test_key_exists(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_KEY_A", "value")
        assert _resolve_env_key("TEST_KEY_A") == "TEST_KEY_A"

    def test_key_not_exists(self, monkeypatch) -> None:
        monkeypatch.delenv("NONEXISTENT_KEY_XYZ", raising=False)
        assert _resolve_env_key("NONEXISTENT_KEY_XYZ") is None

    def test_first_match_returned(self, monkeypatch) -> None:
        monkeypatch.delenv("FIRST_KEY", raising=False)
        monkeypatch.setenv("SECOND_KEY", "val")
        assert _resolve_env_key("FIRST_KEY", "SECOND_KEY") == "SECOND_KEY"


# ---------------------------------------------------------------------------
# _describe_env_source
# ---------------------------------------------------------------------------
class TestDescribeEnvSource:
    def test_key_in_env_file(self) -> None:
        result = _describe_env_source("MY_KEY", {"MY_KEY", "OTHER"})
        assert ".env" in result

    def test_key_in_shell(self) -> None:
        result = _describe_env_source("MY_KEY", {"OTHER"})
        assert "shell" in result


# ---------------------------------------------------------------------------
# _format_preflight_item
# ---------------------------------------------------------------------------
class TestFormatPreflightItem:
    def test_ok_item(self) -> None:
        item = _PreflightItem(label="Auth", ok=True, detail="configured")
        result = _format_preflight_item(item)
        assert "OK" in result
        assert "Auth" in result

    def test_missing_item(self) -> None:
        item = _PreflightItem(label="Auth", ok=False, detail="not found")
        result = _format_preflight_item(item)
        assert "MISSING" in result


# ---------------------------------------------------------------------------
# _build_serve_preflight
# ---------------------------------------------------------------------------
def _make_settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    defaults = dict(
        base_dir=tmp_path,
        provider="claude",
        model="claude-3-7-sonnet",
        resolved_profile=None,
        claude_api_key="sk-test",
        claude_auth_token=None,
        claude_base_url=None,
        openai_api_key=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_access_token=None,
        codex_refresh_token=None,
        codex_auth_mode=None,
        feishu_app_id="",
        feishu_app_secret="",
        feishu_thread_progress=False,
        scheduler_feishu_chat_id="",
        telegram_bot_token="",
        slack_bot_token="",
        slack_app_token="",
        has_auth=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestBuildServePreflight:
    def test_claude_with_api_key_env(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        settings = _make_settings(tmp_path)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            items, errors = _build_serve_preflight("cli", settings)
        assert not errors
        labels = [i.label for i in items]
        assert any("auth" in lbl.lower() or "Auth" in lbl for lbl in labels)

    def test_claude_missing_auth(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
        settings = _make_settings(tmp_path, claude_api_key=None, claude_auth_token=None)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            items, errors = _build_serve_preflight("cli", settings)
        assert len(errors) > 0
        assert any(not i.ok for i in items)

    def test_claude_with_auth_token_env(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.setenv("HERMIT_CLAUDE_AUTH_TOKEN", "token")
        monkeypatch.setenv("HERMIT_CLAUDE_BASE_URL", "https://proxy.example.com")
        settings = _make_settings(tmp_path, claude_api_key=None)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_claude_auth_token_env_no_base_url(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.setenv("HERMIT_AUTH_TOKEN", "token")
        monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
        monkeypatch.delenv("HERMIT_BASE_URL", raising=False)
        settings = _make_settings(tmp_path, claude_api_key=None)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            items, errors = _build_serve_preflight("cli", settings)
        assert not errors
        # Should mention base URL not set
        auth_items = [i for i in items if "auth" in i.label.lower()]
        assert any("base" in i.detail.lower() or "not set" in i.detail.lower() for i in auth_items)

    def test_claude_auth_from_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
        settings = _make_settings(tmp_path, claude_api_key="from-profile")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_claude_auth_token_from_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
        settings = _make_settings(
            tmp_path,
            claude_api_key=None,
            claude_auth_token="profile-token",
            claude_base_url="https://proxy.example.com",
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_claude_auth_token_from_profile_no_base_url(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
        settings = _make_settings(
            tmp_path,
            claude_api_key=None,
            claude_auth_token="profile-token",
            claude_base_url=None,
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_codex_with_env_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        settings = _make_settings(tmp_path, provider="codex")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_codex_with_resolved_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        settings = _make_settings(tmp_path, provider="codex", resolved_openai_api_key="resolved")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_codex_auth_file_no_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        settings = _make_settings(
            tmp_path,
            provider="codex",
            resolved_openai_api_key=None,
            codex_auth_file_exists=True,
            codex_auth_mode="desktop-login",
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert len(errors) > 0

    def test_codex_no_auth(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        settings = _make_settings(tmp_path, provider="codex", resolved_openai_api_key=None)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert len(errors) > 0

    def test_codex_oauth_ready(self, tmp_path: Path, monkeypatch) -> None:
        settings = _make_settings(
            tmp_path,
            provider="codex-oauth",
            codex_auth_file_exists=True,
            codex_access_token="access",
            codex_refresh_token="refresh",
            codex_auth_mode="oauth",
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert not errors

    def test_codex_oauth_incomplete(self, tmp_path: Path, monkeypatch) -> None:
        settings = _make_settings(
            tmp_path,
            provider="codex-oauth",
            codex_auth_file_exists=True,
            codex_access_token=None,
            codex_refresh_token=None,
            codex_auth_mode="oauth",
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert len(errors) > 0

    def test_codex_oauth_missing(self, tmp_path: Path, monkeypatch) -> None:
        settings = _make_settings(
            tmp_path,
            provider="codex-oauth",
            codex_auth_file_exists=False,
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("cli", settings)
        assert len(errors) > 0

    def test_feishu_adapter_ok(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "app-id")
        monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(
            tmp_path,
            feishu_app_id="app-id",
            feishu_app_secret="secret",
        )
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("feishu", settings)
        assert not errors

    def test_feishu_adapter_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, feishu_app_id="", feishu_app_secret="")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("feishu", settings)
        assert len(errors) >= 2

    def test_telegram_adapter_ok(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, telegram_bot_token="token")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("telegram", settings)
        assert not errors

    def test_telegram_adapter_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, telegram_bot_token="")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("telegram", settings)
        assert len(errors) > 0

    def test_slack_adapter_ok(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_SLACK_BOT_TOKEN", "xoxb-token")
        monkeypatch.setenv("HERMIT_SLACK_APP_TOKEN", "xapp-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, slack_bot_token="xoxb", slack_app_token="xapp")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("slack", settings)
        assert not errors

    def test_slack_adapter_missing_both(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("HERMIT_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_SLACK_APP_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, slack_bot_token="", slack_app_token="")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            _items, errors = _build_serve_preflight("slack", settings)
        assert len(errors) >= 2

    def test_profile_configured(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, resolved_profile="my-profile")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            items, _errors = _build_serve_preflight("cli", settings)
        labels = [i.label for i in items]
        assert any("rofile" in lbl for lbl in labels)

    def test_model_from_env(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HERMIT_MODEL", "custom-model")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path, model="custom-model")
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            items, _errors = _build_serve_preflight("cli", settings)
        model_items = [i for i in items if "odel" in i.label]
        assert model_items


# ---------------------------------------------------------------------------
# run_serve_preflight
# ---------------------------------------------------------------------------
class TestRunServePreflight:
    def test_all_ok(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        settings = _make_settings(tmp_path)
        with patch(
            "hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"
        ):
            run_serve_preflight("cli", settings)
        captured = capsys.readouterr()
        assert "check" in captured.out.lower() or "OK" in captured.out

    def test_fails_with_errors(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
        settings = _make_settings(tmp_path, claude_api_key=None, claude_auth_token=None)
        with (
            patch("hermit.surfaces.cli._preflight.hermit_env_path", return_value=tmp_path / ".env"),
            pytest.raises(typer.Exit),
        ):
            run_serve_preflight("cli", settings)


# ---------------------------------------------------------------------------
# write_serve_status
# ---------------------------------------------------------------------------
class TestWriteServeStatus:
    def test_basic_write(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        write_serve_status(settings, "feishu", phase="running", reason="startup", detail="Started.")
        status_file = tmp_path / "logs" / "serve-feishu-status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["phase"] == "running"
        assert data["adapter"] == "feishu"

    def test_with_exception(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        exc = ValueError("test error")
        write_serve_status(
            settings, "feishu", phase="crashed", reason="exception", detail="Crashed.", exc=exc
        )
        data = json.loads((tmp_path / "logs" / "serve-feishu-status.json").read_text())
        assert data["exception_type"] == "ValueError"
        assert "test error" in data["exception_message"]

    def test_with_append_history(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        write_serve_status(
            settings,
            "feishu",
            phase="stopped",
            reason="signal",
            detail="Stopped.",
            append_history=True,
        )
        history_file = tmp_path / "logs" / "serve-feishu-exit-history.jsonl"
        assert history_file.exists()
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
class TestPathHelpers:
    def test_serve_log_dir(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        result = _serve_log_dir(settings)
        assert result == tmp_path / "logs"
        assert result.exists()

    def test_serve_status_path(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        result = _serve_status_path(settings, "feishu")
        assert result.name == "serve-feishu-status.json"

    def test_serve_exit_history_path(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        result = _serve_exit_history_path(settings, "feishu")
        assert result.name == "serve-feishu-exit-history.jsonl"


# ---------------------------------------------------------------------------
# iso_now
# ---------------------------------------------------------------------------
class TestIsoNow:
    def test_returns_valid_iso(self) -> None:
        result = iso_now()
        assert "T" in result
        # Should be parseable
        from datetime import datetime

        parsed = datetime.fromisoformat(result)
        assert parsed is not None
