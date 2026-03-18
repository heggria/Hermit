from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.lineage import MemoryLineageService
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


def test_record_influence_creates_links(tmp_path: Path) -> None:
    """record_influence should create influence_link memory records."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Important preference")
        decision = store.create_decision(
            task_id="t1",
            step_id="s1",
            step_attempt_id="sa1",
            decision_type="policy",
            verdict="approved",
            reason="ok",
        )

        links = service.record_influence(
            context_pack_id="cp-1",
            decision_ids=[decision.decision_id],
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t1",
        )

        assert len(links) == 1
        assert links[0].decision_id == decision.decision_id
        assert links[0].memory_id == mem.memory_id
        assert links[0].context_pack_id == "cp-1"

        # Verify the influence_link record was persisted
        all_records = store.list_memory_records(status="active", limit=100)
        link_records = [r for r in all_records if r.memory_kind == "influence_link"]
        assert len(link_records) >= 1
    finally:
        store.close()


def test_trace_decision_finds_memories(tmp_path: Path) -> None:
    """trace_decision should return the memories that influenced a decision."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem_a = _create_memory(store, claim_text="Preference A")
        mem_b = _create_memory(store, task_id="task-2", claim_text="Preference B")
        decision = store.create_decision(
            task_id="t1",
            step_id="s1",
            step_attempt_id="sa1",
            decision_type="policy",
            verdict="approved",
            reason="ok",
        )

        service.record_influence(
            context_pack_id="cp-1",
            decision_ids=[decision.decision_id],
            memory_ids=[mem_a.memory_id, mem_b.memory_id],
            store=store,
            task_id="t1",
        )

        lineage = service.trace_decision(decision.decision_id, store)

        assert lineage.decision_id == decision.decision_id
        assert mem_a.memory_id in lineage.influencing_memories
        assert mem_b.memory_id in lineage.influencing_memories
        assert lineage.link_count == 2
        assert "cp-1" in lineage.context_pack_ids
    finally:
        store.close()


def test_trace_memory_finds_decisions(tmp_path: Path) -> None:
    """trace_memory should return decisions influenced by a given memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Shared preference")
        decision_a = store.create_decision(
            task_id="t1",
            step_id="s1",
            step_attempt_id="sa1",
            decision_type="policy",
            verdict="approved",
            reason="ok",
        )
        decision_b = store.create_decision(
            task_id="t2",
            step_id="s2",
            step_attempt_id="sa2",
            decision_type="policy",
            verdict="denied",
            reason="rejected",
        )

        service.record_influence(
            context_pack_id="cp-1",
            decision_ids=[decision_a.decision_id, decision_b.decision_id],
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t1",
        )

        impact = service.trace_memory(mem.memory_id, store)

        assert impact.memory_id == mem.memory_id
        assert decision_a.decision_id in impact.influenced_decisions
        assert decision_b.decision_id in impact.influenced_decisions
        assert impact.total_decisions == 2
        assert impact.success_count == 1
        assert impact.failure_count == 1
        assert impact.failure_rate == 0.5
    finally:
        store.close()


def test_find_stale_influencers_above_threshold(tmp_path: Path) -> None:
    """find_stale_influencers should find memories with high failure rate."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Bad advice memory")

        # Create 6 decisions: 5 failed, 1 succeeded → failure_rate = 5/6 ≈ 0.833
        decision_ids = []
        for i in range(5):
            d = store.create_decision(
                task_id=f"t-fail-{i}",
                step_id=f"s-{i}",
                step_attempt_id=f"sa-{i}",
                decision_type="policy",
                verdict="denied",
                reason="failed",
            )
            decision_ids.append(d.decision_id)

        d_ok = store.create_decision(
            task_id="t-ok",
            step_id="s-ok",
            step_attempt_id="sa-ok",
            decision_type="policy",
            verdict="approved",
            reason="ok",
        )
        decision_ids.append(d_ok.decision_id)

        service.record_influence(
            context_pack_id="cp-stale",
            decision_ids=decision_ids,
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t-stale",
        )

        stale = service.find_stale_influencers(store, min_decisions=5, failure_rate_threshold=0.5)

        assert len(stale) >= 1
        assert any(s.memory_id == mem.memory_id for s in stale)
        match = next(s for s in stale if s.memory_id == mem.memory_id)
        assert match.failure_rate > 0.5
        assert match.decision_count == 6
    finally:
        store.close()


def test_find_stale_influencers_skips_below_min_decisions(tmp_path: Path) -> None:
    """Memories with fewer than min_decisions should not appear as stale influencers."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Low-usage memory")

        # Create only 2 failed decisions (below min_decisions=5)
        decision_ids = []
        for i in range(2):
            d = store.create_decision(
                task_id=f"t-few-{i}",
                step_id=f"s-{i}",
                step_attempt_id=f"sa-{i}",
                decision_type="policy",
                verdict="denied",
                reason="failed",
            )
            decision_ids.append(d.decision_id)

        service.record_influence(
            context_pack_id="cp-few",
            decision_ids=decision_ids,
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t-few",
        )

        stale = service.find_stale_influencers(store, min_decisions=5, failure_rate_threshold=0.5)

        assert not any(s.memory_id == mem.memory_id for s in stale)
    finally:
        store.close()


def test_record_influence_empty_lists(tmp_path: Path) -> None:
    """record_influence should return empty list when no memory or decision ids."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        # Empty decision_ids
        links = service.record_influence(
            context_pack_id="cp-empty",
            decision_ids=[],
            memory_ids=["m-1"],
            store=store,
        )
        assert links == []

        # Empty memory_ids
        links = service.record_influence(
            context_pack_id="cp-empty",
            decision_ids=["d-1"],
            memory_ids=[],
            store=store,
        )
        assert links == []
    finally:
        store.close()


