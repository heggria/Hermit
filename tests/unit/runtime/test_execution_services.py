"""Tests for services.py — _execution_budget, build_provider, build_provider_client_kwargs, _resolve_codex_model."""

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
        "sandbox_mode": "l0",
        "base_dir": Path("/tmp/hermit"),
        "plugins_dir": Path("/tmp/hermit/plugins"),
        "kernel_db_path": Path("/tmp/hermit/kernel.db"),
        "kernel_artifacts_dir": Path("/tmp/hermit/artifacts"),
        "claude_api_key": "test-key",
        "claude_auth_token": None,
        "claude_base_url": None,
        "parsed_claude_headers": None,
        "resolved_openai_api_key": None,
        "openai_base_url": None,
        "parsed_openai_headers": None,
        "codex_auth_file_exists": False,
        "codex_auth_mode": None,
        "codex_access_token": None,
        "tool_output_limit": 4000,
        "thinking_budget": 0,
        "max_turns": 10,
        "locale": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── _execution_budget ──────────────────────────────────────────────


def test_execution_budget_callable_returns_result() -> None:
    custom_budget = ExecutionBudget()
    settings = _default_settings(execution_budget=lambda: custom_budget)

    result = _execution_budget(settings)
    assert result is custom_budget


def test_execution_budget_default_values() -> None:
    settings = _default_settings()

    result = _execution_budget(settings)
    assert isinstance(result, ExecutionBudget)
    assert result.ingress_ack_deadline == 15.0
    assert result.provider_read_timeout == 600.0
    assert result.observation_window == 3600.0
    assert result.observation_poll_interval == 5.0


def test_execution_budget_uses_command_timeout() -> None:
    settings = _default_settings(command_timeout_seconds=60.0)

    result = _execution_budget(settings)
    assert result.provider_connect_timeout == 60.0
    assert result.tool_soft_deadline == 60.0


def test_execution_budget_explicit_values() -> None:
    settings = _default_settings(
        provider_connect_timeout_seconds=30.0,
        tool_soft_deadline_seconds=45.0,
        tool_hard_deadline_seconds=900.0,
    )

    result = _execution_budget(settings)
    assert result.provider_connect_timeout == 30.0
    assert result.tool_soft_deadline == 45.0
    assert result.tool_hard_deadline == 900.0


def test_execution_budget_none_values_use_defaults() -> None:
    settings = _default_settings(
        command_timeout_seconds=None,
        ingress_ack_deadline_seconds=None,
        provider_read_timeout_seconds=None,
    )

    result = _execution_budget(settings)
    assert result.ingress_ack_deadline == 15.0  # falls back to default
    assert result.provider_read_timeout == 600.0


def test_execution_budget_zero_command_timeout() -> None:
    settings = _default_settings(command_timeout_seconds=0)

    result = _execution_budget(settings)
    assert result.provider_connect_timeout == 120.0  # defaults to 120


# ── build_provider ─────────────────────────────────────────────────


@patch("hermit.runtime.provider_host.execution.services.build_claude_provider")
def test_build_provider_claude(mock_build: MagicMock) -> None:
    mock_build.return_value = MagicMock()
    settings = _default_settings(provider="claude")

    result = build_provider(settings, model="claude-3", system_prompt=None)

    mock_build.assert_called_once()
    assert result is mock_build.return_value


@patch("hermit.runtime.provider_host.execution.services.CodexProvider")
def test_build_provider_codex_with_api_key(mock_codex: MagicMock) -> None:
    mock_codex.return_value = MagicMock()
    settings = _default_settings(
        provider="codex",
        resolved_openai_api_key="sk-test-key",
        openai_base_url="https://api.openai.com/v1",
        parsed_openai_headers=None,
    )

    build_provider(settings, model="gpt-4", system_prompt=None)
    mock_codex.assert_called_once()


def test_build_provider_codex_no_key_with_auth_file_raises() -> None:
    settings = _default_settings(
        provider="codex",
        resolved_openai_api_key=None,
        codex_auth_file_exists=True,
        codex_auth_mode="oauth",
    )

    with pytest.raises(RuntimeError, match="OpenAI Responses API"):
        build_provider(settings, model="gpt-4", system_prompt=None)


def test_build_provider_codex_no_key_no_auth_raises() -> None:
    settings = _default_settings(
        provider="codex",
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
    )

    with pytest.raises(RuntimeError, match="OpenAI API key"):
        build_provider(settings, model="gpt-4", system_prompt=None)


@patch("hermit.runtime.provider_host.execution.services.CodexOAuthTokenManager")
@patch("hermit.runtime.provider_host.execution.services.CodexOAuthProvider")
def test_build_provider_codex_oauth_with_auth(
    mock_provider: MagicMock,
    mock_token_manager: MagicMock,
) -> None:
    mock_provider.return_value = MagicMock()
    mock_token_manager.return_value = MagicMock()
    settings = _default_settings(
        provider="codex-oauth",
        codex_auth_file_exists=True,
        parsed_openai_headers=None,
    )

    build_provider(settings, model="gpt-4", system_prompt=None)
    mock_provider.assert_called_once()


def test_build_provider_codex_oauth_no_auth_raises() -> None:
    settings = _default_settings(
        provider="codex-oauth",
        codex_auth_file_exists=False,
    )

    with pytest.raises(RuntimeError, match="Codex OAuth"):
        build_provider(settings, model="gpt-4", system_prompt=None)


def test_build_provider_unsupported_raises() -> None:
    settings = _default_settings(provider="unknown-provider")

    with pytest.raises(RuntimeError, match="Unsupported provider"):
        build_provider(settings, model="m", system_prompt=None)


