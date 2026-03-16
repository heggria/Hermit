from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PROFILE_FIELDS = {
    "provider",
    "model",
    "max_tokens",
    "max_turns",
    "tool_output_limit",
    "thinking_budget",
    "image_model",
    "image_context_limit",
    "claude_api_key",
    "claude_auth_token",
    "claude_base_url",
    "claude_headers",
    "openai_api_key",
    "openai_base_url",
    "openai_headers",
    "codex_command",
    "prevent_sleep",
    "log_level",
    "sandbox_mode",
    "command_timeout_seconds",
    "session_idle_timeout_seconds",
    "feishu_app_id",
    "feishu_app_secret",
    "feishu_thread_progress",
    "scheduler_enabled",
    "scheduler_catch_up",
    "scheduler_feishu_chat_id",
    "webhook_enabled",
    "webhook_host",
    "webhook_port",
}


@dataclass(frozen=True)
class ProfileCatalog:
    path: Path
    exists: bool
    default_profile: str | None
    disabled_builtin_plugins: list[str]
    profiles: dict[str, dict[str, Any]]
    plugins: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ResolvedProfile:
    name: str | None
    source_path: Path
    values: dict[str, Any]
    exists: bool


def config_path_for_base_dir(base_dir: Path) -> Path:
    return base_dir.expanduser() / "config.toml"


def load_profile_catalog(base_dir: Path) -> ProfileCatalog:
    path = config_path_for_base_dir(base_dir)
    if not path.exists():
        return ProfileCatalog(
            path=path,
            exists=False,
            default_profile=None,
            disabled_builtin_plugins=[],
            profiles={},
            plugins={},
        )

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    default_profile = raw.get("default_profile")
    disabled_builtin_plugins_raw = raw.get("disabled_builtin_plugins", [])
    profiles_raw = raw.get("profiles", {})
    plugins_raw = raw.get("plugins", {})

    disabled_builtin_plugins: list[str] = []
    if isinstance(disabled_builtin_plugins_raw, list):
        for item in cast(list[Any], disabled_builtin_plugins_raw):
            name_str = str(item).strip()
            if name_str:
                disabled_builtin_plugins.append(name_str)

    profiles: dict[str, dict[str, Any]] = {}
    if isinstance(profiles_raw, dict):
        for prof_name, profile in cast(dict[str, Any], profiles_raw).items():
            if not isinstance(profile, dict):
                continue
            typed_profile = cast(dict[str, Any], profile)
            filtered: dict[str, Any] = {
                key: value for key, value in typed_profile.items() if key in PROFILE_FIELDS
            }
            if filtered:
                profiles[str(prof_name)] = filtered
            else:
                profiles[str(prof_name)] = {}

    plugins: dict[str, dict[str, Any]] = {}
    if isinstance(plugins_raw, dict):
        for plug_name, plugin in cast(dict[str, Any], plugins_raw).items():
            if not isinstance(plugin, dict):
                continue
            typed_plugin = cast(dict[str, Any], plugin)
            variables = typed_plugin.get("variables", {})
            if isinstance(variables, dict):
                plugins[str(plug_name)] = dict(cast(dict[str, Any], variables))

    resolved_default = str(default_profile).strip() if default_profile is not None else None
    return ProfileCatalog(
        path=path,
        exists=True,
        default_profile=resolved_default or None,
        disabled_builtin_plugins=disabled_builtin_plugins,
        profiles=profiles,
        plugins=plugins,
    )


def resolve_profile(base_dir: Path, profile_name: str | None) -> ResolvedProfile:
    catalog = load_profile_catalog(base_dir)
    if profile_name is None:
        selected = catalog.default_profile
    else:
        selected = profile_name.strip() or None
    values = catalog.profiles.get(selected, {}) if selected else {}
    return ResolvedProfile(
        name=selected,
        source_path=catalog.path,
        values=dict(values),
        exists=catalog.exists and bool(selected in catalog.profiles if selected else True),
    )


def load_plugin_variables(base_dir: Path, plugin_name: str) -> dict[str, Any]:
    catalog = load_profile_catalog(base_dir)
    return dict(catalog.plugins.get(plugin_name, {}))
