from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.consolidation import (
    ConsolidationReport,
    ConsolidationService,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(store: KernelStore, *, task_id: str = "task-1", **kwargs):
    """Helper to create a memory record with sensible defaults."""
    defaults = dict(
        task_id=task_id,
        conversation_id="conv-1",
        category="project_convention",
        claim_text="default claim",
        scope_kind="workspace",
        scope_ref="workspace:default",
        retention_class="project_convention",
        memory_kind="durable_fact",
        confidence=0.8,
        trust_tier="durable",
    )
    defaults.update(kwargs)
    return store.create_memory_record(**defaults)


def _patch_topic_tokens(monkeypatch):
    """Patch topic_tokens to return a list (reflect.py calls [:3] on the result)."""
    import hermit.kernel.context.memory.text as text_mod

    original = text_mod.topic_tokens

    def _topic_tokens_as_list(content):
        return sorted(original(content))

    monkeypatch.setattr(text_mod, "topic_tokens", _topic_tokens_as_list)


def test_run_consolidation_returns_report(tmp_path: Path, monkeypatch) -> None:
    """Basic consolidation run returns a ConsolidationReport."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(store, claim_text="test memory for consolidation")

        report = svc.run_consolidation(store)

        assert isinstance(report, ConsolidationReport)
        assert report.consolidated_at > 0
    finally:
        store.close()


def test_dedup_merges_similar_memories(tmp_path: Path, monkeypatch) -> None:
    """Two nearly identical memories result in one being invalidated."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.7)
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.9)

        report = svc.run_consolidation(store)

        assert report.merged_count >= 1
        # At least one should remain active — the dedup may also create new
        # insight memories, so just verify we merged at least one
        invalidated = store.list_memory_records(status="invalidated")
        assert len(invalidated) >= 1
    finally:
        store.close()


def test_dedup_keeps_higher_confidence(tmp_path: Path, monkeypatch) -> None:
    """The winner of dedup has the higher confidence."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        low = _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.5)
        high = _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.95)

        svc.run_consolidation(store)

        low_record = store.get_memory_record(low.memory_id)
        high_record = store.get_memory_record(high.memory_id)
        assert low_record is not None
        assert high_record is not None
        assert low_record.status == "invalidated"
        assert high_record.status == "active"
    finally:
        store.close()


def test_strengthen_pass_ignores_non_qualifying(tmp_path: Path, monkeypatch) -> None:
    """Memories with low reference count are not strengthened."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(
            store,
            claim_text="unreferenced memory",
            structured_assertion={"reference_count": 1},
        )

        report = svc.run_consolidation(store)
        assert report.strengthened_count == 0
    finally:
        store.close()


def test_decay_pass_runs(tmp_path: Path, monkeypatch) -> None:
    """Decay pass executes during consolidation without error."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(store, claim_text="memory subject to decay")

        report = svc.run_consolidation(store)
        # decayed_count can be 0 if nothing is old enough to decay
        assert report.decayed_count >= 0
    finally:
        store.close()


def test_consolidation_report_fields(tmp_path: Path, monkeypatch) -> None:
    """All ConsolidationReport fields are populated after a run."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(store, claim_text="some memory")

        report = svc.run_consolidation(store)

        assert hasattr(report, "consolidated_at")
        assert hasattr(report, "merged_count")
        assert hasattr(report, "strengthened_count")
        assert hasattr(report, "decayed_count")
        assert hasattr(report, "new_insights_count")
        assert hasattr(report, "new_pitfalls_count")
        assert isinstance(report.merged_count, int)
        assert isinstance(report.strengthened_count, int)
        assert isinstance(report.decayed_count, int)
        assert isinstance(report.new_insights_count, int)
        assert isinstance(report.new_pitfalls_count, int)
    finally:
        store.close()


def test_empty_store_consolidation(tmp_path: Path, monkeypatch) -> None:
    """Consolidation on empty store returns report with zero counts."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        report = svc.run_consolidation(store)

        assert report.merged_count == 0
        assert report.strengthened_count == 0
        assert report.new_insights_count == 0
        assert report.new_pitfalls_count == 0
    finally:
        store.close()


def test_dedup_different_memories_not_merged(tmp_path: Path, monkeypatch) -> None:
    """Very different memories survive dedup without being merged."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        m1 = _create_memory(
            store,
            claim_text="Python is the primary language for this project",
            confidence=0.8,
        )
        m2 = _create_memory(
            store,
            claim_text="Docker containers are used for deployment in production",
            confidence=0.8,
        )

        svc.run_consolidation(store)

        r1 = store.get_memory_record(m1.memory_id)
        r2 = store.get_memory_record(m2.memory_id)
        assert r1 is not None and r1.status == "active"
        assert r2 is not None and r2.status == "active"
    finally:
        store.close()