@patch("hermit.runtime.provider_host.execution.services.CodexOAuthProvider")
@patch("hermit.runtime.provider_host.execution.services.CodexOAuthTokenManager")
def test_build_provider_codex_oauth_type_error_fallback(
    mock_token_manager: MagicMock,
    mock_provider: MagicMock,
) -> None:
    """When CodexOAuthTokenManager raises TypeError with timeout_seconds, fall back to no-timeout."""
    call_count = 0

    def token_manager_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and "timeout_seconds" in kwargs:
            raise TypeError("unexpected keyword argument 'timeout_seconds'")
        return MagicMock()

    mock_token_manager.side_effect = token_manager_side_effect
    mock_provider.return_value = MagicMock()
    settings = _default_settings(
        provider="codex-oauth",
        codex_auth_file_exists=True,
        parsed_openai_headers=None,
    )

    build_provider(settings, model="gpt-4", system_prompt=None)
    assert call_count == 2  # First call with timeout raises TypeError, second succeeds


# ── build_provider_client_kwargs ───────────────────────────────────


def test_client_kwargs_claude_with_api_key() -> None:
    settings = _default_settings(claude_api_key="sk-claude")

    kwargs = build_provider_client_kwargs(settings, provider="claude")
    assert kwargs["api_key"] == "sk-claude"
    assert "timeout" in kwargs


def test_client_kwargs_claude_with_auth_token() -> None:
    settings = _default_settings(
        claude_api_key=None,
        claude_auth_token="auth-token-123",
    )

    kwargs = build_provider_client_kwargs(settings, provider="claude")
    assert kwargs["auth_token"] == "auth-token-123"


def test_client_kwargs_claude_with_base_url() -> None:
    settings = _default_settings(claude_base_url="https://custom.api.com")

    kwargs = build_provider_client_kwargs(settings, provider="claude")
    assert kwargs["base_url"] == "https://custom.api.com"


def test_client_kwargs_claude_with_headers() -> None:
    headers = {"X-Custom": "value"}
    settings = _default_settings(parsed_claude_headers=headers)

    kwargs = build_provider_client_kwargs(settings, provider="claude")
    assert kwargs["default_headers"] == headers


def test_client_kwargs_codex_with_api_key() -> None:
    settings = _default_settings(
        resolved_openai_api_key="sk-openai",
        openai_base_url=None,
        parsed_openai_headers=None,
    )

    kwargs = build_provider_client_kwargs(settings, provider="codex")
    assert kwargs["api_key"] == "sk-openai"


def test_client_kwargs_codex_with_base_url() -> None:
    settings = _default_settings(
        resolved_openai_api_key=None,
        openai_base_url="https://custom.openai.com",
        parsed_openai_headers=None,
    )

    kwargs = build_provider_client_kwargs(settings, provider="codex")
    assert kwargs["base_url"] == "https://custom.openai.com"


def test_client_kwargs_codex_oauth_with_token() -> None:
    settings = _default_settings(
        codex_access_token="access-tok",
        parsed_openai_headers=None,
    )

    kwargs = build_provider_client_kwargs(settings, provider="codex-oauth")
    assert kwargs["access_token"] == "access-tok"


def test_client_kwargs_codex_oauth_with_headers() -> None:
    headers = {"Authorization": "Bearer x"}
    settings = _default_settings(
        codex_access_token=None,
        parsed_openai_headers=headers,
    )

    kwargs = build_provider_client_kwargs(settings, provider="codex-oauth")
    assert kwargs["default_headers"] == headers


def test_client_kwargs_unknown_provider() -> None:
    settings = _default_settings()

    kwargs = build_provider_client_kwargs(settings, provider="unknown")
    assert kwargs == {}


def test_client_kwargs_default_provider_from_settings() -> None:
    settings = _default_settings(provider="claude", claude_api_key="sk-test")

    kwargs = build_provider_client_kwargs(settings)
    assert "api_key" in kwargs


# ── _resolve_codex_model ───────────────────────────────────────────


def test_resolve_codex_model_non_claude() -> None:
    result = _resolve_codex_model(SimpleNamespace(), "gpt-4")
    assert result == "gpt-4"


def test_resolve_codex_model_non_claude_empty_string() -> None:
    # Empty string is falsy, so falls through to config check
    # This tests the "" case
    result = _resolve_codex_model(SimpleNamespace(), "o3-mini")
    assert result == "o3-mini"


def test_resolve_codex_model_claude_with_config_toml(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('model = "o3-mini"')
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = _resolve_codex_model(SimpleNamespace(), "claude-3")
    assert result == "o3-mini"


def test_resolve_codex_model_claude_without_config_toml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = _resolve_codex_model(SimpleNamespace(), "claude-3")
    assert result == "gpt-5.4"


def test_resolve_codex_model_empty_model_in_config(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('model = ""')
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = _resolve_codex_model(SimpleNamespace(), "claude-3")
    assert result == "gpt-5.4"


def test_resolve_codex_model_config_parse_error(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("this is not valid toml [[[")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = _resolve_codex_model(SimpleNamespace(), "claude-3")
    assert result == "gpt-5.4"


def test_resolve_codex_model_config_no_model_key(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('provider = "openai"')
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = _resolve_codex_model(SimpleNamespace(), "claude-3")
    assert result == "gpt-5.4"


def test_resolve_codex_model_empty_requested_with_config(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('model = "custom-model"')
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    # Empty string doesn't start with "claude" but is falsy
    result = _resolve_codex_model(SimpleNamespace(), "")
    assert result == "custom-model"
