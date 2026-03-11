from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

HookHandler = Callable[..., Any]


class HooksEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)

    def register(self, event: str, handler: HookHandler) -> None:
        self._handlers[event].append(handler)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> list[Any]:
        results: list[Any] = []
        for handler in self._handlers.get(event, []):
            results.append(handler(*args, **kwargs))
        return results
