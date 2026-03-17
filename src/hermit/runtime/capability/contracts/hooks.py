from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger()

_Handler = Callable[..., Any]
_HandlerBucket = list[tuple[int, _Handler]]


def _event_key(event: object) -> str:
    value = getattr(event, "value", None)
    if isinstance(value, str):
        return value
    return str(event)


class HooksEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, _HandlerBucket] = defaultdict(list)

    def register(
        self,
        event: str,
        handler: _Handler,
        priority: int = 0,
    ) -> None:
        bucket: _HandlerBucket = self._handlers[_event_key(event)]
        bucket.append((priority, handler))
        bucket.sort(key=lambda t: t[0])

    def fire(self, event: str, **kwargs: Any) -> list[Any]:
        results: list[Any] = []
        handlers: _HandlerBucket = self._handlers.get(_event_key(event), [])
        for _priority, handler in handlers:
            result = _safe_call(handler, kwargs)
            results.append(result)
        return results

    def fire_first(self, event: str, **kwargs: Any) -> Any | None:
        handlers: _HandlerBucket = self._handlers.get(_event_key(event), [])
        for _priority, handler in handlers:
            result = _safe_call(handler, kwargs)
            if result is not None:
                return result
        return None

    def has_handlers(self, event: str) -> bool:
        handlers: _HandlerBucket | None = self._handlers.get(_event_key(event))
        return bool(handlers)


def _safe_call(handler: _Handler, kwargs: dict[str, Any]) -> Any:
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