def test_trace_memory_skips_none_decisions(tmp_path: Path) -> None:
    """trace_memory skips decisions that return None from store.get_decision."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Decision skip test")

        # Create influence links referencing a non-existent decision
        # by creating a link record manually
        store.create_memory_record(
            task_id="t-skip",
            conversation_id="conv-1",
            category="other",
            claim_text="Memory mem-skip influenced decision fake-decision",
            structured_assertion={
                "link_id": "infl-fake",
                "context_pack_id": "cp-skip",
                "decision_id": "fake-decision",
                "memory_id": mem.memory_id,
            },
            scope_kind="workspace",
            scope_ref="workspace:default",
            promotion_reason="lineage_tracking",
            retention_class="volatile_fact",
            memory_kind="influence_link",
            confidence=0.9,
            trust_tier="observed",
        )

        # Line 133: decision is None → continue
        impact = service.trace_memory(mem.memory_id, store)
        assert impact.memory_id == mem.memory_id
        assert impact.success_count == 0
        assert impact.failure_count == 0
    finally:
        store.close()


def test_find_stale_influencers_skips_below_failure_threshold(tmp_path: Path) -> None:
    """Memories with failure_rate below threshold are skipped."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Good advice memory")

        # Create 6 decisions: 5 succeeded, 1 failed → failure_rate = 1/6 ≈ 0.167
        decision_ids = []
        for i in range(5):
            d = store.create_decision(
                task_id=f"t-ok-{i}",
                step_id=f"s-{i}",
                step_attempt_id=f"sa-{i}",
                decision_type="policy",
                verdict="approved",
                reason="ok",
            )
            decision_ids.append(d.decision_id)

        d_fail = store.create_decision(
            task_id="t-fail",
            step_id="s-fail",
            step_attempt_id="sa-fail",
            decision_type="policy",
            verdict="denied",
            reason="failed",
        )
        decision_ids.append(d_fail.decision_id)

        service.record_influence(
            context_pack_id="cp-good",
            decision_ids=decision_ids,
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t-good",
        )

        # Line 175: failure_rate < threshold → continue
        stale = service.find_stale_influencers(store, min_decisions=5, failure_rate_threshold=0.5)
        assert not any(s.memory_id == mem.memory_id for s in stale)
    finally:
        store.close()


def test_find_stale_influencers_skips_non_active_records(tmp_path: Path) -> None:
    """Memories that are not active should be skipped by find_stale_influencers."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        mem = _create_memory(store, claim_text="Will be invalidated")

        # Create 6 failed decisions
        decision_ids = []
        for i in range(6):
            d = store.create_decision(
                task_id=f"t-inv-{i}",
                step_id=f"s-{i}",
                step_attempt_id=f"sa-{i}",
                decision_type="policy",
                verdict="denied",
                reason="failed",
            )
            decision_ids.append(d.decision_id)

        service.record_influence(
            context_pack_id="cp-inv",
            decision_ids=decision_ids,
            memory_ids=[mem.memory_id],
            store=store,
            task_id="t-inv",
        )

        # Invalidate the memory so it's skipped (line 179)
        store.update_memory_record(mem.memory_id, status="invalidated")

        stale = service.find_stale_influencers(store, min_decisions=5, failure_rate_threshold=0.5)
        assert not any(s.memory_id == mem.memory_id for s in stale)
    finally:
        store.close()


def test_trace_decision_empty_result(tmp_path: Path) -> None:
    """trace_decision should return empty lineage for an unknown decision."""
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryLineageService()

        lineage = service.trace_decision("decision-nonexistent", store)

        assert lineage.decision_id == "decision-nonexistent"
        assert lineage.influencing_memories == []
        assert lineage.link_count == 0
    finally:
        store.close()


def test_list_memory_records_filters_by_task_id(tmp_path: Path) -> None:
    """list_memory_records with task_id should only return records for that task."""
    store = KernelStore(tmp_path / "state.db")
    try:
        _create_memory(store, task_id="task-A", claim_text="Memory A")
        _create_memory(store, task_id="task-B", claim_text="Memory B")

        # Lines 993-994 in store_ledger.py: task_id filter
        result_a = store.list_memory_records(task_id="task-A", status="active", limit=100)
        result_b = store.list_memory_records(task_id="task-B", status="active", limit=100)

        assert all(r.task_id == "task-A" for r in result_a)
        assert all(r.task_id == "task-B" for r in result_b)
        assert len(result_a) >= 1
        assert len(result_b) >= 1
    finally:
        store.close()
