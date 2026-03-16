from __future__ import annotations

from hermit.plugin.base import PluginContext, SubagentSpec


def register(ctx: PluginContext) -> None:
    ctx.add_subagent(
        SubagentSpec(
            name="researcher",
            description="Research a topic using web search and synthesize findings into a structured report.",
            system_prompt=(
                "You are a research specialist. "
                "Use web_search to find relevant information, web_fetch to read full articles, "
                "and synthesize findings into concise, well-cited summaries."
            ),
            tools=["web_search", "web_fetch", "bash", "read_file"],
        )
    )
    ctx.add_subagent(
        SubagentSpec(
            name="coder",
            description="Write, review, refactor, or debug code with best practices.",
            system_prompt=(
                "You are a coding specialist. "
                "Write clean, tested, well-documented code. Follow existing project conventions. "
                "Use web_search and web_fetch to look up documentation when needed."
            ),
            tools=["web_search", "web_fetch", "read_file", "write_file", "bash"],
        )
    )
