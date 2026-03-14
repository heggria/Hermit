"""MCP Client Manager — connects to MCP servers, discovers tools, routes calls.

Uses a dedicated background event loop so that async MCP operations can be
called from the synchronous runtime tool loop without conflicts.

Key design: all context-manager enter/exit operations happen inside a single
long-lived ``lifecycle()`` coroutine (Task A).  anyio's CancelScope requires
that __aexit__ is called from the same task as __aenter__, so we must never
close the AsyncExitStack from a different task.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from hermit.core.budgets import get_runtime_budget
from hermit.core.tools import ToolSpec
from hermit.plugin.base import McpServerSpec

log = structlog.get_logger()

MCP_TOOL_PREFIX = "mcp__"
MCP_TOOL_SEP = "__"


def _sanitize_http_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    """Drop invalid or empty HTTP headers before constructing the client."""
    sanitized: dict[str, str] = {}
    for raw_key, raw_value in (headers or {}).items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if not key or not value:
            continue
        if key.lower() == "authorization":
            lower = value.lower()
            if lower == "bearer" or (lower.startswith("bearer ") and not value[7:].strip()):
                continue
        sanitized[key] = value
    return sanitized


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{server_name}{MCP_TOOL_SEP}{tool_name}"


def parse_mcp_tool_name(full_name: str) -> tuple[str, str]:
    """Extract (server_name, tool_name) from 'mcp__server__tool'."""
    if not full_name.startswith(MCP_TOOL_PREFIX):
        raise ValueError(f"Not an MCP tool name: {full_name}")
    rest = full_name[len(MCP_TOOL_PREFIX):]
    server, _, tool = rest.partition(MCP_TOOL_SEP)
    if not tool:
        raise ValueError(f"Invalid MCP tool name: {full_name}")
    return server, tool


@dataclass
class _ServerConnection:
    spec: McpServerSpec
    session: ClientSession | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)


class McpClientManager:
    """Manages connections to multiple MCP servers.

    Runs all async MCP operations on a dedicated background event loop so
    synchronous tool handlers can call MCP without blocking or conflicting
    with the caller's event loop.

    The entire connection lifecycle (enter → hold → exit) runs in a single
    ``lifecycle()`` coroutine so that anyio cancel scopes are always entered
    and exited in the same asyncio Task.
    """

    def __init__(self) -> None:
        self._connections: dict[str, _ServerConnection] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-event-loop",
        )
        self._thread.start()
        # Set by lifecycle() once it is running inside the background loop
        self._shutdown_event: asyncio.Event | None = None
        # Future for the lifecycle() coroutine itself
        self._lifecycle_future: "asyncio.Future[None] | None" = None

    def _run_async(self, coro: Any, timeout: float = 60) -> Any:
        """Submit a coroutine to the background loop and wait synchronously."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── public sync API (called from main thread) ──────────────────

    def connect_all_sync(self, specs: list[McpServerSpec]) -> None:
        """Connect to all MCP servers synchronously.

        Starts the lifecycle coroutine in the background loop and blocks until
        all connections are attempted. Individual MCP failures are logged and
        must not interrupt the main Hermit process.
        """
        ready = threading.Event()

        async def lifecycle() -> None:
            # asyncio.Event must be created inside the target loop
            self._shutdown_event = asyncio.Event()
            try:
                async with AsyncExitStack() as stack:
                    for spec in specs:
                        try:
                            await self._connect_one(spec, stack)
                        except Exception:
                            log.exception("mcp_connect_error", server=spec.name)
                    # Unblock connect_all_sync — connections are ready
                    ready.set()
                    # Hold all context managers open until shutdown is signalled
                    await self._shutdown_event.wait()
                # AsyncExitStack.__aexit__ runs here, in the same task
            except Exception:
                log.exception("mcp_lifecycle_error")
                ready.set()

        self._lifecycle_future = asyncio.run_coroutine_threadsafe(lifecycle(), self._loop)
        ready.wait(timeout=get_runtime_budget().provider_read_timeout)

    def get_tool_specs(self) -> list[ToolSpec]:
        """Return ToolSpec instances for all discovered MCP tools."""
        specs: list[ToolSpec] = []
        for server_name, conn in self._connections.items():
            for tool in conn.tools:
                full_name = mcp_tool_name(server_name, tool["name"])

                def _make_handler(sn: str, tn: str):
                    def handler(payload: dict[str, Any]) -> str:
                        budget = get_runtime_budget()
                        return self._run_async(
                            self._call_tool(sn, tn, payload),
                            timeout=budget.tool_hard_deadline,
                        )
                    return handler

                specs.append(ToolSpec(
                    name=full_name,
                    description=f"[MCP:{server_name}] {tool['description']}",
                    input_schema=tool.get("input_schema", {
                        "type": "object", "properties": {}, "required": [],
                    }),
                    handler=_make_handler(server_name, tool["name"]),
                ))
        return specs

    def close_all_sync(self) -> None:
        """Disconnect all servers and shut down the background loop."""
        if not self._loop.is_running():
            self._connections.clear()
            return

        # Signal the lifecycle coroutine to exit its AsyncExitStack (same task)
        if self._shutdown_event is not None:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)

        if self._lifecycle_future is not None:
            try:
                self._lifecycle_future.result(timeout=15)
            except Exception:
                log.exception("mcp_close_error")

        self._connections.clear()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ── async internals (run on background loop) ───────────────────

    async def _connect_one(self, spec: McpServerSpec, stack: AsyncExitStack) -> None:
        """Enter all context managers for one MCP server into *stack*."""
        if spec.transport == "stdio":
            if not spec.command:
                log.error("mcp_missing_command", server=spec.name)
                return
            params = StdioServerParameters(
                command=spec.command[0],
                args=spec.command[1:] if len(spec.command) > 1 else [],
                env=spec.env,
            )
            transport = await stack.enter_async_context(stdio_client(params))
        elif spec.transport == "http":
            if not spec.url:
                log.error("mcp_missing_url", server=spec.name)
                return
            import httpx
            headers = _sanitize_http_headers(spec.headers)
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers=headers)
            )
            transport = await stack.enter_async_context(
                streamable_http_client(spec.url, http_client=http_client)
            )
        else:
            log.error("mcp_unknown_transport", server=spec.name, transport=spec.transport)
            return

        read_stream, write_stream = transport[0], transport[1]
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        tools_result = await session.list_tools()
        raw_tools = []
        for tool in tools_result.tools:
            if spec.allowed_tools and tool.name not in spec.allowed_tools:
                continue
            raw_tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            })

        conn = _ServerConnection(spec=spec, session=session, tools=raw_tools)
        self._connections[spec.name] = conn
        log.info(
            "mcp_server_connected",
            server=spec.name,
            transport=spec.transport,
            tools=[t["name"] for t in raw_tools],
        )

    async def _call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any],
    ) -> Any:
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            return f"Error: MCP server '{server_name}' not connected"
        try:
            result = await conn.session.call_tool(tool_name, arguments)
            if result.isError:
                parts = []
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                return f"Error: {' '.join(parts)}" if parts else "Error: MCP tool failed"

            if result.structuredContent is not None:
                structured = result.structuredContent
                if isinstance(structured, dict) and "_hermit_observation" in structured:
                    return structured
                return json.dumps(structured, ensure_ascii=True, indent=2)
            parts = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "\n".join(parts) if parts else "(no output)"
        except Exception as exc:
            log.error("mcp_call_error", server=server_name, tool=tool_name, error=str(exc))
            return f"Error calling MCP tool {server_name}/{tool_name}: {exc}"
