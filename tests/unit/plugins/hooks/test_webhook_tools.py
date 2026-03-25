"""Tests for webhook tools CRUD operations (webhook_list, webhook_add, webhook_delete, webhook_update)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hermit.plugins.builtin.hooks.webhook import tools as webhook_tools
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def _setup(tmp_path: Path) -> None:
    """Reset module-level state and point to tmp_path."""
    webhook_tools._settings = SimpleNamespace(base_dir=str(tmp_path), locale="en-US")


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "webhooks.json"


def _write_config(tmp_path: Path, data: dict) -> None:
    path = _config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── set_settings ──


def test_set_settings_updates_global() -> None:
    original = webhook_tools._settings
    try:
        sentinel = SimpleNamespace(base_dir="/tmp/test")
        webhook_tools.set_settings(sentinel)
        assert webhook_tools._settings is sentinel
    finally:
        webhook_tools._settings = original


# ── _config_path ──


def test_config_path_with_base_dir(tmp_path: Path) -> None:
    _setup(tmp_path)
    assert webhook_tools._config_path() == tmp_path / "webhooks.json"


def test_config_path_without_base_dir() -> None:
    original = webhook_tools._settings
    try:
        webhook_tools._settings = SimpleNamespace(base_dir=None)
        result = webhook_tools._config_path()
        assert result == Path.home() / ".hermit" / "webhooks.json"
    finally:
        webhook_tools._settings = original


# ── _load_raw ──


def test_load_raw_no_file(tmp_path: Path) -> None:
    _setup(tmp_path)
    data = webhook_tools._load_raw()
    assert data == {"host": "0.0.0.0", "port": 8321, "routes": {}}


def test_load_raw_valid_file(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(tmp_path, {"host": "127.0.0.1", "port": 9000, "routes": {"r1": {}}})
    data = webhook_tools._load_raw()
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 9000
    assert "r1" in data["routes"]


def test_load_raw_corrupt_file(tmp_path: Path) -> None:
    _setup(tmp_path)
    _config_path(tmp_path).write_text("not json", encoding="utf-8")
    data = webhook_tools._load_raw()
    assert data == {"host": "0.0.0.0", "port": 8321, "routes": {}}


# ── _save_raw ──


def test_save_raw_creates_file(tmp_path: Path) -> None:
    _setup(tmp_path)
    webhook_tools._save_raw({"host": "0.0.0.0", "port": 8321, "routes": {}})
    assert _config_path(tmp_path).exists()
    loaded = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert loaded["port"] == 8321


# ── _handle_list ──


def test_handle_list_empty_routes(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_list({})
    # Should mention config path or empty message
    assert isinstance(result, str)
    assert len(result) > 0


def test_handle_list_with_routes(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {
            "host": "0.0.0.0",
            "port": 8321,
            "routes": {
                "github": {
                    "path": "/webhook/github",
                    "prompt_template": "Handle: {message}",
                    "secret": "s3cret",
                    "signature_header": "X-Hub-Signature-256",
                    "notify": {"feishu_chat_id": "chat123"},
                },
                "simple": {
                    "path": "/webhook/simple",
                    "prompt_template": "Process: {data}",
                },
            },
        },
    )
    result = webhook_tools._handle_list({})
    assert "github" in result
    assert "simple" in result


def test_handle_list_route_without_secret(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {
            "host": "0.0.0.0",
            "port": 8321,
            "routes": {
                "nosecret": {
                    "path": "/webhook/nosecret",
                    "prompt_template": "test",
                },
            },
        },
    )
    result = webhook_tools._handle_list({})
    assert "nosecret" in result


# ── _handle_add ──


def test_handle_add_missing_name(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_add({"prompt_template": "test"})
    assert isinstance(result, str)


def test_handle_add_missing_prompt_template(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_add({"name": "test"})
    assert isinstance(result, str)


def test_handle_add_success(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_add({"name": "myroute", "prompt_template": "Handle: {msg}"})
    assert isinstance(result, str)
    # Verify it was persisted
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert "myroute" in data["routes"]
    assert data["routes"]["myroute"]["prompt_template"] == "Handle: {msg}"


def test_handle_add_with_secret_and_feishu(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_add(
        {
            "name": "secured",
            "prompt_template": "Process: {data}",
            "secret": "mysecret",
            "signature_header": "X-Sig",
            "feishu_chat_id": "chat456",
            "path": "/custom/path",
        }
    )
    assert isinstance(result, str)
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    route = data["routes"]["secured"]
    assert route["secret"] == "mysecret"
    assert route["signature_header"] == "X-Sig"
    assert route["notify"]["feishu_chat_id"] == "chat456"
    assert route["path"] == "/custom/path"


def test_handle_add_duplicate_without_overwrite(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"existing": {"path": "/webhook/existing", "prompt_template": "old"}}},
    )
    result = webhook_tools._handle_add({"name": "existing", "prompt_template": "new"})
    # Should report the route already exists
    assert isinstance(result, str)
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    # Old route should be unchanged
    assert data["routes"]["existing"]["prompt_template"] == "old"


def test_handle_add_duplicate_with_overwrite(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"existing": {"path": "/webhook/existing", "prompt_template": "old"}}},
    )
    result = webhook_tools._handle_add(
        {"name": "existing", "prompt_template": "new", "overwrite": True}
    )
    assert isinstance(result, str)
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["existing"]["prompt_template"] == "new"


# ── _handle_delete ──


def test_handle_delete_missing_name(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_delete({})
    assert isinstance(result, str)


def test_handle_delete_not_found(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(tmp_path, {"routes": {}})
    result = webhook_tools._handle_delete({"name": "nonexistent"})
    assert isinstance(result, str)


def test_handle_delete_success(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"todelete": {"path": "/webhook/todelete", "prompt_template": "bye"}}},
    )
    result = webhook_tools._handle_delete({"name": "todelete"})
    assert isinstance(result, str)
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert "todelete" not in data["routes"]


# ── _handle_update ──


def test_handle_update_missing_name(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = webhook_tools._handle_update({})
    assert isinstance(result, str)


def test_handle_update_not_found(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(tmp_path, {"routes": {}})
    result = webhook_tools._handle_update({"name": "nonexistent"})
    assert isinstance(result, str)


def test_handle_update_no_fields(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"r1": {"path": "/webhook/r1", "prompt_template": "orig"}}},
    )
    result = webhook_tools._handle_update({"name": "r1"})
    assert isinstance(result, str)


def test_handle_update_prompt_template(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"r1": {"path": "/webhook/r1", "prompt_template": "orig"}}},
    )
    result = webhook_tools._handle_update({"name": "r1", "prompt_template": "updated"})
    assert isinstance(result, str)
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["r1"]["prompt_template"] == "updated"


def test_handle_update_path(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"r1": {"path": "/webhook/r1", "prompt_template": "orig"}}},
    )
    webhook_tools._handle_update({"name": "r1", "path": "/new/path"})
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["r1"]["path"] == "/new/path"


def test_handle_update_secret_add(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"r1": {"path": "/webhook/r1", "prompt_template": "t"}}},
    )
    webhook_tools._handle_update({"name": "r1", "secret": "newsecret"})
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["r1"]["secret"] == "newsecret"
    assert "signature_header" in data["routes"]["r1"]


def test_handle_update_secret_remove(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {
            "routes": {
                "r1": {
                    "path": "/webhook/r1",
                    "prompt_template": "t",
                    "secret": "old",
                    "signature_header": "X-Sig",
                }
            }
        },
    )
    webhook_tools._handle_update({"name": "r1", "secret": ""})
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert "secret" not in data["routes"]["r1"]
    assert "signature_header" not in data["routes"]["r1"]


def test_handle_update_feishu_add_and_remove(tmp_path: Path) -> None:
    _setup(tmp_path)
    _write_config(
        tmp_path,
        {"routes": {"r1": {"path": "/webhook/r1", "prompt_template": "t"}}},
    )
    webhook_tools._handle_update({"name": "r1", "feishu_chat_id": "chat789"})
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["r1"]["notify"]["feishu_chat_id"] == "chat789"

    # Remove feishu
    webhook_tools._handle_update({"name": "r1", "feishu_chat_id": ""})
    data = json.loads(_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["routes"]["r1"].get("notify", {}).get("feishu_chat_id") is None


# ── register ──


def test_register_adds_four_tools(tmp_path: Path) -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine, settings=SimpleNamespace(base_dir=str(tmp_path)))
    webhook_tools.register(ctx)
    tool_names = [t.name for t in ctx.tools]
    assert "webhook_list" in tool_names
    assert "webhook_add" in tool_names
    assert "webhook_delete" in tool_names
    assert "webhook_update" in tool_names
    assert len(ctx.tools) == 4
