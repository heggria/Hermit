"""Tests for MCP loader plugin (mcp.json config loading and server registration)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermit.plugins.builtin.mcp.mcp_loader.mcp import (
    _load_mcp_json,
    _parse_server_entry,
    _parse_tool_governance,
    register,
)
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ── _load_mcp_json ──


def test_load_mcp_json_missing_file(tmp_path: Path) -> None:
    result = _load_mcp_json(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_mcp_json_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"s1": {"command": "echo"}}}), encoding="utf-8")
    result = _load_mcp_json(path)
    assert "mcpServers" in result
    assert "s1" in result["mcpServers"]


def test_load_mcp_json_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    result = _load_mcp_json(path)
    assert result == {}


# ── _parse_server_entry ──


def test_parse_server_entry_stdio() -> None:
    entry = {
        "command": "npx",
        "args": ["-y", "my-server"],
        "env": {"KEY": "value"},
        "description": "My server",
        "allowedTools": ["tool1"],
    }
    spec = _parse_server_entry("test-server", entry)
    assert spec is not None
    assert spec.name == "test-server"
    assert spec.transport == "stdio"
    assert spec.command == ["npx", "-y", "my-server"]
    assert spec.env == {"KEY": "value"}
    assert spec.description == "My server"
    assert spec.allowed_tools == ["tool1"]


def test_parse_server_entry_stdio_no_args() -> None:
    entry = {"command": "my-binary"}
    spec = _parse_server_entry("bin", entry)
    assert spec is not None
    assert spec.command == ["my-binary"]


def test_parse_server_entry_stdio_non_list_args() -> None:
    entry = {"command": "cmd", "args": "not-a-list"}
    spec = _parse_server_entry("s", entry)
    assert spec is not None
    assert spec.command == ["cmd"]


def test_parse_server_entry_http() -> None:
    entry = {
        "url": "https://api.example.com/mcp/",
        "headers": {"Authorization": "Bearer tok"},
        "description": "Remote MCP",
    }
    spec = _parse_server_entry("remote", entry)
    assert spec is not None
    assert spec.name == "remote"
    assert spec.transport == "http"
    assert spec.url == "https://api.example.com/mcp/"
    assert spec.headers == {"Authorization": "Bearer tok"}


def test_parse_server_entry_no_command_or_url() -> None:
    entry = {"description": "incomplete"}
    spec = _parse_server_entry("bad", entry)
    assert spec is None


def test_parse_server_entry_default_description() -> None:
    entry = {"command": "npx"}
    spec = _parse_server_entry("myname", entry)
    assert spec is not None
    assert spec.description == "MCP server: myname"


def test_parse_server_entry_with_tool_governance() -> None:
    entry = {
        "command": "npx",
        "toolGovernance": {
            "read_data": {
                "actionClass": "network_read",
                "riskHint": "low",
                "requiresReceipt": False,
                "readonly": True,
            }
        },
    }
    spec = _parse_server_entry("gov", entry)
    assert spec is not None
    assert "read_data" in spec.tool_governance
    gov = spec.tool_governance["read_data"]
    assert gov.action_class == "network_read"
    assert gov.risk_hint == "low"
    assert gov.readonly is True
    assert gov.requires_receipt is False


# ── _parse_tool_governance ──


def test_parse_tool_governance_empty() -> None:
    assert _parse_tool_governance(None) == {}
    assert _parse_tool_governance("string") == {}
    assert _parse_tool_governance(42) == {}


def test_parse_tool_governance_valid() -> None:
    raw = {
        "tool1": {
            "actionClass": "write_local",
            "riskHint": "high",
            "requiresReceipt": True,
        },
        "tool2": {
            "action_class": "read_local",
            "risk_hint": "low",
            "requires_receipt": False,
            "readonly": True,
            "supports_preview": True,
        },
    }
    parsed = _parse_tool_governance(raw)
    assert len(parsed) == 2
    assert parsed["tool1"].action_class == "write_local"
    assert parsed["tool1"].requires_receipt is True
    assert parsed["tool2"].action_class == "read_local"
    assert parsed["tool2"].readonly is True
    assert parsed["tool2"].supports_preview is True


def test_parse_tool_governance_skips_non_dict_entries() -> None:
    raw = {"good": {"actionClass": "x", "riskHint": "low"}, "bad": "not-a-dict"}
    parsed = _parse_tool_governance(raw)
    assert len(parsed) == 1
    assert "good" in parsed


# ── register ──


def test_register_loads_from_base_dir(tmp_path: Path) -> None:
    mcp_config = {
        "mcpServers": {
            "my-server": {
                "command": "npx",
                "args": ["-y", "server-pkg"],
            }
        }
    }
    (tmp_path / "mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = HooksEngine()
    settings = SimpleNamespace(base_dir=str(tmp_path))
    ctx = PluginContext(hooks_engine=engine, settings=settings)

    with patch("hermit.plugins.builtin.mcp.mcp_loader.mcp.Path") as mock_path_cls:
        # Make Path.cwd() return a path that doesn't have .mcp.json
        mock_cwd = tmp_path / "cwd"
        mock_cwd.mkdir()
        mock_path_cls.cwd.return_value = mock_cwd
        mock_path_cls.side_effect = Path

        register(ctx)

    assert len(ctx.mcp_servers) >= 1
    server_names = [s.name for s in ctx.mcp_servers]
    assert "my-server" in server_names


def test_register_loads_from_cwd(tmp_path: Path, monkeypatch) -> None:
    mcp_config = {
        "mcpServers": {
            "cwd-server": {
                "url": "https://example.com/mcp/",
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine, settings=None)

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    register(ctx)

    server_names = [s.name for s in ctx.mcp_servers]
    assert "cwd-server" in server_names


def test_register_skips_invalid_entries(tmp_path: Path, monkeypatch) -> None:
    mcp_config = {
        "mcpServers": {
            "valid": {"command": "echo"},
            "invalid": "not-a-dict",
            "no-transport": {"description": "missing command and url"},
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine, settings=None)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    register(ctx)

    server_names = [s.name for s in ctx.mcp_servers]
    assert "valid" in server_names
    assert "invalid" not in server_names
    assert "no-transport" not in server_names


def test_register_skips_non_dict_mcp_servers(tmp_path: Path, monkeypatch) -> None:
    mcp_config = {"mcpServers": "not-a-dict"}
    (tmp_path / ".mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine, settings=None)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    register(ctx)

    assert len(ctx.mcp_servers) == 0
