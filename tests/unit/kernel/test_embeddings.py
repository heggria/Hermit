from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hermit.kernel.context.memory.embeddings import (
    EmbeddingService,
    _decode_embedding,
    _encode_embedding,
    ensure_embedding_schema,
)
from hermit.kernel.ledger.journal.store import KernelStore


def test_fallback_embed_deterministic() -> None:
    """Same text produces the same vector every time."""
    svc = EmbeddingService()
    svc._available = False

    vec_a = svc._fallback_embed("hello world")
    vec_b = svc._fallback_embed("hello world")

    assert vec_a == vec_b
    assert len(vec_a) == 64


def test_fallback_embed_different_texts() -> None:
    """Different texts produce different vectors."""
    svc = EmbeddingService()
    svc._available = False

    vec_a = svc._fallback_embed("python programming language")
    vec_b = svc._fallback_embed("quantum physics research")

    assert vec_a != vec_b
    assert len(vec_a) == 64
    assert len(vec_b) == 64


def test_similarity_identical() -> None:
    """Cosine similarity of a vector with itself is approximately 1.0."""
    svc = EmbeddingService()
    vec = svc._fallback_embed("test vector similarity")

    sim = svc.similarity(vec, vec)

    assert abs(sim - 1.0) < 1e-6


def test_similarity_orthogonal() -> None:
    """Very different texts have low similarity."""
    svc = EmbeddingService()
    svc._available = False

    vec_a = svc._fallback_embed("python programming code function class")
    vec_b = svc._fallback_embed("ocean waves beach sunset surfing")

    sim = svc.similarity(vec_a, vec_b)

    # Different topics should have low similarity
    assert sim < 0.5


def test_similarity_edge_cases() -> None:
    """Edge cases: empty vectors, mismatched lengths, zero vectors."""
    svc = EmbeddingService()

    assert svc.similarity([], []) == 0.0
    assert svc.similarity([1.0, 2.0], [1.0]) == 0.0
    assert svc.similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_encode_decode_roundtrip() -> None:
    """Encoding then decoding preserves float values."""
    original = [0.1, 0.2, -0.3, 0.0, 1.0, -1.0]

    blob = _encode_embedding(original)
    recovered = _decode_embedding(blob)

    assert len(recovered) == len(original)
    for a, b in zip(original, recovered, strict=True):
        assert abs(a - b) < 1e-6


def test_index_and_search_with_fallback(tmp_path: Path) -> None:
    """Index memories and search returns correct results using fallback embeddings."""
    store = KernelStore(tmp_path / "embed.db")
    try:
        ensure_embedding_schema(store)

        svc = EmbeddingService()
        svc._available = False

        # Index some memories
        svc.index_memory("mem-1", "python programming language design", store)
        svc.index_memory("mem-2", "ocean waves beach sunset", store)
        svc.index_memory("mem-3", "python code function class module", store)

        # Search for python-related content
        results = svc.search("python programming", store, limit=3)

        assert len(results) == 3
        # All memory IDs should be present
        result_ids = [mid for mid, _ in results]
        assert "mem-1" in result_ids
        assert "mem-2" in result_ids
        assert "mem-3" in result_ids

        # Python-related memories should score higher than ocean/beach
        scores = {mid: score for mid, score in results}
        assert scores["mem-1"] > scores["mem-2"]
    finally:
        store.close()


def test_is_available_without_transformers() -> None:
    """Returns False when sentence-transformers is not importable."""
    svc = EmbeddingService()
    svc._available = None  # Reset cached state

    with patch.dict("sys.modules", {"sentence_transformers": None}):
        # Force re-evaluation by clearing cached state
        svc._available = None
        result = svc.is_available()

    assert result is False


def test_embed_uses_fallback_when_unavailable() -> None:
    """embed() falls back to _fallback_embed when transformers not available."""
    svc = EmbeddingService()
    svc._available = False

    vec = svc.embed("test text for embedding")

    assert isinstance(vec, list)
    assert len(vec) == 64
    assert all(isinstance(v, float) for v in vec)


def test_embed_batch_uses_fallback() -> None:
    """embed_batch() falls back to per-item _fallback_embed."""
    svc = EmbeddingService()
    svc._available = False

    texts = ["first text", "second text", "third text"]
    vecs = svc.embed_batch(texts)

    assert len(vecs) == 3
    # Each vector should be deterministic
    assert vecs[0] == svc._fallback_embed("first text")
    assert vecs[1] == svc._fallback_embed("second text")


def test_is_available_returns_true_when_transformers_installed() -> None:
    """Returns True and caches result when sentence-transformers is importable."""
    import types

    fake_module = types.ModuleType("sentence_transformers")
    svc = EmbeddingService()
    svc._available = None

    with patch.dict("sys.modules", {"sentence_transformers": fake_module}):
        result = svc.is_available()

    assert result is True
    # Cached — second call returns same without re-checking
    assert svc._available is True


def test_ensure_model_returns_cached_model() -> None:
    """_ensure_model returns the cached model if already loaded."""
    svc = EmbeddingService()
    sentinel = object()
    svc._model = sentinel

    result = svc._ensure_model()

    assert result is sentinel


def test_ensure_model_loads_and_returns_model() -> None:
    """_ensure_model loads the SentenceTransformer model when available."""
    import types

    fake_st_module = types.ModuleType("sentence_transformers")
    fake_model_instance = type("FakeModel", (), {})()
    fake_st_module.SentenceTransformer = lambda name: fake_model_instance  # type: ignore[attr-defined]

    svc = EmbeddingService()
    svc._available = True
    svc._model = None

    with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
        result = svc._ensure_model()

    assert result is not None
    assert svc._model is not None


class _FakeVector:
    """Mimics a numpy array with .tolist() for testing without numpy."""

    def __init__(self, data: list[float]) -> None:
        self._data = data

    def tolist(self) -> list[float]:
        return self._data


class _FakeMatrix:
    """Mimics a 2D numpy array that is iterable and each row has .tolist()."""

    def __init__(self, rows: list[list[float]]) -> None:
        self._rows = [_FakeVector(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


def test_embed_uses_model_when_available() -> None:
    """embed() uses the real model.encode() when sentence-transformers is available."""
    svc = EmbeddingService()
    fake_vec = _FakeVector([0.1, 0.2, 0.3])
    fake_model = type("FakeModel", (), {"encode": lambda self, text, **kw: fake_vec})()
    svc._model = fake_model
    svc._available = True

    result = svc.embed("hello")

    assert result == [0.1, 0.2, 0.3]


def test_embed_batch_uses_model_when_available() -> None:
    """embed_batch() uses the real model.encode() for batch encoding."""
    svc = EmbeddingService()
    fake_vecs = _FakeMatrix([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])
    fake_model = type("FakeModel", (), {"encode": lambda self, texts, **kw: fake_vecs})()
    svc._model = fake_model
    svc._available = True

    result = svc.embed_batch(["a", "b", "c"])

    assert len(result) == 3
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]
    assert result[2] == [0.5, 0.6]
