from __future__ import annotations

import os
import re
from typing import Any, cast

import structlog

from hermit.runtime.capability.contracts.base import PluginManifest
from hermit.runtime.provider_host.shared.profiles import load_plugin_variables

log = structlog.get_logger()

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def resolve_plugin_context(
    manifest: PluginManifest, settings: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    plugin_vars = _resolve_plugin_variables(manifest, settings)
    config = _resolve_templates(manifest.config, plugin_vars)
    return plugin_vars, config


def _resolve_plugin_variables(manifest: PluginManifest, settings: Any) -> dict[str, Any]:
    base_dir = getattr(settings, "base_dir", None)
    configured = load_plugin_variables(base_dir, manifest.name) if base_dir else {}
    resolved: dict[str, Any] = {}

    for name, spec in manifest.variables.items():
        value = configured.get(name)
        if value in (None, "") and spec.setting:
            value = getattr(settings, spec.setting, None)
        if value in (None, ""):
            for env_key in spec.env:
                env_value = os.environ.get(env_key)
                if env_value not in (None, ""):
                    value = env_value
                    break
        if value in (None, "") and spec.default is not None:
            value = spec.default
        if value in (None, "") and spec.required:
            log.error(
                "plugin_variable_required_missing",
                plugin=manifest.name,
                variable=name,
                env_vars=spec.env,
            )
        resolved[name] = value
    return resolved


def has_missing_required_variables(
    manifest: PluginManifest, resolved: dict[str, Any]
) -> list[str]:
    """Return names of required variables that are still None or empty.

    Callers (e.g. loader) can use this to decide whether to skip entry
    registration for a plugin whose required configuration is incomplete.
    """
    missing: list[str] = []
    for name, spec in manifest.variables.items():
        if spec.required and resolved.get(name) in (None, ""):
            missing.append(name)
    return missing


def _resolve_templates(value: Any, plugin_vars: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        value_dict = cast(dict[str, Any], value)
        resolved: dict[str, Any] = {}
        for key, inner in value_dict.items():
            rendered = _resolve_templates(inner, plugin_vars)
            if rendered is None:
                continue
            resolved[key] = rendered
        return resolved
    if isinstance(value, list):
        value_list = cast(list[Any], value)
        items: list[Any] = []
        for inner in value_list:
            rendered = _resolve_templates(inner, plugin_vars)
            if rendered is not None:
                items.append(rendered)
        return items
    if not isinstance(value, str):
        return value

    full = _TEMPLATE_RE.fullmatch(value)
    if full:
        return plugin_vars.get(full.group(1))

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        raw = plugin_vars.get(var_name)
        return "" if raw is None else str(raw)

    rendered = _TEMPLATE_RE.sub(_replace, value)
    return rendered
