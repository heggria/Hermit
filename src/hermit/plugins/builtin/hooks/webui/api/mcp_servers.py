"""WebUI API router for user MCP server configuration management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner

_log = structlog.get_logger()

router = APIRouter(tags=["mcp-servers"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class McpServerCreateRequest(BaseModel):
    name: str
    transport: str  # "stdio" | "http"
    description: str = ""
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    auth: dict[str, str] | None = None  # {"type": "api_key"|"oauth", ...}


class McpServerUpdateRequest(BaseModel):
    description: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    allowed_tools: list[str] | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mcp_json_path() -> Path:
    """Get the mcp.json path for the current environment."""
    runner = get_runner()
    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Settings not available")
    return settings.base_dir / "mcp.json"


def _read_mcp_json() -> dict[str, Any]:
    path = _mcp_json_path()
    if not path.is_file():
        return {"mcpServers": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "mcpServers" not in data:
            data["mcpServers"] = {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("mcp_json_read_error", path=str(path), error=str(exc))
        return {"mcpServers": {}}


def _write_mcp_json(data: dict[str, Any]) -> None:
    path = _mcp_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _build_entry(body: McpServerCreateRequest | McpServerUpdateRequest) -> dict[str, Any]:
    """Build a mcp.json entry dict from request body."""
    entry: dict[str, Any] = {}

    if isinstance(body, McpServerCreateRequest):
        transport = body.transport
    else:
        # For update, determine transport from fields present
        transport = "http" if body.url is not None else "stdio"

    if body.description is not None and body.description:
        entry["description"] = body.description

    if transport == "stdio":
        if body.command is not None:
            entry["command"] = body.command
        if body.args is not None:
            entry["args"] = body.args
        if body.env is not None:
            entry["env"] = body.env
    else:
        if body.url is not None:
            entry["url"] = body.url
        if body.headers is not None:
            entry["headers"] = body.headers

    if body.allowed_tools is not None:
        entry["allowedTools"] = body.allowed_tools

    if isinstance(body, McpServerCreateRequest) and body.auth is not None:
        entry["_auth"] = body.auth

    return entry


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/mcp-servers")
def create_mcp_server(body: McpServerCreateRequest) -> dict[str, Any]:
    """Create a user MCP server configuration."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")

    if body.transport == "stdio" and not body.command:
        raise HTTPException(status_code=422, detail="Command is required for stdio transport")
    if body.transport == "http" and not body.url:
        raise HTTPException(status_code=422, detail="URL is required for HTTP transport")

    data = _read_mcp_json()
    if name in data["mcpServers"]:
        raise HTTPException(status_code=409, detail=f"MCP server '{name}' already exists")

    entry = _build_entry(body)
    data["mcpServers"][name] = entry

    try:
        _write_mcp_json(data)
    except Exception as exc:
        _log.exception("mcp_server_create_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

    return {"name": name, "needs_reload": True}


@router.patch("/mcp-servers/{name}")
def update_mcp_server(name: str, body: McpServerUpdateRequest) -> dict[str, Any]:
    """Update a user MCP server configuration."""
    data = _read_mcp_json()
    if name not in data["mcpServers"]:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found in user config")

    existing = data["mcpServers"][name]

    # Merge updates into existing entry
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"name": name, "needs_reload": False}

    if "description" in updates:
        existing["description"] = updates["description"]
    if "command" in updates:
        existing["command"] = updates["command"]
    if "args" in updates:
        existing["args"] = updates["args"]
    if "env" in updates:
        existing["env"] = updates["env"]
    if "url" in updates:
        existing["url"] = updates["url"]
    if "headers" in updates:
        existing["headers"] = updates["headers"]
    if "allowed_tools" in updates:
        existing["allowedTools"] = updates["allowed_tools"]

    data["mcpServers"][name] = existing

    try:
        _write_mcp_json(data)
    except Exception as exc:
        _log.exception("mcp_server_update_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

    return {"name": name, "needs_reload": True}


@router.delete("/mcp-servers/{name}")
def delete_mcp_server(name: str) -> dict[str, Any]:
    """Delete a user MCP server configuration."""
    data = _read_mcp_json()
    if name not in data["mcpServers"]:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found in user config")

    del data["mcpServers"][name]

    try:
        _write_mcp_json(data)
    except Exception as exc:
        _log.exception("mcp_server_delete_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

    return {"name": name, "status": "deleted", "needs_reload": True}


@router.post("/mcp-servers/reload")
def reload_mcp_servers() -> dict[str, Any]:
    """Reload MCP server connections from mcp.json."""
    runner = get_runner()
    pm = runner.pm
    reload_fn = getattr(pm, "reload_user_mcp_servers", None)
    if reload_fn is None:
        raise HTTPException(status_code=501, detail="MCP reload not supported")
    try:
        reload_fn()
    except Exception as exc:
        _log.exception("mcp_reload_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Reload failed: {exc}") from exc
    return {"status": "ok"}


# ------------------------------------------------------------------
# Auth endpoints
# ------------------------------------------------------------------


class EnvUpdateRequest(BaseModel):
    key: str
    value: str


@router.patch("/mcp-servers/{name}/env")
def update_mcp_server_env(name: str, body: EnvUpdateRequest) -> dict[str, Any]:
    """Update a single environment variable for a user MCP server."""
    data = _read_mcp_json()
    if name not in data["mcpServers"]:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    entry = data["mcpServers"][name]
    env = entry.get("env") or {}
    env[body.key] = body.value
    entry["env"] = env
    data["mcpServers"][name] = entry

    try:
        _write_mcp_json(data)
    except Exception as exc:
        _log.exception("mcp_env_update_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

    return {"name": name, "needs_reload": True}


class OAuthStartRequest(BaseModel):
    server_url: str | None = None


@router.post("/mcp-servers/{name}/oauth/start")
def start_mcp_oauth(name: str, body: OAuthStartRequest | None = None) -> dict[str, Any]:
    """Start an OAuth2 authorization flow for an HTTP MCP server."""
    runner = get_runner()
    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Settings not available")

    # Resolve server URL from config or request body
    server_url = body.server_url if body and body.server_url else None
    if not server_url:
        data = _read_mcp_json()
        entry = data.get("mcpServers", {}).get(name, {})
        server_url = entry.get("url")
    if not server_url:
        raise HTTPException(status_code=422, detail="Server URL required for OAuth flow")

    # Build callback URL
    from hermit.plugins.builtin.hooks.webui.api.deps import get_server

    webui = get_server()
    port = getattr(webui, "_port", 8323)
    callback_url = f"http://127.0.0.1:{port}/api/mcp-servers/oauth/callback"

    try:
        from hermit.runtime.capability.resolver.mcp_oauth import McpOAuthManager

        manager = McpOAuthManager(settings.base_dir)
        auth_url = manager.start_oauth_flow(name, server_url, callback_url)
    except Exception as exc:
        _log.exception("mcp_oauth_start_error", server=name, error=str(exc))
        raise HTTPException(status_code=500, detail=f"OAuth start failed: {exc}") from exc

    return {"auth_url": auth_url}


@router.get("/mcp-servers/oauth/callback")
def oauth_callback(code: str = "", state: str = "") -> Any:
    """Handle OAuth provider redirect callback."""
    from fastapi.responses import HTMLResponse

    if not code or not state:
        return HTMLResponse(
            "<html><body><h2>Missing code or state</h2></body></html>",
            status_code=400,
        )

    runner = get_runner()
    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        return HTMLResponse(
            "<html><body><h2>Settings not available</h2></body></html>",
            status_code=503,
        )

    try:
        from hermit.runtime.capability.resolver.mcp_oauth import McpOAuthManager

        manager = McpOAuthManager(settings.base_dir)
        server_name = manager.complete_oauth_flow(state, code)
    except Exception as exc:
        _log.exception("mcp_oauth_callback_error", error=str(exc))
        return HTMLResponse(
            f"<html><body><h2>Authorization failed</h2><p>{exc}</p></body></html>",
            status_code=400,
        )

    # Trigger reload in background
    pm = runner.pm
    reload_fn = getattr(pm, "reload_user_mcp_servers", None)
    if reload_fn:
        try:
            reload_fn()
        except Exception:
            _log.exception("mcp_oauth_reload_after_callback")

    # Return HTML that notifies the opener and closes
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Authorization Complete</title></head>
<body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0;background:#faf9f7">
<div style="text-align:center">
<h2 style="color:#333">Authorization Successful</h2>
<p style="color:#666">{server_name} is now connected. You can close this window.</p>
</div>
<script>
if (window.opener) {{
  window.opener.postMessage({{ type: 'mcp-oauth-complete', server: '{server_name}' }}, '*');
}}
setTimeout(function() {{ window.close(); }}, 2000);
</script>
</body></html>"""
    return HTMLResponse(html)


@router.delete("/mcp-servers/{name}/oauth")
def clear_mcp_oauth(name: str) -> dict[str, Any]:
    """Clear stored OAuth tokens for an MCP server."""
    runner = get_runner()
    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Settings not available")

    from hermit.runtime.capability.resolver.mcp_oauth import McpOAuthManager

    manager = McpOAuthManager(settings.base_dir)
    manager.clear_token(name)
    return {"name": name, "status": "cleared", "needs_reload": True}
