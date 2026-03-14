from __future__ import annotations

import json
from pathlib import Path

from jsonschema.validators import Draft202012Validator

from hermit.builtin.feishu.tools import _all_tools as all_feishu_tools
from hermit.config import Settings
from hermit.i18n import catalog_locales, load_catalog, localize_schema, normalize_locale, tr
from hermit.plugin.loader import parse_manifest


def test_normalize_locale_aliases() -> None:
    assert normalize_locale("zh_CN.UTF-8".split(".", 1)[0]) == "zh-CN"
    assert normalize_locale("en") == "en-US"


def test_settings_normalizes_locale(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh")

    settings = Settings()

    assert settings.locale == "zh-CN"


def test_translate_uses_locale_catalog() -> None:
    assert tr("cli.app.help", locale="zh-CN") == "Hermit 个人 AI Agent CLI。"
    assert tr("cli.app.help", locale="en-US") == "Hermit personal AI agent CLI."


def test_translate_falls_back_to_default_text() -> None:
    assert tr("missing.key", locale="zh-CN", default="fallback") == "fallback"


def test_catalog_locales_include_directory_and_file_locales() -> None:
    locales = catalog_locales()

    assert "en-US" in locales
    assert "zh-CN" in locales


def test_load_catalog_merges_single_file_and_directory(monkeypatch, tmp_path: Path) -> None:
    import hermit.i18n as i18n_mod

    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "en-US.json").write_text(
        json.dumps({"root.key": "root", "shared.key": "root-shared"}),
        encoding="utf-8",
    )
    (locales_dir / "en-US").mkdir()
    (locales_dir / "en-US" / "cli.json").write_text(
        json.dumps({"dir.key": "dir", "shared.key": "dir-shared"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(i18n_mod, "_catalog_dir", lambda: locales_dir)
    i18n_mod._load_catalog.cache_clear()

    assert load_catalog("en-US", include_default=False) == {
        "root.key": "root",
        "shared.key": "dir-shared",
        "dir.key": "dir",
    }


def test_parse_manifest_uses_description_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    plugin_dir = tmp_path / "usage"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(
        """
[plugin]
name = "usage"
description_key = "plugin.usage.description"
description = "Show token usage statistics for the current session"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = parse_manifest(plugin_dir)

    assert manifest is not None
    assert manifest.description == "显示当前会话的 token 消耗统计"


def test_localize_schema_preserves_property_named_title_or_description() -> None:
    schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Document title.",
            },
            "description": {
                "type": "string",
                "description": "Optional event description.",
            },
        },
        "required": ["title"],
    }

    localized = localize_schema(schema, locale="en-US")

    assert localized["properties"]["title"] == {
        "type": "string",
        "description": "Document title.",
    }
    assert localized["properties"]["description"] == {
        "type": "string",
        "description": "Optional event description.",
    }


def test_feishu_tool_input_schemas_are_valid_json_schema() -> None:
    for tool in all_feishu_tools():
        Draft202012Validator.check_schema(tool.input_schema)
