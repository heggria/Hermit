"""Tests for runtime/capability/resolver/mcp_client.py — coverage for missed lines.

Covers: _sanitize_http_headers, mcp_tool_name, parse_mcp_tool_name,
McpClientManager._tool_governance, McpClientManager._call_tool error paths,
McpClientManager.get_tool_specs, McpClientManager.close_all_sync edge cases.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermit.runtime.capability.contracts.base import McpToolGovernance
from hermit.runtime.capability.resolver.mcp_client import (
    McpClientManager,
    _sanitize_http_headers,
    _ServerConnection,
    mcp_tool_name,
    parse_mcp_tool_name,
)

# ---------------------------------------------------------------------------
# _sanitize_http_headers
# ---------------------------------------------------------------------------


class TestSanitizeHttpHeaders:
    def test_none_returns_empty(self) -> None:
        assert _sanitize_http_headers(None) == {}

    def test_empty_dict_returns_empty(self) -> None:
        assert _sanitize_http_headers({}) == {}

    def test_valid_headers_pass_through(self) -> None:
        result = _sanitize_http_headers({"X-Custom": "value", "Accept": "json"})
        assert result == {"X-Custom": "value", "Accept": "json"}

    def test_empty_key_dropped(self) -> None:
        result = _sanitize_http_headers({"": "value", "ok": "v"})
        assert result == {"ok": "v"}

    def test_empty_value_dropped(self) -> None:
        result = _sanitize_http_headers({"key": "", "ok": "v"})
        assert result == {"ok": "v"}

    def test_whitespace_only_key_dropped(self) -> None:
        result = _sanitize_http_headers({"   ": "value"})
        assert result == {}

    def test_whitespace_only_value_dropped(self) -> None:
        result = _sanitize_http_headers({"key": "   "})
        assert result == {}

    def test_bearer_only_authorization_dropped(self) -> None:
        result = _sanitize_http_headers({"Authorization": "bearer"})
        assert result == {}

    def test_bearer_case_insensitive_dropped(self) -> None:
        result = _sanitize_http_headers({"Authorization": "Bearer"})
        assert result == {}

    def test_bearer_with_empty_token_dropped(self) -> None:
        result = _sanitize_http_headers({"Authorization": "Bearer   "})
        assert result == {}

    def test_bearer_with_real_token_kept(self) -> None:
        result = _sanitize_http_headers({"Authorization": "Bearer sk-123"})
        assert result == {"Authorization": "Bearer sk-123"}

    def test_non_bearer_authorization_kept(self) -> None:
        result = _sanitize_http_headers({"Authorization": "Basic abc123"})
        assert result == {"Authorization": "Basic abc123"}

    def test_values_stripped(self) -> None:
        result = _sanitize_http_headers({"key": "  value  "})
        assert result == {"key": "value"}

    def test_numeric_values_converted(self) -> None:
        result = _sanitize_http_headers({"X-Num": 42})
        assert result == {"X-Num": "42"}


# ---------------------------------------------------------------------------
# mcp_tool_name / parse_mcp_tool_name
# ---------------------------------------------------------------------------


class TestMcpToolNaming:
    def test_mcp_tool_name_format(self) -> None:
        assert mcp_tool_name("server", "tool") == "mcp__server__tool"

    def test_parse_mcp_tool_name_round_trip(self) -> None:
        full = mcp_tool_name("myserver", "mytool")
        server, tool = parse_mcp_tool_name(full)
        assert server == "myserver"
        assert tool == "mytool"

    def test_parse_non_mcp_tool_raises(self) -> None:
        with pytest.raises(ValueError, match="Not an MCP tool"):
            parse_mcp_tool_name("regular_tool")

    def test_parse_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid MCP tool"):
            parse_mcp_tool_name("mcp__serveronly")

    def test_parse_tool_with_separator_in_name(self) -> None:
        full = "mcp__server__tool__sub"
        server, tool = parse_mcp_tool_name(full)
        assert server == "server"
        assert tool == "tool__sub"


# ---------------------------------------------------------------------------
# McpClientManager._tool_governance
# ---------------------------------------------------------------------------


class TestToolGovernance:
    def test_governance_found(self) -> None:
        gov = McpToolGovernance(
            action_class="read_api", risk_hint="low", requires_receipt=False, readonly=True
        )
        spec = SimpleNamespace(
            name="test",
            tool_governance={"my_tool": gov},
        )
        result = McpClientManager._tool_governance(spec, "my_tool")
        assert result is gov

    def test_governance_missing_raises(self) -> None:
        spec = SimpleNamespace(
            name="test",
            tool_governance={},
        )
        with pytest.raises(ValueError, match="governance missing"):
            McpClientManager._tool_governance(spec, "missing_tool")


# ---------------------------------------------------------------------------
# McpClientManager._call_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCallTool:
    @pytest.fixture
    def manager(self) -> McpClientManager:
        mgr = McpClientManager()
        yield mgr
        # Clean up background thread
        try:
            mgr._loop.call_soon_threadsafe(mgr._loop.stop)
            mgr._thread.join(timeout=2)
        except Exception:
            pass

    async def test_call_tool_server_not_connected(self, manager: McpClientManager) -> None:
        result = await manager._call_tool("missing", "tool", {})
        assert "not connected" in result

    async def test_call_tool_session_none(self, manager: McpClientManager) -> None:
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=None
        )
        result = await manager._call_tool("srv", "tool", {})
        assert "not connected" in result

    async def test_call_tool_error_result(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        error_block = SimpleNamespace(text="tool failed badly")
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=True,
            content=[error_block],
            structuredContent=None,
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert "Error:" in result
        assert "tool failed badly" in result

    async def test_call_tool_error_no_text(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text=None)],
            structuredContent=None,
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert "Error: MCP tool failed" in result

    async def test_call_tool_structured_content_with_observation(
        self, manager: McpClientManager
    ) -> None:
        mock_session = AsyncMock()
        structured = {"_hermit_observation": True, "data": "test"}
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=False,
            content=[],
            structuredContent=structured,
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert result == structured

    async def test_call_tool_structured_content_json(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=False,
            content=[],
            structuredContent={"key": "value"},
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        import json

        parsed = json.loads(result)
        assert parsed["key"] == "value"

    async def test_call_tool_text_content(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="line1"), SimpleNamespace(text="line2")],
            structuredContent=None,
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert result == "line1\nline2"

    async def test_call_tool_no_output(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text=None)],
            structuredContent=None,
        )
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert result == "(no output)"

    async def test_call_tool_exception(self, manager: McpClientManager) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.side_effect = RuntimeError("connection lost")
        manager._connections["srv"] = _ServerConnection(
            spec=SimpleNamespace(name="srv"), session=mock_session
        )
        result = await manager._call_tool("srv", "tool", {})
        assert "Error calling MCP tool" in result
        assert "connection lost" in result


# ---------------------------------------------------------------------------
# McpClientManager.get_tool_specs
# ---------------------------------------------------------------------------


class TestGetToolSpecs:
    def test_returns_specs_for_connected_tools(self) -> None:
        mgr = McpClientManager()
        try:
            gov = McpToolGovernance(
                action_class="read_api", risk_hint="low", requires_receipt=False, readonly=True
            )
            spec = SimpleNamespace(
                name="srv",
                tool_governance={"do_thing": gov},
            )
            mgr._connections["srv"] = _ServerConnection(
                spec=spec,
                session=MagicMock(),
                tools=[{"name": "do_thing", "description": "Does a thing"}],
            )
            tool_specs = mgr.get_tool_specs()
            assert len(tool_specs) == 1
            assert tool_specs[0].name == "mcp__srv__do_thing"
            assert tool_specs[0].readonly is True
            assert tool_specs[0].action_class == "read_api"
            assert "[MCP:srv]" in tool_specs[0].description
        finally:
            mgr._loop.call_soon_threadsafe(mgr._loop.stop)
            mgr._thread.join(timeout=2)

    def test_empty_connections_returns_empty(self) -> None:
        mgr = McpClientManager()
        try:
            assert mgr.get_tool_specs() == []
        finally:
            mgr._loop.call_soon_threadsafe(mgr._loop.stop)
            mgr._thread.join(timeout=2)


# ---------------------------------------------------------------------------
# McpClientManager.close_all_sync
# ---------------------------------------------------------------------------


class TestCloseAllSync:
    def test_close_without_connect_clears_connections(self) -> None:
        mgr = McpClientManager()
        mgr._connections["x"] = _ServerConnection(spec=SimpleNamespace(name="x"), session=None)
        mgr.close_all_sync()
        assert mgr._connections == {}

    def test_close_when_loop_not_running(self) -> None:
        mgr = McpClientManager()
        mgr._loop.call_soon_threadsafe(mgr._loop.stop)
        mgr._thread.join(timeout=2)
        mgr._connections["x"] = _ServerConnection(spec=SimpleNamespace(name="x"), session=None)
        mgr.close_all_sync()
        assert mgr._connections == {}
