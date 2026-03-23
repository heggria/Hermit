from __future__ import annotations

import inspect
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

_Handler = Callable[..., Any]
_HandlerBucket = list[tuple[int, _Handler]]


def _handler_name(handler: _Handler) -> str:
    """Return a human-readable name for a handler function."""
    module = getattr(handler, "__module__", None) or ""
    qualname = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", str(handler))
    if module:
        return f"{module}.{qualname}"
    return qualname


def _event_key(event: object) -> str:
    value = getattr(event, "value", None)
    if isinstance(value, str):
        return value
    return str(event)


@dataclass
class HookInvocationRecord:
    """Tracks cumulative stats for a single handler on a specific event."""

    event: str
    handler_name: str
    invocations: int = 0
    failures: int = 0
    total_duration_ms: float = 0.0


@dataclass
class HookExecutionTracker:
    """Tracks hook execution activity for observability."""

    _records: dict[str, HookInvocationRecord] = field(default_factory=dict)

    def record(
        self,
        event: str,
        handler_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        key = f"{event}:{handler_name}"
        if key not in self._records:
            self._records[key] = HookInvocationRecord(event=event, handler_name=handler_name)
        rec = self._records[key]
        rec.invocations += 1
        rec.total_duration_ms += duration_ms
        if not success:
            rec.failures += 1

    def summary(self) -> list[dict[str, Any]]:
        """Return a list of dicts summarizing hook execution activity."""
        return [
            {
                "event": rec.event,
                "handler": rec.handler_name,
                "invocations": rec.invocations,
                "failures": rec.failures,
                "total_duration_ms": round(rec.total_duration_ms, 2),
            }
            for rec in sorted(self._records.values(), key=lambda r: r.invocations, reverse=True)
        ]

    def reset(self) -> None:
        self._records.clear()


class HooksEngine:
    def __init__(self) -> None:
        self._handlers: dict[str, _HandlerBucket] = defaultdict(list)
        # Cache inspect.signature() results per handler, keyed by id(handler).
        # This avoids repeated introspection on every fire() call.
        self._sig_cache: dict[int, inspect.Signature | None] = {}
        self.tracker = HookExecutionTracker()

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
        key = _event_key(event)
        handlers: _HandlerBucket = self._handlers.get(key, [])
        for _priority, handler in handlers:
            name = _handler_name(handler)
            t0 = time.monotonic()
            try:
                result = self._safe_call(handler, kwargs)
                duration_ms = (time.monotonic() - t0) * 1000
                self.tracker.record(key, name, duration_ms, success=True)
                log.debug(
                    "hook_fired",
                    hook_event=key,
                    handler=name,
                    duration_ms=round(duration_ms, 2),
                    success=True,
                )
                results.append(result)
            except Exception:
                duration_ms = (time.monotonic() - t0) * 1000
                self.tracker.record(key, name, duration_ms, success=False)
                log.warning(
                    "hook_handler_failed",
                    hook_event=key,
                    handler=name,
                    duration_ms=round(duration_ms, 2),
                    exc_info=True,
                )
                results.append(None)
        return results

    def fire_first(self, event: str, **kwargs: Any) -> Any | None:
        key = _event_key(event)
        handlers: _HandlerBucket = self._handlers.get(key, [])
        for _priority, handler in handlers:
            name = _handler_name(handler)
            t0 = time.monotonic()
            try:
                result = self._safe_call(handler, kwargs)
                duration_ms = (time.monotonic() - t0) * 1000
                self.tracker.record(key, name, duration_ms, success=True)
                log.debug(
                    "hook_fired",
                    hook_event=key,
                    handler=name,
                    duration_ms=round(duration_ms, 2),
                    success=True,
                )
                if result is not None:
                    return result
            except Exception:
                duration_ms = (time.monotonic() - t0) * 1000
                self.tracker.record(key, name, duration_ms, success=False)
                log.warning(
                    "hook_fire_first_handler_error",
                    hook_event=key,
                    handler=name,
                    duration_ms=round(duration_ms, 2),
                    exc_info=True,
                )
                continue
        return None

    def has_handlers(self, event: str) -> bool:
        handlers: _HandlerBucket | None = self._handlers.get(_event_key(event))
        return bool(handlers)
