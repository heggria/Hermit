from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.context.memory.decay import MemoryDecayService
from hermit.kernel.context.memory.decay_models import FreshnessState
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(store: KernelStore, *, task_id: str = "task-1", **kwargs):
    """Helper to create a memory record with sensible defaults."""
    defaults = dict(
        task_id=task_id,
        conversation_id="conv-1",
        category="user_preference",
        claim_text="User prefers dark mode",
        scope_kind="global",
        scope_ref="global",
        retention_class="user_preference",
        memory_kind="durable_fact",
        confidence=0.8,
        trust_tier="durable",
    )
    defaults.update(kwargs)
    return store.create_memory_record(**defaults)


def _age_memory(store: KernelStore, memory_id: str, created_at: float) -> None:
    """Backdate a memory's created_at via direct SQL."""
    store._conn.execute(
        "UPDATE memory_records SET created_at = ? WHERE memory_id = ?",
        (created_at, memory_id),
    )
    store._conn.commit()


def test_fresh_memory_within_half_ttl(tmp_path: Path) -> None:
    """Memory at 10% TTL consumed should be classified as FRESH."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        record = _create_memory(store, retention_class="user_preference")
        # user_preference TTL = 365 days; put memory at 10% of TTL
        ttl_seconds = 365 * 24 * 60 * 60
        age_seconds = ttl_seconds * 0.10
        _age_memory(store, record.memory_id, now - age_seconds)

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        assert assessment.freshness_state == FreshnessState.FRESH
        assert assessment.pct_remaining > 0.50
    finally:
        store.close()


def test_aging_memory_between_half_and_three_quarter_ttl(tmp_path: Path) -> None:
    """Memory at 60% TTL consumed should be classified as AGING."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        record = _create_memory(store, retention_class="user_preference")
        ttl_seconds = 365 * 24 * 60 * 60
        age_seconds = ttl_seconds * 0.60
        _age_memory(store, record.memory_id, now - age_seconds)

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        assert assessment.freshness_state == FreshnessState.AGING
    finally:
        store.close()


def test_stale_memory_between_three_quarter_and_ninety_pct(tmp_path: Path) -> None:
    """Memory at 80% TTL consumed should be classified as STALE."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        record = _create_memory(store, retention_class="user_preference")
        ttl_seconds = 365 * 24 * 60 * 60
        age_seconds = ttl_seconds * 0.80
        _age_memory(store, record.memory_id, now - age_seconds)

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        assert assessment.freshness_state == FreshnessState.STALE
    finally:
        store.close()


def test_expired_memory_beyond_ninety_pct(tmp_path: Path) -> None:
    """Memory at 95% TTL consumed should be classified as EXPIRED."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        record = _create_memory(store, retention_class="user_preference")
        ttl_seconds = 365 * 24 * 60 * 60
        age_seconds = ttl_seconds * 0.95
        _age_memory(store, record.memory_id, now - age_seconds)

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        assert assessment.freshness_state == FreshnessState.EXPIRED
    finally:
        store.close()


def test_decay_sweep_updates_freshness_class(tmp_path: Path) -> None:
    """run_decay_sweep() should update structured_assertion with freshness_class."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        record = _create_memory(store, retention_class="user_preference")
        # Age to 60% TTL so it transitions to AGING
        ttl_seconds = 365 * 24 * 60 * 60
        age_seconds = ttl_seconds * 0.60
        _age_memory(store, record.memory_id, now - age_seconds)

        report = service.run_decay_sweep(store, now=now)

        assert report.total_evaluated >= 1
        assert len(report.transitions) >= 1

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assertion = dict(refreshed.structured_assertion or {})
        assert assertion.get("freshness_class") == FreshnessState.AGING.value
        assert "last_decay_sweep" in assertion
    finally:
        store.close()


def test_quarantine_moves_to_quarantined_status(tmp_path: Path) -> None:
    """quarantine() should set status to 'quarantined' and record the reason."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        record = _create_memory(store)

        result = service.quarantine(store, record.memory_id, reason="test decay")

        assert result is True
        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assert refreshed.status == "quarantined"
        assert refreshed.invalidation_reason is not None
        assert "test decay" in refreshed.invalidation_reason
    finally:
        store.close()


