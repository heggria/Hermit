from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.anti_pattern import AntiPatternService
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(
    store: KernelStore,
    *,
    confidence: float = 0.2,
    claim_text: str = "test memory",
    retention_class: str = "volatile_fact",
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


def test_invert_to_pitfall_creates_warning(tmp_path: Path) -> None:
    """invert_to_pitfall should create a new pitfall_warning memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        mid = _create_memory(store, confidence=0.2, claim_text="always use X")

        pitfall = svc.invert_to_pitfall(mid, store, task_id="task-invert")
        assert pitfall is not None
        assert pitfall.memory_kind == "pitfall_warning"
        assert pitfall.memory_id != mid
    finally:
        store.close()


def test_invert_invalidates_original(tmp_path: Path) -> None:
    """invert_to_pitfall should invalidate the original memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        mid = _create_memory(store, confidence=0.2, claim_text="always use Y")

        svc.invert_to_pitfall(mid, store, task_id="task-invert")

        original = store.get_memory_record(mid)
        assert original is not None
        assert original.status == "invalidated"
        assert original.invalidation_reason == "inverted_to_pitfall"
    finally:
        store.close()


def test_pitfall_confidence_capped(tmp_path: Path) -> None:
    """Pitfall confidence should not exceed 0.95 even with high multiplier."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        # confidence=0.5, multiplier=4.0 → 2.0, should be capped at 0.95
        mid = _create_memory(store, confidence=0.5, claim_text="high confidence claim")

        pitfall = svc.invert_to_pitfall(mid, store, task_id="task-cap")
        assert pitfall is not None
        assert pitfall.confidence == 0.95
    finally:
        store.close()


def test_pitfall_text_has_prefix(tmp_path: Path) -> None:
    """Pitfall claim_text should start with 'PITFALL: '."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        mid = _create_memory(store, confidence=0.2, claim_text="never use Z")

        pitfall = svc.invert_to_pitfall(mid, store, task_id="task-prefix")
        assert pitfall is not None
        assert pitfall.claim_text == "PITFALL: never use Z"
    finally:
        store.close()


def test_invert_returns_none_for_invalid(tmp_path: Path) -> None:
    """invert_to_pitfall should return None for non-active memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        mid = _create_memory(store, confidence=0.2, claim_text="some claim")

        # Invalidate the memory first
        store.update_memory_record(mid, status="invalidated", invalidation_reason="test")

        result = svc.invert_to_pitfall(mid, store, task_id="task-invalid")
        assert result is None
    finally:
        store.close()


def test_detect_pitfalls_empty_when_no_links(tmp_path: Path) -> None:
    """detect_pitfalls should return empty list when no influence links exist."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = AntiPatternService()
        _create_memory(store, confidence=0.5, claim_text="no links here")

        candidates = svc.detect_pitfalls(store, min_decisions=1, failure_rate_threshold=0.5)
        assert candidates == []
    finally:
        store.close()
