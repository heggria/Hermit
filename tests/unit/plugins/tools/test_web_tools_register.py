"""Tests for web_tools tools.py registration."""

from __future__ import annotations

from hermit.plugins.builtin.tools.web_tools.tools import register
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_register_adds_web_search_and_web_fetch() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    assert len(ctx.tools) == 2
    names = [t.name for t in ctx.tools]
    assert "web_search" in names
    assert "web_fetch" in names


def test_web_tools_are_readonly_and_low_risk() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    for tool in ctx.tools:
        assert tool.readonly is True
        assert tool.action_class == "network_read"
        assert tool.idempotent is True
        assert tool.risk_hint == "low"
        assert tool.requires_receipt is False


def test_web_search_schema_requires_query() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    search_tool = next(t for t in ctx.tools if t.name == "web_search")
    assert search_tool.input_schema["required"] == ["query"]
    assert "query" in search_tool.input_schema["properties"]
    assert "max_results" in search_tool.input_schema["properties"]
    assert "region" in search_tool.input_schema["properties"]
    assert "time_filter" in search_tool.input_schema["properties"]
    assert "search_type" in search_tool.input_schema["properties"]


def test_web_fetch_schema_requires_url() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    fetch_tool = next(t for t in ctx.tools if t.name == "web_fetch")
    assert fetch_tool.input_schema["required"] == ["url"]
    assert "url" in fetch_tool.input_schema["properties"]
    assert "max_length" in fetch_tool.input_schema["properties"]


def test_web_tools_handlers_are_callable() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    for tool in ctx.tools:
        assert callable(tool.handler)
