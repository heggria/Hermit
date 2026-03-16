from __future__ import annotations

from typing import Any, cast

import structlog

from hermit.runtime.capability.contracts.base import McpServerSpec, McpToolGovernance, PluginContext

log = structlog.get_logger()
DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"

_GITHUB_READ_TOOLS = {
    "get_commit",
    "get_file_contents",
    "get_label",
    "get_latest_release",
    "get_me",
    "get_release_by_tag",
    "get_tag",
    "get_team_members",
    "get_teams",
    "issue_read",
    "list_branches",
    "list_commits",
    "list_issue_types",
    "list_issues",
    "list_pull_requests",
    "list_releases",
    "list_tags",
    "pull_request_read",
    "search_code",
    "search_issues",
    "search_pull_requests",
    "search_repositories",
    "search_users",
}

_GITHUB_MUTATION_TOOLS = {
    "add_comment_to_pending_review",
    "add_issue_comment",
    "add_reply_to_pull_request_comment",
    "assign_copilot_to_issue",
    "create_branch",
    "create_or_update_file",
    "create_pull_request",
    "create_pull_request_with_copilot",
    "create_repository",
    "delete_file",
    "fork_repository",
    "get_copilot_job_status",
    "issue_write",
    "merge_pull_request",
    "pull_request_review_write",
    "push_files",
    "request_copilot_review",
    "sub_issue_write",
    "update_pull_request",
    "update_pull_request_branch",
}

_GITHUB_TOOL_GOVERNANCE = {
    **{
        name: McpToolGovernance(
            action_class="network_read",
            risk_hint="low",
            requires_receipt=False,
            readonly=True,
        )
        for name in sorted(_GITHUB_READ_TOOLS)
    },
    **{
        name: McpToolGovernance(
            action_class="external_mutation",
            risk_hint="high",
            requires_receipt=True,
        )
        for name in sorted(_GITHUB_MUTATION_TOOLS)
    },
}


def _build_github_spec(ctx: PluginContext | None = None) -> McpServerSpec:
    token = ""
    url = DEFAULT_GITHUB_MCP_URL
    headers: dict[str, str] = {}

    if ctx is not None:
        token = str(ctx.get_var("github_pat", "") or "").strip()
        url = str(ctx.config.get("url", "") or "").strip() or DEFAULT_GITHUB_MCP_URL
        raw_headers = ctx.config.get("headers", {})
        if isinstance(raw_headers, dict):
            headers = {
                str(key): str(value)
                for key, value in cast(dict[str, Any], raw_headers).items()
                if value
            }
    else:
        import os

        token = (
            os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
            or os.getenv("GITHUB_PAT", "").strip()
            or os.getenv("GITHUB_TOKEN", "").strip()
        )
        url = os.getenv("GITHUB_MCP_URL", DEFAULT_GITHUB_MCP_URL).strip() or DEFAULT_GITHUB_MCP_URL
        if token:
            headers["Authorization"] = f"Bearer {token}"

    if not token:
        log.warning(
            "github_mcp_missing_token",
            plugin="github",
            variable="github_pat",
            message="GitHub MCP may fail to authenticate without a PAT",
        )

    return McpServerSpec(
        name="github",
        description="GitHub MCP server for issues, pull requests, repository search, and file reads",
        transport="http",
        url=url,
        headers=headers or None,
        allowed_tools=sorted(_GITHUB_TOOL_GOVERNANCE),
        tool_governance=dict(_GITHUB_TOOL_GOVERNANCE),
    )


def register(ctx: PluginContext) -> None:
    ctx.add_mcp(_build_github_spec(ctx))
