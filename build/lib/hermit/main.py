from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer

from hermit.config import Settings, get_settings
from hermit.provider.profiles import load_profile_catalog, resolve_profile


def _hermit_env_path() -> Path:
    base_dir = os.environ.get("HERMIT_BASE_DIR")
    if base_dir:
        return Path(base_dir).expanduser() / ".env"
    return Path.home() / ".hermit" / ".env"


def _load_hermit_env() -> None:
    """Load ~/.hermit/.env into os.environ before Settings is instantiated.

    Existing env vars take precedence (they are not overwritten), so shell-level
    exports always win over the file.
    """
    env_path = _hermit_env_path()
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_hermit_env()

from hermit.context import build_base_context, ensure_default_context_file
from hermit.core.runner import AgentRunner
from hermit.core.session import SessionManager
from hermit.i18n import resolve_locale, tr
from hermit.kernel import (
    ApprovalCopyService,
    KernelStore,
    ProjectionService,
    RollbackService,
    SupervisionService,
    TaskController,
)
from hermit.kernel.claims import repository_claim_status, task_claim_status
from hermit.kernel.knowledge import MemoryRecordService
from hermit.kernel.memory_governance import MemoryGovernanceService
from hermit.kernel.proofs import ProofService
from hermit.logging import configure_logging
from hermit.plugin.manager import PluginManager
from hermit.provider.runtime import AgentResult
from hermit.provider.services import build_provider_client_kwargs, build_runtime

CLI_LOCALE = resolve_locale()


def _current_locale() -> str:
    try:
        return resolve_locale(get_settings().locale)
    except Exception:
        return resolve_locale()


def _cli_t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=CLI_LOCALE, default=default, **kwargs)


def _t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=_current_locale(), default=default, **kwargs)


app = typer.Typer(help=_cli_t("cli.app.help"))
plugin_app = typer.Typer(help=_cli_t("cli.plugin.help"))
autostart_app = typer.Typer(help=_cli_t("cli.autostart.help"))
schedule_app = typer.Typer(help=_cli_t("cli.schedule.help"))
config_app = typer.Typer(help=_cli_t("cli.config.help"))
profiles_app = typer.Typer(help=_cli_t("cli.profiles.help"))
auth_app = typer.Typer(help=_cli_t("cli.auth.help"))
task_app = typer.Typer(
    help=_cli_t(
        "cli.task.help",
        "Task kernel inspection and approval commands.",
    )
)
task_capability_app = typer.Typer(
    help=_cli_t(
        "cli.task_capability.help",
        "Capability grant inspection and revocation commands.",
    )
)
memory_app = typer.Typer(
    help=_cli_t(
        "cli.memory.help",
        "Memory inspection and governance debugging commands.",
    )
)
app.add_typer(plugin_app, name="plugin")
app.add_typer(autostart_app, name="autostart")
app.add_typer(schedule_app, name="schedule")
app.add_typer(config_app, name="config")
app.add_typer(profiles_app, name="profiles")
app.add_typer(auth_app, name="auth")
app.add_typer(task_app, name="task")
app.add_typer(memory_app, name="memory")
task_app.add_typer(task_capability_app, name="capability")

DIM = "\033[2m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _tool_result_preview(result: object, limit: int = 200) -> str:
    text = result if isinstance(result, str) else str(result)
    preview = text[:limit].replace("\n", " ")
    if len(text) > limit:
        preview += "..."
    return preview


def _on_tool_call(name: str, inputs: dict, result: object) -> None:
    compact_input = ", ".join(f"{k}={repr(v)[:60]}" for k, v in inputs.items())
    preview = _tool_result_preview(result)
    typer.echo(f"{CYAN}  ▸ {name}({compact_input}){RESET}")
    typer.echo(f"{DIM}    → {preview}{RESET}")


def _print_result(result: AgentResult) -> None:
    if result.thinking:
        typer.echo(f"\n{DIM}── thinking ──{RESET}")
        for line in result.thinking.splitlines():
            typer.echo(f"{DIM}{line}{RESET}")
        typer.echo(f"{DIM}── /thinking ──{RESET}")
    typer.echo(f"\n{result.text}")


class _StreamPrinter:
    """Handles real-time token printing with thinking/text state transitions."""

    def __init__(self) -> None:
        self._in_thinking = False
        self._has_output = False

    def on_token(self, kind: str, text: str) -> None:
        if kind == "thinking":
            if not self._in_thinking:
                self._in_thinking = True
                sys.stdout.write(f"\n{DIM}── thinking ──\n")
            sys.stdout.write(text)
            sys.stdout.flush()
        elif kind == "text":
            if self._in_thinking:
                self._in_thinking = False
                sys.stdout.write(f"\n── /thinking ──{RESET}\n\n")
            elif not self._has_output:
                sys.stdout.write("\n")
            sys.stdout.write(text)
            sys.stdout.flush()
            self._has_output = True
        elif kind == "block_end":
            pass

    def finish(self) -> None:
        if self._in_thinking:
            sys.stdout.write(f"\n── /thinking ──{RESET}")
        sys.stdout.write("\n")
        sys.stdout.flush()


def _build_anthropic_client_kwargs(settings: Settings) -> dict:
    return build_provider_client_kwargs(settings, "claude")


def _stop_runner_background_services(runner: Any) -> None:
    stopper = getattr(runner, "stop_background_services", None)
    if callable(stopper):
        stopper()


def _auth_status_summary(settings: Settings) -> dict[str, str | bool | None]:
    if settings.provider == "claude":
        if settings.claude_api_key:
            return {
                "provider": "claude",
                "ok": True,
                "source": "HERMIT_CLAUDE_API_KEY / ANTHROPIC_API_KEY",
            }
        if settings.claude_auth_token:
            return {
                "provider": "claude",
                "ok": True,
                "source": "HERMIT_CLAUDE_AUTH_TOKEN / HERMIT_AUTH_TOKEN",
                "base_url": settings.claude_base_url,
            }
        return {"provider": "claude", "ok": False, "source": None}
    if settings.provider == "codex":
        if settings.openai_api_key:
            return {
                "provider": "codex",
                "ok": True,
                "source": "HERMIT_OPENAI_API_KEY / OPENAI_API_KEY",
            }
        if settings.resolved_openai_api_key:
            return {"provider": "codex", "ok": True, "source": "~/.codex/auth.json api_key"}
        return {
            "provider": "codex",
            "ok": False,
            "source": None,
            "auth_mode": settings.codex_auth_mode,
        }
    if settings.provider == "codex-oauth":
        ok = bool(
            settings.codex_auth_file_exists
            and settings.codex_access_token
            and settings.codex_refresh_token
        )
        return {
            "provider": "codex-oauth",
            "ok": ok,
            "source": "~/.codex/auth.json" if settings.codex_auth_file_exists else None,
            "auth_mode": settings.codex_auth_mode,
        }
    return {"provider": settings.provider, "ok": settings.has_auth, "source": None}


def _resolved_config_snapshot(settings: Settings) -> dict[str, object]:
    return {
        "base_dir": str(settings.base_dir),
        "config_file": str(settings.config_file),
        "config_file_exists": settings.config_file.exists(),
        "default_profile": settings.default_profile,
        "selected_profile": settings.resolved_profile,
        "provider": settings.provider,
        "model": settings.model,
        "image_model": settings.image_model,
        "max_tokens": settings.max_tokens,
        "max_turns": settings.max_turns,
        "tool_output_limit": settings.tool_output_limit,
        "thinking_budget": settings.thinking_budget,
        "openai_base_url": settings.openai_base_url,
        "claude_base_url": settings.claude_base_url,
        "sandbox_mode": settings.sandbox_mode,
        "log_level": settings.log_level,
        "feishu": {
            "app_id_configured": bool(settings.feishu_app_id),
            "thread_progress": settings.feishu_thread_progress,
        },
        "scheduler": {
            "enabled": settings.scheduler_enabled,
            "catch_up": settings.scheduler_catch_up,
            "feishu_chat_id_configured": bool(settings.scheduler_feishu_chat_id),
        },
        "webhook": {
            "enabled": settings.webhook_enabled,
            "host": settings.resolved_webhook_host,
            "port": settings.resolved_webhook_port,
        },
        "auth": _auth_status_summary(settings),
    }


