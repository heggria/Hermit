from __future__ import annotations

import typer

from .main import autostart_app, cli_t


@autostart_app.command("enable")
def autostart_enable(
    adapter: str = typer.Option(
        "feishu",
        help=cli_t("cli.autostart.enable.adapter", "Adapter to run (e.g. feishu)."),
    ),
) -> None:
    """Install a per-adapter launchd LaunchAgent (macOS only).

    Multiple adapters each get their own LaunchAgent and do not conflict.
    """
    from hermit.surfaces.cli import autostart as _autostart

    typer.echo(_autostart.enable(adapter=adapter))


@autostart_app.command("disable")
def autostart_disable(
    adapter: str = typer.Option(
        "feishu",
        help=cli_t("cli.autostart.disable.adapter", "Adapter whose agent to remove."),
    ),
) -> None:
    """Remove the launchd LaunchAgent for a specific adapter."""
    from hermit.surfaces.cli import autostart as _autostart

    typer.echo(_autostart.disable(adapter=adapter))


@autostart_app.command("status")
def autostart_status(
    adapter: str | None = typer.Option(
        None,
        help=cli_t("cli.autostart.status.adapter", "Show only this adapter; omit for all."),
    ),
) -> None:
    """Show auto-start state for one adapter or all configured agents."""
    from hermit.surfaces.cli import autostart as _autostart

    typer.echo(_autostart.status(adapter=adapter))
