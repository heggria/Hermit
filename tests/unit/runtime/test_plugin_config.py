"""Tests for src/hermit/runtime/capability/loader/config.py"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hermit.runtime.capability.contracts.base import PluginManifest, PluginVariableSpec
from hermit.runtime.capability.loader.config import (
    _resolve_plugin_variables,
    _resolve_templates,
    resolve_plugin_context,
)


def _manifest(
    name: str = "test-plugin",
    variables: dict | None = None,
    config: dict | None = None,
) -> PluginManifest:
    vars_ = {}
    if variables:
        for k, v in variables.items():
            if isinstance(v, PluginVariableSpec):
                vars_[k] = v
            else:
                vars_[k] = PluginVariableSpec(name=k, **v)
    return PluginManifest(
        name=name,
        variables=vars_,
        config=config or {},
    )


# ---------------------------------------------------------------------------
# _resolve_templates
# ---------------------------------------------------------------------------


class TestResolveTemplates:
    def test_plain_string_unchanged(self) -> None:
        assert _resolve_templates("hello", {}) == "hello"

    def test_full_template_returns_raw_value(self) -> None:
        result = _resolve_templates("{{ api_key }}", {"api_key": "sk-123"})
        assert result == "sk-123"

    def test_full_template_missing_returns_none(self) -> None:
        result = _resolve_templates("{{ missing }}", {})
        assert result is None

    def test_partial_template_substituted(self) -> None:
        result = _resolve_templates(
            "https://{{ host }}:{{ port }}/api",
            {"host": "localhost", "port": 8080},
        )
        assert result == "https://localhost:8080/api"

    def test_partial_template_missing_var_becomes_empty(self) -> None:
        result = _resolve_templates("prefix-{{ missing }}-suffix", {})
        assert result == "prefix--suffix"

    def test_dict_resolved_recursively(self) -> None:
        value = {"url": "https://{{ host }}", "key": "{{ api_key }}"}
        result = _resolve_templates(value, {"host": "example.com", "api_key": "secret"})
        assert result == {"url": "https://example.com", "key": "secret"}

    def test_dict_none_values_dropped(self) -> None:
        value = {"present": "yes", "absent": "{{ missing }}"}
        result = _resolve_templates(value, {})
        assert result == {"present": "yes"}

    def test_list_resolved_recursively(self) -> None:
        value = ["{{ a }}", "static", "{{ b }}"]
        result = _resolve_templates(value, {"a": "one", "b": "two"})
        assert result == ["one", "static", "two"]

    def test_list_none_values_dropped(self) -> None:
        value = ["{{ present }}", "{{ missing }}"]
        result = _resolve_templates(value, {"present": "yes"})
        assert result == ["yes"]

    def test_non_string_passthrough(self) -> None:
        assert _resolve_templates(42, {}) == 42
        assert _resolve_templates(True, {}) is True
        assert _resolve_templates(None, {}) is None

    def test_nested_dict_and_list(self) -> None:
        value = {
            "servers": [
                {"host": "{{ host }}", "port": 8080},
            ]
        }
        result = _resolve_templates(value, {"host": "prod.example.com"})
        assert result == {"servers": [{"host": "prod.example.com", "port": 8080}]}


# ---------------------------------------------------------------------------
# _resolve_plugin_variables
# ---------------------------------------------------------------------------


class TestResolvePluginVariables:
    def test_uses_configured_value(self) -> None:
        manifest = _manifest(
            variables={
                "api_key": {"default": None, "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={"api_key": "configured-value"},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["api_key"] == "configured-value"

    def test_falls_back_to_setting_attr(self) -> None:
        manifest = _manifest(
            variables={
                "provider": {"setting": "provider", "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit", provider="claude")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["provider"] == "claude"

    def test_falls_back_to_env_var(self, monkeypatch) -> None:
        manifest = _manifest(
            variables={
                "token": {"env": ["MY_TOKEN", "FALLBACK_TOKEN"], "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        monkeypatch.setenv("MY_TOKEN", "env-token")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["token"] == "env-token"

    def test_falls_back_to_second_env_var(self, monkeypatch) -> None:
        manifest = _manifest(
            variables={
                "token": {"env": ["MISSING_TOKEN", "FALLBACK_TOKEN"], "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        monkeypatch.delenv("MISSING_TOKEN", raising=False)
        monkeypatch.setenv("FALLBACK_TOKEN", "fallback-value")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["token"] == "fallback-value"

    def test_uses_default_value(self) -> None:
        manifest = _manifest(
            variables={
                "mode": {"default": "fast", "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["mode"] == "fast"

    def test_required_missing_logs_warning(self) -> None:
        manifest = _manifest(
            variables={
                "critical": {"required": True},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["critical"] is None

    def test_no_base_dir_skips_configured(self) -> None:
        manifest = _manifest(
            variables={
                "key": {"default": "fallback", "required": False},
            }
        )
        settings = SimpleNamespace()  # no base_dir attr
        result = _resolve_plugin_variables(manifest, settings)
        assert result["key"] == "fallback"

    def test_empty_string_treated_as_missing(self, monkeypatch) -> None:
        manifest = _manifest(
            variables={
                "key": {"default": "default_val", "required": False},
            }
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={"key": ""},
        ):
            result = _resolve_plugin_variables(manifest, settings)
        assert result["key"] == "default_val"


# ---------------------------------------------------------------------------
# resolve_plugin_context
# ---------------------------------------------------------------------------


class TestResolvePluginContext:
    def test_returns_variables_and_config(self) -> None:
        manifest = _manifest(
            variables={"host": {"default": "localhost", "required": False}},
            config={"url": "https://{{ host }}/api"},
        )
        settings = SimpleNamespace(base_dir="/tmp/hermit")
        with patch(
            "hermit.runtime.capability.loader.config.load_plugin_variables",
            return_value={},
        ):
            plugin_vars, config = resolve_plugin_context(manifest, settings)
        assert plugin_vars["host"] == "localhost"
        assert config["url"] == "https://localhost/api"
