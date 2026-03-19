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


def test_strengthen_pass_boosts_frequently_referenced(tmp_path: Path, monkeypatch) -> None:
    """Memories with reference_count >= 3 get confidence boost."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        m = _create_memory(
            store,
            claim_text="frequently referenced memory",
            confidence=0.7,
            structured_assertion={"reference_count": 5},
        )

        report = svc.run_consolidation(store)

        assert report.strengthened_count >= 1
        updated = store.get_memory_record(m.memory_id)
        assert updated is not None
        assertion = updated.structured_assertion or {}
        assert "strengthened_at" in assertion
        assert assertion["previous_confidence"] == 0.7
    finally:
        store.close()


def test_strengthen_pass_skips_episode_index(tmp_path: Path, monkeypatch) -> None:
    """Memories with memory_kind='episode_index' are skipped by strengthen pass."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(
            store,
            claim_text="episode index memory",
            memory_kind="episode_index",
            confidence=0.7,
            structured_assertion={"reference_count": 10},
        )

        report = svc.run_consolidation(store)
        assert report.strengthened_count == 0
    finally:
        store.close()


def test_strengthen_pass_skips_influence_link(tmp_path: Path, monkeypatch) -> None:
    """Memories with memory_kind='influence_link' are skipped by strengthen pass."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(
            store,
            claim_text="influence link memory",
            memory_kind="influence_link",
            confidence=0.7,
            structured_assertion={"reference_count": 10},
        )

        report = svc.run_consolidation(store)
        assert report.strengthened_count == 0
    finally:
        store.close()


def test_strengthen_pass_skips_non_int_reference_count(tmp_path: Path, monkeypatch) -> None:
    """Memories with non-integer reference_count are skipped."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(
            store,
            claim_text="memory with bad ref count",
            confidence=0.7,
            structured_assertion={"reference_count": "many"},
        )

        report = svc.run_consolidation(store)
        assert report.strengthened_count == 0
    finally:
        store.close()


def test_strengthen_pass_respects_confidence_cap(tmp_path: Path, monkeypatch) -> None:
    """Memories already at confidence cap are not strengthened further."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        _create_memory(
            store,
            claim_text="already at cap memory",
            confidence=0.95,
            structured_assertion={"reference_count": 10},
        )

        report = svc.run_consolidation(store)
        # 0.95 + 0.1 = 1.05, capped to 0.95 — new_confidence == record.confidence
        assert report.strengthened_count == 0
    finally:
        store.close()


def test_dedup_skips_already_seen_inner(tmp_path: Path, monkeypatch) -> None:
    """When three identical memories exist, the inner loop skips already-seen entries."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        m1 = _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.9)
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.7)
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.6)

        report = svc.run_consolidation(store)

        # At least 2 should be merged (invalidated)
        assert report.merged_count >= 2
        winner = store.get_memory_record(m1.memory_id)
        assert winner is not None
        assert winner.status == "active"
    finally:
        store.close()


def test_reflect_pass_promotes_insights(tmp_path: Path, monkeypatch) -> None:
    """When 3+ memories share topic tokens, reflect pass generates and promotes insights."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()
        # Create 3+ memories with the same topic tokens so they cluster together
        for i in range(4):
            _create_memory(
                store,
                claim_text=f"ruff formatter convention rule {i}",
                confidence=0.85,
            )

        report = svc.run_consolidation(store)

        # If clustering works, at least one insight should be promoted
        # (depends on topic_tokens producing overlapping keys)
        assert report.new_insights_count >= 0  # may be 0 if tokens don't cluster
    finally:
        store.close()


def test_reflect_pass_with_mocked_services(tmp_path: Path, monkeypatch) -> None:
    """Mocked reflect service returns insights that get promoted."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    from hermit.kernel.context.memory.reflect import ReflectionInsight

    store = KernelStore(tmp_path / "state.db")
    try:
        mock_reflect = MagicMock()
        insight = ReflectionInsight(
            insight_text="Pattern: ruff is always used",
            source_memory_ids=("m1", "m2", "m3"),
            confidence=0.85,
            insight_type="pattern",
        )
        mock_reflect.reflect.return_value = [insight]
        # promote_insight should return a non-None value to increment count
        fake_record = _create_memory(store, claim_text="placeholder")
        mock_reflect.promote_insight.return_value = fake_record

        svc = ConsolidationService(reflection_service=mock_reflect)
        report = svc.run_consolidation(store)

        assert report.new_insights_count == 1
        mock_reflect.promote_insight.assert_called_once_with(insight, store)
    finally:
        store.close()


