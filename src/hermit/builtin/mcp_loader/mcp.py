"""Load MCP server configurations from .mcp.json files.

Searches two locations (later wins on conflict):
  1. ~/.hermit/mcp.json  (global)
  2. <cwd>/.mcp.json         (project-level)

Format is compatible with Claude Code / Cursor:
    {
      "mcpServers": {
        "server-name": {
          "command": "npx",
          "args": ["-y", "pkg-name"],
          "env": {"KEY": "val"}
        },
        "remote": {
          "url": "https://...",
          "headers": {"Authorization": "Bearer ..."}
        }
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.plugin.base import McpServerSpec, McpToolGovernance, PluginContext

log = structlog.get_logger()


def _load_mcp_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("mcp_config_error", path=str(path), error=str(exc))
        return {}


def _parse_server_entry(name: str, entry: dict[str, Any]) -> McpServerSpec | None:
    tool_governance = _parse_tool_governance(entry.get("toolGovernance"))
    if "command" in entry:
        cmd = entry["command"]
        raw_args = entry.get("args", [])
        command = [cmd] + cast(list[Any], raw_args) if isinstance(raw_args, list) else [cmd]
        return McpServerSpec(
            name=name,
            description=entry.get("description", f"MCP server: {name}"),
            transport="stdio",
            command=command,
            env=entry.get("env"),
            allowed_tools=entry.get("allowedTools"),
            tool_governance=tool_governance,
        )
    elif "url" in entry:
        return McpServerSpec(
            name=name,
            description=entry.get("description", f"MCP server: {name}"),
            transport="http",
            url=entry["url"],
            headers=entry.get("headers"),
            allowed_tools=entry.get("allowedTools"),
            tool_governance=tool_governance,
        )
    else:
        log.warning("mcp_config_skip", server=name, reason="no 'command' or 'url'")
        return None


def _parse_tool_governance(raw: Any) -> dict[str, McpToolGovernance]:
    if not isinstance(raw, dict):
        return {}
    raw_dict = cast(dict[str, Any], raw)
    parsed: dict[str, McpToolGovernance] = {}
    for tool_name, payload in raw_dict.items():
        if not isinstance(payload, dict):
            continue
        payload_dict = cast(dict[str, Any], payload)
        action_class = payload_dict.get("actionClass", payload_dict.get("action_class", ""))
        risk_hint = payload_dict.get("riskHint", payload_dict.get("risk_hint", ""))
        requires_receipt = payload_dict.get(
            "requiresReceipt", payload_dict.get("requires_receipt", False)
        )
        readonly = payload_dict.get("readonly", False)
        supports_preview = payload_dict.get(
            "supportsPreview", payload_dict.get("supports_preview", False)
        )
        parsed[str(tool_name)] = McpToolGovernance(
            action_class=str(action_class or ""),
            risk_hint=str(risk_hint or ""),
            requires_receipt=bool(requires_receipt),
            readonly=bool(readonly),
            supports_preview=bool(supports_preview),
        )
    return parsed


def register(ctx: PluginContext) -> None:
    """Discover .mcp.json configs and register MCP servers."""
    search_paths: list[Path] = []

    if ctx.settings is not None:
        base_dir = getattr(ctx.settings, "base_dir", None)
        if base_dir:
            search_paths.append(Path(base_dir) / "mcp.json")

    search_paths.append(Path.cwd() / ".mcp.json")

    servers: dict[str, McpServerSpec] = {}
    for config_path in search_paths:
        data = _load_mcp_json(config_path)
        raw_mcp_servers = data.get("mcpServers", {})
        if not isinstance(raw_mcp_servers, dict):
            continue
        mcp_servers = cast(dict[str, Any], raw_mcp_servers)
        for name, entry in mcp_servers.items():
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            spec = _parse_server_entry(name, entry_dict)
            if spec:
                servers[name] = spec
                log.info("mcp_config_loaded", server=name, source=str(config_path))

    for spec in servers.values():
        ctx.add_mcp(spec)
