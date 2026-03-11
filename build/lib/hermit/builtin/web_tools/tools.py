"""Builtin web tools: web_search + web_fetch.

Pure stdlib — DuckDuckGo Lite HTML scraping for search,
urllib for fetching, html.parser for extraction.
Zero external dependencies.
"""
from __future__ import annotations

from hermit.builtin.web_tools.fetch import handle_fetch
from hermit.builtin.web_tools.search import handle_search
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_tool(ToolSpec(
        name="web_search",
        description=(
            "Search the web and return real search results. "
            "Supports any query: facts, weather, news, documentation, code, etc. "
            "Returns top search results with titles, snippets, and URLs. "
            "For recent events or breaking news, set search_type='news' and/or time_filter='day' or 'week' "
            "to filter results by recency and get the latest information."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 8, max: 20)",
                },
                "region": {
                    "type": "string",
                    "description": "Region code, e.g. 'us-en', 'cn-zh', 'wt-wt' (worldwide, default)",
                },
                "time_filter": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                    "description": (
                        "Filter results by recency. Use 'day' for news from the past 24 hours, "
                        "'week' for the past 7 days, 'month' for the past 30 days, "
                        "'year' for the past year. Omit for all-time results."
                    ),
                },
                "search_type": {
                    "type": "string",
                    "enum": ["web", "news"],
                    "description": (
                        "Search mode. Use 'news' to search the news index for current events "
                        "and breaking news (recommended for recent events). Default is 'web'."
                    ),
                },
            },
            "required": ["query"],
        },
        handler=handle_search,
        readonly=True,
    ))

    ctx.add_tool(ToolSpec(
        name="web_fetch",
        description=(
            "Fetch a web page by URL and return its text content in a readable format. "
            "Strips HTML tags, scripts, styles, and extracts the main text. "
            "Useful for reading articles, documentation, API references, blog posts, etc."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max characters to return (default: 20000)",
                },
            },
            "required": ["url"],
        },
        handler=handle_fetch,
        readonly=True,
    ))
