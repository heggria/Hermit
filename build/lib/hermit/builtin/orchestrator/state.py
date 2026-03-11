from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

Worker = Callable[[Dict[str, object]], Awaitable[Dict[str, object]]]


@dataclass
class SharedState:
    messages: List[Dict[str, object]] = field(default_factory=list)
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
            messages=list(payload.get("messages", [])),
            route=str(payload.get("route", "direct")),
            research=payload.get("research"),  # type: ignore[arg-type]
            code=payload.get("code"),  # type: ignore[arg-type]
            final=payload.get("final"),  # type: ignore[arg-type]
        )
