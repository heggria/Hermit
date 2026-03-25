"""Tests for computer_use tools.py registration."""

from __future__ import annotations

from hermit.plugins.builtin.tools.computer_use.tools import _all_tools, register
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_all_tools_returns_expected_tool_specs() -> None:
    tools = _all_tools()
    assert len(tools) == 8
    names = [t.name for t in tools]
    expected = [
        "computer_screenshot",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_move",
        "computer_scroll",
        "computer_get_screen_size",
        "computer_open_app",
    ]
    assert names == expected


def test_readonly_tools_are_marked_correctly() -> None:
    tools = _all_tools()
    readonly_names = {"computer_screenshot", "computer_get_screen_size"}
    for tool in tools:
        if tool.name in readonly_names:
            assert tool.readonly is True
            assert tool.risk_hint == "low"
            assert tool.requires_receipt is False
        else:
            assert tool.readonly is False
            assert tool.risk_hint == "critical"
            assert tool.requires_receipt is True


def test_register_adds_all_tools() -> None:
    engine = HooksEngine()
    ctx = PluginContext(hooks_engine=engine)
    register(ctx)
    assert len(ctx.tools) == 8
    tool_names = {t.name for t in ctx.tools}
    assert "computer_screenshot" in tool_names
    assert "computer_open_app" in tool_names


def test_tool_handlers_are_callable() -> None:
    tools = _all_tools()
    for tool in tools:
        assert callable(tool.handler)


def test_tool_input_schemas_have_correct_required_fields() -> None:
    tools = _all_tools()
    by_name = {t.name: t for t in tools}

    # Screenshot and get_screen_size have no required fields
    assert "required" not in by_name["computer_screenshot"].input_schema
    assert "required" not in by_name["computer_get_screen_size"].input_schema

    # Click requires x, y
    assert by_name["computer_click"].input_schema["required"] == ["x", "y"]

    # Type requires text
    assert by_name["computer_type"].input_schema["required"] == ["text"]

    # Key requires key
    assert by_name["computer_key"].input_schema["required"] == ["key"]

    # Scroll requires x, y, direction
    assert by_name["computer_scroll"].input_schema["required"] == ["x", "y", "direction"]
