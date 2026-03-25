"""Tests for kernel/context/memory/knowledge.py — BeliefService and MemoryRecordService."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = time.time()


def _make_belief(
    *,
    belief_id: str = "belief-1",
    task_id: str = "task-1",
    conversation_id: str | None = "conv-1",
    category: str = "user_preference",
    claim_text: str = "User prefers dark mode",
    confidence: float = 0.9,
    evidence_refs: list[str] | None = None,
    structured_assertion: dict | None = None,
) -> BeliefRecord:
    return BeliefRecord(
        belief_id=belief_id,
        task_id=task_id,
        conversation_id=conversation_id,
        scope_kind="global",
        scope_ref="global",
        category=category,
        claim_text=claim_text,
        confidence=confidence,
        evidence_refs=evidence_refs or ["ev-1"],
        structured_assertion=structured_assertion or {},
    )


def _make_memory(
    *,
    memory_id: str = "mem-1",
    task_id: str = "task-1",
    conversation_id: str | None = "conv-1",
    category: str = "user_preference",
    claim_text: str = "User prefers dark mode",
    scope_kind: str = "global",
    scope_ref: str = "global",
    retention_class: str = "user_preference",
    status: str = "active",
    trust_tier: str = "durable",
    confidence: float = 0.9,
    structured_assertion: dict | None = None,
    supersedes: list[str] | None = None,
    learned_from_reconciliation_ref: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        task_id=task_id,
        conversation_id=conversation_id,
        category=category,
        claim_text=claim_text,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        retention_class=retention_class,
        status=status,
        trust_tier=trust_tier,
        confidence=confidence,
        structured_assertion=structured_assertion or {},
        supersedes=supersedes or [],
        learned_from_reconciliation_ref=learned_from_reconciliation_ref,
    )


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.create_belief.return_value = _make_belief()
    store.create_memory_record.return_value = _make_memory()
    store.list_memory_records.return_value = []
    store.get_memory_record.return_value = None
    return store


# ---------------------------------------------------------------------------
# BeliefService
# ---------------------------------------------------------------------------


class TestBeliefService:
    def test_record_creates_belief(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)

        result = svc.record(
            task_id="task-1",
            conversation_id="conv-1",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="User prefers dark mode",
            confidence=0.9,
            evidence_refs=["ev-1"],
        )

        store.create_belief.assert_called_once()
        assert result.belief_id == "belief-1"

    def test_record_with_all_optional_params(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)

        svc.record(
            task_id="task-1",
            conversation_id=None,
            scope_kind="workspace",
            scope_ref="/project",
            category="project_convention",
            content="Use ruff for formatting",
            confidence=0.95,
            evidence_refs=["ev-2"],
            trust_tier="bootstrap",
            supersedes=["old-1"],
            contradicts=["contra-1"],
            evidence_case_ref="ec-1",
            epistemic_origin="inference",
            freshness_class="stable",
            validation_basis="reconciliation:rec-1",
        )

        call_kwargs = store.create_belief.call_args[1]
        assert call_kwargs["trust_tier"] == "bootstrap"
        assert call_kwargs["supersedes"] == ["old-1"]
        assert call_kwargs["contradicts"] == ["contra-1"]
        assert call_kwargs["evidence_case_ref"] == "ec-1"
        assert call_kwargs["epistemic_origin"] == "inference"
        assert call_kwargs["freshness_class"] == "stable"
        assert call_kwargs["validation_basis"] == "reconciliation:rec-1"
        assert call_kwargs["last_validated_at"] is not None

    def test_record_without_validation_basis_sets_no_validated_at(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)

        svc.record(
            task_id="task-1",
            conversation_id="conv-1",
            scope_kind="global",
            scope_ref="global",
            category="user_preference",
            content="test",
            confidence=0.5,
            evidence_refs=[],
        )

        call_kwargs = store.create_belief.call_args[1]
        assert call_kwargs["last_validated_at"] is None

    def test_supersede_updates_belief(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)
        svc.supersede("belief-1", ["old content"])
        store.update_belief.assert_called_once_with(
            "belief-1", status="superseded", supersedes=["old content"]
        )

    def test_contradict_updates_belief(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)
        svc.contradict("belief-1", ["contra-id"])
        store.update_belief.assert_called_once_with(
            "belief-1", status="contradicted", contradicts=["contra-id"]
        )

    def test_invalidate_updates_belief(self) -> None:
        store = _mock_store()
        svc = BeliefService(store)
        svc.invalidate("belief-1")
        call_kwargs = store.update_belief.call_args[1]
        assert call_kwargs["status"] == "invalidated"
        assert call_kwargs["invalidated_at"] is not None


# ---------------------------------------------------------------------------
# MemoryRecordService
# ---------------------------------------------------------------------------


class TestMemoryRecordService:
    def test_promote_from_belief_creates_memory(self) -> None:
        store = _mock_store()
        belief = _make_belief()
        reconciliation = SimpleNamespace(reconciliation_id="rec-1", result_class="satisfied")
        store.list_reconciliations.return_value = [reconciliation]
        store.get_reconciliation.return_value = reconciliation
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is not None
        store.create_memory_record.assert_called_once()

    def test_promote_from_belief_with_explicit_reconciliation_ref(self) -> None:
        store = _mock_store()
        belief = _make_belief()
        reconciliation = SimpleNamespace(reconciliation_id="explicit-rec", result_class="satisfied")
        store.get_reconciliation.return_value = reconciliation
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
            reconciliation_ref="explicit-rec",
        )

        assert result is not None

    def test_promote_from_belief_blocked_no_reconciliation(self) -> None:
        store = _mock_store()
        belief = _make_belief()
        store.list_reconciliations.return_value = []
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is None
        store.update_belief.assert_called_once()
        call_kwargs = store.update_belief.call_args[1]
        assert call_kwargs["promotion_candidate"] is False
        assert "promotion_blocked" in call_kwargs["validation_basis"]

    def test_promote_from_belief_blocked_no_satisfied_reconciliation(self) -> None:
        store = _mock_store()
        belief = _make_belief()
        reconciliation = SimpleNamespace(reconciliation_id="rec-1", result_class="violated")
        store.list_reconciliations.return_value = [reconciliation]
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is None

    def test_promote_from_belief_blocked_store_without_reconciliations(self) -> None:
        store = _mock_store()
        if hasattr(store, "list_reconciliations"):
            del store.list_reconciliations
        belief = _make_belief()
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is None

    def test_promote_from_belief_duplicate_returns_existing(self) -> None:
        store = _mock_store()
        belief = _make_belief()
        existing = _make_memory(memory_id="existing-mem")
        store.list_memory_records.return_value = [existing]
        reconciliation = SimpleNamespace(reconciliation_id="rec-1", result_class="satisfied")
        store.list_reconciliations.return_value = [reconciliation]
        store.get_reconciliation.return_value = reconciliation
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is not None
        assert result.memory_id == "existing-mem"
        store.create_memory_record.assert_not_called()

    def test_promote_from_belief_supersedes_old_records(self) -> None:
        store = _mock_store()
        belief = _make_belief(claim_text="Use 4 spaces for indentation")
        old_memory = _make_memory(
            memory_id="old-mem",
            claim_text="Use 2 spaces for indentation",
            scope_kind="global",
            scope_ref="global",
            retention_class="user_preference",
        )
        store.list_memory_records.return_value = [old_memory]
        reconciliation = SimpleNamespace(reconciliation_id="rec-1", result_class="satisfied")
        store.list_reconciliations.return_value = [reconciliation]
        store.get_reconciliation.return_value = reconciliation
        svc = MemoryRecordService(store)

        svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        # old_memory should have been superseded
        update_calls = store.update_memory_record.call_args_list
        found_supersede = any(
            c[1].get("status") == "invalidated" and c[1].get("invalidation_reason") == "superseded"
            for c in update_calls
        )
        assert found_supersede or store.update_memory_record.call_count >= 1

    def test_invalidate_marks_memory_invalidated(self) -> None:
        store = _mock_store()
        store.get_memory_record.return_value = None
        svc = MemoryRecordService(store)

        svc.invalidate("mem-1")

        store.update_memory_record.assert_called_once()
        call_kwargs = store.update_memory_record.call_args[1]
        assert call_kwargs["status"] == "invalidated"

    def test_invalidate_by_reconciliation_violated(self) -> None:
        store = _mock_store()
        record = _make_memory(
            memory_id="mem-1",
            learned_from_reconciliation_ref="rec-1",
        )
        store.list_memory_records.return_value = [record]
        svc = MemoryRecordService(store)

        ids = svc.invalidate_by_reconciliation("rec-1", "violated")

        assert ids == ["mem-1"]

    def test_invalidate_by_reconciliation_non_violated_returns_empty(self) -> None:
        store = _mock_store()
        svc = MemoryRecordService(store)

        ids = svc.invalidate_by_reconciliation("rec-1", "satisfied")

        assert ids == []

    def test_reconcile_active_records(self) -> None:
        store = _mock_store()
        rec1 = _make_memory(memory_id="m-1", claim_text="Fact A")
        rec1.created_at = 1.0
        rec1.updated_at = 1.0
        rec2 = _make_memory(memory_id="m-2", claim_text="Fact B")
        rec2.created_at = 2.0
        rec2.updated_at = 2.0
        store.list_memory_records.return_value = [rec1, rec2]
        store.get_memory_record.side_effect = lambda mid: {"m-1": rec1, "m-2": rec2}.get(mid)
        svc = MemoryRecordService(store)

        result = svc.reconcile_active_records()

        assert "active_count" in result
        assert "superseded_count" in result
        assert "duplicate_count" in result

    def test_export_mirror_no_path_returns_none(self) -> None:
        store = _mock_store()
        svc = MemoryRecordService(store, mirror_path=None)

        result = svc.export_mirror()

        assert result is None

    def test_export_mirror_with_path(self, tmp_path: Path) -> None:
        store = _mock_store()
        store.list_memory_records.return_value = []
        mirror = tmp_path / "memories.md"
        svc = MemoryRecordService(store, mirror_path=mirror)

        result = svc.export_mirror()

        assert result == mirror

    def test_active_categories(self) -> None:
        store = _mock_store()
        rec = _make_memory(category="user_preference")
        store.list_memory_records.return_value = [rec]
        svc = MemoryRecordService(store)

        cats = svc.active_categories()

        assert "user_preference" in cats
        assert len(cats["user_preference"]) == 1

    def test_active_categories_with_conversation_id(self) -> None:
        store = _mock_store()
        store.list_memory_records.return_value = []
        svc = MemoryRecordService(store)

        svc.active_categories(conversation_id="conv-1")

        store.list_memory_records.assert_called_once()
        call_kwargs = store.list_memory_records.call_args[1]
        assert call_kwargs["conversation_id"] == "conv-1"

    def test_entry_from_memory_static_method(self) -> None:
        record = _make_memory(trust_tier="durable", confidence=0.95)

        entry = MemoryRecordService._entry_from_memory(record)

        assert entry.category == "user_preference"
        assert entry.content == "User prefers dark mode"
        assert entry.score == 8
        assert entry.locked is True
        assert entry.confidence == 0.95

    def test_entry_from_memory_non_durable(self) -> None:
        record = _make_memory(trust_tier="observed")

        entry = MemoryRecordService._entry_from_memory(record)

        assert entry.score == 5
        assert entry.locked is False

    def test_issue_memory_write_receipt_no_service(self) -> None:
        store = _mock_store()
        svc = MemoryRecordService(store, receipt_service=None)

        result = svc._issue_memory_write_receipt(
            belief=_make_belief(),
            memory=_make_memory(),
            superseded_records=[],
        )

        assert result is None

    def test_issue_memory_invalidate_receipt_no_service(self) -> None:
        store = _mock_store()
        svc = MemoryRecordService(store, receipt_service=None)

        result = svc._issue_memory_invalidate_receipt("mem-1")

        assert result is None

    def test_issue_memory_invalidate_receipt_no_record(self) -> None:
        store = _mock_store()
        receipt_svc = MagicMock()
        store.get_memory_record.return_value = None
        svc = MemoryRecordService(store, receipt_service=receipt_svc)

        result = svc._issue_memory_invalidate_receipt("mem-nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# GC3: Reconciliation gate for durable memory promotion
# ---------------------------------------------------------------------------


class TestReconciliationGate:
    """GC3: No durable learning without reconciliation.

    Durable-scoped memories (global/workspace) require a valid reconciliation_ref.
    Conversation-scoped memories are allowed without reconciliation.
    """

    def test_durable_promotion_blocked_without_reconciliation(self) -> None:
        """Durable scope (global) promotion without reconciliation_ref is blocked."""
        store = _mock_store()
        # Belief classifies as user_preference -> scope=global (durable)
        belief = _make_belief(
            category="user_preference",
            claim_text="User prefers dark mode",
        )
        store.list_reconciliations.return_value = []
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is None
        store.create_memory_record.assert_not_called()
        # Belief should be marked as blocked
        call_kwargs = store.update_belief.call_args[1]
        assert call_kwargs["promotion_candidate"] is False
        assert "promotion_blocked" in call_kwargs["validation_basis"]

    def test_durable_promotion_succeeds_with_valid_reconciliation(self) -> None:
        """Durable scope (global) promotion with valid reconciliation_ref succeeds."""
        store = _mock_store()
        belief = _make_belief(
            category="user_preference",
            claim_text="User prefers dark mode",
        )
        reconciliation = SimpleNamespace(
            reconciliation_id="rec-1",
            result_class="satisfied",
        )
        store.get_reconciliation.return_value = reconciliation
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
            reconciliation_ref="rec-1",
        )

        assert result is not None
        store.create_memory_record.assert_called_once()
        call_kwargs = store.create_memory_record.call_args[1]
        assert call_kwargs["trust_tier"] == "durable"
        assert call_kwargs["learned_from_reconciliation_ref"] == "rec-1"

    def test_conversation_scope_without_reconciliation_allowed(self) -> None:
        """Conversation-scoped memory promotion is allowed without reconciliation."""
        store = _mock_store()
        # tech_decision classifies as scope=conversation (ephemeral)
        belief = _make_belief(
            belief_id="belief-conv",
            category="tech_decision",
            claim_text="Using PostgreSQL for this project",
            confidence=0.8,
        )
        store.list_reconciliations.return_value = []
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
        )

        assert result is not None
        store.create_memory_record.assert_called_once()
        call_kwargs = store.create_memory_record.call_args[1]
        assert call_kwargs["trust_tier"] == "observed"
        assert call_kwargs["validation_basis"] == "ephemeral_working_memory"
        assert call_kwargs["learned_from_reconciliation_ref"] is None

    def test_durable_promotion_blocked_with_violated_reconciliation(self) -> None:
        """Durable promotion is blocked when reconciliation_ref points to violated record."""
        store = _mock_store()
        belief = _make_belief(
            category="user_preference",
            claim_text="User prefers dark mode",
        )
        violated_rec = SimpleNamespace(
            reconciliation_id="rec-violated",
            result_class="violated",
        )
        store.get_reconciliation.return_value = violated_rec
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
            reconciliation_ref="rec-violated",
        )

        assert result is None
        store.create_memory_record.assert_not_called()
        call_kwargs = store.update_belief.call_args[1]
        assert "reconciliation_violated" in call_kwargs["validation_basis"]

    def test_durable_promotion_blocked_with_nonexistent_reconciliation(self) -> None:
        """Durable promotion is blocked when reconciliation_ref doesn't exist."""
        store = _mock_store()
        belief = _make_belief(
            category="user_preference",
            claim_text="User prefers dark mode",
        )
        store.get_reconciliation.return_value = None
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
            reconciliation_ref="rec-nonexistent",
        )

        assert result is None
        store.create_memory_record.assert_not_called()
        call_kwargs = store.update_belief.call_args[1]
        assert "reconciliation_not_found" in call_kwargs["validation_basis"]

    def test_workspace_scope_requires_reconciliation(self) -> None:
        """Workspace-scoped (durable) promotion requires reconciliation."""
        store = _mock_store()
        # project_convention classifies as scope=workspace (durable)
        belief = _make_belief(
            category="project_convention",
            claim_text="Always use ruff for formatting",
        )
        store.list_reconciliations.return_value = []
        svc = MemoryRecordService(store)

        result = svc.promote_from_belief(
            belief=belief,
            conversation_id="conv-1",
            workspace_root="/project",
        )

        assert result is None
        store.create_memory_record.assert_not_called()
