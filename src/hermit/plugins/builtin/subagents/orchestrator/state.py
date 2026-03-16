from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast

Worker = Callable[[Dict[str, object]], Awaitable[Dict[str, object]]]


@dataclass
class SharedState:
    messages: List[Dict[str, object]] = field(default_factory=list[Dict[str, object]])
    route: str = "direct"
    research: Optional[str] = None
    code: Optional[str] = None
    final: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
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
            messages=list(cast(List[Any], payload.get("messages") or [])),
            route=str(payload.get("route", "direct")),
            research=cast(Optional[str], payload.get("research")),
            code=cast(Optional[str], payload.get("code")),
            final=cast(Optional[str], payload.get("final")),
        )
