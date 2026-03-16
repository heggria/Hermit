"""Builtin web tools: web_search + web_fetch.

Pure stdlib — DuckDuckGo Lite HTML scraping for search,
urllib for fetching, html.parser for extraction.
Zero external dependencies.
"""

from __future__ import annotations

from hermit.plugins.builtin.tools.web_tools.fetch import handle_fetch
from hermit.plugins.builtin.tools.web_tools.search import handle_search
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="web_search",
            description=(
                "Search the web and return real search results. "
                "Supports any query: facts, weather, news, documentation, code, etc. "
                "Returns top search results with titles, snippets, and URLs. "
                "For recent events or breaking news, set search_type='news' and/or time_filter='day' or 'week' "
                "to filter results by recency and get the latest information."
            ),
            description_key="tools.web.search.description",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description_key": "tools.web.search.query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description_key": "tools.web.search.max_results",
                    },
                    "region": {
                        "type": "string",
                        "description_key": "tools.web.search.region",
                    },
                    "time_filter": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description_key": "tools.web.search.time_filter",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["web", "news"],
                        "description_key": "tools.web.search.search_type",
                    },
                },
                "required": ["query"],
            },
            handler=handle_search,
            readonly=True,
            action_class="network_read",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="web_fetch",
            description=(
                "Fetch a web page by URL and return its text content in a readable format. "
                "Strips HTML tags, scripts, styles, and extracts the main text. "
                "Useful for reading articles, documentation, API references, blog posts, etc."
            ),
            description_key="tools.web.fetch.description",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description_key": "tools.web.fetch.url",
                    },
                    "max_length": {
                        "type": "integer",
                        "description_key": "tools.web.fetch.max_length",
                    },
                },
                "required": ["url"],
            },
            handler=handle_fetch,
            readonly=True,
            action_class="network_read",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