def _ensure_workspace(settings: Settings) -> None:
    for directory in (
        settings.base_dir,
        settings.memory_dir,
        settings.skills_dir,
        settings.rules_dir,
        settings.hooks_dir,
        settings.plugins_dir,
        settings.sessions_dir,
        settings.image_memory_dir,
        settings.kernel_dir,
        settings.kernel_artifacts_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        ensure_default_context_file(settings.context_file, locale=getattr(settings, "locale", None))
    except TypeError:
        ensure_default_context_file(settings.context_file)
    if not settings.memory_file.exists():
        from hermit.builtin.memory.engine import MemoryEngine

        MemoryEngine(settings.memory_file).save({})


@contextlib.contextmanager
def _caffeinate(settings: Settings):
    """Prevent macOS from sleeping while Hermit is running.

    Uses the system's built-in ``caffeinate -i`` command so the process keeps
    an IOKit power assertion alive.  No-op on non-macOS platforms or when
    ``HERMIT_PREVENT_SLEEP=false``.
    """
    if not settings.prevent_sleep or sys.platform != "darwin" or not shutil.which("caffeinate"):
        yield
        return

    proc = subprocess.Popen(
        ["caffeinate", "-i"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield
    finally:
        proc.terminate()
        proc.wait()


def _require_auth(settings: Settings) -> None:
    if not settings.has_auth:
        if settings.provider == "codex":
            if settings.codex_auth_file_exists:
                auth_mode = settings.codex_auth_mode or "unknown"
                raise typer.BadParameter(
                    _t(
                        "cli.auth_error.codex.local_login_missing_api_key",
                        (
                            "Codex provider now uses the OpenAI Responses API, but the local "
                            "~/.codex/auth.json login (auth_mode={auth_mode}) does not expose an OpenAI API key. "
                            "ChatGPT/Codex desktop login alone cannot call /v1/responses. "
                            "Set HERMIT_OPENAI_API_KEY / OPENAI_API_KEY, or switch your local Codex auth to an API-key-backed login."
                        ),
                        auth_mode=auth_mode,
                    )
                )
            raise typer.BadParameter(
                _t(
                    "cli.auth_error.codex.requires_api_key",
                    "Codex provider now uses the OpenAI Responses API and requires HERMIT_OPENAI_API_KEY / OPENAI_API_KEY.",
                )
            )
        if settings.provider == "codex-oauth":
            raise typer.BadParameter(
                _t(
                    "cli.auth_error.codex_oauth.requires_local_login",
                    "Codex OAuth provider requires a local Codex login with ~/.codex/auth.json.",
                )
            )
        raise typer.BadParameter(
            _t(
                "cli.auth_error.missing_provider_auth",
                "Missing authentication for the selected provider.",
            )
        )


@dataclass(frozen=True)
class _PreflightItem:
    label: str
    ok: bool
    detail: str


def _read_env_file_keys() -> set[str]:
    env_path = _hermit_env_path()
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
        return _t("cli.preflight.source.env_file", "~/.hermit/.env")
    return _t("cli.preflight.source.shell", "shell env")


def _format_preflight_item(item: _PreflightItem) -> str:
    prefix = (
        _t("cli.preflight.prefix.ok", "[OK]")
        if item.ok
        else _t(
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
            label=_t("cli.preflight.item.config_file.label", "Config file"),
            ok=env_path.exists(),
            detail=_t(
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
                label=_t("cli.preflight.item.profile.label", "Profile"),
                ok=True,
                detail=(
                    f"{settings.resolved_profile} ({_describe_env_source(profile_key, env_file_keys)})"
                    if profile_key
                    else _t(
                        "cli.preflight.item.profile.detail.config",
                        "{profile} (config.toml)",
                        profile=settings.resolved_profile,
                    )
                ),
            )
        )
    items.append(
        _PreflightItem(
            label=_t("cli.preflight.item.provider.label", "Provider"),
            ok=True,
            detail=(
                f"{settings.provider} ({_describe_env_source(provider_key, env_file_keys)})"
                if provider_key
                else _t(
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
                    else _t(
                        "cli.preflight.item.auth.detail.missing_base_url",
                        ", Claude base URL is not set",
                    )
                )
            items.append(
                _PreflightItem(
                    label=_t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=True,
                    detail=detail,
                )
            )
        elif settings.claude_api_key or settings.claude_auth_token:
            detail = _t("cli.preflight.item.auth.detail.profile", "config.toml profile")
            if settings.claude_auth_token:
                detail += (
                    _t(
                        "cli.preflight.item.auth.detail.profile_base_url",
                        ", config.toml profile base_url",
                    )
                    if settings.claude_base_url
                    else _t(
                        "cli.preflight.item.auth.detail.missing_base_url",
                        ", Claude base URL is not set",
                    )
                )
            items.append(
                _PreflightItem(
                    label=_t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=True,
                    detail=detail,
                )
            )
        else:
            errors.append(
                _t(
                    "cli.preflight.error.claude_auth_missing",
                    "Missing Claude auth. Set `HERMIT_CLAUDE_API_KEY` / `ANTHROPIC_API_KEY`, or set `HERMIT_CLAUDE_AUTH_TOKEN` (usually together with `HERMIT_CLAUDE_BASE_URL`).",
                )
            )
            items.append(
                _PreflightItem(
                    label=_t("cli.preflight.item.auth.label", "LLM auth"),
                    ok=False,
                    detail=_t(
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
                    detail=_t(
                        "cli.preflight.item.codex_auth.detail.local_api_key",
                        "~/.codex/auth.json (contains a local OpenAI API key)",
                    ),
                )
            )
        elif settings.codex_auth_file_exists:
            auth_mode = settings.codex_auth_mode or "unknown"
            errors.append(
                _t(
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
                _t(
                    "cli.preflight.error.codex_auth_missing",
                    "Missing Codex/OpenAI auth. Set `HERMIT_OPENAI_API_KEY` or `OPENAI_API_KEY`.",
                )
            )
            items.append(
                _PreflightItem(
                    label="Codex 鉴权",
                    ok=False,
                    detail=_t(
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
                    label=_t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=True,
                    detail=_t(
                        "cli.preflight.item.codex_oauth.detail.ready",
                        "~/.codex/auth.json (auth_mode={auth_mode})",
                        auth_mode=auth_mode,
                    ),
                )
            )
        elif settings.codex_auth_file_exists:
            auth_mode = settings.codex_auth_mode or "unknown"
            errors.append(
                _t(
                    "cli.preflight.error.codex_oauth_incomplete",
                    "Detected `~/.codex/auth.json`, but it does not contain a usable access_token / refresh_token.",
                )
            )
            items.append(
                _PreflightItem(
                    label=_t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=False,
                    detail=_t(
                        "cli.preflight.item.codex_oauth.detail.incomplete",
                        "Detected local Codex login (auth_mode={auth_mode}), but tokens are incomplete",
                        auth_mode=auth_mode,
                    ),
                )
            )
        else:
            errors.append(
                _t(
                    "cli.preflight.error.codex_oauth_missing",
                    "Missing Codex OAuth auth. Complete local Codex login first.",
                )
            )
            items.append(
                _PreflightItem(
                    label=_t(
                        "cli.preflight.item.codex_oauth.label",
                        "Codex OAuth auth",
                    ),
                    ok=False,
                    detail=_t(
                        "cli.preflight.item.codex_oauth.detail.missing",
                        "~/.codex/auth.json not found",
                    ),
                )
            )

    model_key = _resolve_env_key("HERMIT_MODEL")
    items.append(
        _PreflightItem(
            label=_t("cli.preflight.item.model.label", "Model"),
            ok=True,
            detail=(
                f"{settings.model} ({_describe_env_source(model_key, env_file_keys)})"
                if model_key
                else _t(
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
                    label=_t("cli.preflight.item.feishu_app_id.label", "Feishu App ID"),
                    ok=True,
                    detail=(
                        f"{app_id_key} ({_describe_env_source(app_id_key, env_file_keys)})"
                        if app_id_key
                        else _t(
                            "cli.preflight.item.profile_source.config",
                            "config.toml profile",
                        )
                    ),
                )
            )
        else:
            errors.append(
                _t(
                    "cli.preflight.error.feishu_app_id_missing",
                    "Missing Feishu App ID. Set `HERMIT_FEISHU_APP_ID`.",
                )
            )
            items.append(
                _PreflightItem(
                    label=_t("cli.preflight.item.feishu_app_id.label", "Feishu App ID"),
                    ok=False,
                    detail=_t(
                        "cli.preflight.item.feishu_app_id.detail.missing",
                        "HERMIT_FEISHU_APP_ID not found",
                    ),
                )
            )

        if app_secret_key or settings.feishu_app_secret:
            items.append(
                _PreflightItem(
                    label=_t(
                        "cli.preflight.item.feishu_app_secret.label",
                        "Feishu App Secret",
                    ),
                    ok=True,
                    detail=(
                        f"{app_secret_key} ({_describe_env_source(app_secret_key, env_file_keys)})"
                        if app_secret_key
                        else _t(
                            "cli.preflight.item.profile_source.config",
                            "config.toml profile",
                        )
                    ),
                )
            )
        else:
            errors.append(
                _t(
                    "cli.preflight.error.feishu_app_secret_missing",
                    "Missing Feishu App Secret. Set `HERMIT_FEISHU_APP_SECRET`.",
                )
            )
            items.append(
                _PreflightItem(
                    label=_t(
                        "cli.preflight.item.feishu_app_secret.label",
                        "Feishu App Secret",
                    ),
                    ok=False,
                    detail=_t(
                        "cli.preflight.item.feishu_app_secret.detail.missing",
                        "HERMIT_FEISHU_APP_SECRET not found",
                    ),
                )
            )

        items.append(
            _PreflightItem(
                label=_t(
                    "cli.preflight.item.feishu_progress.label",
                    "Feishu progress cards",
                ),
                ok=True,
                detail=_t(
                    "cli.preflight.item.boolean.enabled"
                    if settings.feishu_thread_progress
                    else "cli.preflight.item.boolean.disabled",
                    "enabled" if settings.feishu_thread_progress else "disabled",
                ),
            )
        )

        items.append(
            _PreflightItem(
                label=_t(
                    "cli.preflight.item.scheduler_feishu.label",
                    "Scheduler Feishu notifications",
                ),
                ok=True,
                detail=(
                    _t(
                        "cli.preflight.item.scheduler_feishu.detail.configured",
                        "configured",
                    )
                    if settings.scheduler_feishu_chat_id
                    else _t(
                        "cli.preflight.item.scheduler_feishu.detail.missing",
                        "not set (optional; reload and scheduler will not proactively send Feishu notifications)",
                    )
                ),
            )
        )

    return items, errors


def _run_serve_preflight(adapter: str, settings: Settings) -> None:
    items, errors = _build_serve_preflight(adapter, settings)
    typer.echo("Hermit 启动前环境自检")
    for item in items:
        typer.echo(_format_preflight_item(item))
    typer.echo("")
    if errors:
        typer.echo(_t("cli.preflight.failed", "Pre-start checks failed:"))
        for message in errors:
            typer.echo(f"  - {message}")
        typer.echo("")
        raise typer.Exit(1)


@app.command()
def setup() -> None:
    """Interactive first-run wizard: configure API keys and initialize workspace."""
    GREEN = "\033[32m"
    BOLD = "\033[1m"

    typer.echo(f"\n{BOLD}{_t('cli.setup.title', 'Hermit Setup')}{RESET}\n")

    settings = get_settings()
    env_path = settings.base_dir / ".env"
    if env_path.exists():
        overwrite = typer.confirm(
            _t(
                "cli.setup.confirm_overwrite",
                "Config already exists at {path}. Overwrite?",
                path=env_path,
            ),
            default=False,
        )
        if not overwrite:
            typer.echo(_t("cli.setup.cancelled", "Setup cancelled."))
            raise typer.Exit()

    lines: list[str] = []

    # --- API credentials ---
    typer.echo(_t("cli.setup.step1", "Step 1/2  API credentials") + "\n")
    use_proxy = typer.confirm(
        _t(
            "cli.setup.use_proxy",
            "Use Claude-compatible proxy/gateway instead of Anthropic API directly?",
        ),
        default=False,
    )
    if use_proxy:
        auth_token = typer.prompt(
            _t(
                "cli.setup.prompt.auth_token",
                "  HERMIT_CLAUDE_AUTH_TOKEN (Bearer token)",
            ),
            hide_input=True,
        )
        base_url = typer.prompt(
            _t(
                "cli.setup.prompt.base_url",
                "  HERMIT_CLAUDE_BASE_URL  (proxy endpoint URL)",
            )
        )
        custom_headers = typer.prompt(
            _t(
                "cli.setup.prompt.custom_headers",
                "  HERMIT_CLAUDE_HEADERS (optional, e.g. 'X-Biz-Id: foo')",
            ),
            default="",
        )
        model = typer.prompt(
            _t("cli.setup.prompt.model", "  HERMIT_MODEL"),
            default="claude-3-7-sonnet-latest",
        )
        lines += [
            f"HERMIT_AUTH_TOKEN={auth_token}",
            f"HERMIT_BASE_URL={base_url}",
        ]
        if custom_headers:
            lines.append(f"HERMIT_CUSTOM_HEADERS={custom_headers}")
        lines.append(f"HERMIT_MODEL={model}")
    else:
        api_key = typer.prompt(
            _t("cli.setup.prompt.anthropic_api_key", "  ANTHROPIC_API_KEY"),
            hide_input=True,
        )
        lines.append(f"ANTHROPIC_API_KEY={api_key}")

    # --- Feishu (optional) ---
    typer.echo("\n" + _t("cli.setup.step2", "Step 2/2  Feishu bot adapter (optional)") + "\n")
    use_feishu = typer.confirm(_t("cli.setup.use_feishu", "Configure Feishu bot?"), default=False)
    if use_feishu:
        app_id = typer.prompt(_t("cli.setup.prompt.feishu_app_id", "  HERMIT_FEISHU_APP_ID"))
        app_secret = typer.prompt(
            _t("cli.setup.prompt.feishu_app_secret", "  HERMIT_FEISHU_APP_SECRET"),
            hide_input=True,
        )
        lines += [
            f"HERMIT_FEISHU_APP_ID={app_id}",
            f"HERMIT_FEISHU_APP_SECRET={app_secret}",
        ]

    # --- Write .env ---
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    get_settings.cache_clear()

    settings = get_settings()
    _ensure_workspace(settings)

    typer.echo(f"\n{GREEN}{_t('cli.setup.done', 'Done!')}{RESET}")
    typer.echo(_t("cli.setup.output.config", "  Config  -> {path}", path=env_path))
    typer.echo(
        _t(
            "cli.setup.output.workspace",
            "  Workspace -> {path}",
            path=settings.base_dir,
        )
    )
    typer.echo("\n" + _t("cli.setup.next_steps", "Next steps:"))
    typer.echo(_t("cli.setup.next_step.chat", "  hermit chat"))
    if use_feishu:
        typer.echo(_t("cli.setup.next_step.serve_feishu", "  hermit serve --adapter feishu"))
    typer.echo("")


@config_app.command("show")
def config_show() -> None:
    """Show the fully resolved runtime configuration."""
    get_settings.cache_clear()
    settings = get_settings()
    typer.echo(json.dumps(_resolved_config_snapshot(settings), ensure_ascii=False, indent=2))


@profiles_app.command("list")
def profiles_list() -> None:
    """List configured provider profiles from ~/.hermit/config.toml."""
    settings = get_settings()
    catalog = load_profile_catalog(settings.base_dir)
    if not catalog.exists:
        typer.echo(
            _t(
                "cli.profiles_list.no_config",
                "No config.toml found at {path}",
                path=catalog.path,
            )
        )
        raise typer.Exit()
    if not catalog.profiles:
        typer.echo(
            _t(
                "cli.profiles_list.no_profiles",
                "No profiles defined in {path}",
                path=catalog.path,
            )
        )
        raise typer.Exit()

    for name, values in sorted(catalog.profiles.items()):
        marker = (
            _t("cli.profiles_list.default_marker", " (default)")
            if name == catalog.default_profile
            else ""
        )
        provider = values.get("provider", "claude")
        model = values.get("model", "")
        suffix = _t(
            "cli.profiles_list.item",
            " provider={provider}{model_suffix}",
            provider=provider,
            model_suffix=(
                _t("cli.profiles_list.model_suffix", " model={model}", model=model) if model else ""
            ),
        )
        typer.echo(f"{name}{marker}{suffix}")


@profiles_app.command("resolve")
def profiles_resolve(name: str | None = None) -> None:
    """Resolve one profile as Hermit would read it from config.toml."""
    settings = get_settings()
    resolved = resolve_profile(settings.base_dir, name)
    payload = {
        "requested_profile": name,
        "resolved_profile": resolved.name,
        "config_file": str(resolved.source_path),
        "config_file_exists": resolved.source_path.exists(),
        "values": resolved.values,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@auth_app.command("status")
def auth_status() -> None:
    """Show which auth source the current provider will use."""
    get_settings.cache_clear()
    settings = get_settings()
    payload = _auth_status_summary(settings)
    payload["selected_profile"] = settings.resolved_profile
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def init(base_dir: Optional[Path] = None) -> None:
    """Initialize the local Hermit workspace."""
    settings = get_settings()
    if base_dir is not None:
        settings.base_dir = base_dir
    _ensure_workspace(settings)
    typer.echo(
        _t(
            "cli.init.done",
            "Initialized Hermit workspace at {path}",
            path=settings.base_dir,
        )
    )


@app.command()
def startup_prompt() -> None:
    """Print the full startup system prompt."""
    settings = get_settings()
    _ensure_workspace(settings)

    pm = PluginManager(settings=settings)
    builtin_dir = Path(__file__).parent / "builtin"
    pm.discover_and_load(builtin_dir, settings.plugins_dir)

    base = build_base_context(settings, Path.cwd())
    typer.echo(pm.build_system_prompt(base))


def _build_runner(
    settings: Settings,
    preloaded_skills: list[str] | None = None,
    pm: PluginManager | None = None,
    serve_mode: bool = False,
) -> tuple[AgentRunner, PluginManager]:
    """Build an AgentRunner (agent + session manager + plugin manager)."""
    store = KernelStore(settings.kernel_db_path)
    agent, pm = build_runtime(
        settings,
        preloaded_skills=preloaded_skills,
        pm=pm,
        serve_mode=serve_mode,
        store=store,
    )
    manager = SessionManager(
        settings.sessions_dir,
        settings.session_idle_timeout_seconds,
        store=store,
    )
    runner = AgentRunner(
        agent,
        manager,
        pm,
        serve_mode=serve_mode,
        task_controller=TaskController(store),
    )
    pm.setup_commands(runner)
    runner.start_background_services()
    return runner, pm


@app.command()
def run(prompt: str) -> None:
    """Run a one-shot CLI agent session."""
    settings = get_settings()
    _ensure_workspace(settings)
    configure_logging(settings.log_level)
    _require_auth(settings)

    runner, pm = _build_runner(settings)
    with _caffeinate(settings):
        try:
            result = runner.handle("cli-oneshot", prompt, on_tool_call=_on_tool_call)
            runner.close_session("cli-oneshot")
            _print_result(result)
        finally:
            _stop_runner_background_services(runner)
            pm.stop_mcp_servers()


@app.command()
def chat(session_id: str = "cli", debug: bool = False) -> None:
    """Interactive multi-turn chat session."""
    settings = get_settings()
    _ensure_workspace(settings)
    configure_logging("DEBUG" if debug else settings.log_level)
    _require_auth(settings)

    runner, pm = _build_runner(settings)
    typer.echo(
        _t(
            "cli.chat.banner",
            "Hermit chat (session={session_id}). Type /help for commands.",
            session_id=session_id,
        )
    )

    with _caffeinate(settings):
        try:
            while True:
                try:
                    user_input = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    typer.echo("\n" + _t("cli.chat.bye", "Bye."))
                    break

                if not user_input:
                    continue

                result = runner.dispatch(session_id, user_input, on_tool_call=_on_tool_call)
                if result.is_command:
                    typer.echo(result.text)
                    if result.should_exit:
                        break
                elif result.agent_result:
                    _print_result(result.agent_result)
        finally:
            # Always close the session so SESSION_END hook fires and memories are saved,
            # even if the user hits Ctrl+C during an LLM generation turn.
            runner.close_session(session_id)
            _stop_runner_background_services(runner)
            pm.stop_mcp_servers()


_serve_log = logging.getLogger("hermit.serve")


@dataclass(frozen=True)
class _ServeRunResult:
    reload_requested: bool
    reason: str
    detail: str
    signal_name: str | None = None


def _pid_path(settings: Any, adapter: str) -> Path:
    return settings.base_dir / f"serve-{adapter}.pid"


def _write_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _serve_log_dir(settings: Any) -> Path:
    path = settings.base_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serve_status_path(settings: Any, adapter: str) -> Path:
    return _serve_log_dir(settings) / f"serve-{adapter}-status.json"


def _serve_exit_history_path(settings: Any, adapter: str) -> Path:
    return _serve_log_dir(settings) / f"serve-{adapter}-exit-history.jsonl"


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _configure_unbuffered_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(line_buffering=True, write_through=True)
            except TypeError:
                reconfigure(line_buffering=True)


def _write_serve_status(
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
    payload: dict[str, Any] = {
        "adapter": adapter,
        "pid": os.getpid(),
        "phase": phase,
        "reason": reason,
        "detail": detail,
        "signal": signal_name,
        "run_started_at": run_started_at,
        "updated_at": _iso_now(),
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


async def _serve_with_signals(
    adapter_instance: Any,
    runner: Any,
) -> _ServeRunResult:
    """Run the adapter until it exits or a lifecycle signal is received.

    Returns a structured result describing why the adapter stopped.
    """
    loop = asyncio.get_running_loop()
    reload_event = asyncio.Event()
    terminate_event = asyncio.Event()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGHUP, reload_event.set)
        loop.add_signal_handler(signal.SIGTERM, terminate_event.set)

    start_task = asyncio.ensure_future(adapter_instance.start(runner))
    reload_task = asyncio.ensure_future(reload_event.wait())
    terminate_task = asyncio.ensure_future(terminate_event.wait())

    try:
        done, pending = await asyncio.wait(
            {start_task, reload_task, terminate_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if reload_event.is_set():
            _serve_log.info("SIGHUP received — stopping adapter for reload...")
            await adapter_instance.stop()
            return _ServeRunResult(
                reload_requested=True,
                reason="signal",
                detail="SIGHUP received — stopping adapter for reload.",
                signal_name="SIGHUP",
            )

        if terminate_event.is_set():
            _serve_log.warning("SIGTERM received — stopping adapter for shutdown...")
            await adapter_instance.stop()
            return _ServeRunResult(
                reload_requested=False,
                reason="signal",
                detail="SIGTERM received — stopping adapter for shutdown.",
                signal_name="SIGTERM",
            )

        if start_task in done:
            exc = start_task.exception()
            if exc is not None:
                raise exc
            return _ServeRunResult(
                reload_requested=False,
                reason="adapter_stopped",
                detail="Adapter returned control without an explicit reload request.",
            )

        return _ServeRunResult(
            reload_requested=False,
            reason="unknown",
            detail="Serve loop exited without a recognized stop condition.",
        )
    finally:
        if sys.platform != "win32":
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(signal.SIGHUP)
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(signal.SIGTERM)


def _notify_reload(settings: Any, adapter: str) -> None:
    """Fire a DISPATCH_RESULT so the Feishu hook sends a reload notification."""
    from hermit.plugin.base import HookEvent

    chat_id = getattr(settings, "scheduler_feishu_chat_id", "") or os.environ.get(
        "HERMIT_SCHEDULER_FEISHU_CHAT_ID", ""
    )
    if not chat_id:
        return
    try:
        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).parent / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)
        pm.hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="system",
            title=_t("cli.reload.notify.title", "Hermit Reloaded"),
            result_text=_t(
                "cli.reload.notify.body",
                "Hermit (`{adapter}`) has been reloaded successfully.\n\nConfiguration, plugins, and tools were rebuilt.",
                adapter=adapter,
            ),
            success=True,
            notify={"feishu_chat_id": chat_id},
        )
    except Exception:
        _serve_log.debug("Failed to send reload notification", exc_info=True)


@app.command()
def serve(adapter: str = "feishu") -> None:
    """Start Hermit as a long-running service with a message adapter.

    Supports graceful reload via SIGHUP: the adapter is stopped, all
    configuration / plugins / tools are rebuilt from scratch, and the adapter
    restarts.  Use ``hermit reload`` to send SIGHUP conveniently.
    """

    _configure_unbuffered_stdio()
    settings = get_settings()
    _ensure_workspace(settings)
    configure_logging(settings.log_level)
    _run_serve_preflight(adapter, settings)

    pid_file = _pid_path(settings, adapter)
    _write_pid(pid_file)
    _write_serve_status(
        settings,
        adapter,
        phase="starting",
        reason="startup",
        detail=f"Serve command is starting for adapter '{adapter}'.",
    )

    try:
        _serve_loop(adapter, pid_file)
    except BaseException as exc:
        refreshed_settings = get_settings()
        _write_serve_status(
            refreshed_settings,
            adapter,
            phase="crashed",
            reason="exception",
            detail=f"Serve process exited because of an unhandled {type(exc).__name__}.",
            exc=exc,
            append_history=True,
        )
        raise
    finally:
        _remove_pid(pid_file)


def _serve_loop(adapter: str, pid_file: Path) -> None:
    """Inner restart loop — each iteration rebuilds everything from scratch."""
    from hermit.plugin.base import HookEvent

    while True:
        get_settings.cache_clear()
        settings = get_settings()
        configure_logging(settings.log_level)
        cycle_started_at = _iso_now()

        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).parent / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)

        try:
            adapter_instance = pm.get_adapter(adapter)
        except KeyError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1)

        preloaded = getattr(adapter_instance, "required_skills", [])
        runner, _ = _build_runner(settings, preloaded_skills=preloaded, pm=pm, serve_mode=True)
        pm.hooks.fire(HookEvent.SERVE_START, runner=runner, settings=settings)
        _write_serve_status(
            settings,
            adapter,
            phase="running",
            reason="startup",
            detail=f"Adapter '{adapter}' is running and waiting for events.",
            run_started_at=cycle_started_at,
        )

        typer.echo(
            _t(
                "cli.serve.starting",
                "Starting Hermit with '{adapter}' adapter...",
                adapter=adapter,
            )
        )

        run_result = _ServeRunResult(
            reload_requested=False,
            reason="unknown",
            detail="Serve loop exited without updating the run result.",
        )
        with _caffeinate(settings):
            try:
                run_result = asyncio.run(_serve_with_signals(adapter_instance, runner))
            except KeyboardInterrupt:
                typer.echo("\n" + _t("cli.serve.shutting_down", "Shutting down..."))
                asyncio.run(adapter_instance.stop())
                run_result = _ServeRunResult(
                    reload_requested=False,
                    reason="signal",
                    detail="SIGINT received — stopping adapter for shutdown.",
                    signal_name="SIGINT",
                )
            finally:
                pm.hooks.fire(HookEvent.SERVE_STOP)
                _stop_runner_background_services(runner)
                pm.stop_mcp_servers()

        if run_result.reload_requested:
            _serve_log.info("Reloading Hermit...")
            typer.echo(
                _t(
                    "cli.serve.reloading",
                    "Reloading Hermit - rebuilding config, plugins, tools...",
                )
            )
            _write_serve_status(
                settings,
                adapter,
                phase="reloading",
                reason=run_result.reason,
                detail=run_result.detail,
                signal_name=run_result.signal_name,
                run_started_at=cycle_started_at,
            )
            _write_pid(pid_file)
            _notify_reload(settings, adapter)
            continue

        _write_serve_status(
            settings,
            adapter,
            phase="stopped",
            reason=run_result.reason,
            detail=run_result.detail,
            signal_name=run_result.signal_name,
            run_started_at=cycle_started_at,
            append_history=True,
        )
        break


@app.command()
def reload(adapter: str = "feishu") -> None:
    """Send SIGHUP to a running ``hermit serve`` process to trigger a graceful reload.

    The serve process re-reads configuration, rediscovers plugins, rebuilds
    the tool registry and system prompt, and restarts the adapter — all without
    losing the PID.
    """
    if sys.platform == "win32":
        typer.echo(
            _t(
                "cli.reload.windows_unsupported",
                "Reload via signal is not supported on Windows.",
            )
        )
        raise typer.Exit(1)

    settings = get_settings()
    pid_file = _pid_path(settings, adapter)
    pid = _read_pid(pid_file)

    if pid is None:
        typer.echo(
            _t(
                "cli.reload.no_process",
                "No running serve process found for adapter '{adapter}'.\n  PID file: {pid_file}",
                adapter=adapter,
                pid_file=pid_file,
            )
        )
        raise typer.Exit(1)

    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        typer.echo(
            _t(
                "cli.reload.process_missing",
                "Process {pid} not found (stale PID file). Cleaning up.",
                pid=pid,
            )
        )
        _remove_pid(pid_file)
        raise typer.Exit(1)
    except PermissionError:
        typer.echo(
            _t(
                "cli.reload.permission_denied",
                "Permission denied sending SIGHUP to PID {pid}.",
                pid=pid,
            )
        )
        raise typer.Exit(1)

    typer.echo(
        _t(
            "cli.reload.sent",
            "Sent SIGHUP to Hermit serve (PID {pid}, adapter='{adapter}').",
            pid=pid,
            adapter=adapter,
        )
    )
    typer.echo(
        _t(
            "cli.reload.followup",
            "The service will reload configuration, plugins, and tools.",
        )
    )


@app.command()
def sessions() -> None:
    """List known sessions."""
    settings = get_settings()
    _ensure_workspace(settings)
    manager = SessionManager(settings.sessions_dir, settings.session_idle_timeout_seconds)
    for sid in manager.list_sessions():
        typer.echo(sid)


def _get_kernel_store() -> KernelStore:
    settings = get_settings()
    _ensure_workspace(settings)
    return KernelStore(settings.kernel_db_path)


def _format_epoch(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")


def _memory_payload_from_record(record: Any, *, settings: Settings) -> dict[str, Any]:
    governance = MemoryGovernanceService()
    workspace_root = (
        record.scope_ref
        if getattr(record, "scope_kind", "") == "workspace" and getattr(record, "scope_ref", "")
        else str(settings.base_dir)
    )
    inspection = governance.inspect_claim(
        category=record.category,
        claim_text=record.claim_text,
        conversation_id=record.conversation_id,
        workspace_root=workspace_root,
        promotion_reason=record.promotion_reason,
    )
    assertion = dict(getattr(record, "structured_assertion", {}) or {})
    return {
        "memory_id": record.memory_id,
        "task_id": record.task_id,
        "conversation_id": record.conversation_id,
        "claim_text": record.claim_text,
        "stored_category": record.category,
        "status": record.status,
        "scope_kind": record.scope_kind,
        "scope_ref": record.scope_ref,
        "retention_class": record.retention_class,
        "promotion_reason": record.promotion_reason,
        "confidence": record.confidence,
        "trust_tier": record.trust_tier,
        "evidence_refs": list(record.evidence_refs),
        "supersedes": list(record.supersedes),
        "supersedes_memory_ids": list(record.supersedes_memory_ids),
        "superseded_by_memory_id": record.superseded_by_memory_id,
        "source_belief_ref": record.source_belief_ref,
        "invalidation_reason": record.invalidation_reason,
        "invalidated_at": record.invalidated_at,
        "expires_at": record.expires_at,
        "structured_assertion": assertion,
        "inspection": inspection,
    }


def _render_memory_payload(payload: dict[str, Any]) -> str:
    inspection = dict(payload.get("inspection", {}) or {})
    lines = [
        f"Memory ID: {payload.get('memory_id', '-')}",
        f"Claim: {payload.get('claim_text', '')}",
        f"Stored Category: {payload.get('stored_category', payload.get('category', '-'))}",
        f"Resolved Category: {inspection.get('category', '-')}",
        f"Retention: {inspection.get('retention_class', payload.get('retention_class', '-'))}",
        f"Status: {payload.get('status', inspection.get('status', '-'))}",
        f"Scope: {inspection.get('scope_kind', payload.get('scope_kind', '-'))} {inspection.get('scope_ref', payload.get('scope_ref', '-'))}",
        f"Subject: {inspection.get('subject_key', '-') or '-'}",
        f"Topic: {inspection.get('topic_key', '-') or '-'}",
        f"Promotion Reason: {payload.get('promotion_reason', '-')}",
        f"Confidence: {payload.get('confidence', '-')}",
        f"Trust Tier: {payload.get('trust_tier', '-')}",
        f"Expires At: {_format_epoch(payload.get('expires_at'))}",
        f"Invalidated At: {_format_epoch(payload.get('invalidated_at'))}",
        f"Superseded By: {payload.get('superseded_by_memory_id') or '-'}",
    ]
    source_belief_ref = payload.get("source_belief_ref")
    if source_belief_ref:
        lines.append(f"Source Belief: {source_belief_ref}")
    if payload.get("supersedes"):
        lines.append("Supersedes:")
        lines.extend([f"  - {item}" for item in payload["supersedes"]])
    explanations = list(inspection.get("explanation", []) or [])
    if explanations:
        lines.append("Governance:")
        lines.extend([f"  - {item}" for item in explanations])
    matched_signals = dict(
        (inspection.get("structured_assertion", {}) or {}).get("matched_signals", {}) or {}
    )
    if matched_signals:
        lines.append("Matched Signals:")
        for name, hits in sorted(matched_signals.items()):
            lines.append(f"  - {name}: {', '.join(hits)}")
    return "\n".join(lines)


def _memory_list_payload(
    records: list[Any],
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    governance = MemoryGovernanceService()
    payload: list[dict[str, Any]] = []
    for record in records:
        payload.append(
            {
                "memory_id": record.memory_id,
                "status": record.status,
                "category": record.category,
                "retention_class": record.retention_class,
                "scope_kind": record.scope_kind,
                "scope_ref": record.scope_ref,
                "subject_key": governance.subject_key_for_memory(record),
                "topic_key": governance.topic_key_for_memory(record),
                "claim_text": record.claim_text,
                "updated_at": record.updated_at,
                "expires_at": record.expires_at,
                "superseded_by_memory_id": record.superseded_by_memory_id,
            }
        )
    return payload


@task_app.command("list")
def task_list(
    limit: int = typer.Option(
        20,
        help=_cli_t("cli.task.list.limit", "Maximum number of tasks to show."),
    ),
) -> None:
    """List recent tasks from the kernel ledger."""
    store = _get_kernel_store()
    tasks = store.list_tasks(limit=limit)
    if not tasks:
        typer.echo(_t("cli.task.list.empty", "No tasks found."))
        return
    for task in tasks:
        typer.echo(
            _t(
                "cli.task.list.item",
                "[{task_id}] {status} {source_channel} {title}",
                task_id=task.task_id,
                status=task.status,
                source_channel=task.source_channel,
                title=task.title,
            )
        )


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show one task and its pending approvals."""
    store = _get_kernel_store()
    task = store.get_task(task_id)
    if task is None:
        typer.echo(_t("cli.task.show.not_found", "Task not found: {task_id}", task_id=task_id))
        raise typer.Exit(1)
    typer.echo(json.dumps(task.__dict__, ensure_ascii=False, indent=2))
    approvals = store.list_approvals(task_id=task_id, limit=20)
    if approvals:
        typer.echo("\n" + _t("cli.task.show.approvals", "Pending/Recent approvals:"))
        copy_service = ApprovalCopyService()
        for approval in approvals:
            typer.echo(
                _t(
                    "cli.task.show.approval_item",
                    "  [{approval_id}] {status} {approval_type}",
                    approval_id=approval.approval_id,
                    status=approval.status,
                    approval_type=approval.approval_type,
                )
            )
            summary = copy_service.resolve_copy(
                approval.requested_action, approval.approval_id
            ).summary
            typer.echo(_t("cli.task.show.indented", "    {value}", value=summary))
            if approval.decision_ref:
                typer.echo(
                    _t(
                        "cli.task.show.decision_ref",
                        "    decision_ref={decision_ref}",
                        decision_ref=approval.decision_ref,
                    )
                )
            if approval.state_witness_ref:
                typer.echo(
                    _t(
                        "cli.task.show.witness_ref",
                        "    witness_ref={witness_ref}",
                        witness_ref=approval.state_witness_ref,
                    )
                )

    decisions = store.list_decisions(task_id=task_id, limit=20)
    if decisions:
        typer.echo("\n" + _t("cli.task.show.decisions", "Recent decisions:"))
        for decision in decisions:
            typer.echo(
                _t(
                    "cli.task.show.decision_item",
                    "  [{decision_id}] {verdict} {decision_type} action={action_type}",
                    decision_id=decision.decision_id,
                    verdict=decision.verdict,
                    decision_type=decision.decision_type,
                    action_type=decision.action_type,
                )
            )
            typer.echo(_t("cli.task.show.indented", "    {value}", value=decision.reason))

    capability_grants = store.list_capability_grants(task_id=task_id, limit=20)
    if capability_grants:
        typer.echo("\n" + _t("cli.task.show.capability_grants", "Recent capability grants:"))
        for grant in capability_grants:
            typer.echo(
                _t(
                    "cli.task.show.capability_grant_item",
                    "  [{grant_id}] {status} {action_class}",
                    grant_id=grant.grant_id,
                    status=grant.status,
                    action_class=grant.action_class,
                )
            )
            typer.echo(
                _t(
                    "cli.task.show.decision_ref",
                    "    decision_ref={decision_ref}",
                    decision_ref=grant.decision_ref,
                )
            )

    workspace_leases = store.list_workspace_leases(task_id=task_id, limit=20)
    if workspace_leases:
        typer.echo("\n" + _t("cli.task.show.workspace_leases", "Recent workspace leases:"))
        for lease in workspace_leases:
            typer.echo(
                _t(
                    "cli.task.show.workspace_lease_item",
                    "  [{lease_id}] {status} {mode} root={root_path}",
                    lease_id=lease.lease_id,
                    status=lease.status,
                    mode=lease.mode,
                    root_path=lease.root_path,
                )
            )
    case = SupervisionService(store).build_task_case(task_id)
    claims = dict(case["operator_answers"].get("claims", {}) or {})
    task_gate = dict(claims.get("task_gate", {}) or {})
    claimable = list(claims.get("repository", {}).get("claimable_profiles", []) or [])
    reentry = dict(case["operator_answers"].get("reentry", {}) or {})
    typer.echo("\n" + _t("cli.task.show.claims", "Claim status:"))
    typer.echo(
        _t(
            "cli.task.show.indented",
            "    {value}",
            value=(
                f"repository={', '.join(claimable) or '-'} "
                f"verifiable_ready={bool(task_gate.get('verifiable_ready'))} "
                f"strong_verifiable_ready={bool(task_gate.get('strong_verifiable_ready'))} "
                f"proof_mode={task_gate.get('proof_mode') or '-'} "
                f"strongest_export_mode={task_gate.get('strongest_export_mode') or '-'}"
            ),
        )
    )
    typer.echo("\n" + _t("cli.task.show.reentry", "Re-entry status:"))
    typer.echo(
        _t(
            "cli.task.show.indented",
            "    {value}",
            value=(
                f"required={int(reentry.get('required_count', 0) or 0)} "
                f"resolved={int(reentry.get('resolved_count', 0) or 0)}"
            ),
        )
    )
    for item in list(reentry.get("recent_attempts", []) or [])[:3]:
        typer.echo(
            _t(
                "cli.task.show.indented",
                "    {value}",
                value=(
                    f"[{item.get('step_attempt_id')}] {item.get('status')} "
                    f"reason={item.get('reentry_reason') or '-'} "
                    f"boundary={item.get('reentry_boundary') or '-'} "
                    f"recovery_required={bool(item.get('recovery_required'))}"
                ),
            )
        )


@task_app.command("events")
def task_events(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
    limit: int = 100,
) -> None:
    """Show task events."""
    store = _get_kernel_store()
    typer.echo(
        json.dumps(store.list_events(task_id=task_id, limit=limit), ensure_ascii=False, indent=2)
    )


@task_app.command("receipts")
def task_receipts(
    task_id: Optional[str] = typer.Option(
        None,
        help=_cli_t("cli.task.receipts.task_id", "Optional task ID filter."),
    ),
    limit: int = 50,
) -> None:
    """Show receipts."""
    store = _get_kernel_store()
    payload = [receipt.__dict__ for receipt in store.list_receipts(task_id=task_id, limit=limit)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@task_app.command("explain")
def task_explain(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Explain why a task executed, under what authority, and what changed."""
    store = _get_kernel_store()
    typer.echo(
        json.dumps(SupervisionService(store).build_task_case(task_id), ensure_ascii=False, indent=2)
    )


@task_app.command("case")
def task_case(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show unified operator case view for one task."""
    store = _get_kernel_store()
    typer.echo(
        json.dumps(SupervisionService(store).build_task_case(task_id), ensure_ascii=False, indent=2)
    )


@task_app.command("proof")
def task_proof(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
) -> None:
    """Show proof summary for one task."""
    store = _get_kernel_store()
    summary = ProofService(store).build_proof_summary(task_id)
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@task_app.command("proof-export")
def task_proof_export(
    task_id: str = typer.Argument(..., help=_cli_t("cli.task.common.task_id", "Task ID.")),
    output: Optional[Path] = typer.Option(
        None,
        help=_cli_t(
            "cli.task.proof_export.output",
            "Optional path to write the exported proof bundle.",
        ),
    ),
) -> None:
    """Export one task's proof bundle."""
    store = _get_kernel_store()
    bundle = ProofService(store).export_task_proof(task_id)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    typer.echo(payload)


@task_app.command("claim-status")
def task_claims(
    task_id: Optional[str] = typer.Argument(
        None,
        help=_cli_t("cli.task.common.task_id", "Optional task ID."),
    ),
) -> None:
    """Show repository claim gate status, optionally with task-level proof readiness."""
    store = _get_kernel_store()
    if not task_id:
        typer.echo(json.dumps(repository_claim_status(), ensure_ascii=False, indent=2))
        return
    proof = ProofService(store).build_proof_summary(task_id)
    typer.echo(
        json.dumps(
            task_claim_status(store, task_id, proof_summary=proof),
            ensure_ascii=False,
            indent=2,
        )
    )


@task_app.command("rollback")
def task_rollback(
    receipt_id: str = typer.Argument(
        ..., help=_cli_t("cli.task.rollback.receipt_id", "Receipt ID.")
    ),
) -> None:
    """Execute a supported rollback for one receipt."""
    store = _get_kernel_store()
    payload = RollbackService(store).execute(receipt_id)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@task_app.command("projections-rebuild")
def task_projections_rebuild(
    task_id: Optional[str] = typer.Argument(
        None,
        help=_cli_t("cli.task.projections.task_id", "Optional task ID."),
    ),
    all_tasks: bool = typer.Option(
        False,
        "--all",
        help=_cli_t("cli.task.projections.all", "Rebuild all task projections."),
    ),
) -> None:
    """Rebuild operator projection cache."""
    store = _get_kernel_store()
    service = ProjectionService(store)
    payload = service.rebuild_all() if all_tasks or not task_id else service.rebuild_task(task_id)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _task_resolution(action: str, approval_id: str, reason: str = "") -> None:
    settings = get_settings()
    _ensure_workspace(settings)
    runner, pm = _build_runner(settings)
    try:
        store = _get_kernel_store()
        approval = store.get_approval(approval_id)
        if approval is None:
            typer.echo(
                _t(
                    "cli.task.approval.not_found",
                    "Approval not found: {approval_id}",
                    approval_id=approval_id,
                )
            )
            raise typer.Exit(1)
        task = store.get_task(approval.task_id)
        conversation_id = task.conversation_id if task is not None else "cli"
        result = runner._resolve_approval(  # type: ignore[attr-defined]
            conversation_id,
            action=action,
            approval_id=approval_id,
            reason=reason,
        )
        typer.echo(result.text)
    finally:
        _stop_runner_background_services(runner)
        pm.stop_mcp_servers()


@task_app.command("approve")
def task_approve(
    approval_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
) -> None:
    """Approve once and resume a blocked task."""
    _task_resolution("approve_once", approval_id)


@task_app.command("approve-mutable-workspace")
def task_approve_mutable_workspace(
    approval_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
) -> None:
    """Approve a mutable workspace lease for the current blocked attempt."""
    _task_resolution("approve_mutable_workspace", approval_id)


@task_app.command("deny")
def task_deny(
    approval_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.task.common.approval_id", "Approval ID."),
    ),
    reason: str = typer.Option(
        "",
        help=_cli_t("cli.task.deny.reason", "Optional deny reason."),
    ),
) -> None:
    """Deny a blocked task."""
    _task_resolution("deny", approval_id, reason=reason)


@task_app.command("resume")
def task_resume(
    approval_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.task.resume.approval_id", "Approval ID to resume."),
    ),
) -> None:
    """Resume a blocked task by approving its latest pending approval."""
    _task_resolution("approve_once", approval_id)


def _task_capability_list(
    limit: int = typer.Option(
        50,
        help=_cli_t("cli.task.capability.limit", "Maximum number of grants to show."),
    ),
) -> None:
    """Show active and recent capability grants."""
    store = _get_kernel_store()
    payload = [grant.__dict__ for grant in store.list_capability_grants(limit=limit)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _task_capability_revoke(
    grant_id: str = typer.Argument(
        ..., help=_cli_t("cli.task.capability.grant_id", "Capability grant ID.")
    ),
) -> None:
    """Revoke a capability grant."""
    store = _get_kernel_store()
    grant = store.get_capability_grant(grant_id)
    if grant is None:
        typer.echo(
            _t(
                "cli.task.capability.not_found",
                "Capability grant not found: {grant_id}",
                grant_id=grant_id,
            )
        )
        raise typer.Exit(1)
    store.update_capability_grant(
        grant_id,
        status="revoked",
        revoked_at=time.time(),
    )
    typer.echo(
        _t(
            "cli.task.capability.revoked",
            "Revoked capability grant '{grant_id}'.",
            grant_id=grant_id,
        )
    )


@task_capability_app.command("list")
def task_capability_list(
    limit: int = typer.Option(
        50,
        help=_cli_t("cli.task.capability.limit", "Maximum number of grants to show."),
    ),
) -> None:
    """Show active and recent capability grants."""
    _task_capability_list(limit=limit)


@task_capability_app.command("revoke")
def task_capability_revoke(
    grant_id: str = typer.Argument(
        ..., help=_cli_t("cli.task.capability.grant_id", "Capability grant ID.")
    ),
) -> None:
    """Revoke a capability grant."""
    _task_capability_revoke(grant_id)


@memory_app.command("inspect")
def memory_inspect(
    memory_id: Optional[str] = typer.Argument(
        None,
        help=_cli_t("cli.memory.inspect.memory_id", "Optional memory ID."),
    ),
    claim_text: Optional[str] = typer.Option(
        None,
        "--claim-text",
        help=_cli_t(
            "cli.memory.inspect.claim_text",
            "Inspect a raw claim without reading a stored memory record.",
        ),
    ),
    category: str = typer.Option(
        "其他",
        "--category",
        help=_cli_t("cli.memory.inspect.category", "Category hint used for raw claim inspection."),
    ),
    conversation_id: Optional[str] = typer.Option(
        None,
        "--conversation-id",
        help=_cli_t("cli.memory.inspect.conversation_id", "Conversation scope hint."),
    ),
    workspace_root: Optional[Path] = typer.Option(
        None,
        "--workspace-root",
        help=_cli_t("cli.memory.inspect.workspace_root", "Workspace scope hint."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=_cli_t("cli.memory.inspect.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Inspect a stored memory record or preview governance classification for a raw claim."""
    settings = get_settings()
    _ensure_workspace(settings)
    governance = MemoryGovernanceService()

    if not memory_id and not claim_text:
        typer.echo(
            _t(
                "cli.memory.inspect.require_target",
                "Provide either a memory_id argument or --claim-text.",
            )
        )
        raise typer.Exit(1)

    if memory_id:
        store = _get_kernel_store()
        record = store.get_memory_record(memory_id)
        if record is None:
            typer.echo(
                _t(
                    "cli.memory.inspect.not_found",
                    "Memory not found: {memory_id}",
                    memory_id=memory_id,
                )
            )
            raise typer.Exit(1)
        payload = _memory_payload_from_record(record, settings=settings)
    else:
        resolved_workspace_root = (
            str(workspace_root.resolve()) if workspace_root else str(settings.base_dir)
        )
        inspection = governance.inspect_claim(
            category=category,
            claim_text=str(claim_text or ""),
            conversation_id=conversation_id,
            workspace_root=resolved_workspace_root,
        )
        payload = {
            "memory_id": None,
            "claim_text": claim_text,
            "stored_category": category,
            "status": "preview",
            "scope_kind": inspection["scope_kind"],
            "scope_ref": inspection["scope_ref"],
            "retention_class": inspection["retention_class"],
            "promotion_reason": "belief_promotion",
            "confidence": None,
            "trust_tier": None,
            "supersedes": [],
            "superseded_by_memory_id": None,
            "source_belief_ref": None,
            "invalidated_at": None,
            "expires_at": inspection["expires_at"],
            "structured_assertion": inspection["structured_assertion"],
            "inspection": inspection,
        }

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(_render_memory_payload(payload))


@memory_app.command("list")
def memory_list(
    status: Optional[str] = typer.Option(
        "active",
        "--status",
        help=_cli_t("cli.memory.list.status", "Optional status filter."),
    ),
    conversation_id: Optional[str] = typer.Option(
        None,
        "--conversation-id",
        help=_cli_t("cli.memory.list.conversation_id", "Optional conversation filter."),
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help=_cli_t("cli.memory.list.limit", "Maximum number of memory records to show."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=_cli_t("cli.memory.list.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """List recent memory records with governance-facing metadata."""
    settings = get_settings()
    _ensure_workspace(settings)
    store = _get_kernel_store()
    records = store.list_memory_records(status=status, conversation_id=conversation_id, limit=limit)
    payload = _memory_list_payload(records, settings=settings)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload:
        typer.echo(_t("cli.memory.list.empty", "No memory records found."))
        return
    for item in payload:
        typer.echo(
            f"[{item['memory_id']}] {item['status']} {item['category']} "
            f"{item['retention_class']} {item['subject_key'] or '-'} {item['claim_text']}"
        )


@memory_app.command("status")
def memory_status(
    limit: int = typer.Option(
        1000,
        "--limit",
        help=_cli_t("cli.memory.status.limit", "Maximum number of memory records to scan."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=_cli_t("cli.memory.status.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Show aggregate memory health and governance counts."""
    settings = get_settings()
    _ensure_workspace(settings)
    governance = MemoryGovernanceService()
    store = _get_kernel_store()
    records = store.list_memory_records(limit=limit)
    by_status: dict[str, int] = {}
    by_retention: dict[str, int] = {}
    by_category: dict[str, int] = {}
    expired = 0
    superseded_links = 0
    for record in records:
        by_status[record.status] = by_status.get(record.status, 0) + 1
        by_retention[record.retention_class] = by_retention.get(record.retention_class, 0) + 1
        by_category[record.category] = by_category.get(record.category, 0) + 1
        if governance.is_expired(record):
            expired += 1
        if record.superseded_by_memory_id:
            superseded_links += 1
    payload = {
        "total_records": len(records),
        "active_records": sum(
            1
            for record in records
            if record.status == "active" and not governance.is_expired(record)
        ),
        "expired_records": expired,
        "superseded_links": superseded_links,
        "by_status": by_status,
        "by_retention_class": by_retention,
        "by_category": by_category,
        "memory_file": str(settings.memory_file),
        "kernel_db_path": str(settings.kernel_db_path),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Total Records: {payload['total_records']}")
    typer.echo(f"Active Records: {payload['active_records']}")
    typer.echo(f"Expired Records: {payload['expired_records']}")
    typer.echo(f"Superseded Links: {payload['superseded_links']}")
    typer.echo("By Status:")
    for key, value in sorted(by_status.items()):
        typer.echo(f"  - {key}: {value}")
    typer.echo("By Retention:")
    for key, value in sorted(by_retention.items()):
        typer.echo(f"  - {key}: {value}")


@memory_app.command("rebuild")
def memory_rebuild(
    json_output: bool = typer.Option(
        False,
        "--json",
        help=_cli_t("cli.memory.rebuild.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Reconcile active records and export the mirror file from kernel state."""
    settings = get_settings()
    _ensure_workspace(settings)
    store = _get_kernel_store()
    service = MemoryRecordService(store, mirror_path=settings.memory_file)
    before_active = len(store.list_memory_records(status="active", limit=5000))
    result = service.reconcile_active_records()
    export_path = service.export_mirror(settings.memory_file)
    after_active = len(store.list_memory_records(status="active", limit=5000))
    payload = {
        "before_active": before_active,
        "after_active": after_active,
        **result,
        "mirror_path": str(settings.memory_file),
        "export_path": str(export_path) if export_path is not None else None,
        "render_mode": "export_only",
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        f"Rebuilt memory mirror. active {before_active} -> {after_active}; "
        f"superseded={result['superseded_count']} duplicate={result['duplicate_count']}"
    )


@memory_app.command("export")
def memory_export(
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help=_cli_t("cli.memory.export.output", "Optional output path for the exported mirror."),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=_cli_t("cli.memory.export.json", "Emit JSON instead of human-readable text."),
    ),
) -> None:
    """Export the current kernel-backed memory mirror without mutating records."""
    settings = get_settings()
    _ensure_workspace(settings)
    store = _get_kernel_store()
    target = output or settings.memory_file
    service = MemoryRecordService(store, mirror_path=target)
    export_path = service.export_mirror(target)
    active_records = len(store.list_memory_records(status="active", limit=5000))
    payload = {
        "active_records": active_records,
        "export_path": str(export_path) if export_path is not None else None,
        "render_mode": "export_only",
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(
        _t(
            "cli.memory.export.done",
            "Exported memory mirror from kernel state to {path} ({count} active records).",
            path=payload["export_path"] or "-",
            count=active_records,
        )
    )


# --------------- Plugin sub-commands ---------------


@plugin_app.command("list")
def plugin_list() -> None:
    """List discovered plugins (builtin + installed)."""
    settings = get_settings()
    _ensure_workspace(settings)

    pm = PluginManager(settings=settings)
    builtin_dir = Path(__file__).parent / "builtin"
    pm.discover_and_load(builtin_dir, settings.plugins_dir)

    if not pm.manifests:
        typer.echo(_t("cli.plugin.list.empty", "No plugins found."))
        return

    for m in pm.manifests:
        tag = "builtin" if m.builtin else "installed"
        typer.echo(f"  [{tag}] {m.name} v{m.version} — {m.description}")


@plugin_app.command("install")
def plugin_install(url: str) -> None:
    """Install a plugin from a git URL."""
    settings = get_settings()
    _ensure_workspace(settings)

    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    target = settings.plugins_dir / name
    if target.exists():
        typer.echo(
            _t(
                "cli.plugin.install.exists",
                "Plugin directory already exists: {path}",
                path=target,
            )
        )
        raise typer.Exit(1)

    typer.echo(_t("cli.plugin.install.cloning", "Cloning {url} -> {path}", url=url, path=target))
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo(
            _t(
                "cli.plugin.install.clone_failed",
                "git clone failed:\n{stderr}",
                stderr=result.stderr,
            )
        )
        raise typer.Exit(1)

    toml_path = target / "plugin.toml"
    if not toml_path.exists():
        typer.echo(
            _t(
                "cli.plugin.install.missing_manifest",
                "Warning: No plugin.toml found in {path}",
                path=target,
            )
        )

    typer.echo(_t("cli.plugin.install.done", "Installed plugin '{name}'.", name=name))


@plugin_app.command("remove")
def plugin_remove(name: str) -> None:
    """Remove an installed plugin."""
    settings = get_settings()
    _ensure_workspace(settings)

    target = settings.plugins_dir / name
    if not target.exists():
        typer.echo(_t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
        raise typer.Exit(1)

    shutil.rmtree(target)
    typer.echo(_t("cli.plugin.remove.done", "Removed plugin '{name}'.", name=name))


@plugin_app.command("info")
def plugin_info(name: str) -> None:
    """Show details about a plugin."""
    from hermit.plugin.loader import parse_manifest

    settings = get_settings()
    _ensure_workspace(settings)

    builtin_dir = Path(__file__).parent / "builtin"
    for search_dir in (builtin_dir, settings.plugins_dir):
        candidate = search_dir / name
        manifest = parse_manifest(candidate) if candidate.is_dir() else None
        if manifest is not None:
            typer.echo(f"Name:        {manifest.name}")
            typer.echo(f"Version:     {manifest.version}")
            typer.echo(f"Description: {manifest.description}")
            typer.echo(f"Author:      {manifest.author or '(none)'}")
            typer.echo(f"Builtin:     {manifest.builtin}")
            typer.echo(f"Location:    {candidate}")
            if manifest.entry:
                typer.echo(f"Entry:       {manifest.entry}")
            if manifest.dependencies:
                typer.echo(f"Deps:        {manifest.dependencies}")
            return

    typer.echo(_t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
    raise typer.Exit(1)


# --------------- Autostart sub-commands ---------------


@autostart_app.command("enable")
def autostart_enable(
    adapter: str = typer.Option(
        "feishu",
        help=_cli_t("cli.autostart.enable.adapter", "Adapter to run (e.g. feishu)."),
    ),
) -> None:
    """Install a per-adapter launchd LaunchAgent (macOS only).

    Multiple adapters each get their own LaunchAgent and do not conflict.
    """
    from hermit import autostart as _autostart

    typer.echo(_autostart.enable(adapter=adapter))


@autostart_app.command("disable")
def autostart_disable(
    adapter: str = typer.Option(
        "feishu",
        help=_cli_t("cli.autostart.disable.adapter", "Adapter whose agent to remove."),
    ),
) -> None:
    """Remove the launchd LaunchAgent for a specific adapter."""
    from hermit import autostart as _autostart

    typer.echo(_autostart.disable(adapter=adapter))


@autostart_app.command("status")
def autostart_status(
    adapter: Optional[str] = typer.Option(
        None,
        help=_cli_t("cli.autostart.status.adapter", "Show only this adapter; omit for all."),
    ),
) -> None:
    """Show auto-start state for one adapter or all configured agents."""
    from hermit import autostart as _autostart

    typer.echo(_autostart.status(adapter=adapter))


# --------------- Schedule sub-commands ---------------


def _get_schedule_store() -> KernelStore:
    return _get_kernel_store()


@schedule_app.command("list")
def schedule_list() -> None:
    """List all scheduled tasks."""
    import datetime

    store = _get_schedule_store()
    jobs = store.list_schedules()
    if not jobs:
        typer.echo(_t("cli.schedule.list.empty", "No scheduled tasks."))
        return

    def fmt(ts: float | None) -> str:
        if ts is None:
            return _t("cli.schedule.common.not_available", "N/A")
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    for j in jobs:
        status = (
            _t("cli.schedule.status.enabled", "enabled")
            if j.enabled
            else _t("cli.schedule.status.disabled", "disabled")
        )
        schedule_info = j.cron_expr or (
            _t("cli.schedule.list.once_at", "once at {time}", time=fmt(j.once_at))
            if j.once_at
            else _t(
                "cli.schedule.list.every_seconds",
                "every {seconds}s",
                seconds=j.interval_seconds,
            )
            if j.interval_seconds
            else _t("cli.schedule.list.unknown", "unknown")
        )
        typer.echo(
            f"  [{j.id}] {j.name} ({status})\n"
            f"    {_t('cli.schedule.list.schedule', 'Schedule')}: {schedule_info}\n"
            f"    {_t('cli.schedule.list.next_run', 'Next run')}: {fmt(j.next_run_at)}\n"
            f"    {_t('cli.schedule.list.last_run', 'Last run')}: {fmt(j.last_run_at)}"
        )


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Option(..., help=_cli_t("cli.schedule.add.name", "Task name.")),
    prompt: str = typer.Option(
        ...,
        help=_cli_t("cli.schedule.add.prompt", "Agent prompt to execute."),
    ),
    cron: Optional[str] = typer.Option(
        None,
        help=_cli_t("cli.schedule.add.cron", "Cron expression (e.g. '0 9 * * 1-5')."),
    ),
    once: Optional[str] = typer.Option(
        None,
        help=_cli_t(
            "cli.schedule.add.once",
            "One-time datetime (ISO format, e.g. '2026-03-15T14:00').",
        ),
    ),
    interval: Optional[int] = typer.Option(
        None,
        help=_cli_t("cli.schedule.add.interval", "Interval in seconds (minimum 60)."),
    ),
) -> None:
    """Add a new scheduled task."""
    import datetime as dt

    from hermit.builtin.scheduler.models import ScheduledJob

    if sum(x is not None for x in (cron, once, interval)) != 1:
        typer.echo(
            _t(
                "cli.schedule.add.error.schedule_choice",
                "Error: specify exactly one of --cron, --once, or --interval.",
            )
        )
        raise typer.Exit(1)

    schedule_type = "cron" if cron else "once" if once else "interval"
    once_at: float | None = None

    if cron:
        try:
            from croniter import croniter

            croniter(cron)
        except (ValueError, KeyError) as exc:
            typer.echo(
                _t(
                    "cli.schedule.add.error.invalid_cron",
                    "Error: invalid cron expression: {error}",
                    error=exc,
                )
            )
            raise typer.Exit(1)
    elif once:
        try:
            once_at = dt.datetime.fromisoformat(once).timestamp()
        except ValueError:
            typer.echo(
                _t(
                    "cli.schedule.add.error.invalid_datetime",
                    "Error: invalid datetime format. Use ISO format.",
                )
            )
            raise typer.Exit(1)
    elif interval is not None and interval < 60:
        typer.echo(
            _t(
                "cli.schedule.add.error.invalid_interval",
                "Error: interval must be >= 60 seconds.",
            )
        )
        raise typer.Exit(1)

    job = ScheduledJob.create(
        name=name,
        prompt=prompt,
        schedule_type=schedule_type,
        cron_expr=cron,
        once_at=once_at,
        interval_seconds=interval,
    )

    store = _get_schedule_store()
    store.create_schedule(job)
    typer.echo(
        _t(
            "cli.schedule.add.done",
            "Added task [{job_id}] '{name}' ({schedule_type}).",
            job_id=job.id,
            name=job.name,
            schedule_type=schedule_type,
        )
    )
    typer.echo(
        _t(
            "cli.schedule.add.followup",
            "Task is now stored in the kernel ledger and will be picked up by `hermit serve`.",
        )
    )


@schedule_app.command("remove")
def schedule_remove(
    job_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.schedule.common.job_id_remove", "Task ID to remove."),
    ),
) -> None:
    """Remove a scheduled task."""
    store = _get_schedule_store()
    if not store.delete_schedule(job_id):
        typer.echo(
            _t(
                "cli.schedule.common.job_not_found",
                "Error: no task with id '{job_id}' found.",
                job_id=job_id,
            )
        )
        raise typer.Exit(1)
    typer.echo(_t("cli.schedule.remove.done", "Removed task '{job_id}'.", job_id=job_id))


@schedule_app.command("enable")
def schedule_enable(
    job_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.schedule.common.job_id_enable", "Task ID to enable."),
    ),
) -> None:
    """Enable a scheduled task."""
    store = _get_schedule_store()
    if store.update_schedule(job_id, enabled=True):
        typer.echo(_t("cli.schedule.enable.done", "Enabled task '{job_id}'.", job_id=job_id))
        return
    typer.echo(
        _t(
            "cli.schedule.common.job_not_found",
            "Error: no task with id '{job_id}' found.",
            job_id=job_id,
        )
    )
    raise typer.Exit(1)


@schedule_app.command("disable")
def schedule_disable(
    job_id: str = typer.Argument(
        ...,
        help=_cli_t("cli.schedule.common.job_id_disable", "Task ID to disable."),
    ),
) -> None:
    """Disable a scheduled task."""
    store = _get_schedule_store()
    if store.update_schedule(job_id, enabled=False):
        typer.echo(_t("cli.schedule.disable.done", "Disabled task '{job_id}'.", job_id=job_id))
        return
    typer.echo(
        _t(
            "cli.schedule.common.job_not_found",
            "Error: no task with id '{job_id}' found.",
            job_id=job_id,
        )
    )
    raise typer.Exit(1)


@schedule_app.command("history")
def schedule_history(
    job_id: Optional[str] = typer.Option(
        None,
        help=_cli_t("cli.schedule.history.job_id", "Filter by task ID."),
    ),
    limit: int = typer.Option(
        10,
        help=_cli_t("cli.schedule.history.limit", "Number of records to show."),
    ),
) -> None:
    """Show execution history for scheduled tasks."""
    import datetime

    store = _get_schedule_store()
    records = [
        record.to_dict() for record in store.list_schedule_history(job_id=job_id, limit=limit)
    ]

    if not records:
        typer.echo(_t("cli.schedule.history.empty", "No execution history."))
        return

    for r in records:
        status = (
            _t("cli.schedule.history.status.ok", "OK")
            if r.get("success")
            else _t("cli.schedule.history.status.fail", "FAIL")
        )
        started = datetime.datetime.fromtimestamp(r.get("started_at", 0)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        duration = r.get("finished_at", 0) - r.get("started_at", 0)
        preview = (r.get("result_text", "") or "")[:100].replace("\n", " ")
        typer.echo(f"  [{status}] {r.get('job_name', '?')} @ {started} ({duration:.1f}s)")
        if preview:
            typer.echo(f"    {preview}")
        if r.get("error"):
            typer.echo(
                _t(
                    "cli.schedule.history.error",
                    "    Error: {error}",
                    error=r["error"],
                )
            )


if __name__ == "__main__":
    app()
