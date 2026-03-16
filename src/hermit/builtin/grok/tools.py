"""Grok plugin — registers grok_search tool."""

from __future__ import annotations

from hermit.builtin.grok.search import handle_grok_search
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="grok_search",
            description=(
                "Search the web using Grok (xAI) with real-time live search. "
                "Unlike web_search (DuckDuckGo), Grok directly reads and synthesizes "
                "current web content — ideal for breaking news, stock prices, recent events, "
                "or any query where freshness matters. "
                "Returns a comprehensive answer with inline citations. "
                "Requires XAI_API_KEY to be set."
            ),
            description_key="tools.grok.search.description",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description_key": "tools.grok.search.query",
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["auto", "on", "off"],
                        "description_key": "tools.grok.search.search_mode",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description_key": "tools.grok.search.max_tokens",
                    },
                },
                "required": ["query"],
            },
            handler=handle_grok_search,
            readonly=True,
            action_class="network_read",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
