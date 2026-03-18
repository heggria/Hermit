from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.reflect import ReflectionInsight, ReflectionService
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


def test_reflect_finds_topic_clusters(tmp_path: Path, monkeypatch) -> None:
    """3+ memories on the same topic produce at least one insight."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        # Create 3 memories with overlapping topic tokens so they cluster together.
        # The varying word sorts after the first 3 to ensure the same cluster key.
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_linting")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_checks")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_style")

        insights = svc.reflect(store)
        assert len(insights) >= 1
    finally:
        store.close()


def test_reflect_returns_empty_below_cluster_size(tmp_path: Path, monkeypatch) -> None:
    """Fewer than 3 memories on a topic produces no insights."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_linting")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_checks")

        insights = svc.reflect(store)
        assert insights == []
    finally:
        store.close()


def test_promote_insight_creates_memory(tmp_path: Path) -> None:
    """Promoting an insight with confidence >= 0.7 creates a durable_fact memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        insight = ReflectionInsight(
            insight_text="Ruff is the standard formatter",
            source_memory_ids=("mem-1", "mem-2", "mem-3"),
            confidence=0.85,
            insight_type="pattern",
        )

        record = svc.promote_insight(insight, store, task_id="t-reflect")
        assert record is not None
        assert record.memory_kind == "durable_fact"
        assert record.claim_text == "Ruff is the standard formatter"
        assert record.confidence == 0.85
        assert record.structured_assertion["epistemic_origin"] == "reflection"
    finally:
        store.close()


def test_promote_insight_below_threshold(tmp_path: Path) -> None:
    """Insight with confidence < 0.7 is not promoted."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        insight = ReflectionInsight(
            insight_text="Maybe useful pattern",
            source_memory_ids=("mem-1",),
            confidence=0.5,
            insight_type="pattern",
        )

        result = svc.promote_insight(insight, store)
        assert result is None
    finally:
        store.close()


def test_insight_types_generalization(tmp_path: Path, monkeypatch) -> None:
    """A cluster of 5+ memories produces a 'generalization' insight type."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        for i in range(5):
            _create_memory(store, claim_text=f"ruff formatter mandatory tool zzz_rule_{i}")

        insights = svc.reflect(store)
        assert len(insights) >= 1
        assert insights[0].insight_type == "generalization"
    finally:
        store.close()


def test_insight_types_pattern(tmp_path: Path, monkeypatch) -> None:
    """A cluster of 3-4 memories produces a 'pattern' insight type."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_linting")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_checks")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_style")

        insights = svc.reflect(store)
        assert len(insights) >= 1
        assert insights[0].insight_type == "pattern"
    finally:
        store.close()


def test_reflect_skips_small_clusters(tmp_path: Path, monkeypatch) -> None:
    """Clusters smaller than _MIN_CLUSTER_SIZE (3) are skipped in reflect loop."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        # Create 2 memories with one topic and 2 with another — neither reaches 3
        _create_memory(store, claim_text="alpha beta gamma zzz_one")
        _create_memory(store, claim_text="alpha beta gamma zzz_two")

        # Line 60: cluster size < _MIN_CLUSTER_SIZE → continue
        insights = svc.reflect(store)
        assert insights == []
    finally:
        store.close()


def test_synthesize_cluster_returns_none_below_min(tmp_path: Path) -> None:
    """_synthesize_cluster returns None when cluster has fewer than 3 memories."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        m1 = _create_memory(store, claim_text="claim one")
        m2 = _create_memory(store, claim_text="claim two")

        records = [store.get_memory_record(m.memory_id) for m in [m1, m2]]
        records = [r for r in records if r is not None]

        # Line 141: len(cluster) < _MIN_CLUSTER_SIZE → return None
        result = svc._synthesize_cluster(records)
        assert result is None
    finally:
        store.close()


def test_insight_type_contradiction_resolution(tmp_path: Path, monkeypatch) -> None:
    """A cluster containing a pitfall_warning should produce 'contradiction_resolution' type."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_linting")
        _create_memory(store, claim_text="ruff formatter mandatory tool zzz_checks")
        # Lines 149-150: pitfall_warning in cluster → contradiction_resolution
        _create_memory(
            store,
            claim_text="ruff formatter mandatory tool zzz_warn",
            memory_kind="pitfall_warning",
        )

        insights = svc.reflect(store)
        assert len(insights) >= 1
        assert insights[0].insight_type == "contradiction_resolution"
    finally:
        store.close()


def test_insight_includes_source_ids(tmp_path: Path, monkeypatch) -> None:
    """Synthesized insight includes source_memory_ids from the cluster."""
    _patch_topic_tokens(monkeypatch)
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = ReflectionService()
        m1 = _create_memory(store, claim_text="ruff formatter mandatory tool zzz_linting")
        m2 = _create_memory(store, claim_text="ruff formatter mandatory tool zzz_checks")
        m3 = _create_memory(store, claim_text="ruff formatter mandatory tool zzz_style")

        insights = svc.reflect(store)
        assert len(insights) >= 1

        source_ids = set(insights[0].source_memory_ids)
        assert m1.memory_id in source_ids
        assert m2.memory_id in source_ids
        assert m3.memory_id in source_ids
    finally:
        store.close()
