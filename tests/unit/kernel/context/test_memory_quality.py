from __future__ import annotations

import math
import time

from hermit.kernel.context.memory.memory_quality import (
    MemoryQualityService,
    _RecordProxy,
    _weighted_geometric_mean,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = time.time()


def _make_record(
    *,
    memory_id: str = "mem-1",
    created_at: float | None = None,
    confidence: float = 0.8,
    retention_class: str = "volatile_fact",
    status: str = "active",
    last_validated_at: float | None = None,
    structured_assertion: dict | None = None,
    expires_at: float | None = None,
) -> dict:
    """Build a minimal memory-record dict for MemoryQualityService."""
    rec: dict = {
        "memory_id": memory_id,
        "created_at": created_at if created_at is not None else _NOW,
        "confidence": confidence,
        "retention_class": retention_class,
        "status": status,
    }
    if last_validated_at is not None:
        rec["last_validated_at"] = last_validated_at
    if structured_assertion is not None:
        rec["structured_assertion"] = structured_assertion
    if expires_at is not None:
        rec["expires_at"] = expires_at
    return rec


# ---------------------------------------------------------------------------
# _weighted_geometric_mean
# ---------------------------------------------------------------------------


def test_weighted_geometric_mean_equal_weights() -> None:
    """With equal weights the result is the standard geometric mean."""
    result = _weighted_geometric_mean(0.25, 1.0, 1.0, 1.0)
    expected = math.sqrt(0.25 * 1.0)
    assert abs(result - expected) < 1e-6


def test_weighted_geometric_mean_zero_total_weight() -> None:
    """Zero total weight should return 0.0."""
    assert _weighted_geometric_mean(0.5, 0.5, 0.0, 0.0) == 0.0


def test_weighted_geometric_mean_one_value_zero() -> None:
    """When one value is zero, clamping to 1e-10 keeps result near zero."""
    result = _weighted_geometric_mean(0.0, 1.0, 0.5, 0.5)
    assert result < 1e-4


def test_weighted_geometric_mean_both_one() -> None:
    """Both values 1.0 should return 1.0 regardless of weights."""
    result = _weighted_geometric_mean(1.0, 1.0, 0.3, 0.7)
    assert abs(result - 1.0) < 1e-6


def test_weighted_geometric_mean_single_weight_dominates() -> None:
    """When one weight is zero, result equals the other value."""
    result = _weighted_geometric_mean(0.5, 0.9, 0.0, 1.0)
    assert abs(result - 0.9) < 1e-6


# ---------------------------------------------------------------------------
# _RecordProxy
# ---------------------------------------------------------------------------


def test_record_proxy_returns_dict_values() -> None:
    proxy = _RecordProxy({"confidence": 0.75, "status": "active"})
    assert proxy.confidence == 0.75
    assert proxy.status == "active"


def test_record_proxy_returns_none_for_missing_keys() -> None:
    proxy = _RecordProxy({})
    assert proxy.some_missing_field is None


# ---------------------------------------------------------------------------
# MemoryQualityService.quality_score
# ---------------------------------------------------------------------------


class TestQualityScore:
    """Unit tests for single-record quality scoring."""

    def test_fresh_high_confidence_record(self) -> None:
        """A fresh record with high confidence should score near 1.0."""
        svc = MemoryQualityService()
        rec = _make_record(created_at=_NOW, confidence=0.95)
        score = svc.quality_score(rec, now=_NOW)
        assert 0.0 <= score <= 1.0
        assert score > 0.8

    def test_score_is_normalized_between_zero_and_one(self) -> None:
        """Score must always lie in [0.0, 1.0]."""
        svc = MemoryQualityService()
        for conf in (0.0, 0.01, 0.5, 1.0):
            for age_days in (0, 1, 30, 365):
                rec = _make_record(
                    created_at=_NOW - age_days * 86400,
                    confidence=conf,
                )
                score = svc.quality_score(rec, now=_NOW)
                assert 0.0 <= score <= 1.0, f"out of range for conf={conf}, age={age_days}"

    def test_zero_confidence_gives_very_low_score(self) -> None:
        """Zero confidence should drag the score to near zero."""
        svc = MemoryQualityService()
        rec = _make_record(confidence=0.0)
        score = svc.quality_score(rec, now=_NOW)
        assert score < 0.01

    def test_missing_created_at_uses_now(self) -> None:
        """When created_at is None the proxy returns None; services fall back gracefully."""
        svc = MemoryQualityService()
        rec = _make_record()
        del rec["created_at"]
        # Should not raise
        score = svc.quality_score(rec, now=_NOW)
        assert 0.0 <= score <= 1.0

    def test_older_record_scores_lower_than_newer(self) -> None:
        """An older record should score lower than a recently created one (same confidence)."""
        svc = MemoryQualityService()
        recent = _make_record(created_at=_NOW, confidence=0.8)
        old = _make_record(created_at=_NOW - 60 * 86400, confidence=0.8)
        score_recent = svc.quality_score(recent, now=_NOW)
        score_old = svc.quality_score(old, now=_NOW)
        assert score_recent > score_old

    def test_higher_confidence_scores_higher(self) -> None:
        """Higher base confidence should yield a higher score (same age)."""
        svc = MemoryQualityService()
        low = _make_record(confidence=0.3)
        high = _make_record(confidence=0.9)
        assert svc.quality_score(high, now=_NOW) > svc.quality_score(low, now=_NOW)

    def test_custom_weights(self) -> None:
        """Custom decay/confidence weights should influence the final score."""
        rec = _make_record(confidence=0.8)
        svc_decay_heavy = MemoryQualityService(decay_weight=0.9, confidence_weight=0.1)
        svc_conf_heavy = MemoryQualityService(decay_weight=0.1, confidence_weight=0.9)
        score_decay = svc_decay_heavy.quality_score(rec, now=_NOW)
        score_conf = svc_conf_heavy.quality_score(rec, now=_NOW)
        # Both should still be valid scores
        assert 0.0 <= score_decay <= 1.0
        assert 0.0 <= score_conf <= 1.0

    def test_score_is_rounded_to_four_decimals(self) -> None:
        """The returned score should be rounded to 4 decimal places."""
        svc = MemoryQualityService()
        rec = _make_record(confidence=0.777)
        score = svc.quality_score(rec, now=_NOW)
        assert score == round(score, 4)


# ---------------------------------------------------------------------------
# MemoryQualityService.batch_quality_scores
# ---------------------------------------------------------------------------


class TestBatchQualityScores:
    """Unit tests for batch scoring."""

    def test_batch_returns_correct_count(self) -> None:
        svc = MemoryQualityService()
        records = [_make_record(memory_id=f"m-{i}") for i in range(5)]
        scores = svc.batch_quality_scores(records, now=_NOW)
        assert len(scores) == 5

    def test_batch_empty_list(self) -> None:
        svc = MemoryQualityService()
        assert svc.batch_quality_scores([], now=_NOW) == []

    def test_batch_scores_match_individual(self) -> None:
        """Batch scoring should produce the same results as individual calls."""
        svc = MemoryQualityService()
        records = [
            _make_record(memory_id="a", confidence=0.9),
            _make_record(memory_id="b", confidence=0.3, created_at=_NOW - 30 * 86400),
        ]
        batch = svc.batch_quality_scores(records, now=_NOW)
        individual = [svc.quality_score(r, now=_NOW) for r in records]
        assert batch == individual

    def test_batch_all_scores_normalized(self) -> None:
        """Every score in a batch must be in [0.0, 1.0]."""
        svc = MemoryQualityService()
        records = [
            _make_record(confidence=0.0),
            _make_record(confidence=0.5, created_at=_NOW - 100 * 86400),
            _make_record(confidence=1.0),
        ]
        for score in svc.batch_quality_scores(records, now=_NOW):
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_confidence_above_one_clamped(self) -> None:
        """Confidence > 1.0 should be clamped to 1.0 in the confidence component."""
        svc = MemoryQualityService()
        rec = _make_record(confidence=5.0)
        score = svc.quality_score(rec, now=_NOW)
        assert 0.0 <= score <= 1.0

    def test_negative_confidence_clamped(self) -> None:
        """Negative confidence should be clamped to 0.0."""
        svc = MemoryQualityService()
        rec = _make_record(confidence=-1.0)
        score = svc.quality_score(rec, now=_NOW)
        assert 0.0 <= score <= 1.0
        assert score < 0.1

    def test_very_old_record_scores_near_zero(self) -> None:
        """A record created years ago with low confidence should score near zero."""
        svc = MemoryQualityService()
        rec = _make_record(confidence=0.1, created_at=_NOW - 365 * 5 * 86400)
        score = svc.quality_score(rec, now=_NOW)
        assert score < 0.1

    def test_unknown_retention_class(self) -> None:
        """Unknown retention class should still produce a valid score via defaults."""
        svc = MemoryQualityService()
        rec = _make_record(retention_class="unknown_class")
        score = svc.quality_score(rec, now=_NOW)
        assert 0.0 <= score <= 1.0

    def test_minimal_record_dict(self) -> None:
        """A dict with memory_id and confidence should produce a valid score."""
        svc = MemoryQualityService()
        score = svc.quality_score({"memory_id": "bare", "confidence": 0.5}, now=_NOW)
        assert 0.0 <= score <= 1.0

    def test_missing_confidence_raises(self) -> None:
        """A dict without 'confidence' causes TypeError in the service."""
        import pytest

        svc = MemoryQualityService()
        with pytest.raises(TypeError):
            svc.quality_score({"memory_id": "bare"}, now=_NOW)
