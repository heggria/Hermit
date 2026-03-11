from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer

from hermit.config import Settings, get_settings


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

from hermit.context import build_base_context, ensure_default_context_file, load_context_text
from hermit.core.agent import AgentResult, ClaudeAgent
from hermit.core.runner import AgentRunner
from hermit.core.sandbox import CommandSandbox
from hermit.core.session import SessionManager
from hermit.core.tools import create_builtin_tool_registry
from hermit.logging import configure_logging
from hermit.plugin.manager import PluginManager

app = typer.Typer(help="Hermit personal AI agent CLI.")
plugin_app = typer.Typer(help="Manage plugins.")
autostart_app = typer.Typer(help="Manage auto-start at login (macOS launchd).")
schedule_app = typer.Typer(help="Manage scheduled tasks.")
app.add_typer(plugin_app, name="plugin")
app.add_typer(autostart_app, name="autostart")
app.add_typer(schedule_app, name="schedule")

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
    kwargs = {}
    if settings.anthropic_api_key:
        kwargs["api_key"] = settings.anthropic_api_key
    if settings.auth_token:
        kwargs["auth_token"] = settings.auth_token
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.parsed_custom_headers:
        kwargs["default_headers"] = settings.parsed_custom_headers
    return kwargs


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
    ):
        directory.mkdir(parents=True, exist_ok=True)
    ensure_default_context_file(settings.context_file)
    if not settings.memory_file.exists():
        from hermit.builtin.memory.engine import MemoryEngine
        MemoryEngine(settings.memory_file).save({})


