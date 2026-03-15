from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Callable, List, Optional

import structlog

log = structlog.get_logger()


def _event_key(event: object) -> str:
    value = getattr(event, "value", None)
    if isinstance(value, str):
        return value
    return str(event)


class HooksEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[int, Callable]]] = defaultdict(list)

    def register(
        self,
        event: str,
        handler: Callable,
        priority: int = 0,
    ) -> None:
        bucket = self._handlers[_event_key(event)]
        bucket.append((priority, handler))
        bucket.sort(key=lambda t: t[0])

    def fire(self, event: str, **kwargs: Any) -> List[Any]:
        results: List[Any] = []
        for _priority, handler in self._handlers.get(_event_key(event), []):
            result = _safe_call(handler, kwargs)
            results.append(result)
        return results

    def fire_first(self, event: str, **kwargs: Any) -> Optional[Any]:
        for _priority, handler in self._handlers.get(_event_key(event), []):
            result = _safe_call(handler, kwargs)
            if result is not None:
                return result
        return None

    def has_handlers(self, event: str) -> bool:
        return bool(self._handlers.get(_event_key(event)))


def _safe_call(handler: Callable, kwargs: dict[str, Any]) -> Any:
    """Call handler with only the kwargs it accepts."""
    try:
        sig = inspect.signature(handler)
    except (ValueError, TypeError):
        return handler(**kwargs)

    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return handler(**kwargs)

    accepted = {k: v for k, v in kwargs.items() if k in params}
    return handler(**accepted)
