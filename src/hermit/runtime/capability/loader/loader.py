from __future__ import annotations

import importlib
import importlib.util
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable, List, Optional, cast

import structlog

from hermit.infra.system.i18n import tr
from hermit.runtime.capability.contracts.base import (
    PluginContext,
    PluginManifest,
    PluginVariableSpec,
)
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.loader.config import resolve_plugin_context

log = structlog.get_logger()


def parse_manifest(plugin_dir: Path) -> Optional[PluginManifest]:
    toml_path = plugin_dir / "plugin.toml"
    if not toml_path.exists():
        return None

    with open(toml_path, "rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)

    plugin_sec = cast(dict[str, Any], data.get("plugin", {}))
    entry_sec = cast(dict[str, Any], data.get("entry", {}))
    config_sec = cast(dict[str, Any], data.get("config", {}))
    deps_sec = cast(dict[str, Any], data.get("dependencies", {}))
    variables_sec = cast(dict[str, Any], data.get("variables", {}))

    variables: dict[str, PluginVariableSpec] = {}
    for name, spec in variables_sec.items():
        if not isinstance(spec, dict):
            continue
        spec_d = cast(dict[str, Any], spec)
        env = spec_d.get("env", [])
        env_list = cast(list[Any], env) if isinstance(env, list) else []
        variables[str(name)] = PluginVariableSpec(
            name=str(name),
            setting=str(spec_d["setting"])
            if "setting" in spec_d and spec_d["setting"] is not None
            else None,
            env=[str(item) for item in env_list],
            default=spec_d.get("default"),
            required=bool(spec_d.get("required", False)),
            secret=bool(spec_d.get("secret", False)),
            description=tr(
                str(spec_d.get("description_key", "")),
                default=str(spec_d.get("description", "")),
            )
            if spec_d.get("description_key") or spec_d.get("description")
            else "",
        )

    requires_raw = deps_sec.get("requires", [])
    dependencies = (
        [str(x) for x in cast(list[Any], requires_raw)] if isinstance(requires_raw, list) else []
    )

    return PluginManifest(
        name=str(plugin_sec.get("name", plugin_dir.name)),
        version=str(plugin_sec.get("version", "0.0.0")),
        description=tr(
            str(plugin_sec.get("description_key", "")),
            default=str(plugin_sec.get("description", "")),
        )
        if plugin_sec.get("description_key") or plugin_sec.get("description")
        else "",
        author=str(plugin_sec.get("author", "")),
        builtin=bool(plugin_sec.get("builtin", False)),
        entry=cast(dict[str, str], dict(entry_sec)),
        config=dict(config_sec),
        variables=variables,
        dependencies=dependencies,
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
            else:
                # Recurse into category subdirectories (adapters/, hooks/, etc.)
                for grandchild in sorted(child.iterdir()):
                    if not grandchild.is_dir():
                        continue
                    sub_manifest = parse_manifest(grandchild)
                    if sub_manifest is not None:
                        manifests.append(sub_manifest)
    return manifests


def load_plugin_entries(
    manifest: PluginManifest,
    hooks_engine: HooksEngine,
    settings: Any = None,
) -> PluginContext:
    if manifest.plugin_dir is None:
        raise ValueError(f"Plugin '{manifest.name}' has no plugin_dir set")
    plugin_dir = Path(manifest.plugin_dir)
    ctx = PluginContext(hooks_engine, settings=settings)
    ctx.manifest = manifest
    ctx.plugin_vars, ctx.config = resolve_plugin_context(manifest, settings)

    for dimension, entry_spec in manifest.entry.items():
        if ":" not in entry_spec:
            log.warning(
                "invalid_entry_spec", plugin=manifest.name, dimension=dimension, spec=entry_spec
            )
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
            # Derive module path from plugin_dir relative to package root
            _plugin_path = Path(
                manifest.plugin_dir if manifest.plugin_dir is not None else plugin_dir
            )
            _pkg_root = Path(__file__).resolve().parents[4]  # src/hermit
            try:
                _rel = _plugin_path.resolve().relative_to(_pkg_root)
                full_module = "hermit." + ".".join(_rel.parts) + "." + module_name
            except ValueError:
                full_module = f"hermit.plugins.builtin.{_plugin_path.name}.{module_name}"
            mod = importlib.import_module(full_module)
        else:
            mod = _import_external_module(manifest.name, plugin_dir, module_name)

        fn: Callable[..., Any] = getattr(mod, func_name)
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
