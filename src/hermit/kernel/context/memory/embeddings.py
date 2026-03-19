from __future__ import annotations

import hashlib
import math
import struct
import time
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class EmbeddingService:
    """Embedding service with lazy loading and graceful degradation.

    Uses sentence-transformers when available, falls back to
    token-overlap based pseudo-embeddings.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Check if sentence-transformers is installed."""
        if self._available is not None:
            return self._available
        try:
            import sentence_transformers  # noqa: F401  # pyright: ignore[reportMissingImports,reportUnusedImport]

            self._available = True
        except ImportError:
            self._available = False
            log.info("sentence_transformers_not_available", fallback="token_overlap")
        return self._available

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.is_available():
            return None
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]
            SentenceTransformer,  # pyright: ignore[reportUnknownVariableType]
        )

        self._model = cast(Any, SentenceTransformer(self._model_name))  # pyright: ignore[reportUnknownVariableType]
        log.info("embedding_model_loaded", model=self._model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        model = self._ensure_model()
        if model is not None:
            vec = model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        return self._fallback_embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts efficiently."""
        model = self._ensure_model()
        if model is not None:
            vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
            return [v.tolist() for v in vecs]
        return [self._fallback_embed(t) for t in texts]

    def similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def index_memory(
        self,
        memory_id: str,
        text: str,
        store: KernelStore,
    ) -> None:
        """Compute and store embedding for a memory record."""
        embedding = self.embed(text)
        blob = _encode_embedding(embedding)
        now = time.time()
        with store._get_conn():
            store._get_conn().execute(
                """
                INSERT INTO memory_embeddings (memory_id, embedding, model_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    embedding = excluded.embedding,
                    model_name = excluded.model_name,
                    created_at = excluded.created_at
                """,
                (memory_id, blob, self._model_name, now),
            )

    def search(
        self,
        query: str,
        store: KernelStore,
        *,
        limit: int = 20,
    ) -> list[tuple[str, float]]:
        """Search indexed memories by embedding similarity."""
        query_vec = self.embed(query)

        rows = (
            store._get_conn()
            .execute("SELECT memory_id, embedding FROM memory_embeddings")
            .fetchall()
        )

        scored: list[tuple[str, float]] = []
        for row in rows:
            mid = str(row["memory_id"])
            stored_vec = _decode_embedding(row["embedding"])
            sim = self.similarity(query_vec, stored_vec)
            scored.append((mid, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def _fallback_embed(text: str, dim: int = 64) -> list[float]:
        """Token-based pseudo-embedding for when sentence-transformers is unavailable.

        Creates a deterministic vector from text tokens using hash-based projection.
        Not semantically meaningful, but provides consistent similarity scoring.
        """
        tokens = set(text.lower().split())
        vec = [0.0] * dim
        for token in tokens:
            h = hashlib.md5(token.encode()).digest()
            for i in range(min(dim, 16)):
                idx = i % dim
                # Use hash bytes to create pseudo-random projection
                byte_val = h[i % len(h)]
                vec[idx] += (byte_val / 128.0) - 1.0
        # Normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


def _encode_embedding(vec: list[float]) -> bytes:
    """Pack float list to binary blob."""
    return struct.pack(f"{len(vec)}f", *vec)


def _decode_embedding(blob: bytes) -> list[float]:
    """Unpack binary blob to float list."""
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def ensure_embedding_schema(store: KernelStore) -> None:
    """Create the memory_embeddings table if it doesn't exist."""
    with store._get_conn():
        store._get_conn().execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                model_name TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )


__all__ = ["EmbeddingService", "ensure_embedding_schema"]
