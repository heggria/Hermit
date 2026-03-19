"""Tests for runtime/provider_host/execution/services.py — coverage for missed lines.

Covers: _execution_budget fallback path, build_provider for codex/codex-oauth,
build_provider_client_kwargs, _resolve_codex_model.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermit.runtime.control.lifecycle.budgets import ExecutionBudget
from hermit.runtime.provider_host.execution.services import (
    _execution_budget,
    _resolve_codex_model,
    build_provider,
    build_provider_client_kwargs,
)


def _default_settings(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "execution_budget": None,
        "command_timeout_seconds": 120.0,
        "ingress_ack_deadline_seconds": 15.0,
        "provider_connect_timeout_seconds": None,
        "provider_read_timeout_seconds": 600.0,
        "provider_stream_idle_timeout_seconds": 600.0,
        "tool_soft_deadline_seconds": None,
        "tool_hard_deadline_seconds": None,
        "observation_window_seconds": 3600.0,
        "observation_poll_interval_seconds": 5.0,
        "provider": "claude",
        "model": "claude-3",
        "claude_api_key": "test-key",
        "claude_auth_token": None,
        "claude_base_url": None,
        "parsed_claude_headers": {},
        "resolved_openai_api_key": "oai-key",
        "openai_api_key": "oai-key",
        "openai_base_url": None,
        "parsed_openai_headers": {},
        "codex_auth_file_exists": False,
        "codex_auth_mode": None,
        "codex_access_token": None,
        "codex_refresh_token": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _execution_budget
# ---------------------------------------------------------------------------


class TestExecutionBudget:
    def test_callable_builder(self) -> None:
        expected = ExecutionBudget(
            ingress_ack_deadline=5.0,
            provider_connect_timeout=30.0,
            provider_read_timeout=60.0,
            provider_stream_idle_timeout=60.0,
            tool_soft_deadline=30.0,
            tool_hard_deadline=120.0,
            observation_window=600.0,
            observation_poll_interval=5.0,
        )
        settings = _default_settings(execution_budget=lambda: expected)
        result = _execution_budget(settings)
        assert result is expected

    def test_fallback_values(self) -> None:
        settings = _default_settings(
            execution_budget=None,
            command_timeout_seconds=30.0,
        )
        result = _execution_budget(settings)
        assert result.tool_soft_deadline == 30.0
        assert result.tool_hard_deadline == 600.0  # max(30, 600)

    def test_zero_timeout_uses_defaults(self) -> None:
        settings = _default_settings(
            execution_budget=None,
            command_timeout_seconds=0,
        )
        result = _execution_budget(settings)
        assert result.tool_soft_deadline >= 1.0


# ---------------------------------------------------------------------------
# build_provider
# ---------------------------------------------------------------------------


class TestBuildProvider:
    def test_claude_provider(self) -> None:
        settings = _default_settings(provider="claude")
        with patch("hermit.runtime.provider_host.execution.services.build_claude_provider") as mock:
            mock.return_value = MagicMock()
            build_provider(settings, model="claude-3")
            mock.assert_called_once()

    def test_codex_provider_no_key_no_auth(self) -> None:
        settings = _default_settings(
            provider="codex",
            resolved_openai_api_key=None,
            codex_auth_file_exists=False,
        )
        with pytest.raises(RuntimeError, match="requires an OpenAI API key"):
            build_provider(settings, model="gpt-4")

    def test_codex_provider_no_key_with_auth_file(self) -> None:
        settings = _default_settings(
            provider="codex",
            resolved_openai_api_key=None,
            codex_auth_file_exists=True,
            codex_auth_mode="chatgpt-login",
        )
        with pytest.raises(RuntimeError, match="chatgpt-login"):
            build_provider(settings, model="gpt-4")

    def test_codex_provider_with_key(self) -> None:
        settings = _default_settings(provider="codex")
        with patch("hermit.runtime.provider_host.execution.services.CodexProvider") as mock:
            mock.return_value = MagicMock()
            build_provider(settings, model="gpt-4")
            mock.assert_called_once()

    def test_codex_oauth_no_auth(self) -> None:
        settings = _default_settings(
            provider="codex-oauth",
            codex_auth_file_exists=False,
        )
        with pytest.raises(RuntimeError, match="local Codex login"):
            build_provider(settings, model="gpt-4")

    def test_codex_oauth_with_auth(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text('{"tokens": {"access_token": "at", "refresh_token": "rt"}}')
        settings = _default_settings(
            provider="codex-oauth",
            codex_auth_file_exists=True,
        )
        with (
            patch(
                "hermit.runtime.provider_host.execution.services.CodexOAuthTokenManager"
            ) as tm_cls,
            patch(
                "hermit.runtime.provider_host.execution.services.CodexOAuthProvider"
            ) as provider_cls,
        ):
            tm_cls.return_value = MagicMock()
            provider_cls.return_value = MagicMock()
            build_provider(settings, model="gpt-4")
            provider_cls.assert_called_once()

    def test_unsupported_provider_raises(self) -> None:
        settings = _default_settings(provider="gemini")
        with pytest.raises(RuntimeError, match="Unsupported"):
            build_provider(settings, model="m")


# ---------------------------------------------------------------------------
# build_provider_client_kwargs
# ---------------------------------------------------------------------------


class TestBuildProviderClientKwargs:
    def test_claude_kwargs(self) -> None:
        settings = _default_settings(
            claude_api_key="key",
            claude_auth_token="token",
            claude_base_url="https://api.example.com",
            parsed_claude_headers={"X-Custom": "v"},
        )
        kwargs = build_provider_client_kwargs(settings, provider="claude")
        assert kwargs["api_key"] == "key"
        assert kwargs["auth_token"] == "token"
        assert kwargs["base_url"] == "https://api.example.com"
        assert kwargs["default_headers"] == {"X-Custom": "v"}
        assert "timeout" in kwargs

    def test_codex_kwargs(self) -> None:
        settings = _default_settings(
            resolved_openai_api_key="oai-key",
            openai_base_url="https://oai.example.com",
            parsed_openai_headers={"X-OAI": "v"},
        )
        kwargs = build_provider_client_kwargs(settings, provider="codex")
        assert kwargs["api_key"] == "oai-key"
        assert kwargs["base_url"] == "https://oai.example.com"

    def test_codex_oauth_kwargs(self) -> None:
        settings = _default_settings(
            codex_access_token="at-123",
            parsed_openai_headers={},
        )
        kwargs = build_provider_client_kwargs(settings, provider="codex-oauth")
        assert kwargs["access_token"] == "at-123"

    def test_unknown_provider_returns_empty(self) -> None:
        settings = _default_settings()
        kwargs = build_provider_client_kwargs(settings, provider="unknown")
        assert kwargs == {}

    def test_default_provider_from_settings(self) -> None:
        settings = _default_settings(provider="claude")
        kwargs = build_provider_client_kwargs(settings)
        assert "timeout" in kwargs


# ---------------------------------------------------------------------------
# _resolve_codex_model
# ---------------------------------------------------------------------------


class TestResolveCodexModel:
    def test_non_claude_model_returned_as_is(self) -> None:
        settings = _default_settings()
        assert _resolve_codex_model(settings, "gpt-4") == "gpt-4"

    def test_claude_model_falls_back_to_default(self, tmp_path: Path) -> None:
        settings = _default_settings()
        with patch("hermit.runtime.provider_host.execution.services.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            result = _resolve_codex_model(settings, "claude-3")
            assert result == "gpt-5.4"

    def test_empty_model_falls_back(self, tmp_path: Path) -> None:
        settings = _default_settings()
        with patch("hermit.runtime.provider_host.execution.services.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            result = _resolve_codex_model(settings, "")
            assert result == "gpt-5.4"

    def test_reads_from_config_toml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text('model = "gpt-4o"\n')
        settings = _default_settings()
        with patch("hermit.runtime.provider_host.execution.services.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            result = _resolve_codex_model(settings, "claude-3")
            assert result == "gpt-4o"