def _build_agent(
    settings: Settings,
    preloaded_skills: list[str] | None = None,
    pm: PluginManager | None = None,
    serve_mode: bool = False,
) -> tuple[ClaudeAgent, PluginManager]:
    from anthropic import Anthropic

    if pm is None:
        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).parent / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)

    sandbox = CommandSandbox(
        mode=settings.sandbox_mode,
        timeout_seconds=settings.command_timeout_seconds,
        cwd=Path.cwd(),
    )
    registry = create_builtin_tool_registry(
        Path.cwd(), sandbox, config_root_dir=settings.base_dir,
    )
    pm.setup_tools(registry)
    pm.start_mcp_servers(registry)

    base_prompt = build_base_context(settings, Path.cwd())

    # Combine core commands + plugin commands for system prompt injection
    visible_commands: list[tuple[str, str]] = [
        (cmd, help_text)
        for cmd, (_fn, help_text, cli_only) in sorted(AgentRunner._core_commands.items())
        if not (serve_mode and cli_only)
    ]
    for spec in pm._all_commands:
        if not (serve_mode and spec.cli_only):
            visible_commands.append((spec.name, spec.help_text))
    visible_commands.sort()
    if visible_commands:
        cmd_lines = ["<available_commands>"]
        cmd_lines.append("以下斜杠命令由系统层处理（不经过 LLM），用户可直接输入使用。当用户询问有哪些命令时，请告知：")
        for cmd, help_text in visible_commands:
            cmd_lines.append(f"- `{cmd}` — {help_text}")
        cmd_lines.append("</available_commands>")
        base_prompt = base_prompt + "\n\n" + "\n".join(cmd_lines)

    system_prompt = pm.build_system_prompt(base_prompt, preloaded_skills=preloaded_skills)

    client = Anthropic(**_build_anthropic_client_kwargs(settings))
    agent = ClaudeAgent(
        client=client,
        registry=registry,
        model=settings.model,
        max_tokens=settings.effective_max_tokens(),
        max_turns=settings.max_turns,
        tool_output_limit=settings.tool_output_limit,
        thinking_budget=settings.thinking_budget,
        system_prompt=system_prompt,
    )
    pm.configure_subagent_runner(
        client=client,
        model=settings.model,
        max_tokens=settings.effective_max_tokens(),
        tool_output_limit=settings.tool_output_limit,
        on_tool_call=None,
    )
    return agent, pm


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
        raise typer.BadParameter(
            "Missing authentication. Set HERMIT_AUTH_TOKEN, HERMIT_ANTHROPIC_API_KEY, or ANTHROPIC_API_KEY."
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
        return "~/.hermit/.env"
    return "shell env"


def _format_preflight_item(item: _PreflightItem) -> str:
    prefix = "[OK]" if item.ok else "[MISSING]"
    return f"  {prefix} {item.label}: {item.detail}"


def _build_serve_preflight(adapter: str, settings: Settings) -> tuple[list[_PreflightItem], list[str]]:
    env_path = settings.base_dir / ".env"
    env_file_keys = _read_env_file_keys()
    items: list[_PreflightItem] = [
        _PreflightItem(
            label="配置文件",
            ok=env_path.exists(),
            detail=f"{env_path} ({'已找到' if env_path.exists() else '未找到，将只读取当前 shell 环境变量'})",
        )
    ]
    errors: list[str] = []

    auth_key = _resolve_env_key(
        "HERMIT_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "HERMIT_AUTH_TOKEN",
    )
    if auth_key:
        detail = f"{auth_key} ({_describe_env_source(auth_key, env_file_keys)})"
        if auth_key == "HERMIT_AUTH_TOKEN":
            base_url_key = _resolve_env_key("HERMIT_BASE_URL")
            if base_url_key:
                detail += f", HERMIT_BASE_URL ({_describe_env_source(base_url_key, env_file_keys)})"
            else:
                detail += ", 未设置 HERMIT_BASE_URL"
        items.append(_PreflightItem(label="LLM 鉴权", ok=True, detail=detail))
    else:
        errors.append(
            "缺少 LLM 鉴权。请设置 `ANTHROPIC_API_KEY` / `HERMIT_ANTHROPIC_API_KEY`，"
            "或设置 `HERMIT_AUTH_TOKEN`（通常还需要 `HERMIT_BASE_URL`）。"
        )
        items.append(
            _PreflightItem(
                label="LLM 鉴权",
                ok=False,
                detail="未找到 ANTHROPIC_API_KEY / HERMIT_ANTHROPIC_API_KEY / HERMIT_AUTH_TOKEN",
            )
        )

    model_key = _resolve_env_key("HERMIT_MODEL")
    items.append(
        _PreflightItem(
            label="模型",
            ok=True,
            detail=(
                f"{settings.model} ({_describe_env_source(model_key, env_file_keys)})"
                if model_key
                else f"{settings.model} (默认值)"
            ),
        )
    )

    if adapter == "feishu":
        app_id_key = _resolve_env_key("HERMIT_FEISHU_APP_ID", "FEISHU_APP_ID")
        app_secret_key = _resolve_env_key("HERMIT_FEISHU_APP_SECRET", "FEISHU_APP_SECRET")
        if app_id_key:
            items.append(
                _PreflightItem(
                    label="飞书 App ID",
                    ok=True,
                    detail=f"{app_id_key} ({_describe_env_source(app_id_key, env_file_keys)})",
                )
            )
        else:
            errors.append(
                "缺少飞书 App ID。请设置 `HERMIT_FEISHU_APP_ID` "
                "（兼容旧变量名 `FEISHU_APP_ID`）。"
            )
            items.append(
                _PreflightItem(
                    label="飞书 App ID",
                    ok=False,
                    detail="未找到 HERMIT_FEISHU_APP_ID / FEISHU_APP_ID",
                )
            )

        if app_secret_key:
            items.append(
                _PreflightItem(
                    label="飞书 App Secret",
                    ok=True,
                    detail=f"{app_secret_key} ({_describe_env_source(app_secret_key, env_file_keys)})",
                )
            )
        else:
            errors.append(
                "缺少飞书 App Secret。请设置 `HERMIT_FEISHU_APP_SECRET` "
                "（兼容旧变量名 `FEISHU_APP_SECRET`）。"
            )
            items.append(
                _PreflightItem(
                    label="飞书 App Secret",
                    ok=False,
                    detail="未找到 HERMIT_FEISHU_APP_SECRET / FEISHU_APP_SECRET",
                )
            )

        thread_progress_key = _resolve_env_key("HERMIT_FEISHU_THREAD_PROGRESS")
        items.append(
            _PreflightItem(
                label="飞书进度卡片",
                ok=True,
                detail=(
                    f"{os.environ.get(thread_progress_key, '')} ({_describe_env_source(thread_progress_key, env_file_keys)})"
                    if thread_progress_key
                    else "true (默认值)"
                ),
            )
        )

        scheduler_chat_key = _resolve_env_key("HERMIT_SCHEDULER_FEISHU_CHAT_ID")
        items.append(
            _PreflightItem(
                label="Scheduler 飞书通知",
                ok=True,
                detail=(
                    f"{scheduler_chat_key} ({_describe_env_source(scheduler_chat_key, env_file_keys)})"
                    if scheduler_chat_key
                    else "未设置（可选；reload/scheduler 不会主动发飞书通知）"
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
        typer.echo("启动前检查未通过：")
        for message in errors:
            typer.echo(f"  - {message}")
        typer.echo("")
        raise typer.Exit(1)


@app.command()
def setup() -> None:
    """Interactive first-run wizard: configure API keys and initialize workspace."""
    GREEN = "\033[32m"
    BOLD = "\033[1m"

    typer.echo(f"\n{BOLD}Hermit Setup{RESET}\n")

    settings = get_settings()
    env_path = settings.base_dir / ".env"
    if env_path.exists():
        overwrite = typer.confirm(
            f"Config already exists at {env_path}. Overwrite?", default=False
        )
        if not overwrite:
            typer.echo("Setup cancelled.")
            raise typer.Exit()

    lines: list[str] = []

    # --- API credentials ---
    typer.echo("Step 1/2  API credentials\n")
    use_proxy = typer.confirm(
        "Use a proxy/gateway instead of Anthropic API directly?", default=False
    )
    if use_proxy:
        auth_token = typer.prompt("  HERMIT_AUTH_TOKEN (Bearer token)", hide_input=True)
        base_url = typer.prompt("  HERMIT_BASE_URL  (proxy endpoint URL)")
        custom_headers = typer.prompt(
            "  HERMIT_CUSTOM_HEADERS (optional, e.g. 'X-Biz-Id: foo')", default=""
        )
        model = typer.prompt("  HERMIT_MODEL", default="claude-3-7-sonnet-latest")
        lines += [
            f"HERMIT_AUTH_TOKEN={auth_token}",
            f"HERMIT_BASE_URL={base_url}",
        ]
        if custom_headers:
            lines.append(f"HERMIT_CUSTOM_HEADERS={custom_headers}")
        lines.append(f"HERMIT_MODEL={model}")
    else:
        api_key = typer.prompt("  ANTHROPIC_API_KEY", hide_input=True)
        lines.append(f"ANTHROPIC_API_KEY={api_key}")

    # --- Feishu (optional) ---
    typer.echo("\nStep 2/2  Feishu bot adapter (optional)\n")
    use_feishu = typer.confirm("Configure Feishu bot?", default=False)
    if use_feishu:
        app_id = typer.prompt("  HERMIT_FEISHU_APP_ID")
        app_secret = typer.prompt("  HERMIT_FEISHU_APP_SECRET", hide_input=True)
        lines += [
            f"HERMIT_FEISHU_APP_ID={app_id}",
            f"HERMIT_FEISHU_APP_SECRET={app_secret}",
        ]

    # --- Write .env ---
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Reload env so get_settings() picks up the new values this session.
    _load_hermit_env()
    get_settings.cache_clear()

    settings = get_settings()
    _ensure_workspace(settings)

    typer.echo(f"\n{GREEN}Done!{RESET}")
    typer.echo(f"  Config  → {env_path}")
    typer.echo(f"  Workspace → {settings.base_dir}")
    typer.echo("\nNext steps:")
    typer.echo("  hermit chat")
    if use_feishu:
        typer.echo("  hermit serve --adapter feishu")
    typer.echo("")


@app.command()
def init(base_dir: Optional[Path] = None) -> None:
    """Initialize the local Hermit workspace."""
    settings = get_settings()
    if base_dir is not None:
        settings.base_dir = base_dir
    _ensure_workspace(settings)
    typer.echo(f"Initialized Hermit workspace at {settings.base_dir}")


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
    agent, pm = _build_agent(settings, preloaded_skills=preloaded_skills, pm=pm, serve_mode=serve_mode)
    manager = SessionManager(settings.sessions_dir, settings.session_idle_timeout_seconds)
    runner = AgentRunner(agent, manager, pm, serve_mode=serve_mode)
    pm.setup_commands(runner)
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
            pm.stop_mcp_servers()


@app.command()
def chat(session_id: str = "cli", debug: bool = False) -> None:
    """Interactive multi-turn chat session."""
    settings = get_settings()
    _ensure_workspace(settings)
    configure_logging("DEBUG" if debug else settings.log_level)
    _require_auth(settings)

    runner, pm = _build_runner(settings)
    typer.echo(f"Hermit chat (session={session_id}). Type /help for commands.")

    with _caffeinate(settings):
        try:
            while True:
                try:
                    user_input = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    typer.echo("\nBye.")
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
            pm.stop_mcp_servers()


_serve_log = logging.getLogger("hermit.serve")


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


async def _serve_with_reload(
    adapter_instance: Any,
    runner: Any,
) -> bool:
    """Run the adapter until it exits or SIGHUP is received.

    Returns True if a reload was requested (SIGHUP), False otherwise.
    """
    loop = asyncio.get_running_loop()
    reload_event = asyncio.Event()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGHUP, reload_event.set)

    start_task = asyncio.ensure_future(adapter_instance.start(runner))
    reload_task = asyncio.ensure_future(reload_event.wait())

    done, pending = await asyncio.wait(
        {start_task, reload_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    if reload_event.is_set():
        _serve_log.info("SIGHUP received — stopping adapter for reload...")
        await adapter_instance.stop()
        return True

    if start_task in done:
        exc = start_task.exception()
        if exc is not None:
            raise exc

    return False


def _notify_reload(settings: Any, adapter: str) -> None:
    """Fire a DISPATCH_RESULT so the Feishu hook sends a reload notification."""
    from hermit.plugin.base import HookEvent

    chat_id = os.environ.get("HERMIT_SCHEDULER_FEISHU_CHAT_ID", "")
    if not chat_id:
        return
    try:
        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).parent / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)
        pm.hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="system",
            title="Hermit Reloaded",
            result_text=(
                f"Hermit (`{adapter}`) 已成功重新加载。\n\n"
                "配置、插件、工具已全部重建。"
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
    from hermit.plugin.base import HookEvent

    settings = get_settings()
    _ensure_workspace(settings)
    configure_logging(settings.log_level)
    _run_serve_preflight(adapter, settings)

    pid_file = _pid_path(settings, adapter)
    _write_pid(pid_file)

    try:
        _serve_loop(adapter, pid_file)
    finally:
        _remove_pid(pid_file)


def _serve_loop(adapter: str, pid_file: Path) -> None:
    """Inner restart loop — each iteration rebuilds everything from scratch."""
    from hermit.plugin.base import HookEvent

    while True:
        get_settings.cache_clear()
        settings = get_settings()
        configure_logging(settings.log_level)

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

        typer.echo(f"Starting Hermit with '{adapter}' adapter...")

        reload_requested = False
        with _caffeinate(settings):
            try:
                reload_requested = asyncio.run(
                    _serve_with_reload(adapter_instance, runner)
                )
            except KeyboardInterrupt:
                typer.echo("\nShutting down...")
                asyncio.run(adapter_instance.stop())
            finally:
                pm.hooks.fire(HookEvent.SERVE_STOP)
                pm.stop_mcp_servers()

        if reload_requested:
            _serve_log.info("Reloading Hermit...")
            typer.echo("Reloading Hermit — rebuilding config, plugins, tools...")
            _write_pid(pid_file)
            _notify_reload(settings, adapter)
            continue

        break


@app.command()
def reload(adapter: str = "feishu") -> None:
    """Send SIGHUP to a running ``hermit serve`` process to trigger a graceful reload.

    The serve process re-reads configuration, rediscovers plugins, rebuilds
    the tool registry and system prompt, and restarts the adapter — all without
    losing the PID.
    """
    if sys.platform == "win32":
        typer.echo("Reload via signal is not supported on Windows.")
        raise typer.Exit(1)

    settings = get_settings()
    pid_file = _pid_path(settings, adapter)
    pid = _read_pid(pid_file)

    if pid is None:
        typer.echo(
            f"No running serve process found for adapter '{adapter}'.\n"
            f"  PID file: {pid_file}"
        )
        raise typer.Exit(1)

    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        typer.echo(f"Process {pid} not found (stale PID file). Cleaning up.")
        _remove_pid(pid_file)
        raise typer.Exit(1)
    except PermissionError:
        typer.echo(f"Permission denied sending SIGHUP to PID {pid}.")
        raise typer.Exit(1)

    typer.echo(f"Sent SIGHUP to Hermit serve (PID {pid}, adapter='{adapter}').")
    typer.echo("The service will reload configuration, plugins, and tools.")


@app.command()
def sessions() -> None:
    """List known sessions."""
    settings = get_settings()
    _ensure_workspace(settings)
    manager = SessionManager(settings.sessions_dir, settings.session_idle_timeout_seconds)
    for sid in manager.list_sessions():
        typer.echo(sid)


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
        typer.echo("No plugins found.")
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
        typer.echo(f"Plugin directory already exists: {target}")
        raise typer.Exit(1)

    typer.echo(f"Cloning {url} → {target}")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        typer.echo(f"git clone failed:\n{result.stderr}")
        raise typer.Exit(1)

    toml_path = target / "plugin.toml"
    if not toml_path.exists():
        typer.echo(f"Warning: No plugin.toml found in {target}")

    typer.echo(f"Installed plugin '{name}'.")


@plugin_app.command("remove")
def plugin_remove(name: str) -> None:
    """Remove an installed plugin."""
    settings = get_settings()
    _ensure_workspace(settings)

    target = settings.plugins_dir / name
    if not target.exists():
        typer.echo(f"Plugin not found: {name}")
        raise typer.Exit(1)

    shutil.rmtree(target)
    typer.echo(f"Removed plugin '{name}'.")


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

    typer.echo(f"Plugin not found: {name}")
    raise typer.Exit(1)


# --------------- Autostart sub-commands ---------------

@autostart_app.command("enable")
def autostart_enable(
    adapter: str = typer.Option("feishu", help="Adapter to run (e.g. feishu)."),
) -> None:
    """Install a per-adapter launchd LaunchAgent (macOS only).

    Multiple adapters each get their own LaunchAgent and do not conflict.
    """
    from hermit import autostart as _autostart
    typer.echo(_autostart.enable(adapter=adapter))


@autostart_app.command("disable")
def autostart_disable(
    adapter: str = typer.Option("feishu", help="Adapter whose agent to remove."),
) -> None:
    """Remove the launchd LaunchAgent for a specific adapter."""
    from hermit import autostart as _autostart
    typer.echo(_autostart.disable(adapter=adapter))


@autostart_app.command("status")
def autostart_status(
    adapter: Optional[str] = typer.Option(None, help="Show only this adapter; omit for all."),
) -> None:
    """Show auto-start state for one adapter or all configured agents."""
    from hermit import autostart as _autostart
    typer.echo(_autostart.status(adapter=adapter))


# --------------- Schedule sub-commands ---------------

def _get_schedule_store() -> Any:
    """Return a JsonStore for reading schedules outside of serve context."""
    from hermit.storage import JsonStore
    settings = get_settings()
    schedules_dir = settings.schedules_dir
    schedules_dir.mkdir(parents=True, exist_ok=True)
    return JsonStore(
        schedules_dir / "jobs.json",
        default={"jobs": []},
        cross_process=True,
    )


@schedule_app.command("list")
def schedule_list() -> None:
    """List all scheduled tasks."""
    from hermit.builtin.scheduler.models import ScheduledJob
    import datetime

    store = _get_schedule_store()
    data = store.read()
    jobs = [ScheduledJob.from_dict(j) for j in data.get("jobs", [])]
    if not jobs:
        typer.echo("No scheduled tasks.")
        return

    def fmt(ts: float | None) -> str:
        if ts is None:
            return "N/A"
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    for j in jobs:
        status = "enabled" if j.enabled else "disabled"
        schedule_info = j.cron_expr or (
            f"once at {fmt(j.once_at)}" if j.once_at
            else f"every {j.interval_seconds}s" if j.interval_seconds
            else "unknown"
        )
        typer.echo(
            f"  [{j.id}] {j.name} ({status})\n"
            f"    Schedule: {schedule_info}\n"
            f"    Next run: {fmt(j.next_run_at)}\n"
            f"    Last run: {fmt(j.last_run_at)}"
        )


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Option(..., help="Task name."),
    prompt: str = typer.Option(..., help="Agent prompt to execute."),
    cron: Optional[str] = typer.Option(None, help="Cron expression (e.g. '0 9 * * 1-5')."),
    once: Optional[str] = typer.Option(None, help="One-time datetime (ISO format, e.g. '2026-03-15T14:00')."),
    interval: Optional[int] = typer.Option(None, help="Interval in seconds (minimum 60)."),
) -> None:
    """Add a new scheduled task."""
    import datetime as dt
    from hermit.builtin.scheduler.models import ScheduledJob

    if sum(x is not None for x in (cron, once, interval)) != 1:
        typer.echo("Error: specify exactly one of --cron, --once, or --interval.")
        raise typer.Exit(1)

    schedule_type = "cron" if cron else "once" if once else "interval"
    once_at: float | None = None

    if cron:
        try:
            from croniter import croniter
            croniter(cron)
        except (ValueError, KeyError) as exc:
            typer.echo(f"Error: invalid cron expression: {exc}")
            raise typer.Exit(1)
    elif once:
        try:
            once_at = dt.datetime.fromisoformat(once).timestamp()
        except ValueError:
            typer.echo("Error: invalid datetime format. Use ISO format.")
            raise typer.Exit(1)
    elif interval is not None and interval < 60:
        typer.echo("Error: interval must be >= 60 seconds.")
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
    data = store.read()
    data.setdefault("jobs", []).append(job.to_dict())
    store.write(data)
    typer.echo(f"Added task [{job.id}] '{job.name}' ({schedule_type}).")
    typer.echo("Note: the schedule will be active next time `hermit serve` starts.")


@schedule_app.command("remove")
def schedule_remove(job_id: str = typer.Argument(..., help="Task ID to remove.")) -> None:
    """Remove a scheduled task."""
    store = _get_schedule_store()
    data = store.read()
    jobs = data.get("jobs", [])
    before = len(jobs)
    data["jobs"] = [j for j in jobs if j.get("id") != job_id]
    if len(data["jobs"]) == before:
        typer.echo(f"Error: no task with id '{job_id}' found.")
        raise typer.Exit(1)
    store.write(data)
    typer.echo(f"Removed task '{job_id}'.")


@schedule_app.command("enable")
def schedule_enable(job_id: str = typer.Argument(..., help="Task ID to enable.")) -> None:
    """Enable a scheduled task."""
    store = _get_schedule_store()
    data = store.read()
    for j in data.get("jobs", []):
        if j.get("id") == job_id:
            j["enabled"] = True
            store.write(data)
            typer.echo(f"Enabled task '{job_id}'.")
            return
    typer.echo(f"Error: no task with id '{job_id}' found.")
    raise typer.Exit(1)


@schedule_app.command("disable")
def schedule_disable(job_id: str = typer.Argument(..., help="Task ID to disable.")) -> None:
    """Disable a scheduled task."""
    store = _get_schedule_store()
    data = store.read()
    for j in data.get("jobs", []):
        if j.get("id") == job_id:
            j["enabled"] = False
            store.write(data)
            typer.echo(f"Disabled task '{job_id}'.")
            return
    typer.echo(f"Error: no task with id '{job_id}' found.")
    raise typer.Exit(1)


@schedule_app.command("history")
def schedule_history(
    job_id: Optional[str] = typer.Option(None, help="Filter by task ID."),
    limit: int = typer.Option(10, help="Number of records to show."),
) -> None:
    """Show execution history for scheduled tasks."""
    import datetime
    import json

    settings = get_settings()
    history_path = settings.schedules_dir / "history.json"
    if not history_path.exists():
        typer.echo("No execution history.")
        return

    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        typer.echo("No execution history.")
        return

    records = data.get("records", [])
    if job_id:
        records = [r for r in records if r.get("job_id") == job_id]
    records = records[-limit:]

    if not records:
        typer.echo("No execution history.")
        return

    for r in records:
        status = "OK" if r.get("success") else "FAIL"
        started = datetime.datetime.fromtimestamp(r.get("started_at", 0)).strftime("%Y-%m-%d %H:%M:%S")
        duration = r.get("finished_at", 0) - r.get("started_at", 0)
        preview = (r.get("result_text", "") or "")[:100].replace("\n", " ")
        typer.echo(f"  [{status}] {r.get('job_name', '?')} @ {started} ({duration:.1f}s)")
        if preview:
            typer.echo(f"    {preview}")
        if r.get("error"):
            typer.echo(f"    Error: {r['error']}")


if __name__ == "__main__":
    app()
