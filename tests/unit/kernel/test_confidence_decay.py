from __future__ import annotations

import math
import time
from pathlib import Path

from hermit.kernel.context.memory.confidence import ConfidenceDecayService, ConfidenceReport
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(
    store: KernelStore,
    *,
    confidence: float = 0.8,
    retention_class: str = "volatile_fact",
    claim_text: str = "test memory",
) -> str:
    """Create a memory record and return its memory_id."""
    record = store.create_memory_record(
        task_id="task-test",
        conversation_id="conv-test",
        category="other",
        claim_text=claim_text,
        scope_kind="workspace",
        scope_ref="workspace:default",
        promotion_reason="test",
        retention_class=retention_class,
        confidence=confidence,
        trust_tier="durable",
    )
    return record.memory_id


def _set_created_at(store: KernelStore, memory_id: str, ts: float) -> None:
    """Directly update created_at via SQL for time manipulation in tests."""
    with store._lock, store._conn:  # type: ignore[attr-defined]
        store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE memory_records SET created_at = ? WHERE memory_id = ?",
            (ts, memory_id),
        )


def test_compute_confidence_fresh_memory(tmp_path: Path) -> None:
    """Memory just created should have confidence approximately equal to base."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8)
        record = store.get_memory_record(mid)
        assert record is not None

        now = time.time()
        effective = svc.compute_confidence(record, now=now)
        assert abs(effective - 0.8) < 0.01
    finally:
        store.close()


def test_compute_confidence_at_half_life(tmp_path: Path) -> None:
    """Memory at exactly 1 half-life should have confidence approximately 0.5 * base."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        # volatile_fact half-life = 14 days
        now = time.time()
        past = now - 14.0 * 86400.0
        _set_created_at(store, mid, past)

        record = store.get_memory_record(mid)
        assert record is not None
        effective = svc.compute_confidence(record, now=now)
        expected = 0.8 * 0.5
        assert abs(effective - expected) < 0.01
    finally:
        store.close()


def test_compute_confidence_at_two_half_lives(tmp_path: Path) -> None:
    """Memory at 2 half-lives should have confidence approximately 0.25 * base."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        now = time.time()
        past = now - 28.0 * 86400.0  # 2 * 14 days
        _set_created_at(store, mid, past)

        record = store.get_memory_record(mid)
        assert record is not None
        effective = svc.compute_confidence(record, now=now)
        expected = 0.8 * 0.25
        assert abs(effective - expected) < 0.01
    finally:
        store.close()


def test_compute_confidence_user_preference_slow_decay(tmp_path: Path) -> None:
    """user_preference has 180-day half-life, should decay slowly."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.9, retention_class="user_preference")
        now = time.time()
        # 30 days ago — well within 180-day half-life
        past = now - 30.0 * 86400.0
        _set_created_at(store, mid, past)

        record = store.get_memory_record(mid)
        assert record is not None
        effective = svc.compute_confidence(record, now=now)
        # decay_factor = 0.5^(30/180) ≈ 0.891
        expected = 0.9 * math.pow(0.5, 30.0 / 180.0)
        assert abs(effective - round(expected, 4)) < 0.01
        # Should still be above 0.7
        assert effective > 0.7
    finally:
        store.close()


def test_compute_confidence_task_state_fast_decay(tmp_path: Path) -> None:
    """task_state has 7-day half-life, should decay fast."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="task_state")
        now = time.time()
        # 7 days ago — exactly one half-life
        past = now - 7.0 * 86400.0
        _set_created_at(store, mid, past)

        record = store.get_memory_record(mid)
        assert record is not None
        effective = svc.compute_confidence(record, now=now)
        expected = 0.8 * 0.5
        assert abs(effective - expected) < 0.01
    finally:
        store.close()


def test_refresh_on_reference_resets_clock(tmp_path: Path) -> None:
    """After refresh_on_reference, confidence should be higher than before."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        now = time.time()
        # Set created_at to 10 days ago
        past = now - 10.0 * 86400.0
        _set_created_at(store, mid, past)

        record_before = store.get_memory_record(mid)
        assert record_before is not None
        confidence_before = svc.compute_confidence(record_before, now=now)

        # Refresh the reference clock
        svc.refresh_on_reference(mid, store, now=now)

        record_after = store.get_memory_record(mid)
        assert record_after is not None
        confidence_after = svc.compute_confidence(record_after, now=now)

        # After refresh, confidence should be restored to base
        assert confidence_after > confidence_before
        assert abs(confidence_after - 0.8) < 0.01
    finally:
        store.close()


def test_batch_recompute_updates_effective_confidence(tmp_path: Path) -> None:
    """batch_recompute should store effective_confidence in structured_assertion."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        now = time.time()

        report = svc.batch_recompute(store, now=now)

        assert isinstance(report, ConfidenceReport)
        assert report.total_evaluated >= 1
        assert report.recomputed_at == now

        record = store.get_memory_record(mid)
        assert record is not None
        assertion = dict(record.structured_assertion or {})
        assert "effective_confidence" in assertion
        assert "confidence_computed_at" in assertion
        assert assertion["confidence_computed_at"] == now
    finally:
        store.close()


def test_compute_confidence_uses_last_validated_at(tmp_path: Path) -> None:
    """When no last_accessed_at in assertion, falls back to last_validated_at."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        now = time.time()
        # Set created_at to 20 days ago
        past = now - 20.0 * 86400.0
        _set_created_at(store, mid, past)

        # Set last_validated_at to 2 days ago (line 61)
        validated_at = now - 2.0 * 86400.0
        store.update_memory_record(mid, last_validated_at=validated_at)

        record = store.get_memory_record(mid)
        assert record is not None
        effective = svc.compute_confidence(record, now=now)

        # Decay from last_validated_at (2 days), not created_at (20 days)
        expected = 0.8 * math.pow(0.5, 2.0 / 14.0)
        assert abs(effective - round(expected, 4)) < 0.01
    finally:
        store.close()


def test_refresh_on_reference_skips_non_active(tmp_path: Path) -> None:
    """refresh_on_reference does nothing for non-active or non-existent memories."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="volatile_fact")
        store.update_memory_record(mid, status="invalidated")

        # Line 83: record.status != "active" → return
        svc.refresh_on_reference(mid, store)

        # Also test with non-existent memory
        svc.refresh_on_reference("nonexistent-id", store)

        # Neither should raise; update should not be called for non-active
        record = store.get_memory_record(mid)
        assert record is not None
        assertion = dict(record.structured_assertion or {})
        assert "last_accessed_at" not in assertion
    finally:
        store.close()


def test_batch_recompute_counts_below_threshold(tmp_path: Path) -> None:
    """batch_recompute should count memories with effective confidence below threshold."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConfidenceDecayService()
        mid = _create_memory(store, confidence=0.8, retention_class="task_state")
        now = time.time()
        # Set created_at to 30 days ago — with 7-day half-life, confidence will be very low
        # decay_factor = 0.5^(30/7) ≈ 0.0155 → effective ≈ 0.012
        past = now - 30.0 * 86400.0
        _set_created_at(store, mid, past)

        report = svc.batch_recompute(store, now=now, low_confidence_threshold=0.1)

        assert report.below_threshold >= 1
    finally:
        store.close()
