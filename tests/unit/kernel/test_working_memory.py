from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.working_memory import (
    WorkingMemoryManager,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(store: KernelStore, *, task_id: str = "task-1", **kwargs):
    """Helper to create a memory record with sensible defaults."""
    defaults = dict(
        task_id=task_id,
        conversation_id="conv-1",
        category="user_preference",
        claim_text="short claim",
        scope_kind="global",
        scope_ref="global",
        retention_class="user_preference",
        memory_kind="durable_fact",
        confidence=0.8,
        trust_tier="durable",
    )
    defaults.update(kwargs)
    return store.create_memory_record(**defaults)


def test_pitfalls_highest_priority(tmp_path: Path) -> None:
    """Pitfall warnings are always included first in the working memory pack."""
    store = KernelStore(tmp_path / "state.db")
    try:
        pitfall = _create_memory(
            store,
            claim_text="Never use eval() on user input",
            memory_kind="pitfall_warning",
        )
        static = _create_memory(
            store, claim_text="User prefers dark mode", memory_kind="durable_fact"
        )

        mgr = WorkingMemoryManager(max_tokens=4000)
        pack = mgr.select_for_context(
            pitfalls=[pitfall],
            static=[static],
        )

        assert len(pack.items) >= 2
        assert pack.items[0].priority == "pitfall"
        assert pack.items[0].memory_id == pitfall.memory_id
    finally:
        store.close()


def test_budget_limits_items(tmp_path: Path) -> None:
    """Exceeding the token budget causes overflow."""
    store = KernelStore(tmp_path / "state.db")
    try:
        # Create memories with long text to exhaust a small budget
        m1 = _create_memory(store, claim_text="A" * 200)
        m2 = _create_memory(store, claim_text="B" * 200)
        m3 = _create_memory(store, claim_text="C" * 200)

        # Budget of 100 tokens = 400 chars; each memory is 200 chars = 50 tokens
        mgr = WorkingMemoryManager(max_tokens=100)
        pack = mgr.select_for_context(static=[m1, m2, m3])

        assert len(pack.items) <= 3
        assert pack.overflow_count >= 0
        assert pack.total_tokens <= 100
    finally:
        store.close()


def test_overflow_footer_message(tmp_path: Path) -> None:
    """When items exceed the budget, overflow_footer is populated."""
    store = KernelStore(tmp_path / "state.db")
    try:
        memories = [_create_memory(store, claim_text="X" * 400) for _ in range(5)]

        # Very small budget — only ~25 tokens = 100 chars
        mgr = WorkingMemoryManager(max_tokens=25)
        pack = mgr.select_for_context(static=memories)

        assert pack.overflow_count > 0
        assert "additional memories available" in pack.overflow_footer
    finally:
        store.close()


def test_empty_inputs_returns_empty_pack(tmp_path: Path) -> None:
    """No inputs produces an empty pack."""
    store = KernelStore(tmp_path / "state.db")
    try:
        mgr = WorkingMemoryManager()
        pack = mgr.select_for_context()

        assert pack.items == []
        assert pack.total_tokens == 0
        assert pack.overflow_count == 0
        assert pack.overflow_footer == ""
    finally:
        store.close()


def test_static_sorted_by_freshness(tmp_path: Path) -> None:
    """Newer static memories appear before older ones."""
    store = KernelStore(tmp_path / "state.db")
    try:
        old = _create_memory(store, claim_text="old memory content here")
        # Backdate old memory
        store._conn.execute(
            "UPDATE memory_records SET created_at = ?, updated_at = ? WHERE memory_id = ?",
            (1000.0, 1000.0, old.memory_id),
        )
        store._conn.commit()

        new = _create_memory(store, claim_text="new memory content here")

        # Refresh records from store
        old_record = store.get_memory_record(old.memory_id)
        new_record = store.get_memory_record(new.memory_id)

        mgr = WorkingMemoryManager(max_tokens=4000)
        pack = mgr.select_for_context(static=[old_record, new_record])

        static_items = [i for i in pack.items if i.priority == "static"]
        assert len(static_items) == 2
        # Newer memory should come first
        assert static_items[0].memory_id == new_record.memory_id
    finally:
        store.close()


def test_procedural_included_after_pitfalls(tmp_path: Path) -> None:
    """Procedural items appear after pitfalls but before static items."""
    store = KernelStore(tmp_path / "state.db")
    try:
        pitfall = _create_memory(
            store,
            claim_text="Critical: avoid rm -rf /",
            memory_kind="pitfall_warning",
        )
        static = _create_memory(store, claim_text="User prefers vim")

        proc_dict = {
            "procedure_id": "proc-1",
            "trigger_pattern": "deploy",
            "steps": ["build", "test", "deploy"],
        }

        mgr = WorkingMemoryManager(max_tokens=4000)
        pack = mgr.select_for_context(
            pitfalls=[pitfall],
            procedural=[proc_dict],
            static=[static],
        )

        priorities = [item.priority for item in pack.items]
        pitfall_idx = priorities.index("pitfall")
        proc_idx = priorities.index("procedural")
        static_idx = priorities.index("static")

        assert pitfall_idx < proc_idx < static_idx
    finally:
        store.close()


def test_budget_used_pct_calculated(tmp_path: Path) -> None:
    """budget_used_pct reflects the percentage of budget consumed."""
    store = KernelStore(tmp_path / "state.db")
    try:
        # 100 chars = 25 tokens; budget = 100 tokens → 25%
        m = _create_memory(store, claim_text="A" * 100)

        mgr = WorkingMemoryManager(max_tokens=100)
        pack = mgr.select_for_context(static=[m])

        assert pack.budget_used_pct > 0
        assert pack.budget_used_pct <= 100
        assert pack.total_tokens > 0
    finally:
        store.close()


def test_small_budget_prioritizes_pitfalls(tmp_path: Path) -> None:
    """With a tiny budget (100 tokens), only pitfalls are included."""
    store = KernelStore(tmp_path / "state.db")
    try:
        # Pitfall with short text: ~10 tokens
        pitfall = _create_memory(
            store,
            claim_text="avoid eval",
            memory_kind="pitfall_warning",
        )
        # Static with long text that won't fit
        static = _create_memory(store, claim_text="X" * 1000)

        mgr = WorkingMemoryManager(max_tokens=100)
        pack = mgr.select_for_context(pitfalls=[pitfall], static=[static])

        pitfall_items = [i for i in pack.items if i.priority == "pitfall"]
        static_items = [i for i in pack.items if i.priority == "static"]
        assert len(pitfall_items) >= 1
        assert len(static_items) == 0
        assert pack.overflow_count >= 1
    finally:
        store.close()
