from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import typer

from hermit.runtime.assembly.config import Settings

from .main import hermit_env_path, t


@dataclass(frozen=True)
class _PreflightItem:
    label: str
    ok: bool
    detail: str


def _read_env_file_keys() -> set[str]:
    env_path = hermit_env_path()
    if not env_path.exists():
        return set()
    keys: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, _value = line.partition("=")
        key = key.strip()
        if key:
            keys.add(key)
    return keys


def _resolve_env_key(*keys: str) -> str | None:
    for key in keys:
        if os.environ.get(key):
            return key
    return None


def _describe_env_source(key: str, env_file_keys: set[str]) -> str:
    if key in env_file_keys:
        return t("cli.preflight.source.env_file", "~/.hermit/.env")
    return t("cli.preflight.source.shell", "shell env")


def _format_preflight_item(item: _PreflightItem) -> str:
    prefix = (
        t("cli.preflight.prefix.ok", "[OK]")
        if item.ok
        else t(
            "cli.preflight.prefix.missing",
            "[MISSING]",
        )
    )
    return f"  {prefix} {item.label}: {item.detail}"


def _build_serve_preflight(
    adapter: str, settings: Settings
) -> tuple[list[_PreflightItem], list[str]]:
    env_path = settings.base_dir / ".env"
    env_file_keys = _read_env_file_keys()
    items: list[_PreflightItem] = [
        _PreflightItem(
            label=t("cli.preflight.item.config_file.label", "Config file"),
            ok=env_path.exists(),
            detail=t(
                "cli.preflight.item.config_file.detail.found"
                if env_path.exists()
                else "cli.preflight.item.config_file.detail.missing",
                (
                    "{path} (found)"
                    if env_path.exists()
                    else "{path} (not found; only current shell environment variables will be used)"
                ),
                path=env_path,
            ),
        )
    ]
    errors: list[str] = []

    provider_key = _resolve_env_key("HERMIT_PROVIDER")
    profile_key = _resolve_env_key("HERMIT_PROFILE")
    if settings.resolved_profile:
        items.append(
            _PreflightItem(
                label=t("cli.preflight.item.profile.label", "Profile"),
                ok=True,
                detail=(
                    f"{settings.resolved_profile} ({_describe_env_source(profile_key, env_file_keys)})"
                    if profile_key
                    else t(
                        "cli.preflight.item.profile.detail.config",
                        "{profile} (config.toml)",
                        profile=settings.resolved_profile,
                    )
                ),
            )
        )
    items.append(
        _PreflightItem(
            label=t("cli.preflight.item.provider.label", "Provider"),
            ok=True,
            detail=(
                f"{settings.provider} ({_describe_env_source(provider_key, env_file_keys)})"
                if provider_key
                else t(
                    "cli.preflight.item.provider.detail.default",
                    "{provider} (default)",
                    provider=settings.provider,
                )
            ),
        )
    )
    if settings.provider == "claude":
        auth_key = _resolve_env_key(
            "HERMIT_CLAUDE_API_KEY",
            "ANTHROPIC_API_KEY",
            "HERMIT_CLAUDE_AUTH_TOKEN",
            "HERMIT_AUTH_TOKEN",
        )
        if auth_key:
            detail = f"{auth_key} ({_describe_env_source(auth_key, env_file_keys)})"
            base_url_key = _resolve_env_key("HERMIT_CLAUDE_BASE_URL", "HERMIT_BASE_URL")
            if auth_key in {"HERMIT_CLAUDE_AUTH_TOKEN", "HERMIT_AUTH_TOKEN"}:
                detail += (
                    f", {base_url_key} ({_describe_env_source(base_url_key, env_file_keys)})"
                    if base_url_key
                    else t(
                        "cli.preflight.item.auth.detail.missing_base_url",
                        ", Claude base URL is not set",
                    )
                )
            items.append(
                _PreflightItem(
                    label=t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=True,
                    detail=detail,
                )
            )
        elif settings.claude_api_key or settings.claude_auth_token:
            detail = t("cli.preflight.item.auth.detail.profile", "config.toml profile")
            if settings.claude_auth_token:
                detail += (
                    t(
                        "cli.preflight.item.auth.detail.profile_base_url",
                        ", config.toml profile base_url",
                    )
                    if settings.claude_base_url
                    else t(
                        "cli.preflight.item.auth.detail.missing_base_url",
                        ", Claude base URL is not set",
                    )
                )
            items.append(
                _PreflightItem(
                    label=t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=True,
                    detail=detail,
                )
            )
        else:
            errors.append(
                t(
                    "cli.preflight.error.claude_auth_missing",
                    "Missing Claude auth. Set `HERMIT_CLAUDE_API_KEY` / `ANTHROPIC_API_KEY`, or set `HERMIT_CLAUDE_AUTH_TOKEN` (usually together with `HERMIT_CLAUDE_BASE_URL`).",
                )
            )
            items.append(
                _PreflightItem(
                    label=t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=False,
                    detail=t(
                        "cli.preflight.item.auth.detail.missing",
                        "Claude API key / auth token not found",
                    ),
                )
            )
    elif settings.provider == "codex":
        auth_key = _resolve_env_key("HERMIT_OPENAI_API_KEY", "OPENAI_API_KEY")
        if auth_key:
            items.append(
                _PreflightItem(
                    label="Codex 鉴权",
                    ok=True,
                    detail=f"{auth_key} ({_describe_env_source(auth_key, env_file_keys)})",
                )
            )
        elif settings.resolved_openai_api_key:
            items.append(
                _PreflightItem(
                    label="Codex 鉴权",
                    ok=True,
                    detail=t(
                        "cli.preflight.item.codex_auth.detail.local_api_key",
                        "~/.codex/auth.json (contains a local OpenAI API key)",
                    ),
                )
            )
        elif settings.codex_auth_file_exists:
            auth_mode = settings.codex_auth_mode or "unknown"
            errors.append(
                t(
                    "cli.preflight.error.codex_auth_missing_api_key",
                    "Detected `~/.codex/auth.json`, but the current login does not expose an OpenAI API key. ChatGPT/Codex Desktop login alone cannot call the OpenAI Responses API; set `HERMIT_OPENAI_API_KEY` / `OPENAI_API_KEY`, or use a local Codex auth state backed by an API key.",
                )
            )
            items.append(
                _PreflightItem(
                    label="Codex 鉴权",
                    ok=False,
                    detail=f"检测到本机 Codex 登录态（auth_mode={auth_mode}），但无可用 OpenAI API Key",
                )
            )
        else:
            errors.append(
                t(
                    "cli.preflight.error.codex_auth_missing",
                    "Missing Codex/OpenAI auth. Set `HERMIT_OPENAI_API_KEY` or `OPENAI_API_KEY`.",
                )
            )
            items.append(
                _PreflightItem(
                    label="Codex 鉴权",
                    ok=False,
                    detail=t(
                        "cli.preflight.item.codex_auth.detail.no_api_key",
                        "OpenAI API key not found",
                    ),
                )
            )
    elif settings.provider == "codex-oauth":
        if (
            settings.codex_auth_file_exists
            and settings.codex_access_token
            and settings.codex_refresh_token
        ):
            auth_mode = settings.codex_auth_mode or "unknown"
            items.append(
                _PreflightItem(
                    label=t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=True,
                    detail=t(
                        "cli.preflight.item.codex_oauth.detail.ready",
                        "~/.codex/auth.json (auth_mode={auth_mode})",
                        auth_mode=auth_mode,
                    ),
                )
            )
        elif settings.codex_auth_file_exists:
            auth_mode = settings.codex_auth_mode or "unknown"
            errors.append(
                t(
                    "cli.preflight.error.codex_oauth_incomplete",
                    "Detected `~/.codex/auth.json`, but it does not contain a usable access_token / refresh_token.",
                )
            )
            items.append(
                _PreflightItem(
                    label=t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=False,
                    detail=t(
                        "cli.preflight.item.codex_oauth.detail.incomplete",
                        "Detected local Codex login (auth_mode={auth_mode}), but tokens are incomplete",
                        auth_mode=auth_mode,
                    ),
                )
            )
        else:
            errors.append(
                t(
                    "cli.preflight.error.codex_oauth_missing",
                    "Missing Codex OAuth auth. Complete local Codex login first.",
                )
            )
            items.append(
                _PreflightItem(
                    label=t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=False,
                    detail=t(
                        "cli.preflight.item.codex_oauth.detail.missing",
                        "~/.codex/auth.json not found",
                    ),
                )
            )

    model_key = _resolve_env_key("HERMIT_MODEL")
    items.append(
        _PreflightItem(
            label=t("cli.preflight.item.model.label", "Model"),
            ok=True,
            detail=(
                f"{settings.model} ({_describe_env_source(model_key, env_file_keys)})"
                if model_key
                else t(
                    "cli.preflight.item.model.detail.default",
                    "{model} (default)",
                    model=settings.model,
                )
            ),
        )
    )

    if adapter == "feishu":
        app_id_key = _resolve_env_key("HERMIT_FEISHU_APP_ID")
        app_secret_key = _resolve_env_key("HERMIT_FEISHU_APP_SECRET")
        if app_id_key or settings.feishu_app_id:
            items.append(
                _PreflightItem(
                    label=t("cli.preflight.item.feishu_app_id.label", "Feishu App ID"),
                    ok=True,
                    detail=(
                        f"{app_id_key} ({_describe_env_source(app_id_key, env_file_keys)})"
                        if app_id_key
                        else t(
                            "cli.preflight.item.profile_source.config",
                            "config.toml profile",
                        )
                    ),
                )
            )
        else:
            errors.append(
                t(
                    "cli.preflight.error.feishu_app_id_missing",
                    "Missing Feishu App ID. Set `HERMIT_FEISHU_APP_ID`.",
                )
            )
            items.append(
                _PreflightItem(
                    label=t("cli.preflight.item.feishu_app_id.label", "Feishu App ID"),
                    ok=False,
                    detail=t(
                        "cli.preflight.item.feishu_app_id.detail.missing",
                        "HERMIT_FEISHU_APP_ID not found",
                    ),
                )
            )

        if app_secret_key or settings.feishu_app_secret:
            items.append(
                _PreflightItem(
                    label=t(
                        "cli.preflight.item.feishu_app_secret.label",
                        "Feishu App Secret",
                    ),
                    ok=True,
                    detail=(
                        f"{app_secret_key} ({_describe_env_source(app_secret_key, env_file_keys)})"
                        if app_secret_key
                        else t(
                            "cli.preflight.item.profile_source.config",
                            "config.toml profile",
                        )
                    ),
                )
            )
        else:
            errors.append(
                t(
                    "cli.preflight.error.feishu_app_secret_missing",
                    "Missing Feishu App Secret. Set `HERMIT_FEISHU_APP_SECRET`.",
                )
            )
            items.append(
                _PreflightItem(
                    label=t(
                        "cli.preflight.item.feishu_app_secret.label",
                        "Feishu App Secret",
                    ),
                    ok=False,
                    detail=t(
                        "cli.preflight.item.feishu_app_secret.detail.missing",
                        "HERMIT_FEISHU_APP_SECRET not found",
                    ),
                )
            )

        items.append(
            _PreflightItem(
                label=t(
                    "cli.preflight.item.feishu_progress.label",
                    "Feishu progress cards",
                ),
                ok=True,
                detail=t(
                    "cli.preflight.item.boolean.enabled"
                    if settings.feishu_thread_progress
                    else "cli.preflight.item.boolean.disabled",
                    "enabled" if settings.feishu_thread_progress else "disabled",
                ),
            )
        )

        items.append(
            _PreflightItem(
                label=t(
                    "cli.preflight.item.scheduler_feishu.label",
                    "Scheduler Feishu notifications",
                ),
                ok=True,
                detail=(
                    t(
                        "cli.preflight.item.scheduler_feishu.detail.configured",
                        "configured",
                    )
                    if settings.scheduler_feishu_chat_id
                    else t(
                        "cli.preflight.item.scheduler_feishu.detail.missing",
                        "not set (optional; reload and scheduler will not proactively send Feishu notifications)",
                    )
                ),
            )
        )

    return items, errors


