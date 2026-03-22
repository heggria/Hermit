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
        # Cache inspect.signature() results per handler, keyed by id(handler).
        # This avoids repeated introspection on every fire() call.
        self._sig_cache: dict[int, inspect.Signature | None] = {}

    def register(
        self,
        event: str,
        handler: _Handler,
        priority: int = 0,
    ) -> None:
        bucket: _HandlerBucket = self._handlers[_event_key(event)]
        bucket.append((priority, handler))
        bucket.sort(key=lambda t: t[0])

    def _safe_call(self, handler: _Handler, kwargs: dict[str, Any]) -> Any:
        """Call handler with only the kwargs it accepts, caching the signature."""
        handler_id = id(handler)
        if handler_id not in self._sig_cache:
            try:
                self._sig_cache[handler_id] = inspect.signature(handler)
            except (ValueError, TypeError):
                self._sig_cache[handler_id] = None

        sig = self._sig_cache[handler_id]
        if sig is None:
            return handler(**kwargs)

        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return handler(**kwargs)

        accepted = {k: v for k, v in kwargs.items() if k in params}
        return handler(**accepted)

    def fire(self, event: str, **kwargs: Any) -> list[Any]:
        results: list[Any] = []
        handlers: _HandlerBucket = self._handlers.get(_event_key(event), [])
        for _priority, handler in handlers:
            try:
                result = self._safe_call(handler, kwargs)
                results.append(result)
            except Exception:
                log.warning("hook_handler_failed", handler=str(handler), exc_info=True)
                results.append(None)
        return results

    def fire_first(self, event: str, **kwargs: Any) -> Any | None:
        handlers: _HandlerBucket = self._handlers.get(_event_key(event), [])
        for _priority, handler in handlers:
            result = self._safe_call(handler, kwargs)
            if result is not None:
                return result
        return None

    def has_handlers(self, event: str) -> bool:
        handlers: _HandlerBucket | None = self._handlers.get(_event_key(event))
        return bool(handlers)
