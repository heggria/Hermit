from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hermit.infra.system.i18n import locale_from_env, normalize_locale
from hermit.runtime.control.lifecycle.budgets import ExecutionBudget
from hermit.runtime.provider_host.shared.profiles import (
    config_path_for_base_dir,
    load_profile_catalog,
    resolve_profile,
)


def _parse_headers_str(raw_headers: str | None) -> dict[str, str]:
    if not raw_headers:
        return {}
    headers: dict[str, str] = {}
    raw_items = raw_headers.replace("\n", ",").split(",")
    for raw_item in raw_items:
        item = raw_item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(
                "Invalid HERMIT_CUSTOM_HEADERS format. Expected 'Key: Value[, Key2: Value2]'."
            )
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def _set_if_present(values: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        values.setdefault(key, value)


def _override_if_present(values: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        values[key] = value


def _read_env_file_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _codex_auth_path() -> Path:
    return Path.home() / ".codex" / "auth.json"


def _read_codex_auth() -> dict[str, Any]:
    auth_path = _codex_auth_path()
    if not auth_path.exists():
        return {}
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _codex_auth_exists() -> bool:
    return _codex_auth_path().exists()


def _codex_auth_mode() -> str | None:
    raw = _read_codex_auth()
    value = raw.get("auth_mode")
    return str(value).strip() if value is not None else None


def _codex_auth_api_key() -> str | None:
    raw = _read_codex_auth()
    for key in ("OPENAI_API_KEY", "openai_api_key", "api_key"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _codex_access_token() -> str | None:
    raw = _read_codex_auth()
    tokens_raw = raw.get("tokens")
    if not isinstance(tokens_raw, dict):
        return None
    tokens = cast(dict[str, Any], tokens_raw)
    value = tokens.get("access_token")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _codex_refresh_token() -> str | None:
    raw = _read_codex_auth()
    tokens_raw = raw.get("tokens")
    if not isinstance(tokens_raw, dict):
        return None
    tokens = cast(dict[str, Any], tokens_raw)
    value = tokens.get("refresh_token")
    return value.strip() if isinstance(value, str) and value.strip() else None


class Settings(BaseSettings):
    """Runtime configuration for Hermit."""

    model_config = SettingsConfigDict(
        env_prefix="HERMIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    profile: str | None = None
    provider: str = "claude"
    claude_api_key: str | None = Field(default=None, alias="CLAUDE_API_KEY")
    claude_auth_token: str | None = None
    claude_base_url: str | None = None
    claude_headers: str | None = None
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = None
    openai_headers: str | None = None
    codex_command: str = "codex"
    locale: str = Field(default_factory=locale_from_env)
    model: str = "claude-3-7-sonnet-latest"
    max_tokens: int = 2048
    max_turns: int = 100
    tool_output_limit: int = 4000
    thinking_budget: int = 0
    image_model: str | None = None
    image_context_limit: int = 3
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_thread_progress: bool = True
    telegram_bot_token: str | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    scheduler_enabled: bool = True
    scheduler_catch_up: bool = True
    scheduler_feishu_chat_id: str | None = None
    kernel_dispatch_worker_count: int = 4
    webhook_enabled: bool = True
    webhook_host: str | None = None
    webhook_port: int | None = None
    webhook_control_secret: str | None = None
    approval_copy_formatter_enabled: bool = True
    approval_copy_model: str | None = None
    approval_copy_formatter_timeout_ms: int = 500
    progress_summary_enabled: bool = True
    progress_summary_model: str | None = None
    progress_summary_max_tokens: int = 160
    progress_summary_keepalive_seconds: float = 15.0

    @model_validator(mode="before")
    @classmethod
    def _apply_legacy_provider_env(cls, data: object) -> object:
        if not isinstance(data, dict):
            data = {}
        values: dict[str, Any] = cast(dict[str, Any], dict(cast(Any, data)))
        base_dir_raw_v = values.get("base_dir") or os.environ.get("HERMIT_BASE_DIR")
        base_dir_raw = str(base_dir_raw_v) if base_dir_raw_v is not None else None
        base_dir = Path(base_dir_raw).expanduser() if base_dir_raw else Path.home() / ".hermit"
        env_file_raw_v = values.get("_env_file")
        if "_env_file" in values and env_file_raw_v is None:
            env_file_values: dict[str, str] = {}
        else:
            env_file_raw = str(env_file_raw_v) if env_file_raw_v is not None else None
            env_file = Path(env_file_raw).expanduser() if env_file_raw else base_dir / ".env"
            env_file_values = _read_env_file_values(env_file)
        profile_name = values.get("profile") or os.environ.get("HERMIT_PROFILE")
        resolved_profile = resolve_profile(
            base_dir, str(profile_name) if profile_name is not None else None
        )
        if resolved_profile.name and "profile" not in values:
            values["profile"] = resolved_profile.name
        for key, value in resolved_profile.values.items():
            values.setdefault(key, value)
        if "anthropic_api_key" in values and "claude_api_key" not in values:
            values["claude_api_key"] = values["anthropic_api_key"]
        if "auth_token" in values and "claude_auth_token" not in values:
            values["claude_auth_token"] = values["auth_token"]
        if "base_url" in values and "claude_base_url" not in values:
            values["claude_base_url"] = values["base_url"]
        if "custom_headers" in values and "claude_headers" not in values:
            values["claude_headers"] = values["custom_headers"]
        if not values.get("provider"):
            values["provider"] = os.environ.get("HERMIT_PROVIDER", "claude")
        _set_if_present(
            values,
            "claude_api_key",
            env_file_values.get("HERMIT_CLAUDE_API_KEY")
            or env_file_values.get("ANTHROPIC_API_KEY"),
        )
        _set_if_present(
            values,
            "claude_auth_token",
            env_file_values.get("HERMIT_CLAUDE_AUTH_TOKEN")
            or env_file_values.get("HERMIT_AUTH_TOKEN"),
        )
        _set_if_present(
            values,
            "claude_base_url",
            env_file_values.get("HERMIT_CLAUDE_BASE_URL") or env_file_values.get("HERMIT_BASE_URL"),
        )
        _set_if_present(
            values,
            "claude_headers",
            env_file_values.get("HERMIT_CLAUDE_HEADERS")
            or env_file_values.get("HERMIT_CUSTOM_HEADERS"),
        )
        _set_if_present(
            values,
            "openai_api_key",
            env_file_values.get("HERMIT_OPENAI_API_KEY") or env_file_values.get("OPENAI_API_KEY"),
        )
        _set_if_present(values, "openai_base_url", env_file_values.get("HERMIT_OPENAI_BASE_URL"))
        _set_if_present(values, "openai_headers", env_file_values.get("HERMIT_OPENAI_HEADERS"))
        _set_if_present(values, "locale", env_file_values.get("HERMIT_LOCALE"))
        _set_if_present(values, "feishu_app_id", env_file_values.get("HERMIT_FEISHU_APP_ID"))
        _set_if_present(
            values, "feishu_app_secret", env_file_values.get("HERMIT_FEISHU_APP_SECRET")
        )
        _set_if_present(
            values, "feishu_thread_progress", env_file_values.get("HERMIT_FEISHU_THREAD_PROGRESS")
        )
        _set_if_present(
            values, "telegram_bot_token", env_file_values.get("HERMIT_TELEGRAM_BOT_TOKEN")
        )
        _set_if_present(values, "slack_bot_token", env_file_values.get("HERMIT_SLACK_BOT_TOKEN"))
        _set_if_present(values, "slack_app_token", env_file_values.get("HERMIT_SLACK_APP_TOKEN"))
        _set_if_present(
            values, "scheduler_enabled", env_file_values.get("HERMIT_SCHEDULER_ENABLED")
        )
        _set_if_present(
            values, "scheduler_catch_up", env_file_values.get("HERMIT_SCHEDULER_CATCH_UP")
        )
        _set_if_present(
            values,
            "scheduler_feishu_chat_id",
            env_file_values.get("HERMIT_SCHEDULER_FEISHU_CHAT_ID"),
        )
        _set_if_present(
            values,
            "kernel_dispatch_worker_count",
            env_file_values.get("HERMIT_KERNEL_DISPATCH_WORKER_COUNT"),
        )
        _set_if_present(values, "webhook_enabled", env_file_values.get("HERMIT_WEBHOOK_ENABLED"))
        _set_if_present(values, "webhook_host", env_file_values.get("HERMIT_WEBHOOK_HOST"))
        _set_if_present(values, "webhook_port", env_file_values.get("HERMIT_WEBHOOK_PORT"))
        _set_if_present(
            values, "webhook_control_secret", env_file_values.get("HERMIT_WEBHOOK_CONTROL_SECRET")
        )
        _set_if_present(
            values,
            "approval_copy_formatter_enabled",
            env_file_values.get("HERMIT_APPROVAL_COPY_FORMATTER_ENABLED"),
        )
        _set_if_present(
            values, "approval_copy_model", env_file_values.get("HERMIT_APPROVAL_COPY_MODEL")
        )
        _set_if_present(
            values,
            "approval_copy_formatter_timeout_ms",
            env_file_values.get("HERMIT_APPROVAL_COPY_FORMATTER_TIMEOUT_MS"),
        )
        _set_if_present(
            values,
            "progress_summary_enabled",
            env_file_values.get("HERMIT_PROGRESS_SUMMARY_ENABLED"),
        )
        _set_if_present(
            values, "progress_summary_model", env_file_values.get("HERMIT_PROGRESS_SUMMARY_MODEL")
        )
        _set_if_present(
            values,
            "progress_summary_max_tokens",
            env_file_values.get("HERMIT_PROGRESS_SUMMARY_MAX_TOKENS"),
        )
        _set_if_present(
            values,
            "progress_summary_keepalive_seconds",
            env_file_values.get("HERMIT_PROGRESS_SUMMARY_KEEPALIVE_SECONDS"),
        )
        _override_if_present(
            values,
            "claude_api_key",
            os.environ.get("HERMIT_CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
        )
        _override_if_present(
            values,
            "claude_auth_token",
            os.environ.get("HERMIT_CLAUDE_AUTH_TOKEN") or os.environ.get("HERMIT_AUTH_TOKEN"),
        )
        _override_if_present(
            values,
            "claude_base_url",
            os.environ.get("HERMIT_CLAUDE_BASE_URL") or os.environ.get("HERMIT_BASE_URL"),
        )
        _override_if_present(
            values,
            "claude_headers",
            os.environ.get("HERMIT_CLAUDE_HEADERS") or os.environ.get("HERMIT_CUSTOM_HEADERS"),
        )
        _override_if_present(
            values,
            "openai_api_key",
            os.environ.get("HERMIT_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        )
        _override_if_present(values, "openai_base_url", os.environ.get("HERMIT_OPENAI_BASE_URL"))
        _override_if_present(values, "openai_headers", os.environ.get("HERMIT_OPENAI_HEADERS"))
        _override_if_present(values, "locale", os.environ.get("HERMIT_LOCALE"))
        _override_if_present(values, "feishu_app_id", os.environ.get("HERMIT_FEISHU_APP_ID"))
        _override_if_present(
            values, "feishu_app_secret", os.environ.get("HERMIT_FEISHU_APP_SECRET")
        )
        _override_if_present(
            values, "feishu_thread_progress", os.environ.get("HERMIT_FEISHU_THREAD_PROGRESS")
        )
        _override_if_present(
            values, "telegram_bot_token", os.environ.get("HERMIT_TELEGRAM_BOT_TOKEN")
        )
        _override_if_present(values, "slack_bot_token", os.environ.get("HERMIT_SLACK_BOT_TOKEN"))
        _override_if_present(values, "slack_app_token", os.environ.get("HERMIT_SLACK_APP_TOKEN"))
        _override_if_present(
            values, "scheduler_enabled", os.environ.get("HERMIT_SCHEDULER_ENABLED")
        )
        _override_if_present(
            values, "scheduler_catch_up", os.environ.get("HERMIT_SCHEDULER_CATCH_UP")
        )
        _override_if_present(
            values, "scheduler_feishu_chat_id", os.environ.get("HERMIT_SCHEDULER_FEISHU_CHAT_ID")
        )
        _override_if_present(
            values,
            "kernel_dispatch_worker_count",
            os.environ.get("HERMIT_KERNEL_DISPATCH_WORKER_COUNT"),
        )
        _override_if_present(values, "webhook_enabled", os.environ.get("HERMIT_WEBHOOK_ENABLED"))
        _override_if_present(values, "webhook_host", os.environ.get("HERMIT_WEBHOOK_HOST"))
        _override_if_present(values, "webhook_port", os.environ.get("HERMIT_WEBHOOK_PORT"))
        _override_if_present(
            values, "webhook_control_secret", os.environ.get("HERMIT_WEBHOOK_CONTROL_SECRET")
        )
        _override_if_present(
            values,
            "approval_copy_formatter_enabled",
            os.environ.get("HERMIT_APPROVAL_COPY_FORMATTER_ENABLED"),
        )
        _override_if_present(
            values, "approval_copy_model", os.environ.get("HERMIT_APPROVAL_COPY_MODEL")
        )
        _override_if_present(
            values,
            "approval_copy_formatter_timeout_ms",
            os.environ.get("HERMIT_APPROVAL_COPY_FORMATTER_TIMEOUT_MS"),
        )
        _override_if_present(
            values, "progress_summary_enabled", os.environ.get("HERMIT_PROGRESS_SUMMARY_ENABLED")
        )
        _override_if_present(
            values, "progress_summary_model", os.environ.get("HERMIT_PROGRESS_SUMMARY_MODEL")
        )
        _override_if_present(
            values,
            "progress_summary_max_tokens",
            os.environ.get("HERMIT_PROGRESS_SUMMARY_MAX_TOKENS"),
        )
        _override_if_present(
            values,
            "progress_summary_keepalive_seconds",
            os.environ.get("HERMIT_PROGRESS_SUMMARY_KEEPALIVE_SECONDS"),
        )
        return values

    @model_validator(mode="after")
    def _normalize_locale_value(self) -> Settings:
        self.locale = normalize_locale(self.locale)
        return self

    def effective_max_tokens(self) -> int:
        if self.thinking_budget > 0 and self.max_tokens <= self.thinking_budget:
            return self.thinking_budget + self.max_tokens
        return self.max_tokens

    prevent_sleep: bool = True
    log_level: str = "INFO"
    sandbox_mode: str = "l0"
    command_timeout_seconds: int = 30
    ingress_ack_deadline_seconds: float = 0.0
    provider_connect_timeout_seconds: float = 0.0
    provider_read_timeout_seconds: float = 0.0
    provider_stream_idle_timeout_seconds: float = 0.0
    tool_soft_deadline_seconds: float = 0.0
    tool_hard_deadline_seconds: float = 0.0
    observation_window_seconds: float = 0.0
    observation_poll_interval_seconds: float = 0.0
    session_idle_timeout_seconds: int = 1800
    base_dir: Path = Field(default_factory=lambda: Path.home() / ".hermit")

    @property
    def memory_dir(self) -> Path:
        return self.base_dir / "memory"

    @property
    def config_file(self) -> Path:
        return config_path_for_base_dir(self.base_dir)

    @property
    def config_profiles(self) -> dict[str, dict[str, object]]:
        return load_profile_catalog(self.base_dir).profiles

    def execution_budget(self) -> ExecutionBudget:
        legacy = max(float(self.command_timeout_seconds or 0), 1.0)
        soft = float(self.tool_soft_deadline_seconds or 0) or legacy
        hard = float(self.tool_hard_deadline_seconds or 0) or max(legacy, 600.0)
        return ExecutionBudget(
            ingress_ack_deadline=float(self.ingress_ack_deadline_seconds or 5.0),
            provider_connect_timeout=float(self.provider_connect_timeout_seconds or legacy),
            provider_read_timeout=float(self.provider_read_timeout_seconds or 600.0),
            provider_stream_idle_timeout=float(self.provider_stream_idle_timeout_seconds or 600.0),
            tool_soft_deadline=soft,
            tool_hard_deadline=max(hard, soft),
            observation_window=float(self.observation_window_seconds or 600.0),
            observation_poll_interval=float(self.observation_poll_interval_seconds or 5.0),
        )

    @property
    def default_profile(self) -> str | None:
        return load_profile_catalog(self.base_dir).default_profile

    @property
    def disabled_builtin_plugins(self) -> list[str]:
        return load_profile_catalog(self.base_dir).disabled_builtin_plugins

    @property
    def resolved_profile(self) -> str | None:
        resolved = resolve_profile(self.base_dir, self.profile)
        return resolved.name

    @property
    def memory_file(self) -> Path:
        return self.memory_dir / "memories.md"

    @property
    def session_state_file(self) -> Path:
        return self.memory_dir / "session_state.json"

    @property
    def skills_dir(self) -> Path:
        return self.base_dir / "skills"

    @property
    def rules_dir(self) -> Path:
        return self.base_dir / "rules"

    @property
    def hooks_dir(self) -> Path:
        return self.base_dir / "hooks"

    @property
    def plugins_dir(self) -> Path:
        return self.base_dir / "plugins"

    @property
    def sessions_dir(self) -> Path:
        return self.base_dir / "sessions"

    @property
    def image_memory_dir(self) -> Path:
        return self.base_dir / "image-memory"

    @property
    def schedules_dir(self) -> Path:
        return self.base_dir / "schedules"

    @property
    def kernel_dir(self) -> Path:
        return self.base_dir / "kernel"

    @property
    def kernel_db_path(self) -> Path:
        return self.kernel_dir / "state.db"

    @property
    def kernel_artifacts_dir(self) -> Path:
        return self.kernel_dir / "artifacts"

    @property
    def context_file(self) -> Path:
        return self.base_dir / "context.md"

    @property
    def parsed_claude_headers(self) -> dict[str, str]:
        return _parse_headers_str(self.claude_headers)

    @property
    def parsed_openai_headers(self) -> dict[str, str]:
        return _parse_headers_str(self.openai_headers)

    @property
    def resolved_webhook_host(self) -> str:
        return self.webhook_host or "0.0.0.0"

    @property
    def resolved_webhook_port(self) -> int:
        return self.webhook_port or 8321

    @property
    def has_auth(self) -> bool:
        if self.provider == "claude":
            return bool(self.claude_api_key or self.claude_auth_token)
        if self.provider == "codex":
            return bool(self.openai_api_key or _codex_auth_api_key())
        if self.provider == "codex-oauth":
            return bool(_codex_access_token() and _codex_refresh_token())
        return bool(self.claude_api_key or self.claude_auth_token or self.openai_api_key)

    @property
    def resolved_openai_api_key(self) -> str | None:
        return self.openai_api_key or _codex_auth_api_key()

    @property
    def codex_auth_mode(self) -> str | None:
        return _codex_auth_mode()

    @property
    def codex_auth_file_exists(self) -> bool:
        return _codex_auth_exists()

    @property
    def codex_access_token(self) -> str | None:
        return _codex_access_token()

    @property
    def codex_refresh_token(self) -> str | None:
        return _codex_refresh_token()

    @property
    def anthropic_api_key(self) -> str | None:
        return self.claude_api_key

    @property
    def auth_token(self) -> str | None:
        return self.claude_auth_token

    @property
    def base_url(self) -> str | None:
        return self.claude_base_url

    @property
    def custom_headers(self) -> str | None:
        return self.claude_headers

    @property
    def parsed_custom_headers(self) -> dict[str, str]:
        return self.parsed_claude_headers


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
