"""Additional coverage tests for src/hermit/kernel/context/memory/reranker.py

Targets the ~5 missed statements: _ensure_model paths, is_available caching.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermit.kernel.context.memory.reranker import CrossEncoderReranker


class TestEnsureModel:
    def test_returns_cached_model(self) -> None:
        reranker = CrossEncoderReranker()
        sentinel = MagicMock()
        reranker._model = sentinel
        assert reranker._ensure_model() is sentinel

    def test_returns_none_when_not_available(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = False
        reranker._model = None
        assert reranker._ensure_model() is None

    def test_loads_model_when_available(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = True
        reranker._model = None
        mock_ce_class = MagicMock()
        mock_ce_instance = MagicMock()
        mock_ce_class.return_value = mock_ce_instance
        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(CrossEncoder=mock_ce_class)},
        ):
            result = reranker._ensure_model()
        assert result is not None


class TestIsAvailableCaching:
    def test_caches_true(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = True
        assert reranker.is_available() is True

    def test_caches_false(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = False
        assert reranker.is_available() is False

    def test_detects_import_failure(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = None
        # sentence_transformers is likely not installed in test env
        result = reranker.is_available()
        assert isinstance(result, bool)
        # Should be cached now
        assert reranker._available is not None


class TestRerankWithModelPredictSuccess:
    def test_scores_are_used_for_ordering(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = True
        mock_model = MagicMock()
        # Reverse scoring: m3 gets highest score
        mock_model.predict.return_value = [0.1, 0.3, 0.9]
        reranker._model = mock_model

        candidates = [
            ("m1", "first text", 0.9),
            ("m2", "second text", 0.5),
            ("m3", "third text", 0.1),
        ]
        result = reranker.rerank("query", candidates, limit=2)
        assert len(result) == 2
        assert result[0][0] == "m3"
        assert result[0][2] == 0.9  # cross-encoder score replaces RRF score

    def test_limit_truncates_after_sort(self) -> None:
        reranker = CrossEncoderReranker()
        reranker._available = True
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.9, 0.1, 0.7]
        reranker._model = mock_model

        candidates = [
            ("m1", "a", 0.0),
            ("m2", "b", 0.0),
            ("m3", "c", 0.0),
            ("m4", "d", 0.0),
        ]
        result = reranker.rerank("q", candidates, limit=2)
        assert len(result) == 2
        assert result[0][0] == "m2"  # highest score 0.9
