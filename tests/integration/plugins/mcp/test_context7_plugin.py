"""Verification for the context7 external plugin.

Tests:
  1. Plugin discovery (loads from a temporary external plugin directory)
  2. McpServerSpec is registered with correct transport/command
  3. Skill 'context7-docs' is discovered and content is non-empty
  4. Optional live MCP integration against the public Context7 endpoint
"""

import os
from pathlib import Path

import pytest

LIVE_MCP = os.environ.get("HERMIT_RUN_LIVE_MCP_TESTS") == "1"


def _write_context7_plugin(base_dir: Path) -> None:
    plugin_dir = base_dir / "plugins" / "context7"
    skill_dir = plugin_dir / "skills" / "context7-docs"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        """
[plugin]
name = "context7"
version = "0.1.0"
description = "Context7 docs integration"
builtin = false

[entry]
mcp = "mcp:register"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "mcp.py").write_text(
        """
from hermit.runtime.capability.contracts.base import McpServerSpec, McpToolGovernance, PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_mcp(
        McpServerSpec(
            name="context7",
            description="Context7 docs",
            transport="http",
            url="https://mcp.context7.com/mcp",
            tool_governance={
                "resolve-library-id": McpToolGovernance(
                    action_class="network_read",
                    risk_hint="low",
                    requires_receipt=False,
                    readonly=True,
                ),
                "query-docs": McpToolGovernance(
                    action_class="network_read",
                    risk_hint="low",
                    requires_receipt=False,
                    readonly=True,
                ),
            },
        )
    )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        """---
name: context7-docs
description: Use Context7 MCP to resolve library ids and query package documentation.
---

Use this skill when the task needs package or framework documentation from Context7.

Available tools:
- `mcp__context7__resolve-library-id`
- `mcp__context7__query-docs`
""",
        encoding="utf-8",
    )


def _make_pm(tmp_path, monkeypatch):
    from hermit.runtime.assembly.config import Settings
    from hermit.runtime.capability.registry.manager import PluginManager

    base_dir = tmp_path / ".hermit"
    _write_context7_plugin(base_dir)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    settings = Settings()
    pm = PluginManager(settings=settings)
    builtin_dir = Path(__file__).resolve().parents[4] / "src" / "hermit" / "plugins" / "builtin"
    pm.discover_and_load(builtin_dir, settings.plugins_dir)
    return pm


# ── 1. Plugin discovery ────────────────────────────────────────────────────


def test_context7_plugin_is_discovered(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    names = [m.name for m in pm.manifests]
    assert "context7" in names, f"context7 not in discovered plugins: {names}"


def test_context7_plugin_is_not_builtin(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    manifest = next(m for m in pm.manifests if m.name == "context7")
    assert manifest.builtin is False


def test_context7_plugin_has_mcp_entry(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    manifest = next(m for m in pm.manifests if m.name == "context7")
    assert "mcp" in manifest.entry, f"entry keys: {list(manifest.entry.keys())}"


# ── 2. McpServerSpec registered ───────────────────────────────────────────


def test_context7_mcp_spec_registered(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    specs = {s.name: s for s in pm.mcp_specs}
    assert "context7" in specs, f"MCP specs: {list(specs.keys())}"


def test_context7_mcp_spec_transport(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    spec = next(s for s in pm.mcp_specs if s.name == "context7")
    assert spec.transport == "http"
    assert spec.url == "https://mcp.context7.com/mcp"
    assert spec.command is None


# ── 3. Skill registered ───────────────────────────────────────────────────


def test_context7_skill_discovered(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    names = [s.name for s in pm._all_skills]
    assert "context7-docs" in names, f"Skills: {names}"


def test_context7_skill_has_content(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    skill = next(s for s in pm._all_skills if s.name == "context7-docs")
    assert len(skill.content) > 100
    assert "mcp__context7__resolve-library-id" in skill.content
    assert "mcp__context7__query-docs" in skill.content


def test_context7_skill_appears_in_system_prompt(tmp_path, monkeypatch):
    pm = _make_pm(tmp_path, monkeypatch)
    prompt = pm.build_system_prompt("BASE")
    assert "context7-docs" in prompt
    # Skill should be in available_skills catalog (not preloaded by default)
    assert "<available_skills>" in prompt


# ── 4. Optional live MCP integration ──────────────────────────────────────


@pytest.fixture(scope="module")
def mcp_manager():
    if not LIVE_MCP:
        pytest.skip("Set HERMIT_RUN_LIVE_MCP_TESTS=1 to run live MCP tests")
    from hermit.runtime.capability.contracts.base import McpServerSpec, McpToolGovernance
    from hermit.runtime.capability.resolver.mcp_client import McpClientManager

    mgr = McpClientManager()
    spec = McpServerSpec(
        name="context7",
        description="Context7 docs",
        transport="http",
        url="https://mcp.context7.com/mcp",
        tool_governance={
            "resolve-library-id": McpToolGovernance(
                action_class="network_read",
                risk_hint="low",
                requires_receipt=False,
                readonly=True,
            ),
            "query-docs": McpToolGovernance(
                action_class="network_read",
                risk_hint="low",
                requires_receipt=False,
                readonly=True,
            ),
        },
    )
    mgr.connect_all_sync([spec])
    yield mgr
    try:
        mgr.close_all_sync()
    except Exception:
        pass


def test_mcp_server_connects(mcp_manager):
    tools = mcp_manager.get_tool_specs()
    assert len(tools) >= 2, f"Expected >=2 tools, got: {[t.name for t in tools]}"


def test_mcp_tools_have_expected_names(mcp_manager):
    tool_names = {t.name for t in mcp_manager.get_tool_specs()}
    assert "mcp__context7__resolve-library-id" in tool_names
    assert "mcp__context7__query-docs" in tool_names


# ── 5. Real tool calls ────────────────────────────────────────────────────


def test_resolve_library_id(mcp_manager):
    """resolve-library-id should return a Context7-compatible library ID."""
    tools = {t.name: t for t in mcp_manager.get_tool_specs()}
    resolve = tools["mcp__context7__resolve-library-id"]

    result = resolve.handler(
        {
            "libraryName": "pydantic-settings",
            "query": "how to load config from .env file with pydantic-settings",
        }
    )

    # Result should contain a library ID like /pydantic/pydantic-settings
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    assert len(result) > 10, "Empty or too-short result"
    print(f"\nresolve result (first 300 chars):\n{result[:300]}")


def test_query_docs(mcp_manager):
    """query-docs should return documentation content."""
    tools = {t.name: t for t in mcp_manager.get_tool_specs()}
    query = tools["mcp__context7__query-docs"]

    result = query.handler(
        {
            "libraryId": "/pydantic/pydantic-settings",
            "query": "load configuration from .env file",
        }
    )

    assert isinstance(result, str), f"Expected str, got {type(result)}"
    assert len(result) > 50, "Too-short response — likely an error"
    # Should contain something relevant to settings/env
    lower = result.lower()
    has_relevant = any(kw in lower for kw in ["env", "settings", "config", "pydantic"])
    assert has_relevant, f"Response doesn't look like docs:\n{result[:400]}"
    print(f"\nquery-docs result (first 400 chars):\n{result[:400]}")