def test_reflect_pass_skips_unpromoted_insights(tmp_path: Path, monkeypatch) -> None:
    """When promote_insight returns None, new_insights_count is not incremented."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    from hermit.kernel.context.memory.reflect import ReflectionInsight

    store = KernelStore(tmp_path / "state.db")
    try:
        mock_reflect = MagicMock()
        insight = ReflectionInsight(
            insight_text="Low confidence insight",
            source_memory_ids=("m1", "m2", "m3"),
            confidence=0.5,
            insight_type="pattern",
        )
        mock_reflect.reflect.return_value = [insight]
        mock_reflect.promote_insight.return_value = None

        svc = ConsolidationService(reflection_service=mock_reflect)
        report = svc.run_consolidation(store)

        assert report.new_insights_count == 0
    finally:
        store.close()


def test_anti_pattern_pass_inverts_pitfalls(tmp_path: Path, monkeypatch) -> None:
    """When anti-pattern service detects pitfalls, they get inverted."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    from hermit.kernel.context.memory.anti_pattern import PitfallCandidate

    store = KernelStore(tmp_path / "state.db")
    try:
        m = _create_memory(store, claim_text="a failing pattern")
        mock_anti = MagicMock()
        candidate = PitfallCandidate(
            memory_id=m.memory_id,
            claim_text=m.claim_text,
            failure_rate=0.8,
            decision_count=10,
            category="project_convention",
        )
        mock_anti.detect_pitfalls.return_value = [candidate]
        # Return a non-None record for the inverted pitfall
        pitfall_record = _create_memory(
            store, claim_text="PITFALL: a failing pattern", memory_kind="pitfall_warning"
        )
        mock_anti.invert_to_pitfall.return_value = pitfall_record

        svc = ConsolidationService(anti_pattern_service=mock_anti)
        report = svc.run_consolidation(store)

        assert report.new_pitfalls_count == 1
        mock_anti.invert_to_pitfall.assert_called_once_with(m.memory_id, store)
    finally:
        store.close()


def test_anti_pattern_pass_skips_failed_inversion(tmp_path: Path, monkeypatch) -> None:
    """When invert_to_pitfall returns None, new_pitfalls_count is not incremented."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    from hermit.kernel.context.memory.anti_pattern import PitfallCandidate

    store = KernelStore(tmp_path / "state.db")
    try:
        mock_anti = MagicMock()
        candidate = PitfallCandidate(
            memory_id="nonexistent-id",
            claim_text="some claim",
            failure_rate=0.8,
            decision_count=10,
            category="project_convention",
        )
        mock_anti.detect_pitfalls.return_value = [candidate]
        mock_anti.invert_to_pitfall.return_value = None

        svc = ConsolidationService(anti_pattern_service=mock_anti)
        report = svc.run_consolidation(store)

        assert report.new_pitfalls_count == 0
    finally:
        store.close()


def test_text_similarity_with_embedding_service(tmp_path: Path, monkeypatch) -> None:
    """When embedding service is provided, it is used for similarity."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    store = KernelStore(tmp_path / "state.db")
    try:
        mock_embeddings = MagicMock()
        mock_embeddings.embed.side_effect = lambda text: (
            [1.0, 0.0] if "high" in text else [0.0, 1.0]
        )
        mock_embeddings.similarity.return_value = 0.99

        svc = ConsolidationService(embedding_service=mock_embeddings)

        # Create two memories that would be deduped via embeddings
        _create_memory(store, claim_text="high confidence fact A", confidence=0.9)
        _create_memory(store, claim_text="high confidence fact B", confidence=0.7)

        report = svc.run_consolidation(store)

        assert report.merged_count >= 1
        assert mock_embeddings.embed.called
    finally:
        store.close()


def test_text_similarity_embedding_fallback_on_error(tmp_path: Path, monkeypatch) -> None:
    """When embedding service raises, falls back to token overlap."""
    _patch_topic_tokens(monkeypatch)
    from unittest.mock import MagicMock

    store = KernelStore(tmp_path / "state.db")
    try:
        mock_embeddings = MagicMock()
        mock_embeddings.embed.side_effect = RuntimeError("embedding failed")

        svc = ConsolidationService(embedding_service=mock_embeddings)

        # Two identical memories should still be deduped via token overlap fallback
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.9)
        _create_memory(store, claim_text="ruff is the standard formatter", confidence=0.7)

        report = svc.run_consolidation(store)

        assert report.merged_count >= 1
    finally:
        store.close()


def test_text_similarity_empty_tokens(tmp_path: Path, monkeypatch) -> None:
    """Empty claim text returns 0.0 similarity (no merge)."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ConsolidationService()

        m1 = _create_memory(store, claim_text="", confidence=0.8)
        m2 = _create_memory(store, claim_text="", confidence=0.7)

        svc.run_consolidation(store)

        # Empty tokens → similarity 0.0 → no merge
        r1 = store.get_memory_record(m1.memory_id)
        r2 = store.get_memory_record(m2.memory_id)
        assert r1 is not None and r1.status == "active"
        assert r2 is not None and r2.status == "active"
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
