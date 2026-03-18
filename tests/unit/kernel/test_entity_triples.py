from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.graph import (
    MemoryGraphService,
    _load_triples_for,
    _store_triple,
    ensure_graph_schema,
)
from hermit.kernel.context.memory.graph_models import EntityTriple
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


def _make_triple(
    *,
    source_memory_id: str = "mem-1",
    subject: str = "project",
    predicate: str = "uses",
    object_: str = "python",
    confidence: float = 0.8,
) -> EntityTriple:
    import time
    import uuid

    return EntityTriple(
        triple_id=f"tri-{uuid.uuid4().hex[:12]}",
        source_memory_id=source_memory_id,
        subject=subject,
        predicate=predicate,
        object_=object_,
        confidence=confidence,
        valid_from=time.time(),
        created_at=time.time(),
    )


def test_store_and_load_triple(tmp_path: Path) -> None:
    """store_triple then _load_triples_for returns the stored triple."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        triple = _make_triple(source_memory_id="mem-abc")
        _store_triple(triple, store)

        loaded = _load_triples_for("mem-abc", store)
        assert len(loaded) == 1
        assert loaded[0].triple_id == triple.triple_id
    finally:
        store.close()


def test_triple_fields_preserved(tmp_path: Path) -> None:
    """All EntityTriple fields roundtrip correctly through store/load."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        triple = _make_triple(
            source_memory_id="mem-fields",
            subject="hermit",
            predicate="requires",
            object_="python 3.13",
            confidence=0.92,
        )
        _store_triple(triple, store)

        loaded = _load_triples_for("mem-fields", store)
        assert len(loaded) == 1
        t = loaded[0]
        assert t.subject == "hermit"
        assert t.predicate == "requires"
        assert t.object_ == "python 3.13"
        assert t.confidence == 0.92
        assert t.valid_from == triple.valid_from
    finally:
        store.close()


def test_multiple_triples_same_memory(tmp_path: Path) -> None:
    """Multiple triples for one memory are all returned."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        t1 = _make_triple(source_memory_id="mem-multi", subject="a", object_="b")
        t2 = _make_triple(source_memory_id="mem-multi", subject="c", object_="d")
        t3 = _make_triple(source_memory_id="mem-multi", subject="e", object_="f")
        svc.store_triples([t1, t2, t3], store)

        loaded = _load_triples_for("mem-multi", store)
        assert len(loaded) == 3
        subjects = {t.subject for t in loaded}
        assert subjects == {"a", "c", "e"}
    finally:
        store.close()


def test_triples_indexed_by_subject(tmp_path: Path) -> None:
    """Triples can be queried by subject via direct SQL."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        t1 = _make_triple(source_memory_id="mem-x", subject="ruff", object_="formatter")
        t2 = _make_triple(source_memory_id="mem-y", subject="ruff", object_="linter")
        t3 = _make_triple(source_memory_id="mem-z", subject="pytest", object_="runner")
        _store_triple(t1, store)
        _store_triple(t2, store)
        _store_triple(t3, store)

        with store._lock:
            rows = store._conn.execute(
                "SELECT * FROM memory_entity_triples WHERE subject = ?", ("ruff",)
            ).fetchall()

        assert len(rows) == 2
    finally:
        store.close()


def test_empty_triples_for_unknown_memory(tmp_path: Path) -> None:
    """Loading triples for a non-existent memory returns an empty list."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        loaded = _load_triples_for("mem-nonexistent", store)
        assert loaded == []
    finally:
        store.close()
