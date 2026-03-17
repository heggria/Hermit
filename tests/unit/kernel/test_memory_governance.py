from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def test_governance_filters_static_categories() -> None:
    governance = MemoryGovernanceService()
    categories = {
        "user_preference": [MemoryEntry(category="user_preference", content="只能用中文回复用户")],
        "project_convention": [
            MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")
        ],
        "active_task": [MemoryEntry(category="active_task", content="当前无任何定时任务")],
        "other": [MemoryEntry(category="other", content="今天已完成热门话题搜索")],
    }

    filtered = governance.filter_static_categories(categories)

    assert set(filtered) == {"user_preference", "project_convention"}


def test_memory_promotion_supersedes_task_state_across_conversations(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    try:
        belief_service = BeliefService(store)
        memory_service = MemoryRecordService(store)

        old_belief = belief_service.record(
            task_id="task_old",
            conversation_id="chat-a",
            scope_kind="conversation",
            scope_ref="chat-a",
            category="active_task",
            content="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
            confidence=0.8,
            evidence_refs=[],
        )
        old_reconciliation = store.create_reconciliation(
            task_id="task_old",
            step_id="step_old",
            step_attempt_id="attempt_old",
            contract_ref="contract_old",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Promote durable memory.",
            authorized_effect_summary="Promote durable memory.",
            observed_effect_summary="Outcome reconciled.",
            receipted_effect_summary="Outcome reconciled.",
            result_class="satisfied",
            recommended_resolution="promote_learning",
        )
        old_memory = memory_service.promote_from_belief(
            belief=old_belief,
            conversation_id="chat-a",
            reconciliation_ref=old_reconciliation.reconciliation_id,
        )

        new_belief = belief_service.record(
            task_id="task_new",
            conversation_id="chat-b",
            scope_kind="conversation",
            scope_ref="chat-b",
            category="active_task",
            content="2026-03-13 用户要求清理全部定时任务，已完成：当前无任何定时任务。",
            confidence=0.8,
            evidence_refs=[],
        )
        new_reconciliation = store.create_reconciliation(
            task_id="task_new",
            step_id="step_new",
            step_attempt_id="attempt_new",
            contract_ref="contract_new",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Promote durable memory.",
            authorized_effect_summary="Promote durable memory.",
            observed_effect_summary="Outcome reconciled.",
            receipted_effect_summary="Outcome reconciled.",
            result_class="satisfied",
            recommended_resolution="promote_learning",
        )
        new_memory = memory_service.promote_from_belief(
            belief=new_belief,
            conversation_id="chat-b",
            reconciliation_ref=new_reconciliation.reconciliation_id,
        )

        refreshed_old = store.get_memory_record(old_memory.memory_id)
        refreshed_new = store.get_memory_record(new_memory.memory_id)

        assert refreshed_old is not None and refreshed_old.status == "invalidated"
        assert refreshed_new is not None and refreshed_new.status == "active"
        assert refreshed_new.conversation_id == "chat-b"
        assert refreshed_old.superseded_by_memory_id == refreshed_new.memory_id
        assert old_memory.content in refreshed_new.supersedes
    finally:
        store.close()


def test_memory_promotion_keeps_unrelated_task_entries_active(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    try:
        belief_service = BeliefService(store)
        memory_service = MemoryRecordService(store)

        schedule_belief = belief_service.record(
            task_id="task_schedule",
            conversation_id="chat-a",
            scope_kind="conversation",
            scope_ref="chat-a",
            category="active_task",
            content="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
            confidence=0.8,
            evidence_refs=[],
        )
        schedule_reconciliation = store.create_reconciliation(
            task_id="task_schedule",
            step_id="step_schedule",
            step_attempt_id="attempt_schedule",
            contract_ref="contract_schedule",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Promote durable memory.",
            authorized_effect_summary="Promote durable memory.",
            observed_effect_summary="Outcome reconciled.",
            receipted_effect_summary="Outcome reconciled.",
            result_class="satisfied",
            recommended_resolution="promote_learning",
        )
        schedule_memory = memory_service.promote_from_belief(
            belief=schedule_belief,
            conversation_id="chat-a",
            reconciliation_ref=schedule_reconciliation.reconciliation_id,
        )

        readme_belief = belief_service.record(
            task_id="task_readme",
            conversation_id="chat-b",
            scope_kind="conversation",
            scope_ref="chat-b",
            category="active_task",
            content="用户希望改写 Hermit 的 README.md，使其更吸引外部开发者参与贡献。",
            confidence=0.8,
            evidence_refs=[],
        )
        readme_reconciliation = store.create_reconciliation(
            task_id="task_readme",
            step_id="step_readme",
            step_attempt_id="attempt_readme",
            contract_ref="contract_readme",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Promote durable memory.",
            authorized_effect_summary="Promote durable memory.",
            observed_effect_summary="Outcome reconciled.",
            receipted_effect_summary="Outcome reconciled.",
            result_class="satisfied",
            recommended_resolution="promote_learning",
        )
        readme_memory = memory_service.promote_from_belief(
            belief=readme_belief,
            conversation_id="chat-b",
            reconciliation_ref=readme_reconciliation.reconciliation_id,
        )

        refreshed_schedule = store.get_memory_record(schedule_memory.memory_id)
        refreshed_readme = store.get_memory_record(readme_memory.memory_id)

        assert refreshed_schedule is not None and refreshed_schedule.status == "active"
        assert refreshed_readme is not None and refreshed_readme.status == "active"
    finally:
        store.close()


def test_reconcile_active_records_supersedes_newer_conflicts_across_conversations(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "state.db")
    try:
        memory_service = MemoryRecordService(store)
        old_memory = store.create_memory_record(
            task_id="task_old",
            conversation_id="chat-a",
            category="active_task",
            content="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
            confidence=0.8,
            evidence_refs=[],
        )
        latest_memory = store.create_memory_record(
            task_id="task_new",
            conversation_id="chat-b",
            category="active_task",
            content="2026-03-13 用户要求清理全部定时任务，已完成：当前无任何定时任务。",
            confidence=0.8,
            evidence_refs=[],
        )

        result = memory_service.reconcile_active_records()

        refreshed_old = store.get_memory_record(old_memory.memory_id)
        refreshed_latest = store.get_memory_record(latest_memory.memory_id)

        assert result["superseded_count"] == 1
        assert refreshed_old is not None and refreshed_old.status == "invalidated"
        assert refreshed_latest is not None and refreshed_latest.status == "active"
    finally:
        store.close()
