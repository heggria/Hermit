"""Tests for GitHub MCP plugin registration and spec building."""

from __future__ import annotations

import os
from unittest.mock import patch

from hermit.plugins.builtin.mcp.github.mcp import (
    _GITHUB_MUTATION_TOOLS,
    _GITHUB_READ_TOOLS,
    _GITHUB_TOOL_GOVERNANCE,
    DEFAULT_GITHUB_MCP_URL,
    _build_github_spec,
    register,
)
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ── Tool governance constants ──


def test_read_tools_are_readonly_low_risk() -> None:
    for name in _GITHUB_READ_TOOLS:
        gov = _GITHUB_TOOL_GOVERNANCE[name]
        assert gov.readonly is True
        assert gov.risk_hint == "low"
        assert gov.requires_receipt is False
        assert gov.action_class == "network_read"


def test_mutation_tools_are_high_risk_with_receipt() -> None:
    for name in _GITHUB_MUTATION_TOOLS:
        gov = _GITHUB_TOOL_GOVERNANCE[name]
        assert gov.risk_hint == "high"
        assert gov.requires_receipt is True
        assert gov.action_class == "external_mutation"


def test_tool_governance_covers_all_tools() -> None:
    all_tools = _GITHUB_READ_TOOLS | _GITHUB_MUTATION_TOOLS
    assert set(_GITHUB_TOOL_GOVERNANCE.keys()) == all_tools


# ── _build_github_spec ──


def test_build_spec_no_context_no_env() -> None:
    with patch.dict(os.environ, {}, clear=True):
        spec = _build_github_spec(None)
    assert spec.name == "github"
    assert spec.transport == "http"
    assert spec.url == DEFAULT_GITHUB_MCP_URL


def test_build_spec_no_context_with_env_token() -> None:
    env = {"GITHUB_PERSONAL_ACCESS_TOKEN": "tok123"}
    with patch.dict(os.environ, env, clear=True):
        spec = _build_github_spec(None)
    assert spec.headers is not None
    assert spec.headers["Authorization"] == "Bearer tok123"


def test_build_spec_no_context_env_fallback_order() -> None:
    # GITHUB_PAT fallback
    with patch.dict(os.environ, {"GITHUB_PAT": "pat-tok"}, clear=True):
        spec = _build_github_spec(None)
    assert spec.headers["Authorization"] == "Bearer pat-tok"

    # GITHUB_TOKEN fallback
    with patch.dict(os.environ, {"GITHUB_TOKEN": "gh-tok"}, clear=True):
        spec = _build_github_spec(None)
    assert spec.headers["Authorization"] == "Bearer gh-tok"


def test_build_spec_no_context_custom_url() -> None:
    env = {"GITHUB_MCP_URL": "https://custom.example.com/mcp/"}
    with patch.dict(os.environ, env, clear=True):
        spec = _build_github_spec(None)
    assert spec.url == "https://custom.example.com/mcp/"


def test_build_spec_with_context() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    ctx.plugin_vars = {"github_pat": "ctx-token"}
    ctx.config = {
        "url": "https://ctx-url.example.com/mcp/",
        "headers": {"X-Custom": "header-val"},
    }
    spec = _build_github_spec(ctx)
    assert spec.url == "https://ctx-url.example.com/mcp/"
    assert spec.headers is not None
    assert spec.headers.get("X-Custom") == "header-val"


def test_build_spec_with_context_no_token_warns() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    ctx.plugin_vars = {}
    ctx.config = {}
    spec = _build_github_spec(ctx)
    # Should still return a spec even without a token
    assert spec.name == "github"
    assert spec.url == DEFAULT_GITHUB_MCP_URL


def test_build_spec_with_context_empty_headers_filtered() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    ctx.plugin_vars = {"github_pat": "tok"}
    ctx.config = {"headers": {"Keep": "value", "Remove": ""}}
    spec = _build_github_spec(ctx)
    # Empty header values should be filtered out
    assert "Remove" not in (spec.headers or {})
    assert (spec.headers or {}).get("Keep") == "value"


def test_build_spec_allowed_tools_sorted() -> None:
    spec = _build_github_spec(None)
    assert spec.allowed_tools == sorted(spec.allowed_tools)


# ── register ──


def test_register_adds_github_mcp_server() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    ctx.plugin_vars = {"github_pat": "test-tok"}
    ctx.config = {}
    register(ctx)
    assert len(ctx.mcp_servers) == 1
    assert ctx.mcp_servers[0].name == "github"
