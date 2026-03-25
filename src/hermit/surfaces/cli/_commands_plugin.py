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
        tag = (
            t("cli.plugin.list.tag.builtin", "builtin")
            if m.builtin
            else t("cli.plugin.list.tag.installed", "installed")
        )
        typer.echo(
            t(
                "cli.plugin.list.item",
                "  [{tag}] {name} v{version} — {description}",
                tag=tag,
                name=m.name,
                version=m.version,
                description=m.description,
            )
        )


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

    # Validate plugin name: no empty, no path separators, no traversal
    stripped = name.strip()
    if (
        not stripped
        or len(stripped) > 255
        or "/" in stripped
        or "\\" in stripped
        or ".." in stripped
    ):
        typer.echo(t("cli.plugin.remove.invalid_name", "Invalid plugin name: {name}", name=name))
        raise typer.Exit(1)

    target = settings.plugins_dir / stripped
    # Ensure resolved path is within plugins_dir
    try:
        resolved = target.resolve()
        plugins_resolved = settings.plugins_dir.resolve()
        if (
            not str(resolved).startswith(str(plugins_resolved) + "/")
            or resolved == plugins_resolved
        ):
            typer.echo(
                t("cli.plugin.remove.invalid_name", "Invalid plugin name: {name}", name=name)
            )
            raise typer.Exit(1)
    except (OSError, ValueError):
        typer.echo(t("cli.plugin.remove.invalid_name", "Invalid plugin name: {name}", name=name))
        raise typer.Exit(1)

    if not target.exists():
        typer.echo(t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
        raise typer.Exit(1)

    shutil.rmtree(target)
    typer.echo(t("cli.plugin.remove.done", "Removed plugin '{name}'.", name=name))


def _print_plugin_info(candidate: Path, manifest: object) -> None:
    typer.echo(t("cli.plugin.info.name", "Name:        {value}", value=manifest.name))
    typer.echo(t("cli.plugin.info.version", "Version:     {value}", value=manifest.version))
    typer.echo(t("cli.plugin.info.description", "Description: {value}", value=manifest.description))
    typer.echo(
        t(
            "cli.plugin.info.author",
            "Author:      {value}",
            value=manifest.author or t("cli.plugin.info.author_none", "(none)"),
        )
    )
    typer.echo(t("cli.plugin.info.builtin", "Builtin:     {value}", value=manifest.builtin))
    typer.echo(t("cli.plugin.info.location", "Location:    {value}", value=candidate))
    if manifest.entry:
        typer.echo(t("cli.plugin.info.entry", "Entry:       {value}", value=manifest.entry))
    if manifest.dependencies:
        typer.echo(t("cli.plugin.info.deps", "Deps:        {value}", value=manifest.dependencies))


@plugin_app.command("info")
def plugin_info(name: str) -> None:
    """Show details about a plugin."""
    from hermit.runtime.capability.loader.loader import parse_manifest

    settings = get_settings()
    ensure_workspace(settings)

    builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"

    # Search installed plugins dir directly
    candidate = settings.plugins_dir / name
    manifest = parse_manifest(candidate) if candidate.is_dir() else None
    if manifest is not None:
        _print_plugin_info(candidate, manifest)
        return

    # Search builtin dir: first try direct path
    candidate = builtin_dir / name
    manifest = parse_manifest(candidate) if candidate.is_dir() else None
    if manifest is not None:
        _print_plugin_info(candidate, manifest)
        return

    # Search category subdirectories (adapters/, hooks/, tools/, mcp/, bundles/, subagents/)
    if builtin_dir.is_dir():
        for category_dir in sorted(builtin_dir.iterdir()):
            if not category_dir.is_dir():
                continue
            candidate = category_dir / name
            manifest = parse_manifest(candidate) if candidate.is_dir() else None
            if manifest is not None:
                _print_plugin_info(candidate, manifest)
                return

    typer.echo(t("cli.plugin.common.not_found", "Plugin not found: {name}", name=name))
    raise typer.Exit(1)
