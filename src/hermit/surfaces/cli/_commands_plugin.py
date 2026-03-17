from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from hermit.runtime.assembly.config import get_settings
from hermit.runtime.capability.registry.manager import PluginManager

from ._helpers import ensure_workspace
from .main import plugin_app, t


@plugin_app.command("list")
def plugin_list() -> None:
    """List discovered plugins (builtin + installed)."""
    settings = get_settings()
    ensure_workspace(settings)

    pm = PluginManager(settings=settings)
    builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"
    pm.discover_and_load(builtin_dir, settings.plugins_dir)

    if not pm.manifests:
        typer.echo(t("cli.plugin.list.empty", "No plugins found."))
        return

    for m in pm.manifests:
        tag = "builtin" if m.builtin else "installed"
        typer.echo(f"  [{tag}] {m.name} v{m.version} — {m.description}")


@plugin_app.command("install")
def plugin_install(url: str) -> None:
    """Install a plugin from a git URL."""
    settings = get_settings()
    ensure_workspace(settings)

    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    target = settings.plugins_dir / name
    if target.exists():
        typer.echo(
            t(
                "cli.plugin.install.exists",
                "Plugin directory already exists: {path}",
                path=target,
            )
        )
        raise typer.Exit(1)

    typer.echo(t("cli.plugin.install.cloning", "Cloning {url} -> {path}", url=url, path=target))
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo(
            t(
                "cli.plugin.install.clone_failed",
                "git clone failed:\n{stderr}",
                stderr=result.stderr,
            )
        )
        raise typer.Exit(1)

    toml_path = target / "plugin.toml"
    if not toml_path.exists():
        typer.echo(
            t(
                "cli.plugin.install.missing_manifest",
                "Warning: No plugin.toml found in {path}",
                path=target,
            )
        )

    typer.echo(t("cli.plugin.install.done", "Installed plugin '{name}'.", name=name))


@plugin_app.command("remove")
def plugin_remove(name: str) -> None:
    """Remove an installed plugin."""
    settings = get_settings()
    ensure_workspace(settings)

    target = settings.plugins_dir / name
    if not target.exists():
        typer.echo(t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
        raise typer.Exit(1)

    shutil.rmtree(target)
    typer.echo(t("cli.plugin.remove.done", "Removed plugin '{name}'.", name=name))


@plugin_app.command("info")
def plugin_info(name: str) -> None:
    """Show details about a plugin."""
    from hermit.runtime.capability.loader.loader import parse_manifest

    settings = get_settings()
    ensure_workspace(settings)

    builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"
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

    typer.echo(t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
    raise typer.Exit(1)
