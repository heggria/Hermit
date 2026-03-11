from __future__ import annotations

from hermit.config import Settings
from hermit.main import _build_anthropic_client_kwargs
from hermit.provider.profiles import load_plugin_variables


def test_settings_parse_prefixed_env_fields(monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_HEADERS", raising=False)
    monkeypatch.setenv("HERMIT_AUTH_TOKEN", "token-123")
    monkeypatch.setenv("HERMIT_BASE_URL", "https://example.internal/claude")
    monkeypatch.setenv("HERMIT_CUSTOM_HEADERS", "X-Biz-Id: claude-code, X-Test: yes")
    monkeypatch.setenv("HERMIT_MODEL", "claude-sonnet-4-6")

    settings = Settings(_env_file=None)

    assert settings.auth_token == "token-123"
    assert settings.base_url == "https://example.internal/claude"
    assert settings.model == "claude-sonnet-4-6"
    assert settings.parsed_custom_headers == {
        "X-Biz-Id": "claude-code",
        "X-Test": "yes",
    }


def test_build_anthropic_client_kwargs_supports_auth_token_and_headers(monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_HEADERS", raising=False)
    settings = Settings(
        auth_token="token-123",
        base_url="https://example.internal/claude",
        custom_headers="X-Biz-Id: claude-code",
        _env_file=None,
    )

    kwargs = _build_anthropic_client_kwargs(settings)

    assert kwargs == {
        "auth_token": "token-123",
        "base_url": "https://example.internal/claude",
        "default_headers": {"X-Biz-Id": "claude-code"},
    }


def test_build_anthropic_client_kwargs_keeps_api_key_when_present(monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_HEADERS", raising=False)
    settings = Settings(
        anthropic_api_key="api-key",
        auth_token="token-123",
        base_url="https://example.internal/claude",
        _env_file=None,
    )

    kwargs = _build_anthropic_client_kwargs(settings)

    assert kwargs["api_key"] == "api-key"
    assert kwargs["auth_token"] == "token-123"


def test_custom_headers_requires_colon_separator(monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_HEADERS", raising=False)
    settings = Settings(custom_headers="broken-header", _env_file=None)

    try:
        _ = settings.parsed_custom_headers
    except ValueError as exc:
        assert "Invalid HERMIT_CUSTOM_HEADERS format" in str(exc)
    else:
        raise AssertionError("Expected custom header parsing failure")


def test_settings_load_default_profile_from_config_toml(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "codex-local"

[profiles.codex-local]
provider = "codex-oauth"
model = "gpt-5.4"
max_turns = 42
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.delenv("HERMIT_PROVIDER", raising=False)
    monkeypatch.delenv("HERMIT_MODEL", raising=False)

    settings = Settings()

    assert settings.resolved_profile == "codex-local"
    assert settings.provider == "codex-oauth"
    assert settings.model == "gpt-5.4"
    assert settings.max_turns == 42
    assert settings.config_file == base_dir / "config.toml"


def test_settings_loads_legacy_auth_keys_from_env_file(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / ".env").write_text(
        "\n".join(
            [
                "HERMIT_AUTH_TOKEN=token-123",
                "HERMIT_BASE_URL=https://example.internal/claude",
                "HERMIT_CUSTOM_HEADERS=X-Biz-Id: claude-code",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (base_dir / "config.toml").write_text(
        """
[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_PROFILE", "claude-code")
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_HEADERS", raising=False)

    settings = Settings(_env_file=base_dir / ".env")

    assert settings.auth_token == "token-123"
    assert settings.base_url == "https://example.internal/claude"
    assert settings.custom_headers == "X-Biz-Id: claude-code"
    assert settings.has_auth is True


def test_env_overrides_profile_values(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
[profiles.shared]
provider = "claude"
model = "claude-3-7-sonnet-latest"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_PROFILE", "shared")
    monkeypatch.setenv("HERMIT_PROVIDER", "codex-oauth")
    monkeypatch.delenv("HERMIT_MODEL", raising=False)

    settings = Settings()

    assert settings.resolved_profile == "shared"
    assert settings.provider == "codex-oauth"
    assert settings.model == "claude-3-7-sonnet-latest"


def test_load_plugin_variables_from_config_toml(tmp_path) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
[plugins.github.variables]
github_pat = "ghp_test_123"
github_mcp_url = "https://example.github.test/mcp"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    variables = load_plugin_variables(base_dir, "github")

    assert variables == {
        "github_pat": "ghp_test_123",
        "github_mcp_url": "https://example.github.test/mcp",
    }
