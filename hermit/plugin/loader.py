from __future__ import annotations

import importlib
import importlib.util
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable, List, Optional

import structlog

from hermit.i18n import tr
from hermit.plugin.base import PluginContext, PluginManifest, PluginVariableSpec
from hermit.plugin.config import resolve_plugin_context
from hermit.plugin.hooks import HooksEngine

log = structlog.get_logger()


def parse_manifest(plugin_dir: Path) -> Optional[PluginManifest]:
    toml_path = plugin_dir / "plugin.toml"
    if not toml_path.exists():
        return None

    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    plugin_sec = data.get("plugin", {})
    entry_sec = data.get("entry", {})
    config_sec = data.get("config", {})
    deps_sec = data.get("dependencies", {})
    variables_sec = data.get("variables", {})

    variables: dict[str, PluginVariableSpec] = {}
    if isinstance(variables_sec, dict):
        for name, spec in variables_sec.items():
            if not isinstance(spec, dict):
                continue
            env = spec.get("env", [])
            variables[str(name)] = PluginVariableSpec(
                name=str(name),
                setting=str(spec["setting"]) if "setting" in spec and spec["setting"] is not None else None,
                env=[str(item) for item in env] if isinstance(env, list) else [],
                default=spec.get("default"),
                required=bool(spec.get("required", False)),
                secret=bool(spec.get("secret", False)),
                description=tr(
                    str(spec.get("description_key", "")),
                    default=str(spec.get("description", "")),
                ) if spec.get("description_key") or spec.get("description") else "",
            )

    return PluginManifest(
        name=str(plugin_sec.get("name", plugin_dir.name)),
        version=str(plugin_sec.get("version", "0.0.0")),
        description=tr(
            str(plugin_sec.get("description_key", "")),
            default=str(plugin_sec.get("description", "")),
        ) if plugin_sec.get("description_key") or plugin_sec.get("description") else "",
        author=str(plugin_sec.get("author", "")),
        builtin=bool(plugin_sec.get("builtin", False)),
        entry=dict(entry_sec),
        config=dict(config_sec),
        variables=variables,
        dependencies=list(deps_sec.get("requires", [])),
        plugin_dir=plugin_dir,
    )


def discover_plugins(*search_dirs: Path) -> List[PluginManifest]:
    manifests: List[PluginManifest] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = parse_manifest(child)
            if manifest is not None:
                manifests.append(manifest)
    return manifests


def load_plugin_entries(
    manifest: PluginManifest,
    hooks_engine: HooksEngine,
    settings: Any = None,
) -> PluginContext:
    plugin_dir = Path(manifest.plugin_dir)
    ctx = PluginContext(hooks_engine, settings=settings)
    ctx.manifest = manifest
    ctx.plugin_vars, ctx.config = resolve_plugin_context(manifest, settings)

    for dimension, entry_spec in manifest.entry.items():
        if ":" not in entry_spec:
            log.warning("invalid_entry_spec", plugin=manifest.name,
                        dimension=dimension, spec=entry_spec)
            continue
        _invoke_entry(manifest, plugin_dir, dimension, entry_spec, ctx)

    return ctx


def _invoke_entry(
    manifest: PluginManifest,
    plugin_dir: Path,
    dimension: str,
    entry_spec: str,
    ctx: PluginContext,
) -> None:
    module_name, func_name = entry_spec.split(":", 1)
    try:
        if manifest.builtin:
            dir_name = Path(manifest.plugin_dir).name
            full_module = f"hermit.builtin.{dir_name}.{module_name}"
            mod = importlib.import_module(full_module)
        else:
            mod = _import_external_module(manifest.name, plugin_dir, module_name)

        fn: Callable = getattr(mod, func_name)
        fn(ctx)
    except Exception:
        log.exception("plugin_entry_error", plugin=manifest.name, dimension=dimension)


def _import_external_module(plugin_name: str, plugin_dir: Path, module_name: str) -> Any:
    module_path = plugin_dir / f"{module_name}.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Plugin module not found: {module_path}")

    unique = f"_hermit_ext_{plugin_name}_{module_name}"
    spec = importlib.util.spec_from_file_location(unique, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {module_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    sys.path.insert(0, str(plugin_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        try:
            sys.path.remove(str(plugin_dir))
        except ValueError:
            pass
    return mod
