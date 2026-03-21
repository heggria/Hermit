"""E2E: TaskOS memory governance chain — belief → classification → promotion → reconciliation gate.

Tests 17–20 exercise the full governed memory lifecycle using real
KernelStore, BeliefService, MemoryRecordService, and MemoryGovernanceService.
No mocks — every record lands in a real SQLite ledger.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path: Path) -> KernelStore:
    """Create a fresh KernelStore in a temp directory."""
    kernel_dir = tmp_path / "kernel"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    return KernelStore(kernel_dir / "state.db")


def _bootstrap_task(store: KernelStore, conv_id: str, title: str) -> str:
    """Create a conversation + task and return the task_id."""
    store.ensure_conversation(conv_id, source_channel="test")
    task = store.create_task(
        conversation_id=conv_id,
        title=title,
        goal=title,
        source_channel="test",
        status="running",
        policy_profile="memory",
    )
    return task.task_id


# ===========================================================================
# Test 17: Durable promotion with valid reconciliation succeeds
# ===========================================================================


def test_durable_promotion_with_valid_reconciliation_succeeds(tmp_path: Path) -> None:
    """Full chain: belief(user_preference) + satisfied reconciliation → durable memory."""
    store = _store(tmp_path)
    try:
        task_id = _bootstrap_task(store, "conv-17", "Test 17: durable promotion")

        # 1. Record a belief via BeliefService
        belief_service = BeliefService(store)
        belief = belief_service.record(
            task_id=task_id,
            conversation_id="conv-17",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="user prefers dark mode",
            confidence=0.9,
            evidence_refs=["ev-17"],
        )

        # 2. Create a satisfied reconciliation in the store
        reconciliation = store.create_reconciliation(
            task_id=task_id,
            step_id="step-17",
            step_attempt_id="sa-17",
            contract_ref="contract-17",
            intended_effect_summary="Apply dark mode preference",
            authorized_effect_summary="Apply dark mode preference",
            observed_effect_summary="Dark mode applied",
            receipted_effect_summary="Dark mode applied",
            result_class="satisfied",
        )

        # 3. Classify the belief via MemoryGovernanceService
        gov = MemoryGovernanceService()
        classification = gov.classify_belief(belief)
        assert classification.scope_kind == "global", (
            f"user_preference should classify as global scope, got {classification.scope_kind}"
        )

        # 4. Promote with explicit reconciliation_ref
        memory_service = MemoryRecordService(store)
        memory = memory_service.promote_from_belief(
            belief=belief,
            conversation_id="conv-17",
            reconciliation_ref=reconciliation.reconciliation_id,
        )

        # 5. Verify: MemoryRecord created with durable properties
        assert memory is not None, "Promotion should succeed with valid reconciliation"
        assert memory.scope_kind == "global"
        assert memory.trust_tier == "durable"
        assert memory.learned_from_reconciliation_ref == reconciliation.reconciliation_id

        # Cross-check in store
        records = store.list_memory_records(status="active", limit=100)
        matching = [r for r in records if r.memory_id == memory.memory_id]
        assert len(matching) == 1
        assert matching[0].learned_from_reconciliation_ref == reconciliation.reconciliation_id
    finally:
        store.close()


# ===========================================================================
# Test 18: Durable promotion blocked without reconciliation
# ===========================================================================


def test_durable_promotion_blocked_without_reconciliation(tmp_path: Path) -> None:
    """Durable-scoped belief without reconciliation_ref → promotion blocked."""
    store = _store(tmp_path)
    try:
        task_id = _bootstrap_task(store, "conv-18", "Test 18: durable blocked")

        # 1. Record a belief that classifies as durable (user_preference → global)
        belief_service = BeliefService(store)
        belief = belief_service.record(
            task_id=task_id,
            conversation_id="conv-18",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="user prefers dark mode",
            confidence=0.9,
            evidence_refs=["ev-18"],
        )

        # 2. Promote WITHOUT reconciliation_ref
        memory_service = MemoryRecordService(store)
        memory = memory_service.promote_from_belief(
            belief=belief,
            conversation_id="conv-18",
        )

        # 3. Verify: promotion blocked
        assert memory is None, "Durable promotion without reconciliation should be blocked"

        # 4. Verify: NO MemoryRecord created
        records = store.list_memory_records(status="active", limit=100)
        assert len(records) == 0, "No memory records should exist after blocked promotion"

        # 5. Verify: belief marked as blocked
        updated_belief = store.get_belief(belief.belief_id)
        assert updated_belief is not None
        assert updated_belief.promotion_candidate is False
        assert "promotion_blocked" in str(updated_belief.validation_basis or "")
    finally:
        store.close()


# ===========================================================================
# Test 19: Conversation-scope memory allowed without reconciliation
# ===========================================================================


def test_conversation_scope_memory_allowed_without_reconciliation(tmp_path: Path) -> None:
    """Ephemeral belief (tech_decision → conversation scope) promoted without reconciliation."""
    store = _store(tmp_path)
    try:
        task_id = _bootstrap_task(store, "conv-19", "Test 19: ephemeral allowed")

        # 1. Record a belief that classifies as ephemeral (tech_decision → conversation)
        belief_service = BeliefService(store)
        belief = belief_service.record(
            task_id=task_id,
            conversation_id="conv-19",
            scope_kind="conversation",
            scope_ref="conv-19",
            category="tech_decision",
            content="Using SQLite for local state storage",
            confidence=0.8,
            evidence_refs=["ev-19"],
        )

        # 2. Promote WITHOUT reconciliation_ref
        memory_service = MemoryRecordService(store)
        memory = memory_service.promote_from_belief(
            belief=belief,
            conversation_id="conv-19",
        )

        # 3. Verify: MemoryRecord created (ephemeral allowed)
        assert memory is not None, (
            "Conversation-scoped promotion should succeed without reconciliation"
        )
        assert memory.scope_kind == "conversation"
        assert memory.validation_basis == "ephemeral_working_memory"

        # 4. Cross-check in store
        records = store.list_memory_records(status="active", limit=100)
        matching = [r for r in records if r.memory_id == memory.memory_id]
        assert len(matching) == 1
        assert matching[0].scope_kind == "conversation"
        assert matching[0].validation_basis == "ephemeral_working_memory"
    finally:
        store.close()


# ===========================================================================
# Test 20: Memory invalidation on violated reconciliation
# ===========================================================================


def test_memory_invalidation_on_violated_reconciliation(tmp_path: Path) -> None:
    """Promote with satisfied reconciliation, then attempt with violated → blocked.
    Original memory must remain (not retroactively invalidated by new violation attempt).
    """
    store = _store(tmp_path)
    try:
        task_id = _bootstrap_task(store, "conv-20", "Test 20: violated reconciliation")

        # 1. Record belief + create satisfied reconciliation + promote
        belief_service = BeliefService(store)
        belief_1 = belief_service.record(
            task_id=task_id,
            conversation_id="conv-20",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="user prefers dark mode",
            confidence=0.9,
            evidence_refs=["ev-20a"],
        )

        satisfied_rec = store.create_reconciliation(
            task_id=task_id,
            step_id="step-20a",
            step_attempt_id="sa-20a",
            contract_ref="contract-20a",
            intended_effect_summary="Apply preference",
            authorized_effect_summary="Apply preference",
            observed_effect_summary="Applied",
            receipted_effect_summary="Applied",
            result_class="satisfied",
        )

        memory_service = MemoryRecordService(store)
        original_memory = memory_service.promote_from_belief(
            belief=belief_1,
            conversation_id="conv-20",
            reconciliation_ref=satisfied_rec.reconciliation_id,
        )
        assert original_memory is not None, (
            "First promotion with satisfied reconciliation should succeed"
        )

        # 2. Create a second belief + violated reconciliation
        belief_2 = belief_service.record(
            task_id=task_id,
            conversation_id="conv-20",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="user prefers light mode",
            confidence=0.85,
            evidence_refs=["ev-20b"],
        )

        violated_rec = store.create_reconciliation(
            task_id=task_id,
            step_id="step-20b",
            step_attempt_id="sa-20b",
            contract_ref="contract-20b",
            intended_effect_summary="Apply preference",
            authorized_effect_summary="Apply preference",
            observed_effect_summary="Failed to apply",
            receipted_effect_summary="Failed to apply",
            result_class="violated",
        )

        # 3. Attempt promotion with violated reconciliation_ref → blocked
        blocked_memory = memory_service.promote_from_belief(
            belief=belief_2,
            conversation_id="conv-20",
            reconciliation_ref=violated_rec.reconciliation_id,
        )
        assert blocked_memory is None, "Promotion with violated reconciliation should be blocked"

        # 4. Verify: belief_2 marked as blocked with reconciliation_violated reason
        updated_belief_2 = store.get_belief(belief_2.belief_id)
        assert updated_belief_2 is not None
        assert updated_belief_2.promotion_candidate is False
        assert "reconciliation_violated" in str(updated_belief_2.validation_basis or "")

        # 5. Verify: original memory still exists and is active
        original_check = store.get_memory_record(original_memory.memory_id)
        assert original_check is not None
        assert original_check.status == "active", (
            "Original memory should not be retroactively invalidated by a new violated reconciliation"
        )
    finally:
        store.close()
