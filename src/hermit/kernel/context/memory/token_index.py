"""Inverted token index for fast memory candidate filtering."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenIndex:
    """Maps tokens to memory IDs for O(1) candidate lookup.

    Instead of scanning all memories for token overlap, query tokens
    are looked up in the index to find only memories that share at
    least one token with the query.
    """

    _index: dict[str, set[str]] = field(default_factory=lambda: {})
    _memory_tokens: dict[str, frozenset[str]] = field(default_factory=lambda: {})

    def add(self, memory_id: str, tokens: frozenset[str]) -> None:
        """Add a memory's tokens to the index."""
        self._memory_tokens[memory_id] = tokens
        for token in tokens:
            bucket: set[str] | None = self._index.get(token)
            if bucket is None:
                bucket = set[str]()
                self._index[token] = bucket
            bucket.add(memory_id)

    def remove(self, memory_id: str) -> None:
        """Remove a memory from the index."""
        tokens = self._memory_tokens.pop(memory_id, None)
        if tokens is None:
            return
        for token in tokens:
            bucket = self._index.get(token)
            if bucket is not None:
                bucket.discard(memory_id)
                if not bucket:
                    del self._index[token]

    def candidates(self, query_tokens: frozenset[str]) -> set[str]:
        """Return memory IDs that share at least one token with the query."""
        result: set[str] = set()
        for token in query_tokens:
            bucket = self._index.get(token)
            if bucket is not None:
                result.update(bucket)
        return result

    def get_tokens(self, memory_id: str) -> frozenset[str]:
        """Return cached tokens for a memory, or empty frozenset."""
        return self._memory_tokens.get(memory_id, frozenset())

    def __len__(self) -> int:
        return len(self._memory_tokens)


__all__ = ["TokenIndex"]
