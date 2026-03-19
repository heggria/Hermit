"""LRU cache with TTL for web_tools results.

Provides a session-scoped, in-process cache that deduplicates identical
web_search and web_fetch calls within a single Hermit process lifetime.

Design choices
--------------
* Pure stdlib — no external dependencies.
* Thread-safe via a single ``threading.Lock``.
* Capacity-bounded (max_size) so memory stays predictable.
* TTL is checked on every *get*, not on insertion, keeping the hot path fast.
* Cache key is a deterministic JSON-serialised tuple of the normalised args so
  minor cosmetic differences (e.g. extra whitespace in a URL) still hit the cache.

Default policy (can be overridden per call-site):
    TTL     = 300 s  (5 min)
    max_size = 256 entries
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

_DEFAULT_TTL: float = 300.0  # seconds
_DEFAULT_MAX_SIZE: int = 256

F = TypeVar("F", bound=Callable[..., Any])


class _CacheEntry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: str, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class WebToolCache:
    """LRU cache with per-entry TTL.

    ``get`` / ``set`` are the primary interface; ``make_key`` is exposed so
    callers can build canonical keys before deciding whether to call the network.
    """

    def __init__(
        self,
        *,
        ttl: float = _DEFAULT_TTL,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(namespace: str, params: dict[str, Any]) -> str:
        """Return a stable, deterministic cache key string.

        Parameters are sorted before serialisation so that equivalent dicts
        (with different insertion order) map to the same key.
        """
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return f"{namespace}:{canonical}"

    def get(self, key: str) -> str | None:
        """Return the cached value for *key*, or ``None`` on a miss/expiry."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() >= entry.expires_at:
                # Expired — evict and report as miss
                del self._store[key]
                self._misses += 1
                return None
            # LRU: move to end (most-recently-used)
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: str, value: str) -> None:
        """Store *value* under *key* with the configured TTL."""
        expires_at = time.monotonic() + self._ttl
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _CacheEntry(value, expires_at)
            # Evict least-recently-used entries when over capacity
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        """Remove a single entry.  Returns True if the key existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._store),
                "max_size": self._max_size,
            }


# ---------------------------------------------------------------------------
# Module-level singleton shared by search.py and fetch.py
# ---------------------------------------------------------------------------

#: Shared process-level cache used by all web_tools handlers.
_cache = WebToolCache()


def get_cache() -> WebToolCache:
    """Return the module-level singleton cache instance."""
    return _cache
