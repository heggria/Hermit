from __future__ import annotations

from typing import Any, cast

import structlog

log = structlog.get_logger()

_DEFAULT_CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Cross-encoder reranker for second-stage precision refinement.

    After RRF fusion produces a ranked candidate list, the cross-encoder
    scores each (query, candidate_text) pair jointly — capturing deeper
    semantic relevance that bi-encoders and token overlap miss.

    Uses sentence-transformers CrossEncoder with lazy loading and
    graceful fallback (returns candidates unchanged when unavailable).
    """

    def __init__(self, model_name: str = _DEFAULT_CROSS_ENCODER) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Check if sentence-transformers CrossEncoder is importable."""
        if self._available is not None:
            return self._available
        try:
            import sentence_transformers  # noqa: F401  # pyright: ignore[reportMissingImports,reportUnusedImport]

            self._available = True
        except ImportError:
            self._available = False
            log.info("cross_encoder_not_available", fallback="passthrough")
        return self._available

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.is_available():
            return None
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]
            CrossEncoder,  # pyright: ignore[reportUnknownVariableType]
        )

        self._model = cast(Any, CrossEncoder(self._model_name))  # pyright: ignore[reportUnknownVariableType]
        log.info("cross_encoder_loaded", model=self._model_name)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str, float]],
        *,
        limit: int = 10,
    ) -> list[tuple[str, str, float]]:
        """Rerank candidates using cross-encoder scores.

        Args:
            query: The search query.
            candidates: List of (memory_id, claim_text, rrf_score) tuples.
            limit: Maximum number of results to return.

        Returns:
            Reranked list of (memory_id, claim_text, cross_encoder_score) tuples,
            or original candidates (truncated) if model is unavailable.
        """
        if not candidates:
            return []

        if len(candidates) <= 1:
            return candidates[:limit]

        model = self._ensure_model()
        if model is None:
            return candidates[:limit]

        try:
            pairs = [(query, text) for _, text, _ in candidates]
            scores = model.predict(pairs)

            scored = [
                (mid, text, float(score))
                for (mid, text, _), score in zip(candidates, scores, strict=True)
            ]
            scored.sort(key=lambda x: x[2], reverse=True)

            log.debug(
                "cross_encoder_reranked",
                candidates=len(candidates),
                limit=limit,
                top_score=round(scored[0][2], 4) if scored else 0.0,
            )
            return scored[:limit]
        except Exception:
            log.warning("cross_encoder_rerank_failed", fallback="passthrough")
            return candidates[:limit]


__all__ = ["CrossEncoderReranker"]
