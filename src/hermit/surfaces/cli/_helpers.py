from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.assembly.config import Settings, get_settings
from hermit.runtime.assembly.context import ensure_default_context_file
from hermit.runtime.provider_host.execution.runtime import AgentResult

DIM = "\033[2m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    try:
        locale = resolve_locale(get_settings().locale)
    except Exception:
        locale = resolve_locale()
    return tr(message_key, locale=locale, default=default, **kwargs)


def _tool_result_preview(result: object, limit: int = 200) -> str:
    text = result if isinstance(result, str) else str(result)
    preview = text[:limit].replace("\n", " ")
    if len(text) > limit:
        preview += "..."
    return preview


def on_tool_call(name: str, inputs: dict[str, Any], result: object) -> None:
    compact_input = ", ".join(f"{k}={repr(v)[:60]}" for k, v in inputs.items())
    preview = _tool_result_preview(result)
    typer.echo(f"{CYAN}  ▸ {name}({compact_input}){RESET}")
    typer.echo(f"{DIM}    → {preview}{RESET}")


def print_result(result: AgentResult) -> None:
    if result.thinking:
        typer.echo(f"\n{DIM}{_t('cli.chat.thinking.header', '── thinking ──')}{RESET}")
        for line in result.thinking.splitlines():
            typer.echo(f"{DIM}{line}{RESET}")
        typer.echo(f"{DIM}{_t('cli.chat.thinking.footer', '── /thinking ──')}{RESET}")
    typer.echo(f"\n{result.text}")


class _StreamPrinter:  # pyright: ignore[reportUnusedClass]
    """Adapter that prints streaming tokens to stdout."""

    def __init__(self) -> None:
        self._in_thinking = False

    def on_token(self, kind: str, text: str) -> None:
        if kind == "thinking":
            if not self._in_thinking:
                sys.stdout.write(f"\n{DIM}── thinking ──{RESET}\n")
                self._in_thinking = True
            sys.stdout.write(f"{DIM}{text}{RESET}")
        else:
            if self._in_thinking:
                sys.stdout.write(f"\n{DIM}── /thinking ──{RESET}\n\n")
                self._in_thinking = False
            sys.stdout.write(text)
        sys.stdout.flush()

    def finish(self) -> None:
        if self._in_thinking:
            sys.stdout.write(f"\n{DIM}── /thinking ──{RESET}\n")
            self._in_thinking = False
        sys.stdout.write("\n")
        sys.stdout.flush()


def stop_runner_background_services(runner: Any) -> None:
    stopper = getattr(runner, "stop_background_services", None)
    if callable(stopper):
        stopper()


def auth_status_summary(settings: Settings) -> dict[str, str | bool | None]:
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


def resolved_config_snapshot(settings: Settings) -> dict[str, object]:
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
        "auth": auth_status_summary(settings),
    }


def ensure_workspace(settings: Settings) -> None:
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
    # Ensure workspace-local tmp directory exists so agents never need /tmp/
    workspace_tmp = Path.cwd().resolve() / ".hermit" / "tmp"
    try:
        workspace_tmp.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        logging.getLogger(__name__).warning(
            "Cannot create workspace tmp directory %s — permission denied, skipping",
            workspace_tmp,
        )
    try:
        ensure_default_context_file(settings.context_file, locale=getattr(settings, "locale", None))
    except TypeError:
        ensure_default_context_file(settings.context_file)
    if not settings.memory_file.exists():
        from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine

        MemoryEngine(settings.memory_file).save({})


@contextlib.contextmanager
def caffeinate(settings: Settings):
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


def require_auth(settings: Settings) -> None:
    if not settings.has_auth:
        if settings.provider == "codex":
            if settings.codex_auth_file_exists:
                auth_mode = settings.codex_auth_mode or "unknown"
                raise typer.BadParameter(
                    _t(
                        "cli.auth_error.codex.local_login_missing_api_key",
                        (
                            "Codex provider now uses the OpenAI Responses API, but the local "
                            "~/.codex/auth.json login (auth_mode={auth_mode}) does not expose "
                            "an OpenAI API key. "
                            "ChatGPT/Codex desktop login alone cannot call /v1/responses. "
                            "Set HERMIT_OPENAI_API_KEY / OPENAI_API_KEY, or switch your local "
                            "Codex auth to an API-key-backed login."
                        ),
                        auth_mode=auth_mode,
                    )
                )
            raise typer.BadParameter(
                _t(
                    "cli.auth_error.codex.requires_api_key",
                    "Codex provider now uses the OpenAI Responses API and requires "
                    "HERMIT_OPENAI_API_KEY / OPENAI_API_KEY.",
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


def get_kernel_store():
    from hermit.kernel import KernelStore

    settings = get_settings()
    ensure_workspace(settings)
    return KernelStore(settings.kernel_db_path)


def format_epoch(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
