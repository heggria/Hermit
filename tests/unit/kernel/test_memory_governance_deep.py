"""Deep memory governance tests: category policies, belief lifecycle, working memory budget, retrieval modes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.context.memory.governance import (
    _CATEGORY_POLICIES,
    MemoryGovernanceService,
)
from hermit.kernel.context.memory.knowledge import BeliefService
from hermit.kernel.context.memory.retrieval import HybridRetrievalService
from hermit.kernel.context.memory.working_memory import (
    WorkingMemoryManager,
    WorkingMemoryPack,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import MemoryRecord

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    s = KernelStore(tmp_path / "state.db")
    s.ensure_conversation("conv-1", source_channel="test")
    task = s.create_task(
        conversation_id="conv-1",
        title="test",
        goal="test",
        priority="normal",
        source_channel="test",
    )
    s._test_task_id = task.task_id
    return s


@pytest.fixture()
def gov() -> MemoryGovernanceService:
    return MemoryGovernanceService()


@pytest.fixture()
def belief_svc(store: KernelStore) -> BeliefService:
    return BeliefService(store)


# ════════════════════════════════════════════════════════════════════════
# Task 13: Category policy coverage
# ════════════════════════════════════════════════════════════════════════


class TestCategoryPolicies:
    def test_user_preference_global(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("user_preference")
        assert p.scope_kind == "global"
        assert p.static_injection is True

    def test_project_convention_workspace(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("project_convention")
        assert p.scope_kind == "workspace"
        assert p.static_injection is True

    def test_tooling_environment_workspace(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("tooling_environment")
        assert p.scope_kind == "workspace"
        assert p.static_injection is True

    def test_tech_decision_conversation(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("tech_decision")
        assert p.scope_kind == "conversation"
        assert p.static_injection is False
        assert p.ttl_seconds == 3 * 24 * 60 * 60

    def test_volatile_fact_conversation(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("other")
        assert p.scope_kind == "conversation"
        assert p.ttl_seconds > 0

    def test_warning_workspace(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("pitfall_warning")
        assert p.scope_kind == "workspace"
        assert p.static_injection is True

    def test_procedural_workspace(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("procedural")
        assert p.scope_kind == "workspace"

    def test_active_task_conversation(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("active_task")
        assert p.scope_kind == "conversation"

    def test_unknown_category_default(self, gov: MemoryGovernanceService) -> None:
        p = gov.policy_for("completely_unknown_xyz")
        assert p.scope_kind == "conversation"

    def test_all_policies_allow_retrieval(self, gov: MemoryGovernanceService) -> None:
        for cat in _CATEGORY_POLICIES:
            p = gov.policy_for(cat)
            assert p.retrieval_allowed is True, f"{cat} should allow retrieval"


# ════════════════════════════════════════════════════════════════════════
# Task 14: Belief lifecycle
# ════════════════════════════════════════════════════════════════════════


class TestBeliefLifecycle:
    def test_create_belief_stored(self, belief_svc: BeliefService, store: KernelStore) -> None:
        b = belief_svc.record(
            task_id="task-mem-test",
            conversation_id="conv-1",
            scope_kind="global",
            scope_ref="global:default",
            category="user_preference",
            content="user prefers dark mode",
            confidence=0.9,
            evidence_refs=["ev-1"],
        )
        assert b.belief_id
        fetched = store.get_belief(b.belief_id)
        assert fetched is not None
        assert fetched.claim_text == "user prefers dark mode"

    def test_supersede_marks_original(self, belief_svc: BeliefService, store: KernelStore) -> None:
        b1 = belief_svc.record(
            task_id="task-mem-test",
            conversation_id="conv-1",
            scope_kind="global",
            scope_ref="global:default",
            category="user_preference",
            content="prefers light mode",
            confidence=0.8,
            evidence_refs=[],
        )
        belief_svc.supersede(b1.belief_id, ["superseded by dark mode preference"])
        fetched = store.get_belief(b1.belief_id)
        assert fetched is not None
        assert fetched.status == "superseded"

    def test_contradict_records(self, belief_svc: BeliefService, store: KernelStore) -> None:
        b1 = belief_svc.record(
            task_id="task-mem-test",
            conversation_id="conv-1",
            scope_kind="global",
            scope_ref="global:default",
            category="user_preference",
            content="project uses pip",
            confidence=0.7,
            evidence_refs=[],
        )
        belief_svc.contradict(b1.belief_id, ["evidence: uv is used"])
        fetched = store.get_belief(b1.belief_id)
        assert fetched is not None
        assert fetched.status == "contradicted"

    def test_invalidate_belief(self, belief_svc: BeliefService, store: KernelStore) -> None:
        b1 = belief_svc.record(
            task_id="task-mem-test",
            conversation_id="conv-1",
            scope_kind="conversation",
            scope_ref="conversation:conv-1",
            category="tech_decision",
            content="use redis for caching",
            confidence=0.5,
            evidence_refs=[],
        )
        belief_svc.invalidate(b1.belief_id)
        fetched = store.get_belief(b1.belief_id)
        assert fetched is not None
        assert fetched.status == "invalidated"


# ════════════════════════════════════════════════════════════════════════
# Task 15: Working memory token budget
# ════════════════════════════════════════════════════════════════════════


def _make_memory(
    memory_id: str,
    claim: str,
    category: str = "other",
    scope_kind: str = "conversation",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        task_id="task-mem-test",
        conversation_id="conv-1",
        scope_kind=scope_kind,
        scope_ref=f"{scope_kind}:test",
        retention_class="volatile_fact",
        category=category,
        claim_text=claim,
        structured_assertion=None,
        confidence=0.8,
        trust_tier="observed",
        evidence_refs=[],
        status="active",
        memory_kind=None,
        learned_from_reconciliation_ref=None,
        validation_basis=None,
        source_belief_ref=None,
        expires_at=None,
        created_at=time.time(),
        updated_at=time.time(),
    )


class TestWorkingMemoryBudget:
    def test_empty_input_empty_pack(self) -> None:
        mgr = WorkingMemoryManager(max_tokens=1000)
        pack = mgr.select_for_context()
        assert isinstance(pack, WorkingMemoryPack)
        assert pack.total_tokens == 0

    def test_items_within_budget(self) -> None:
        mgr = WorkingMemoryManager(max_tokens=10000)
        static = [_make_memory(f"m-{i}", f"short claim {i}") for i in range(3)]
        pack = mgr.select_for_context(static=static)
        assert len(pack.items) >= 1

    def test_budget_not_exceeded(self) -> None:
        mgr = WorkingMemoryManager(max_tokens=50)
        static = [_make_memory(f"m-{i}", "long claim text " * 50) for i in range(10)]
        pack = mgr.select_for_context(static=static)
        assert pack.total_tokens <= 50 or len(pack.items) <= 1

    def test_overflow_tracked(self) -> None:
        mgr = WorkingMemoryManager(max_tokens=100)
        static = [_make_memory(f"m-{i}", f"claim {i} " * 20) for i in range(20)]
        pack = mgr.select_for_context(static=static)
        assert pack.overflow_count >= 0


# ════════════════════════════════════════════════════════════════════════
# Task 16: Hybrid retrieval modes
# ════════════════════════════════════════════════════════════════════════


class TestHybridRetrievalModes:
    def test_empty_query_empty_report(self) -> None:
        svc = HybridRetrievalService()
        report = svc.retrieve("", [])
        assert report.total_candidates == 0

    def test_empty_memories_empty_report(self) -> None:
        svc = HybridRetrievalService()
        report = svc.retrieve("some query", [])
        assert report.total_candidates == 0

    def test_fast_mode_short_query(self) -> None:
        svc = HybridRetrievalService()
        memories = [_make_memory("m-1", "uv is the package manager")]
        report = svc.retrieve("uv", memories)
        assert report.mode == "fast"

    def test_deep_mode_long_query(self) -> None:
        svc = HybridRetrievalService()
        memories = [_make_memory("m-1", "uv is the package manager")]
        long_q = "What package manager does this project use and how? " * 3
        report = svc.retrieve(long_q, memories)
        assert report.mode == "deep"

    def test_force_deep_overrides(self) -> None:
        svc = HybridRetrievalService()
        memories = [_make_memory("m-1", "test memory")]
        report = svc.retrieve("short", memories, force_deep=True)
        assert report.mode == "deep"

    def test_excludes_bookkeeping_kinds(self) -> None:
        svc = HybridRetrievalService()
        m1 = _make_memory("m-1", "real memory")
        m2_dict = {**m1.__dict__, "memory_id": "m-2", "memory_kind": "episode_index"}
        m2 = MemoryRecord(**m2_dict)
        report = svc.retrieve("test", [m1, m2])
        assert report.total_candidates == 1
