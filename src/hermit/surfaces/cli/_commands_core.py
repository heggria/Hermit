from __future__ import annotations

import json
from pathlib import Path

import typer

from hermit.kernel import KernelStore, TaskController
from hermit.runtime.assembly.config import Settings, get_settings
from hermit.runtime.assembly.context import build_base_context
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.control.runner.runner import AgentRunner
from hermit.runtime.observation.logging.setup import configure_logging
from hermit.runtime.provider_host.execution.services import build_runtime
from hermit.runtime.provider_host.shared.profiles import load_profile_catalog, resolve_profile

from ._helpers import (
    auth_status_summary,
    caffeinate,
    ensure_workspace,
    on_tool_call,
    print_result,
    require_auth,
    resolved_config_snapshot,
    stop_runner_background_services,
)
from .main import RESET, app, auth_app, config_app, profiles_app, t


@app.command()
def setup() -> None:
    """Interactive first-run wizard: configure API keys and initialize workspace."""
    GREEN = "\033[32m"
    BOLD = "\033[1m"

    typer.echo(f"\n{BOLD}{t('cli.setup.title', 'Hermit Setup')}{RESET}\n")

    settings = get_settings()
    env_path = settings.base_dir / ".env"
    if env_path.exists():
        overwrite = typer.confirm(
            t(
                "cli.setup.confirm_overwrite",
                "Config already exists at {path}. Overwrite?",
                path=env_path,
            ),
            default=False,
        )
        if not overwrite:
            typer.echo(t("cli.setup.cancelled", "Setup cancelled."))
            raise typer.Exit()

    lines: list[str] = []

    # --- API credentials ---
    typer.echo(t("cli.setup.step1", "Step 1/2  API credentials") + "\n")
    use_proxy = typer.confirm(
        t(
            "cli.setup.use_proxy",
            "Use Claude-compatible proxy/gateway instead of Anthropic API directly?",
        ),
        default=False,
    )
    if use_proxy:
        auth_token = typer.prompt(
            t(
                "cli.setup.prompt.auth_token",
                "  HERMIT_CLAUDE_AUTH_TOKEN (Bearer token)",
            ),
            hide_input=True,
        )
        base_url = typer.prompt(
            t(
                "cli.setup.prompt.base_url",
                "  HERMIT_CLAUDE_BASE_URL  (proxy endpoint URL)",
            )
        )
        custom_headers = typer.prompt(
            t(
                "cli.setup.prompt.custom_headers",
                "  HERMIT_CLAUDE_HEADERS (optional, e.g. 'X-Biz-Id: foo')",
            ),
            default="",
        )
        model = typer.prompt(
            t("cli.setup.prompt.model", "  HERMIT_MODEL"),
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
            t("cli.setup.prompt.anthropic_api_key", "  ANTHROPIC_API_KEY"),
            hide_input=True,
        )
        lines.append(f"ANTHROPIC_API_KEY={api_key}")

    # --- Feishu (optional) ---
    typer.echo("\n" + t("cli.setup.step2", "Step 2/2  Feishu bot adapter (optional)") + "\n")
    use_feishu = typer.confirm(t("cli.setup.use_feishu", "Configure Feishu bot?"), default=False)
    if use_feishu:
        app_id = typer.prompt(t("cli.setup.prompt.feishu_app_id", "  HERMIT_FEISHU_APP_ID"))
        app_secret = typer.prompt(
            t("cli.setup.prompt.feishu_app_secret", "  HERMIT_FEISHU_APP_SECRET"),
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
    ensure_workspace(settings)

    typer.echo(f"\n{GREEN}{t('cli.setup.done', 'Done!')}{RESET}")
    typer.echo(t("cli.setup.output.config", "  Config  -> {path}", path=env_path))
    typer.echo(
        t(
            "cli.setup.output.workspace",
            "  Workspace -> {path}",
            path=settings.base_dir,
        )
    )
    typer.echo("\n" + t("cli.setup.next_steps", "Next steps:"))
    typer.echo(t("cli.setup.next_step.chat", "  hermit chat"))
    if use_feishu:
        typer.echo(t("cli.setup.next_step.serve_feishu", "  hermit serve --adapter feishu"))
    typer.echo("")


@config_app.command("show")
def config_show() -> None:
    """Show the fully resolved runtime configuration."""
    get_settings.cache_clear()
    settings = get_settings()
    typer.echo(json.dumps(resolved_config_snapshot(settings), ensure_ascii=False, indent=2))


@profiles_app.command("list")
def profiles_list() -> None:
    """List configured provider profiles from ~/.hermit/config.toml."""
    settings = get_settings()
    catalog = load_profile_catalog(settings.base_dir)
    if not catalog.exists:
        typer.echo(
            t(
                "cli.profiles_list.no_config",
                "No config.toml found at {path}",
                path=catalog.path,
            )
        )
        raise typer.Exit()
    if not catalog.profiles:
        typer.echo(
            t(
                "cli.profiles_list.no_profiles",
                "No profiles defined in {path}",
                path=catalog.path,
            )
        )
        raise typer.Exit()

    for name, values in sorted(catalog.profiles.items()):
        marker = (
            t("cli.profiles_list.default_marker", " (default)")
            if name == catalog.default_profile
            else ""
        )
        provider = values.get("provider", "claude")
        model = values.get("model", "")
        suffix = t(
            "cli.profiles_list.item",
            " provider={provider}{model_suffix}",
            provider=provider,
            model_suffix=(
                t("cli.profiles_list.model_suffix", " model={model}", model=model) if model else ""
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
    payload = auth_status_summary(settings)
    payload["selected_profile"] = settings.resolved_profile
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def init(base_dir: Path | None = None) -> None:
    """Initialize the local Hermit workspace."""
    settings = get_settings()
    if base_dir is not None:
        settings.base_dir = base_dir
    ensure_workspace(settings)
    typer.echo(
        t(
            "cli.init.done",
            "Initialized Hermit workspace at {path}",
            path=settings.base_dir,
        )
    )


@app.command()
def startup_prompt() -> None:
    """Print the full startup system prompt."""
    settings = get_settings()
    ensure_workspace(settings)

    pm = PluginManager(settings=settings)
    builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"
    pm.discover_and_load(builtin_dir, settings.plugins_dir)

    base = build_base_context(settings, Path.cwd())
    typer.echo(pm.build_system_prompt(base))


def build_runner(
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
    ensure_workspace(settings)
    configure_logging(settings.log_level)
    require_auth(settings)

    runner, pm = build_runner(settings)
    with caffeinate(settings):
        try:
            result = runner.handle("cli-oneshot", prompt, on_tool_call=on_tool_call)
            runner.close_session("cli-oneshot")
            print_result(result)
        finally:
            stop_runner_background_services(runner)
            pm.stop_mcp_servers()


@app.command()
def chat(session_id: str = "cli", debug: bool = False, tui: bool = False) -> None:
    """Interactive multi-turn chat session."""
    settings = get_settings()
    ensure_workspace(settings)
    configure_logging("DEBUG" if debug else settings.log_level)
    require_auth(settings)

    runner, pm = build_runner(settings)

    if tui:
        from hermit.surfaces.cli.tui.app import HermitApp

        with caffeinate(settings):
            try:
                tui_app = HermitApp(runner=runner, pm=pm, session_id=session_id, settings=settings)
                tui_app.run()
            finally:
                stop_runner_background_services(runner)
                pm.stop_mcp_servers()
        return

    typer.echo(
        t(
            "cli.chat.banner",
            "Hermit chat (session={session_id}). Type /help for commands.",
            session_id=session_id,
        )
    )

    with caffeinate(settings):
        try:
            while True:
                try:
                    user_input = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    typer.echo("\n" + t("cli.chat.bye", "Bye."))
                    break

                if not user_input:
                    continue

                result = runner.dispatch(session_id, user_input, on_tool_call=on_tool_call)
                if result.is_command:
                    typer.echo(result.text)
                    if result.should_exit:
                        break
                elif result.agent_result:
                    print_result(result.agent_result)
        finally:
            # Always close the session so SESSION_END hook fires and memories are saved,
            # even if the user hits Ctrl+C during an LLM generation turn.
            runner.close_session(session_id)
            stop_runner_background_services(runner)
            pm.stop_mcp_servers()
