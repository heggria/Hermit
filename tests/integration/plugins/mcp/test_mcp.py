"""Tests for MCP plugin dimension, mcp_client utilities, and mcp_loader config parsing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.runtime.capability.contracts.base import (
    McpServerSpec,
    McpToolGovernance,
    PluginContext,
    PluginManifest,
    PluginVariableSpec,
)
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.loader.config import resolve_plugin_context
from hermit.runtime.capability.loader.loader import load_plugin_entries, parse_manifest
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolRegistry
from hermit.runtime.capability.resolver.mcp_client import (
    MCP_TOOL_PREFIX,
    McpClientManager,
    _sanitize_http_headers,
    mcp_tool_name,
    parse_mcp_tool_name,
)

# ── mcp_tool_name / parse_mcp_tool_name ───────────────────────────


class TestMcpToolNaming:
    def test_roundtrip(self):
        full = mcp_tool_name("notion", "search")
        assert full == "mcp__notion__search"
        server, tool = parse_mcp_tool_name(full)
        assert server == "notion"
        assert tool == "search"

    def test_multi_underscore_tool_name(self):
        full = mcp_tool_name("github", "create_pull_request")
        assert full == "mcp__github__create_pull_request"
        server, tool = parse_mcp_tool_name(full)
        assert server == "github"
        assert tool == "create_pull_request"

    def test_parse_invalid_prefix(self):
        with pytest.raises(ValueError, match="Not an MCP tool"):
            parse_mcp_tool_name("read_file")

    def test_parse_no_tool_part(self):
        with pytest.raises(ValueError, match="Invalid MCP tool"):
            parse_mcp_tool_name("mcp__serveronly")

    def test_prefix_constant(self):
        assert MCP_TOOL_PREFIX == "mcp__"


def test_sanitize_http_headers_drops_empty_bearer_token() -> None:
    sanitized = _sanitize_http_headers(
        {
            "Authorization": "Bearer ",
            "X-Test": "  ok  ",
            "X-Empty": "   ",
        }
    )

    assert sanitized == {"X-Test": "ok"}


def test_sanitize_http_headers_keeps_valid_authorization() -> None:
    sanitized = _sanitize_http_headers(
        {
            "Authorization": "Bearer ghp_test_123",
        }
    )

    assert sanitized == {"Authorization": "Bearer ghp_test_123"}


# ── McpServerSpec ─────────────────────────────────────────────────


class TestMcpServerSpec:
    def test_stdio_spec(self):
        spec = McpServerSpec(
            name="test",
            description="Test server",
            transport="stdio",
            command=["python", "server.py"],
        )
        assert spec.transport == "stdio"
        assert spec.command == ["python", "server.py"]
        assert spec.url is None

    def test_http_spec(self):
        spec = McpServerSpec(
            name="remote",
            description="Remote server",
            transport="http",
            url="https://mcp.example.com/sse",
            headers={"Authorization": "Bearer tok"},
        )
        assert spec.transport == "http"
        assert spec.url == "https://mcp.example.com/sse"
        assert spec.command is None

    def test_allowed_tools(self):
        spec = McpServerSpec(
            name="filtered",
            description="Filtered",
            transport="stdio",
            command=["node", "srv"],
            allowed_tools=["search", "read"],
        )
        assert spec.allowed_tools == ["search", "read"]


# ── PluginContext.add_mcp ─────────────────────────────────────────


class TestPluginContextMcp:
    def test_add_mcp_collects_specs(self):
        ctx = PluginContext(HooksEngine())
        spec1 = McpServerSpec(name="a", description="A", transport="stdio", command=["a"])
        spec2 = McpServerSpec(name="b", description="B", transport="http", url="http://b")
        ctx.add_mcp(spec1)
        ctx.add_mcp(spec2)
        assert len(ctx.mcp_servers) == 2
        assert ctx.mcp_servers[0].name == "a"
        assert ctx.mcp_servers[1].name == "b"


# ── PluginManager MCP collection ─────────────────────────────────


class TestPluginManagerMcpCollection:
    def test_mcp_plugin_entry_collects_specs(self, tmp_path: Path):
        """A plugin with entry.mcp registers McpServerSpec via add_mcp."""
        plugin_dir = tmp_path / "plugins" / "test-mcp"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "test-mcp"\nversion = "0.1.0"\n\n'
            '[entry]\nmcp = "mcp_entry:register"\n',
            encoding="utf-8",
        )
        (plugin_dir / "mcp_entry.py").write_text(
            "from hermit.runtime.capability.contracts.base import McpServerSpec, PluginContext\n"
            "def register(ctx: PluginContext) -> None:\n"
            "    ctx.add_mcp(McpServerSpec(\n"
            '        name="demo", description="Demo", transport="stdio",\n'
            '        command=["echo", "hello"],\n'
            "    ))\n",
            encoding="utf-8",
        )

        pm = PluginManager()
        pm.discover_and_load(tmp_path / "plugins")

        assert len(pm.mcp_specs) == 1
        assert pm.mcp_specs[0].name == "demo"
        assert pm.mcp_specs[0].transport == "stdio"

    def test_no_mcp_returns_empty(self, tmp_path: Path):
        """Plugin without MCP entry has no MCP specs."""
        plugin_dir = tmp_path / "plugins" / "plain"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "plain"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        pm = PluginManager()
        pm.discover_and_load(tmp_path / "plugins")
        assert pm.mcp_specs == []

    def test_start_mcp_noop_when_empty(self):
        """start_mcp_servers is a no-op when no MCP specs registered."""
        pm = PluginManager()
        registry = ToolRegistry()
        pm.start_mcp_servers(registry)
        assert pm._mcp_manager is None

    def test_start_mcp_ignores_connection_failures(self, monkeypatch):
        """MCP connection failures should not abort the main process."""
        pm = PluginManager()
        pm._all_mcp.append(
            McpServerSpec(
                name="broken",
                description="Broken MCP",
                transport="http",
                url="https://example.invalid/mcp",
            )
        )

        def _boom(self, specs):
            raise RuntimeError("401 Unauthorized")

        monkeypatch.setattr(McpClientManager, "connect_all_sync", _boom)

        registry = ToolRegistry()
        pm.start_mcp_servers(registry)

        assert pm._mcp_manager is not None
        assert pm._mcp_manager.get_tool_specs() == []

    def test_manifest_variables_resolve_from_config_toml(self, tmp_path: Path, monkeypatch):
        base_dir = tmp_path / ".hermit"
        plugin_root = tmp_path / "plugins"
        plugin_dir = plugin_root / "demo"
        base_dir.mkdir(parents=True)
        plugin_dir.mkdir(parents=True)
        (base_dir / "config.toml").write_text(
            """
