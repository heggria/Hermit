from __future__ import annotations

import os

import structlog

from hermit.plugin.base import McpServerSpec, PluginContext

log = structlog.get_logger()

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
TOKEN_ENV_KEYS = (
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GITHUB_PAT",
    "GITHUB_TOKEN",
)


def _resolve_github_pat() -> str | None:
    for key in TOKEN_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return None


def _build_github_spec() -> McpServerSpec:
    headers: dict[str, str] = {}
    token = _resolve_github_pat()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        log.warning(
            "github_mcp_missing_token",
            env_keys=list(TOKEN_ENV_KEYS),
            message="GitHub MCP may fail to authenticate without a PAT",
        )

    return McpServerSpec(
        name="github",
        description="GitHub MCP server for issues, pull requests, repository search, and file reads",
        transport="http",
        url=os.getenv("GITHUB_MCP_URL", DEFAULT_GITHUB_MCP_URL).strip() or DEFAULT_GITHUB_MCP_URL,
        headers=headers or None,
    )


def register(ctx: PluginContext) -> None:
    ctx.add_mcp(_build_github_spec())
