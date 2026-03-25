"""Tests for grok tools.py registration."""

from __future__ import annotations

from hermit.plugins.builtin.tools.grok.tools import register
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_register_adds_grok_search() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    assert len(ctx.tools) == 1
    tool = ctx.tools[0]
    assert tool.name == "grok_search"


def test_grok_search_is_readonly_and_low_risk() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    tool = ctx.tools[0]
    assert tool.readonly is True
    assert tool.action_class == "network_read"
    assert tool.idempotent is True
    assert tool.risk_hint == "low"
    assert tool.requires_receipt is True


def test_grok_search_schema_requires_query() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    tool = ctx.tools[0]
    assert tool.input_schema["required"] == ["query"]
    props = tool.input_schema["properties"]
    assert "query" in props
    assert "search_mode" in props
    assert "max_tokens" in props
    assert props["search_mode"]["enum"] == ["auto", "on", "off"]


def test_grok_handler_is_callable() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    assert callable(ctx.tools[0].handler)