[plugins.demo.variables]
api_token = "cfg-token"
base_url = "https://cfg.example.com/mcp"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (plugin_dir / "plugin.toml").write_text(
            """
[plugin]
name = "demo"
version = "0.1.0"

[entry]
mcp = "entry:register"

[config]
url = "{{ base_url }}"

[config.headers]
Authorization = "Bearer {{ api_token }}"

[variables.api_token]
env = ["DEMO_API_TOKEN"]

[variables.base_url]
default = "https://default.example.com/mcp"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (plugin_dir / "entry.py").write_text(
            "from hermit.runtime.capability.contracts.base import PluginContext\n"
            "def register(ctx: PluginContext) -> None:\n"
            "    pass\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))

        manifest = parse_manifest(plugin_dir)
        settings = MagicMock()
        settings.base_dir = base_dir
        ctx = load_plugin_entries(manifest, HooksEngine(), settings=settings)  # type: ignore[arg-type]

        assert ctx.plugin_vars["api_token"] == "cfg-token"
        assert ctx.config["url"] == "https://cfg.example.com/mcp"
        assert ctx.config["headers"]["Authorization"] == "Bearer cfg-token"

    def test_github_plugin_uses_declared_variables(self, tmp_path: Path):
        base_dir = tmp_path / ".hermit"
        base_dir.mkdir(parents=True)
        (base_dir / "config.toml").write_text(
            """
[plugins.github.variables]
github_pat = "ghp_test_123"
github_mcp_url = "https://example.github.test/mcp"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings = MagicMock()
        settings.base_dir = base_dir

        pm = PluginManager(settings=settings)
        pm.discover_and_load(Path("src/hermit/plugins/builtin/mcp/github").resolve().parent)

        github_specs = [spec for spec in pm.mcp_specs if spec.name == "github"]
        assert len(github_specs) == 1
        spec = github_specs[0]
        assert spec.url == "https://example.github.test/mcp"
        assert spec.headers == {"Authorization": "Bearer ghp_test_123"}

    def test_disabled_builtin_plugin_is_skipped(self, tmp_path: Path):
        base_dir = tmp_path / ".hermit"
        base_dir.mkdir(parents=True)
        (base_dir / "config.toml").write_text(
            """
disabled_builtin_plugins = ["github"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings = MagicMock()
        settings.base_dir = base_dir
        settings.disabled_builtin_plugins = ["github"]

        pm = PluginManager(settings=settings)
        pm.discover_and_load(Path("src/hermit/plugins/builtin/mcp/github").resolve().parent)

        assert all(manifest.name != "github" for manifest in pm.manifests)
        assert pm.mcp_specs == []

    def test_resolve_plugin_context_renders_lists_and_errors_for_required_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base_dir = tmp_path / ".hermit"
        base_dir.mkdir(parents=True)
        settings = MagicMock()
        settings.base_dir = base_dir
        settings.api_token = None
        manifest = PluginManifest(
            name="demo",
            config={
                "argv": ["run", "{{ api_token }}", "{{ optional_value }}"],
                "headers": {"Authorization": "Bearer {{ api_token }}"},
            },
            variables={
                "api_token": PluginVariableSpec(
                    name="api_token", setting="api_token", required=True
                ),
                "optional_value": PluginVariableSpec(name="optional_value"),
            },
        )

        errors: list[dict[str, object]] = []
        import hermit.runtime.capability.loader.config as plugin_config

        monkeypatch.setattr(
            plugin_config.log, "error", lambda *args, **kwargs: errors.append(kwargs)
        )

        vars_resolved, config_resolved = resolve_plugin_context(manifest, settings)

        assert vars_resolved["api_token"] is None
        assert config_resolved["argv"] == ["run"]
        assert config_resolved["headers"]["Authorization"] == "Bearer "
        assert errors == [
            {"plugin": "demo", "variable": "api_token", "env_vars": []}
        ]


# ── mcp_loader plugin (.mcp.json parsing) ────────────────────────


class TestMcpLoaderPlugin:
    def test_parse_stdio_config(self, tmp_path: Path):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _parse_server_entry

        spec = _parse_server_entry(
            "notion",
            {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-notion"],
                "env": {"NOTION_API_KEY": "secret"},
            },
        )
        assert spec is not None
        assert spec.name == "notion"
        assert spec.transport == "stdio"
        assert spec.command == ["npx", "-y", "@modelcontextprotocol/server-notion"]
        assert spec.env == {"NOTION_API_KEY": "secret"}

    def test_parse_http_config(self):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _parse_server_entry

        spec = _parse_server_entry(
            "remote",
            {
                "url": "https://mcp.example.com/sse",
                "headers": {"Authorization": "Bearer tok"},
            },
        )
        assert spec is not None
        assert spec.name == "remote"
        assert spec.transport == "http"
        assert spec.url == "https://mcp.example.com/sse"
        assert spec.headers == {"Authorization": "Bearer tok"}

    def test_parse_invalid_entry(self):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _parse_server_entry

        spec = _parse_server_entry("bad", {"nothing": True})
        assert spec is None

    def test_load_mcp_json(self, tmp_path: Path):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _load_mcp_json

        config = {
            "mcpServers": {
                "notion": {
                    "command": "npx",
                    "args": ["-y", "server-notion"],
                },
            }
        }
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")
        data = _load_mcp_json(config_file)
        assert "mcpServers" in data
        assert "notion" in data["mcpServers"]

    def test_load_mcp_json_missing_file(self, tmp_path: Path):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _load_mcp_json

        data = _load_mcp_json(tmp_path / "nonexistent.json")
        assert data == {}

    def test_load_mcp_json_invalid_json(self, tmp_path: Path):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _load_mcp_json

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json", encoding="utf-8")
        data = _load_mcp_json(bad_file)
        assert data == {}

    def test_register_reads_global_and_project_configs(self, tmp_path: Path, monkeypatch):
        """register() reads both global and project-level configs."""
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import register

        base_dir = tmp_path / "hermit-home"
        base_dir.mkdir()
        global_config = {
            "mcpServers": {
                "global-srv": {
                    "command": "echo",
                    "args": ["global"],
                }
            }
        }
        (base_dir / "mcp.json").write_text(json.dumps(global_config), encoding="utf-8")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_config = {
            "mcpServers": {
                "project-srv": {
                    "url": "https://project.example.com/mcp",
                }
            }
        }
        (project_dir / ".mcp.json").write_text(json.dumps(project_config), encoding="utf-8")

        monkeypatch.chdir(project_dir)

        settings = MagicMock()
        settings.base_dir = base_dir

        ctx = PluginContext(HooksEngine(), settings=settings)
        register(ctx)

        names = {s.name for s in ctx.mcp_servers}
        assert "global-srv" in names
        assert "project-srv" in names
        assert len(ctx.mcp_servers) == 2

    def test_project_config_overrides_global(self, tmp_path: Path, monkeypatch):
        """Project-level config overrides global for same server name."""
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import register

        base_dir = tmp_path / "home"
        base_dir.mkdir()
        (base_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "old", "args": []}}}), encoding="utf-8"
        )

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "new", "args": ["--flag"]}}}),
            encoding="utf-8",
        )

        monkeypatch.chdir(project_dir)
        settings = MagicMock()
        settings.base_dir = base_dir

        ctx = PluginContext(HooksEngine(), settings=settings)
        register(ctx)

        assert len(ctx.mcp_servers) == 1
        assert ctx.mcp_servers[0].command == ["new", "--flag"]

    def test_allowed_tools_config(self):
        from hermit.plugins.builtin.mcp.mcp_loader.mcp import _parse_server_entry

        spec = _parse_server_entry(
            "filtered",
            {
                "command": "node",
                "args": ["server.js"],
                "allowedTools": ["search", "read"],
            },
        )
        assert spec is not None
        assert spec.allowed_tools == ["search", "read"]


# ── McpClientManager unit tests (no real MCP server) ─────────────


class TestMcpClientManagerUnit:
    def test_get_tool_specs_empty(self):
        mgr = McpClientManager()
        assert mgr.get_tool_specs() == []
        mgr.close_all_sync()

    def test_get_tool_specs_rejects_missing_governance_metadata(self):
        mgr = object.__new__(McpClientManager)
        mgr._connections = {
            "server": SimpleNamespace(
                spec=McpServerSpec(name="server", description="Server", transport="stdio"),
                tools=[{"name": "tool", "description": "desc", "input_schema": {"type": "object"}}],
            )
        }

        with pytest.raises(ValueError, match="governance"):
            mgr.get_tool_specs()

    def test_get_tool_specs_applies_governance_metadata(self):
        mgr = object.__new__(McpClientManager)
        mgr._run_async = lambda coro, timeout=60: (coro.close(), "ok")[1]  # type: ignore[attr-defined]
        mgr._connections = {
            "server": SimpleNamespace(
                spec=McpServerSpec(
                    name="server",
                    description="Server",
                    transport="stdio",
                    tool_governance={
                        "tool": McpToolGovernance(
                            action_class="network_read",
                            risk_hint="low",
                            requires_receipt=False,
                            readonly=True,
                        )
                    },
                ),
                tools=[{"name": "tool", "description": "desc", "input_schema": {"type": "object"}}],
            )
        }

        specs = mgr.get_tool_specs()

        assert specs[0].name == "mcp__server__tool"
        assert specs[0].action_class == "network_read"
        assert specs[0].readonly is True
        assert specs[0].requires_receipt is False

    def test_connect_skips_bad_transport(self):
        mgr = McpClientManager()
        spec = McpServerSpec(
            name="bad",
            description="Bad transport",
            transport="unknown",
        )
        mgr.connect_all_sync([spec])
        assert "bad" not in mgr._connections
        mgr.close_all_sync()

    def test_connect_skips_stdio_without_command(self):
        mgr = McpClientManager()
        spec = McpServerSpec(
            name="no-cmd",
            description="No command",
            transport="stdio",
        )
        mgr.connect_all_sync([spec])
        assert "no-cmd" not in mgr._connections
        mgr.close_all_sync()

    def test_connect_skips_http_without_url(self):
        mgr = McpClientManager()
        spec = McpServerSpec(
            name="no-url",
            description="No URL",
            transport="http",
        )
        mgr.connect_all_sync([spec])
        assert "no-url" not in mgr._connections
        mgr.close_all_sync()

    def test_close_all_idempotent(self):
        mgr = McpClientManager()
        mgr.close_all_sync()
        mgr2 = McpClientManager()
        mgr2.close_all_sync()
        mgr2.close_all_sync()


# ── builtin mcp_loader plugin discovery ──────────────────────────


class TestMcpLoaderPluginDiscovery:
    def test_mcp_loader_is_discovered(self):
        from hermit.runtime.capability.loader.loader import discover_plugins

        builtin_dir = Path(__file__).resolve().parents[4] / "src" / "hermit" / "plugins" / "builtin"
        manifests = discover_plugins(builtin_dir)
        names = [m.name for m in manifests]
        assert "mcp-loader" in names
        mcp_manifest = next(m for m in manifests if m.name == "mcp-loader")
        assert mcp_manifest.entry == {"mcp": "mcp:register"}
        assert mcp_manifest.builtin is True
