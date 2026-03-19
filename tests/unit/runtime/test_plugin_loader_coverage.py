"""Tests for runtime/capability/loader/loader.py — coverage for missed lines.

Covers: parse_manifest edge cases, discover_plugins recursive scanning,
load_plugin_entries error paths, _invoke_entry builtin vs external,
_import_external_module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hermit.runtime.capability.contracts.base import PluginManifest
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.loader.loader import (
    _import_external_module,
    discover_plugins,
    load_plugin_entries,
    parse_manifest,
)

# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_no_toml_returns_none(self, tmp_path: Path) -> None:
        assert parse_manifest(tmp_path) is None

    def test_minimal_toml(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "test"\nversion = "1.0"\n')
        m = parse_manifest(tmp_path)
        assert m is not None
        assert m.name == "test"
        assert m.version == "1.0"
        assert m.dependencies == []
        assert m.variables == {}

    def test_toml_with_variables(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text(
            '[plugin]\nname = "v"\nversion = "0.1"\n'
            "[variables.api_key]\n"
            'setting = "api_key"\n'
            'env = ["API_KEY"]\n'
            "required = true\n"
            "secret = true\n"
            'description = "API key for service"\n'
        )
        m = parse_manifest(tmp_path)
        assert m is not None
        assert "api_key" in m.variables
        v = m.variables["api_key"]
        assert v.required is True
        assert v.secret is True
        assert v.env == ["API_KEY"]

    def test_toml_with_entry_points(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text(
            '[plugin]\nname = "e"\n[entry]\ntools = "tools:register"\nhooks = "hooks:register"\n'
        )
        m = parse_manifest(tmp_path)
        assert m is not None
        assert m.entry["tools"] == "tools:register"
        assert m.entry["hooks"] == "hooks:register"

    def test_toml_with_dependencies(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "d"\n[dependencies]\nrequires = ["dep1", "dep2"]\n')
        m = parse_manifest(tmp_path)
        assert m is not None
        assert m.dependencies == ["dep1", "dep2"]

    def test_variable_non_dict_ignored(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "x"\n[variables]\nbad_var = "not_a_dict"\n')
        m = parse_manifest(tmp_path)
        assert m is not None
        assert "bad_var" not in m.variables

    def test_description_key_used_for_variable(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "dk"\n[variables.x]\ndescription_key = "some.key"\n')
        m = parse_manifest(tmp_path)
        assert m is not None
        # description_key triggers tr() call; value depends on locale
        assert "x" in m.variables

    def test_description_key_used_for_plugin(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "dk"\ndescription_key = "some.plugin.key"\n')
        m = parse_manifest(tmp_path)
        assert m is not None

    def test_no_plugin_section_uses_dir_name(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()
        toml = plugin_dir / "plugin.toml"
        toml.write_text("[plugin]\n")
        m = parse_manifest(plugin_dir)
        assert m is not None
        assert m.name == "my_plugin"

    def test_dependencies_non_list_ignored(self, tmp_path: Path) -> None:
        toml = tmp_path / "plugin.toml"
        toml.write_text('[plugin]\nname = "nl"\n[dependencies]\nrequires = "not-a-list"\n')
        m = parse_manifest(tmp_path)
        assert m is not None
        assert m.dependencies == []


# ---------------------------------------------------------------------------
# discover_plugins
# ---------------------------------------------------------------------------


class TestDiscoverPlugins:
    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        result = discover_plugins(tmp_path / "nonexistent")
        assert result == []

    def test_discovers_direct_plugins(self, tmp_path: Path) -> None:
        p = tmp_path / "myplugin"
        p.mkdir()
        (p / "plugin.toml").write_text('[plugin]\nname = "myplugin"\n')
        result = discover_plugins(tmp_path)
        assert len(result) == 1
        assert result[0].name == "myplugin"

    def test_discovers_nested_category_plugins(self, tmp_path: Path) -> None:
        cat = tmp_path / "hooks"
        cat.mkdir()
        p = cat / "webhook"
        p.mkdir()
        (p / "plugin.toml").write_text('[plugin]\nname = "webhook"\n')
        result = discover_plugins(tmp_path)
        assert len(result) == 1
        assert result[0].name == "webhook"

    def test_skips_files_at_top_level(self, tmp_path: Path) -> None:
        (tmp_path / "readme.txt").write_text("hi")
        result = discover_plugins(tmp_path)
        assert result == []

    def test_multiple_search_dirs(self, tmp_path: Path) -> None:
        d1 = tmp_path / "dir1"
        d1.mkdir()
        p1 = d1 / "p1"
        p1.mkdir()
        (p1 / "plugin.toml").write_text('[plugin]\nname = "p1"\n')

        d2 = tmp_path / "dir2"
        d2.mkdir()
        p2 = d2 / "p2"
        p2.mkdir()
        (p2 / "plugin.toml").write_text('[plugin]\nname = "p2"\n')

        result = discover_plugins(d1, d2)
        names = {m.name for m in result}
        assert names == {"p1", "p2"}


# ---------------------------------------------------------------------------
# load_plugin_entries
# ---------------------------------------------------------------------------


class TestLoadPluginEntries:
    def test_no_plugin_dir_raises(self) -> None:
        manifest = PluginManifest(
            name="test",
            version="0.1",
            entry={"tools": "tools:register"},
            plugin_dir=None,
        )
        with pytest.raises(ValueError, match="no plugin_dir"):
            load_plugin_entries(manifest, HooksEngine())

    def test_invalid_entry_spec_logged(self, tmp_path: Path) -> None:
        manifest = PluginManifest(
            name="test",
            version="0.1",
            entry={"tools": "no_colon_here"},
            plugin_dir=tmp_path,
        )
        # Should not raise, just log warning
        ctx = load_plugin_entries(manifest, HooksEngine())
        assert ctx is not None

    def test_successful_entry_invocation(self, tmp_path: Path) -> None:
        # Create a simple plugin module
        mod_file = tmp_path / "tools.py"
        mod_file.write_text("def register(ctx):\n    ctx._test_marker = True\n")
        manifest = PluginManifest(
            name="test",
            version="0.1",
            entry={"tools": "tools:register"},
            plugin_dir=tmp_path,
            builtin=False,
        )
        ctx = load_plugin_entries(manifest, HooksEngine())
        assert getattr(ctx, "_test_marker", False) is True


# ---------------------------------------------------------------------------
# _import_external_module
# ---------------------------------------------------------------------------


class TestImportExternalModule:
    def test_missing_module_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _import_external_module("test", tmp_path, "nonexistent")

    def test_loads_external_module(self, tmp_path: Path) -> None:
        mod_file = tmp_path / "mymod.py"
        mod_file.write_text("VALUE = 42\n")
        mod = _import_external_module("test", tmp_path, "mymod")
        assert mod.VALUE == 42

    def test_module_registered_in_sys(self, tmp_path: Path) -> None:
        mod_file = tmp_path / "mymod2.py"
        mod_file.write_text("X = 1\n")
        _import_external_module("test2", tmp_path, "mymod2")
        assert "_hermit_ext_test2_mymod2" in sys.modules

    def test_sys_path_cleaned_up(self, tmp_path: Path) -> None:
        mod_file = tmp_path / "mymod3.py"
        mod_file.write_text("Y = 2\n")
        dir_str = str(tmp_path)
        # Ensure not in path before
        while dir_str in sys.path:
            sys.path.remove(dir_str)
        _import_external_module("test3", tmp_path, "mymod3")
        assert dir_str not in sys.path
