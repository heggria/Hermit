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

DIM = "\033[2m"
CYAN = "\033[36m"
RESET = "\033[0m"

# Import sub-modules to register commands (Flask blueprint pattern).
import hermit.surfaces.cli._commands_autostart as _commands_autostart  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._commands_core as _commands_core  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._commands_memory as _commands_memory  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._commands_plugin as _commands_plugin  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._commands_schedule as _commands_schedule  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._commands_task as _commands_task  # noqa: F401  # pyright: ignore[reportUnusedImport]
import hermit.surfaces.cli._serve as _serve  # noqa: F401  # pyright: ignore[reportUnusedImport]

if __name__ == "__main__":
    app()
