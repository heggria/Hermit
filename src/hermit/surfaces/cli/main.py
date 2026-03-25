from __future__ import annotations

import os
from pathlib import Path

import typer

from hermit.runtime.assembly.config import get_settings


def hermit_env_path() -> Path:
    base_dir = os.environ.get("HERMIT_BASE_DIR")
    if base_dir:
        return Path(base_dir).expanduser() / ".env"
    return Path.home() / ".hermit" / ".env"


def _load_hermit_env() -> None:
    """Load ~/.hermit/.env into os.environ before Settings is instantiated.

    Existing env vars take precedence (they are not overwritten), so shell-level
    exports always win over the file.
    """
    env_path = hermit_env_path()
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

from hermit.infra.system.i18n import resolve_locale, tr

CLI_LOCALE = resolve_locale()


def _current_locale() -> str:
    try:
        return resolve_locale(get_settings().locale)
    except Exception:
        return resolve_locale()


def cli_t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=CLI_LOCALE, default=default, **kwargs)


def t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=_current_locale(), default=default, **kwargs)


app = typer.Typer(help=cli_t("cli.app.help"))
plugin_app = typer.Typer(help=cli_t("cli.plugin.help"))
autostart_app = typer.Typer(help=cli_t("cli.autostart.help"))
schedule_app = typer.Typer(help=cli_t("cli.schedule.help"))
config_app = typer.Typer(help=cli_t("cli.config.help"))
profiles_app = typer.Typer(help=cli_t("cli.profiles.help"))
auth_app = typer.Typer(help=cli_t("cli.auth.help"))
task_app = typer.Typer(
    help=cli_t(
        "cli.task.help",
        "Task kernel inspection and approval commands.",
    )
)
task_capability_app = typer.Typer(
    help=cli_t(
        "cli.task_capability.help",
        "Capability grant inspection and revocation commands.",
    )
)
memory_app = typer.Typer(
    help=cli_t(
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

RESET = "\033[0m"

# ---------------------------------------------------------------------------
# Deferred command registration — import command modules lazily so that
# ``from hermit.surfaces.cli.main import app`` does not pull in the entire
# runtime dependency graph.  The actual imports run once, when this module
# is first imported (which only happens when the CLI is actually invoked).
#
# NOTE: We previously deferred this to ``@app.callback``, but Typer eagerly
# builds the entire Click Group hierarchy (including sub-apps like
# ``task_app``) *before* invoking the root callback, so sub-commands were
# missing.  The fix is to register commands at module-import time — the
# cost is paid only when ``hermit.surfaces.cli.main`` is imported, not at
# ``import hermit``.
# ---------------------------------------------------------------------------

_commands_registered = False


def _register_commands() -> None:
    """Import sub-modules that use Typer decorators to register commands."""
    global _commands_registered
    if _commands_registered:
        return
    _commands_registered = True
    import hermit.surfaces.cli._commands_autostart
    import hermit.surfaces.cli._commands_core
    import hermit.surfaces.cli._commands_memory
    import hermit.surfaces.cli._commands_overnight
    import hermit.surfaces.cli._commands_plugin
    import hermit.surfaces.cli._commands_schedule
    import hermit.surfaces.cli._commands_task
    import hermit.surfaces.cli._serve  # noqa: F401


def _subapp_callback(sub_app: typer.Typer) -> None:
    @sub_app.callback(invoke_without_command=True)
    def _callback(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())
            raise typer.Exit()


for _sub in (
    plugin_app,
    autostart_app,
    schedule_app,
    config_app,
    profiles_app,
    auth_app,
    task_app,
    memory_app,
    task_capability_app,
):
    _subapp_callback(_sub)

_register_commands()


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"hermit {version('hermit-agent')}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Handle the no-subcommand case (show help)."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":
    app()