def test_revive_restores_quarantined_memory(tmp_path: Path) -> None:
    """revive() should reset a quarantined memory to active with fresh evidence."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        record = _create_memory(store)

        # First quarantine the memory
        service.quarantine(store, record.memory_id, reason="stale")

        # Then revive it with new evidence
        result = service.revive(store, record.memory_id, new_evidence_refs=["ev-new-1", "ev-new-2"])

        assert result is True
        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assert refreshed.status == "active"
        assertion = dict(refreshed.structured_assertion or {})
        assert assertion.get("freshness_class") == FreshnessState.FRESH.value
        assert "ev-new-1" in assertion.get("evidence_refs", [])
        assert "ev-new-2" in assertion.get("evidence_refs", [])
    finally:
        store.close()


def test_sweep_skips_audit_retention(tmp_path: Path) -> None:
    """Memories with retention_class='audit' should be skipped during sweep."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()

        # Create an audit memory and age it past expiry
        audit_record = _create_memory(
            store,
            retention_class="audit",
            claim_text="Audit log entry",
        )
        ttl_seconds = 30 * 24 * 60 * 60  # fallback TTL
        _age_memory(store, audit_record.memory_id, now - ttl_seconds * 0.95)

        # Create a normal memory and age it too
        normal_record = _create_memory(
            store,
            task_id="task-2",
            retention_class="volatile_fact",
            claim_text="Normal memory",
        )
        vol_ttl = 24 * 60 * 60  # volatile_fact TTL = 1 day
        _age_memory(store, normal_record.memory_id, now - vol_ttl * 0.95)

        report = service.run_decay_sweep(store, now=now)

        # Audit memory should not appear in quarantine candidates
        assert audit_record.memory_id not in report.quarantine_candidates
        # Normal expired memory should appear
        assert normal_record.memory_id in report.quarantine_candidates
    finally:
        store.close()


def test_sweep_collects_quarantine_candidates(tmp_path: Path) -> None:
    """Expired memories should appear in the report's quarantine_candidates list."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()

        record = _create_memory(store, retention_class="volatile_fact")
        # volatile_fact TTL = 1 day; age to 95% → EXPIRED
        ttl_seconds = 24 * 60 * 60
        _age_memory(store, record.memory_id, now - ttl_seconds * 0.95)

        report = service.run_decay_sweep(store, now=now)

        assert record.memory_id in report.quarantine_candidates
        assert report.total_evaluated >= 1
    finally:
        store.close()


def test_evaluate_freshness_with_last_accessed_at(tmp_path: Path) -> None:
    """When structured_assertion has last_accessed_at, it should be reported in the assessment."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        accessed_at = now - 2 * 86400  # 2 days ago
        record = _create_memory(
            store,
            retention_class="user_preference",
            structured_assertion={"last_accessed_at": accessed_at},
        )

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        # Line 67: last_accessed_days_ago should be approximately 2.0
        assert assessment.last_accessed_days_ago is not None
        assert abs(assessment.last_accessed_days_ago - 2.0) < 0.1
    finally:
        store.close()


def test_quarantine_returns_false_for_nonexistent(tmp_path: Path) -> None:
    """quarantine() returns False when memory does not exist or is not active."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()

        # Non-existent memory (line 147: record is None)
        result = service.quarantine(store, "nonexistent-id", reason="test")
        assert result is False
    finally:
        store.close()


def test_quarantine_returns_false_for_non_active(tmp_path: Path) -> None:
    """quarantine() returns False when memory is not in active status."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        record = _create_memory(store)
        store.update_memory_record(record.memory_id, status="invalidated")

        # Line 147: record.status != "active"
        result = service.quarantine(store, record.memory_id, reason="test")
        assert result is False
    finally:
        store.close()


def test_revive_returns_false_for_nonexistent(tmp_path: Path) -> None:
    """revive() returns False when memory does not exist."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()

        # Line 166: record is None
        result = service.revive(store, "nonexistent-id", new_evidence_refs=["ev-1"])
        assert result is False
    finally:
        store.close()


def test_revive_returns_false_for_active_memory(tmp_path: Path) -> None:
    """revive() returns False when memory is active (not quarantined)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        record = _create_memory(store)

        # Line 166: record.status != "quarantined"
        result = service.revive(store, record.memory_id, new_evidence_refs=["ev-1"])
        assert result is False
    finally:
        store.close()


def test_effective_ttl_uses_explicit_expires_at(tmp_path: Path) -> None:
    """When memory has both created_at and expires_at, TTL is computed from them."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryDecayService()
        now = time.time()
        expires = now + 7200  # 2 hours from now
        record = _create_memory(store, retention_class="volatile_fact")

        # Set expires_at via SQL to trigger line 197
        store._conn.execute(
            "UPDATE memory_records SET expires_at = ? WHERE memory_id = ?",
            (expires, record.memory_id),
        )
        store._conn.commit()

        refreshed = store.get_memory_record(record.memory_id)
        assert refreshed is not None
        assessment = service.evaluate_freshness(refreshed, now=now)

        # With expires_at set, the TTL should be based on expires_at - created_at
        assert assessment.freshness_state == FreshnessState.FRESH
    finally:
        store.close()
