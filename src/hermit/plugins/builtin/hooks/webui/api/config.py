"""WebUI API — configuration and environment info endpoints."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner

router = APIRouter()

_start_time: float = time.time()


@router.get("/config/status")
async def status() -> dict:
    """Return basic environment info and uptime."""
    return {
        "host": os.environ.get("HERMIT_HOST", "127.0.0.1"),
        "port": int(os.environ.get("HERMIT_PORT", "8323")),
        "uptime_start": _start_time,
        "uptime_seconds": time.time() - _start_time,
        "pid": os.getpid(),
    }


@router.get("/config/plugins")
async def list_plugins() -> list:
    """Return loaded plugin manifests."""
    runner = get_runner()
    items = getattr(runner.pm, "manifests", None) or getattr(runner.pm, "_manifests", [])
    result = []
    for m in items:
        result.append(
            {
                "name": getattr(m, "name", ""),
                "version": getattr(m, "version", ""),
                "description": getattr(m, "description", ""),
                "builtin": getattr(m, "builtin", False),
            }
        )
    return result


@router.get("/config/mcp-servers")
async def list_mcp_servers() -> list:
    """Return available MCP server specs with connection status and tools."""
    runner = get_runner()
    specs = getattr(runner.pm, "mcp_specs", []) or getattr(runner.pm, "_all_mcp", [])
    mcp_mgr = getattr(runner.pm, "_mcp_manager", None)

    # Determine which servers come from user mcp.json
    user_servers = _get_user_mcp_servers(runner)
    user_server_names = set(user_servers.keys())

    result = []
    seen_names: set[str] = set()
    for spec in specs:
        name = getattr(spec, "name", "")
        seen_names.add(name)
        info: dict = {
            "name": name,
            "description": getattr(spec, "description", ""),
            "transport": getattr(spec, "transport", ""),
            "connected": False,
            "tools": [],
            "source": "user" if name in user_server_names else "builtin",
        }
        # Include config fields for user-defined servers
        if name in user_server_names:
            info["command"] = getattr(spec, "command", None)
            info["args"] = None
            if info["command"] and len(info["command"]) > 1:
                info["args"] = info["command"][1:]
                info["command"] = info["command"][0]
            elif info["command"]:
                info["command"] = info["command"][0]
            info["env"] = getattr(spec, "env", None)
            info["url"] = getattr(spec, "url", None)
            info["headers"] = getattr(spec, "headers", None)
            info["allowedTools"] = getattr(spec, "allowed_tools", None)

        if mcp_mgr is not None:
            lock = getattr(mcp_mgr, "_connections_lock", None)
            connections = getattr(mcp_mgr, "_connections", {})
            if lock is not None:
                with lock:
                    conn = connections.get(spec.name)
            else:
                conn = connections.get(spec.name)
            if conn is not None:
                info["connected"] = getattr(conn, "session", None) is not None
                for t in getattr(conn, "tools", []):
                    info["tools"].append(
                        {
                            "name": t.get("name", "")
                            if isinstance(t, dict)
                            else getattr(t, "name", ""),
                            "description": (
                                t.get("description", "")
                                if isinstance(t, dict)
                                else getattr(t, "description", "")
                            ),
                        }
                    )
        result.append(info)

    # Add user-configured servers from mcp.json that aren't loaded by runtime
    for name, entry in user_servers.items():
        if name in seen_names:
            continue
        transport = "http" if "url" in entry else "stdio"
        info = {
            "name": name,
            "description": entry.get("description", ""),
            "transport": transport,
            "connected": False,
            "tools": [],
            "source": "user",
        }
        if transport == "stdio":
            cmd = entry.get("command", "")
            args = entry.get("args", [])
            info["command"] = cmd
            info["args"] = args if args else None
            info["env"] = entry.get("env")
        else:
            info["url"] = entry.get("url")
            info["headers"] = entry.get("headers")
        info["allowedTools"] = entry.get("allowedTools")
        result.append(info)

    # Enrich with auth status
    _enrich_auth_status(result, user_servers, runner)

    return result


def _enrich_auth_status(
    result: list[dict],
    user_servers: dict[str, Any],
    runner: Any,
) -> None:
    """Add auth_type, has_empty_env_keys, has_oauth_token to each server info."""
    settings = getattr(runner.pm, "settings", None)
    oauth_manager = None
    if settings is not None:
        try:
            from hermit.runtime.capability.resolver.mcp_oauth import McpOAuthManager

            oauth_manager = McpOAuthManager(settings.base_dir)
        except Exception:
            pass

    for info in result:
        name = info.get("name", "")
        entry = user_servers.get(name, {})

        # Auth type from _auth metadata stored in mcp.json
        auth_meta = entry.get("_auth")
        if isinstance(auth_meta, dict):
            info["auth_type"] = auth_meta.get("type")
            info["auth_token_url"] = auth_meta.get("token_url")
            info["auth_env_key"] = auth_meta.get("env_key")
        elif info.get("transport") == "http" and entry.get("url"):
            # HTTP MCP servers without explicit _auth default to OAuth.
            info["auth_type"] = "oauth"
            info["auth_token_url"] = None
            info["auth_env_key"] = None
        else:
            info["auth_type"] = None
            info["auth_token_url"] = None
            info["auth_env_key"] = None

        # Detect empty env keys
        env = entry.get("env") or info.get("env") or {}
        empty_keys = [k for k, v in env.items() if isinstance(v, str) and not v.strip()]
        info["has_empty_env_keys"] = empty_keys if empty_keys else []

        # Check for stored OAuth token
        info["has_oauth_token"] = oauth_manager.has_token(name) if oauth_manager else False


def _get_user_mcp_servers(runner: Any) -> dict[str, Any]:
    """Get MCP server entries defined in the user's mcp.json."""
    import json

    settings = getattr(runner.pm, "settings", None)
    if settings is None:
        return {}
    mcp_path = settings.base_dir / "mcp.json"
    if not mcp_path.is_file():
        return {}
    try:
        with open(mcp_path, encoding="utf-8") as f:
            data = json.load(f)
        servers = data.get("mcpServers", {})
        return servers if isinstance(servers, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


@router.get("/config/skills")
async def list_skills() -> list:
    """Return available skill definitions."""
    runner = get_runner()
    skills = getattr(runner.pm, "_all_skills", [])

    # Determine user skills directory
    settings = getattr(runner.pm, "settings", None)
    user_skills_dir = settings.skills_dir if settings else None

    return [
        {
            "name": getattr(s, "name", ""),
            "description": getattr(s, "description", ""),
            "source": (
                "user"
                if user_skills_dir and str(getattr(s, "path", "")).startswith(str(user_skills_dir))
                else "builtin"
            ),
            "content": getattr(s, "content", ""),
            "max_tokens": getattr(s, "max_tokens", None),
        }
        for s in skills
    ]


@router.get("/config/schedules")
async def list_schedules() -> list:
    """Return scheduled job specs if available."""
    from hermit.plugins.builtin.hooks.webui.api.deps import get_store

    store = get_store()
    fn = getattr(store, "list_schedules", None)
    if fn is None:
        return []
    schedules = fn()
    return [s.__dict__ for s in schedules]