def run_serve_preflight(adapter: str, settings: Settings) -> None:
    items, errors = _build_serve_preflight(adapter, settings)
    typer.echo("Hermit 启动前环境自检")
    for item in items:
        typer.echo(_format_preflight_item(item))
    typer.echo("")
    if errors:
        typer.echo(t("cli.preflight.failed", "Pre-start checks failed:"))
        for message in errors:
            typer.echo(f"  - {message}")
        typer.echo("")
        raise typer.Exit(1)


def write_serve_status(
    settings: Any,
    adapter: str,
    *,
    phase: str,
    reason: str,
    detail: str,
    signal_name: str | None = None,
    run_started_at: str | None = None,
    exc: BaseException | None = None,
    append_history: bool = False,
) -> None:
    import json
    import traceback

    payload: dict[str, Any] = {
        "adapter": adapter,
        "pid": os.getpid(),
        "phase": phase,
        "reason": reason,
        "detail": detail,
        "signal": signal_name,
        "run_started_at": run_started_at,
        "updated_at": iso_now(),
    }
    if exc is not None:
        payload["exception_type"] = type(exc).__name__
        payload["exception_message"] = str(exc)
        payload["traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )

    _serve_status_path(settings, adapter).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if append_history:
        with _serve_exit_history_path(settings, adapter).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _serve_log_dir(settings: Any):
    from pathlib import Path

    path: Path = settings.base_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serve_status_path(settings: Any, adapter: str):
    return _serve_log_dir(settings) / f"serve-{adapter}-status.json"


def _serve_exit_history_path(settings: Any, adapter: str):
    return _serve_log_dir(settings) / f"serve-{adapter}-exit-history.jsonl"


def iso_now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")
