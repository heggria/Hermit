"""Grok plugin — registers grok_search tool."""
from __future__ import annotations

from hermit.builtin.grok.search import handle_grok_search
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_tool(ToolSpec(
        name="grok_search",
        description=(
            "Search the web using Grok (xAI) with real-time live search. "
            "Unlike web_search (DuckDuckGo), Grok directly reads and synthesizes "
            "current web content — ideal for breaking news, stock prices, recent events, "
            "or any query where freshness matters. "
            "Returns a comprehensive answer with inline citations. "
            "Requires XAI_API_KEY to be set."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The question or search query. Can be a natural-language question "
                        "like 'Why did MiniMax stock surge today?' or a search phrase."
                    ),
                },
                "search_mode": {
                    "type": "string",
                    "enum": ["auto", "on", "off"],
                    "description": (
                        "Live search mode. 'auto' (default) lets Grok decide when to search; "
                        "'on' forces live search; 'off' uses Grok's training data only."
                    ),
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens in response (default: 2048).",
                },
            },
            "required": ["query"],
        },
        handler=handle_grok_search,
        readonly=True,
    ))
