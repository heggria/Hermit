from __future__ import annotations

from typing import Any, Optional

from hermit.core.tools import ToolRegistry
from hermit.provider.messages import normalize_block as _normalize_block
from hermit.provider.providers.claude import ClaudeProvider
from hermit.provider.runtime import (
    AgentResult,
    AgentRuntime,
    ToolCallback,
    ToolStartCallback,
    truncate_middle_text,
)

__all__ = [
    "AgentResult",
    "ClaudeAgent",
    "ToolCallback",
    "ToolStartCallback",
    "_normalize_block",
    "truncate_middle_text",
]


class ClaudeAgent(AgentRuntime):
    """Compatibility wrapper around the provider-backed runtime."""

    def __init__(
        self,
        client: Any,
        registry: ToolRegistry,
        model: str,
        max_tokens: int = 2048,
        max_turns: int = 10,
        tool_output_limit: int = 4000,
        thinking_budget: int = 0,
        system_prompt: Optional[str] = None,
    ) -> None:
        super().__init__(
            provider=ClaudeProvider(client, model=model, system_prompt=system_prompt),
            registry=registry,
            model=model,
            max_tokens=max_tokens,
            max_turns=max_turns,
            tool_output_limit=tool_output_limit,
            thinking_budget=thinking_budget,
            system_prompt=system_prompt,
        )
