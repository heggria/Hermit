from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

Worker = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


@dataclass
class SharedState:
    messages: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    route: str = "direct"
    research: str | None = None
    code: str | None = None
    final: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "messages": list(self.messages),
            "route": self.route,
            "research": self.research,
            "code": self.code,
            "final": self.final,
        }


class SimpleOrchestrator:
    """Minimal async state machine for future multi-agent expansion."""

    def __init__(self, researcher: Worker, coder: Worker) -> None:
        self.researcher = researcher
        self.coder = coder

    async def run(self, state: SharedState) -> SharedState:
        payload = state.to_dict()

        if state.route == "research":
            payload = await self.researcher(payload)
        elif state.route == "code":
            payload = await self.coder(payload)

        return SharedState(
            messages=list(cast(list[Any], payload.get("messages") or [])),
            route=str(payload.get("route", "direct")),
            research=cast(str | None, payload.get("research")),
            code=cast(str | None, payload.get("code")),
            final=cast(str | None, payload.get("final")),
        )
