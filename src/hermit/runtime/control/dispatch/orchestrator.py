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
        payload: Dict[str, Any] = dict(state.to_dict())

        if state.route == "research":
            payload = dict(await self.researcher(cast(Dict[str, object], payload)))
        elif state.route == "code":
            payload = dict(await self.coder(cast(Dict[str, object], payload)))

        raw_messages = payload.get("messages", [])
        typed_messages: list[Dict[str, object]] = (
            cast(List[Dict[str, object]], raw_messages) if isinstance(raw_messages, list) else []
        )
        return SharedState(
            messages=typed_messages,
            route=str(payload.get("route", "direct")),
            research=str(payload["research"]) if payload.get("research") is not None else None,
            code=str(payload["code"]) if payload.get("code") is not None else None,
            final=str(payload["final"]) if payload.get("final") is not None else None,
        )
