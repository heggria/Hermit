from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.compiler.compiler import ContextCompiler
from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.knowledge import BeliefService, MemoryRecordService
from hermit.kernel.context.models.context import (
    TaskExecutionContext,
    WorkingStateSnapshot,
    capture_execution_environment,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord


def _context(tmp_path: Path) -> TaskExecutionContext:
    return TaskExecutionContext(
        conversation_id="chat-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="feishu",
        actor_principal_id="principal_assistant",
        policy_profile="strict",
        workspace_root=str(tmp_path),
        created_at=123.0,
    )


def _memory(
    memory_id: str,
    *,
    category: str,
    claim_text: str,
    retention_class: str = "volatile_fact",
    scope_kind: str = "conversation",
    scope_ref: str = "chat-1",
    status: str = "active",
    confidence: float = 0.8,
    trust_tier: str = "durable",
    expires_at: float | None = None,
    updated_at: float = 100.0,
    supersedes: list[str] | None = None,
    subject_key: str = "",
    topic_key: str = "",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        task_id=f"{memory_id}-task",
        conversation_id="chat-1",
        category=category,
        claim_text=claim_text,
        structured_assertion={
            "subject_key": subject_key,
            "topic_key": topic_key,
        }
        if (subject_key or topic_key)
        else {},
        retention_class=retention_class,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        status=status,
        confidence=confidence,
        trust_tier=trust_tier,
        expires_at=expires_at,
        updated_at=updated_at,
        supersedes=list(supersedes or []),
    )


def _belief(
    belief_id: str,
    *,
    claim_text: str,
    scope_kind: str = "conversation",
    scope_ref: str = "chat-1",
    confidence: float = 0.6,
) -> BeliefRecord:
    return BeliefRecord(
        belief_id=belief_id,
        task_id=f"{belief_id}-task",
        conversation_id="chat-1",
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        category="project_convention",
        claim_text=claim_text,
        confidence=confidence,
    )


def test_task_execution_context_roundtrip_and_environment_capture(tmp_path: Path) -> None:
    ctx = _context(tmp_path)

    payload = ctx.to_dict()
    restored = TaskExecutionContext.from_dict(payload)
    defaulted = TaskExecutionContext.from_dict(
        {
            "conversation_id": "chat-2",
            "task_id": "task-2",
            "step_id": "step-2",
            "step_attempt_id": "attempt-2",
        }
    )
    env = capture_execution_environment(cwd=tmp_path)

    assert restored == ctx
    assert defaulted.source_channel == "unknown"
    assert defaulted.actor_principal_id == "principal_user"
    assert defaulted.policy_profile == "default"
    assert env["cwd"] == str(tmp_path)
    assert env["platform"]
    assert env["python"]


def test_working_state_snapshot_truncates_lengths() -> None:
    snapshot = WorkingStateSnapshot(
        goal_summary="g" * 500,
        open_loops=["a" * 250] * 10,
        active_constraints=["b" * 250] * 10,
        pending_approvals=["c" * 250] * 10,
        recent_results=["d" * 250] * 10,
    )

    assert len(snapshot.goal_summary) == 400
    assert len(snapshot.open_loops) == 8
    assert all(len(item) == 200 for item in snapshot.open_loops)
    assert len(snapshot.active_constraints) == 8
    assert len(snapshot.pending_approvals) == 8
    assert len(snapshot.recent_results) == 8


def test_memory_governance_classification_and_scope_matching(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    governance = MemoryGovernanceService()
    ctx = _context(tmp_path)

    sensitive = governance.classify_claim(
        category="user_preference",
        claim_text="我的手机号不要写进总结里",
        conversation_id="chat-1",
    )
    project = governance.classify_claim(
        category="project_convention",
        claim_text="默认工作目录固定到 /repo",
        conversation_id="chat-1",
        workspace_root=str(tmp_path),
    )
    task_state = governance.classify_claim(
        category="active_task",
        claim_text="当前无任何定时任务",
        conversation_id="chat-9",
    )

    assert sensitive.retention_class == "sensitive_fact"
    assert sensitive.scope_kind == "global"
    assert project.static_injection is True
    assert project.scope_ref == str(tmp_path.resolve())
    assert task_state.scope_kind == "conversation"
    assert task_state.scope_ref == "chat-9"
    assert task_state.expires_at is not None
    assert governance.scope_matches("global", "global", context=ctx) is True
    assert governance.scope_matches("conversation", "chat-1", context=ctx) is True
    assert governance.scope_matches("workspace", str(tmp_path.resolve()), context=ctx) is True
    assert governance.scope_matches("entity", "task-1", context=ctx) is True
    assert governance.scope_matches("mystery", "x", context=ctx) is False


def test_memory_governance_reclassifies_claims_from_text_signals(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    governance = MemoryGovernanceService()

    preference = governance.classify_claim(
        category="other",
        claim_text="以后都用简体中文回复我，不要再切英文。",
        conversation_id="chat-1",
    )
    task_state = governance.classify_claim(
        category="other",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        conversation_id="chat-9",
    )
    tooling = governance.classify_claim(
        category="other",
        claim_text="Hermit 仓库位于 /Users/beta/work/Hermit，默认使用 uv 管理依赖。",
        conversation_id="chat-1",
        workspace_root=str(tmp_path),
    )
    neutral = governance.classify_claim(
        category="other",
        claim_text="SQLite 在这个阶段比向量库更轻。",
        conversation_id="chat-1",
    )

    assert preference.category == "user_preference"
    assert preference.retention_class == "user_preference"
    assert preference.scope_kind == "global"

    assert task_state.category == "active_task"
    assert task_state.retention_class == "task_state"
    assert task_state.scope_ref == "chat-9"
    assert task_state.expires_at is not None

    assert tooling.category == "tooling_environment"
    assert tooling.retention_class == "tooling_environment"
    assert tooling.scope_kind == "workspace"

    assert neutral.category == "other"
    assert neutral.retention_class == "volatile_fact"


def test_memory_governance_inspection_exposes_subject_topic_and_explanation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    governance = MemoryGovernanceService()

    inspection = governance.inspect_claim(
        category="other",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        conversation_id="chat-9",
        workspace_root=str(tmp_path),
    )

    assert inspection["category"] == "active_task"
    assert inspection["retention_class"] == "task_state"
    assert inspection["subject_key"] == "schedule"
    assert inspection["topic_key"]
    assert any(str(item).startswith("signal:task_state=") for item in inspection["explanation"])
    assert inspection["structured_assertion"]["resolved_category"] == "active_task"


def test_memory_governance_static_retrieval_and_expiry_rules(tmp_path: Path) -> None:
    governance = MemoryGovernanceService()
    ctx = _context(tmp_path)

    static_memory = _memory(
        "m-static",
        category="project_convention",
        claim_text="默认工作目录固定到 /repo",
        retention_class="project_convention",
        scope_kind="workspace",
        scope_ref=str(tmp_path.resolve()),
    )
    sensitive_memory = _memory(
        "m-sensitive",
        category="other",
        claim_text="用户病史需要谨慎处理",
        retention_class="sensitive_fact",
        scope_kind="conversation",
        scope_ref="chat-1",
    )
    revoked_memory = _memory(
        "m-revoked",
        category="other",
        claim_text="旧结论",
        retention_class="revoked",
        scope_kind="conversation",
        scope_ref="chat-1",
    )
    expired_memory = _memory(
        "m-expired",
        category="other",
        claim_text="会过期",
        expires_at=time.time() - 1,
    )

    assert governance.eligible_for_static(static_memory, context=ctx) is True
    assert governance.retrieval_reason(static_memory, context=ctx) == "retrieval_policy"
    assert governance.retrieval_reason(sensitive_memory, context=ctx) == "scope_match"
    assert governance.retrieval_reason(revoked_memory, context=ctx) is None
    assert governance.is_expired(expired_memory) is True


def test_memory_governance_candidate_and_supersede_detection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    governance = MemoryGovernanceService()
    classification = governance.classify_claim(
        category="active_task",
        claim_text="当前无任何定时任务",
        conversation_id="chat-2",
    )
    active_records = [
        _memory(
            "m-1",
            category="active_task",
            claim_text="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
            retention_class="task_state",
            scope_ref="chat-1",
        ),
        _memory(
            "m-2",
            category="active_task",
            claim_text="当前无任何定时任务",
            retention_class="task_state",
            scope_ref="chat-3",
        ),
        _memory(
            "m-3",
            category="active_task",
            claim_text="失效记录",
            retention_class="task_state",
            status="invalidated",
        ),
    ]

    candidates = governance.candidate_records_for_supersede(
        classification=classification,
        active_records=active_records,
    )
    duplicate, superseded = governance.find_superseded_records(
        classification=classification,
        claim_text="当前无任何定时任务",
        active_records=active_records,
        entry_from_record=MemoryRecordService._entry_from_memory,
    )

    assert [record.memory_id for record in candidates] == ["m-1", "m-2"]
    assert duplicate is not None and duplicate.memory_id == "m-2"
    assert [record.memory_id for record in superseded] == ["m-1"]

    duplicate, superseded = governance.find_superseded_records(
        classification=classification,
        claim_text="2026-03-13 用户要求清理全部定时任务，已完成：当前无任何定时任务。",
        active_records=active_records[:1],
        entry_from_record=MemoryRecordService._entry_from_memory,
    )
    assert duplicate is None
    assert [record.memory_id for record in superseded] == ["m-1"]


def test_memory_governance_task_state_subjects_do_not_cross_supersede(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    governance = MemoryGovernanceService()
    classification = governance.classify_claim(
        category="other",
        claim_text="当前无任何定时任务，刚刚已经全部清理完成。",
        conversation_id="chat-2",
    )
    active_records = [
        _memory(
            "m-readme",
            category="active_task",
            claim_text="用户希望改写 README.md，使其更吸引外部开发者参与贡献。",
            retention_class="task_state",
            scope_ref="chat-1",
            subject_key="readme",
        ),
        _memory(
            "m-schedule",
            category="active_task",
            claim_text="已设定每日定时任务：每天早上 10 点自动搜索 AI 最新动态并推送日报到飞书群。",
            retention_class="task_state",
            scope_ref="chat-3",
            subject_key="schedule",
        ),
    ]

    candidates = governance.candidate_records_for_supersede(
        classification=classification,
        active_records=active_records,
    )

    assert [record.memory_id for record in candidates] == ["m-schedule"]


def test_context_compiler_builds_pack_artifact_and_prompts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    compiler = ContextCompiler(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    ctx = _context(tmp_path)
    working_state = WorkingStateSnapshot(
        goal_summary="整理记忆",
        open_loops=["确认日报结果"],
        recent_results=["已生成摘要"],
    )
    memories = [
        _memory(
            "m-static",
            category="project_convention",
            claim_text="默认工作目录固定到 /repo",
            retention_class="project_convention",
            scope_kind="workspace",
            scope_ref=str(tmp_path.resolve()),
            updated_at=10.0,
        ),
        _memory(
            "m-rank-1",
            category="other",
            claim_text="AI 日报每天 10 点发送",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=200.0,
        ),
        _memory(
            "m-rank-2",
            category="other",
            claim_text="日报需要带上来源链接",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=150.0,
        ),
        _memory(
            "m-rank-3",
            category="other",
            claim_text="日报内容使用中文",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=140.0,
        ),
        _memory(
            "m-rank-4",
            category="other",
            claim_text="日报风格偏简洁",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=130.0,
        ),
        _memory(
            "m-rank-5",
            category="other",
            claim_text="日报最后附带结论",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=120.0,
        ),
        _memory(
            "m-rank-6",
            category="other",
            claim_text="日报不要写无关背景",
            scope_kind="conversation",
            scope_ref="chat-1",
            updated_at=110.0,
        ),
        _memory(
            "m-expired",
            category="other",
            claim_text="过期记忆",
            expires_at=time.time() - 1,
        ),
        _memory(
            "m-inactive",
            category="other",
            claim_text="失效记忆",
            status="invalidated",
        ),
        _memory(
            "m-out",
            category="other",
            claim_text="别的会话",
            scope_kind="conversation",
            scope_ref="chat-other",
        ),
    ]
    beliefs = [
        _belief("b-in", claim_text="当前默认工作目录固定到 /repo"),
        _belief("b-out", claim_text="别的会话事实", scope_ref="chat-other"),
    ]

    pack = compiler.compile(
        context=ctx,
        working_state=working_state,
        beliefs=beliefs,
        memories=memories,
        query="请整理今天的 AI 日报",
    )

    assert [item["memory_id"] for item in pack.static_memory] == ["m-static"]
    assert len(pack.retrieval_memory) == 5
    assert pack.selected_beliefs == [
        {
            "belief_id": "b-in",
            "claim_text": "当前默认工作目录固定到 /repo",
            "scope_kind": "conversation",
            "scope_ref": "chat-1",
            "confidence": 0.6,
        }
    ]
    assert pack.selection_reasons["m-static"] == "static_policy"
    assert pack.selection_reasons["m-rank-1"] == "retrieval_rank"
    assert "m-expired" in pack.excluded_memory_ids
    assert pack.excluded_reasons["m-expired"] == "expired"
    assert pack.excluded_reasons["m-inactive"] == "status:invalidated"
    assert pack.excluded_reasons["m-out"] == "out_of_scope"
    assert pack.excluded_reasons["m-rank-6"] == "rank_cutoff"
    assert pack.artifact_uri is not None
    assert pack.artifact_hash is not None
    assert pack.to_payload()["kind"] == "context.pack/v3"
    assert pack.retrieval_memory[0]["subject_key"]
    assert isinstance(pack.retrieval_memory[0]["governance_explanation"], list)
    assert "默认工作目录固定到 /repo" in compiler.render_static_prompt(pack)
    assert "与当前任务最相关的跨会话记忆" in compiler.render_retrieval_prompt(pack)


def test_context_compiler_helpers_cover_payload_conversion_and_scoring(tmp_path: Path) -> None:
    compiler = ContextCompiler()
    ctx = _context(tmp_path)
    memory = _memory(
        "m-1",
        category="project_convention",
        claim_text="默认工作目录固定到 /repo",
        retention_class="project_convention",
        scope_kind="workspace",
        scope_ref=str(tmp_path.resolve()),
        confidence=0.9,
        updated_at=1234.0,
        expires_at=50.0,
        supersedes=["旧约定"],
    )

    payload = compiler._memory_payload(memory)
    categories = compiler._categories_from_payload([payload])
    score = compiler._retrieval_score(memory, context=ctx, query="请使用 /repo")

    assert payload["claim_text"] == "默认工作目录固定到 /repo"
    assert categories["project_convention"][0].content == "默认工作目录固定到 /repo"
    assert categories["project_convention"][0].supersedes == ["旧约定"]
    assert score > 100.0


def test_memory_services_support_duplicate_promotion_and_mirror_render(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    mirror = tmp_path / "memories.md"
    try:
        beliefs = BeliefService(store)
        memories = MemoryRecordService(store, mirror_path=mirror)

        belief = beliefs.record(
            task_id="task-1",
            conversation_id="chat-1",
            scope_kind="conversation",
            scope_ref="chat-1",
            category="project_convention",
            content="默认工作目录固定到 /repo",
            confidence=0.8,
            evidence_refs=[],
        )
        reconciliation = store.create_reconciliation(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            contract_ref="contract-memory-1",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Promote durable memory.",
            authorized_effect_summary="Promote durable memory.",
            observed_effect_summary="Belief remained stable after reconciliation.",
            receipted_effect_summary="Belief remained stable after reconciliation.",
            result_class="satisfied",
            recommended_resolution="promote_learning",
        )
        promoted = memories.promote_from_belief(
            belief=belief,
            conversation_id="chat-1",
            workspace_root=str(tmp_path),
            reconciliation_ref=reconciliation.reconciliation_id,
        )
        duplicate = memories.promote_from_belief(
            belief=belief,
            conversation_id="chat-1",
            workspace_root=str(tmp_path),
            reconciliation_ref=reconciliation.reconciliation_id,
        )
        beliefs.supersede(belief.belief_id, ["旧约定"])
        beliefs.contradict(belief.belief_id, ["belief-2"])
        exported = memories.export_mirror()
        categories = memories.active_categories()
        memories.invalidate(promoted.memory_id)
        beliefs.invalidate(belief.belief_id)

        refreshed_belief = store.get_belief(belief.belief_id)
        refreshed_memory = store.get_memory_record(promoted.memory_id)

        assert duplicate.memory_id == promoted.memory_id
        assert categories["project_convention"][0].content == "默认工作目录固定到 /repo"
        assert exported == mirror
        assert "默认工作目录固定到 /repo" in mirror.read_text(encoding="utf-8")
        assert refreshed_belief is not None and refreshed_belief.status == "invalidated"
        assert refreshed_belief.supersedes == ["旧约定"]
        assert refreshed_belief.contradicts == ["belief-2"]
        assert refreshed_memory is not None and refreshed_memory.status == "invalidated"
        assert refreshed_memory.structured_assertion["resolved_category"] == "project_convention"
    finally:
        store.close()


def test_memory_record_service_reconcile_marks_duplicates(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    try:
        service = MemoryRecordService(store)
        first = store.create_memory_record(
            task_id="task-1",
            conversation_id="chat-1",
            category="project_convention",
            claim_text="默认工作目录固定到 /repo",
            scope_kind="workspace",
            scope_ref=str(tmp_path.resolve()),
            retention_class="project_convention",
        )
        second = store.create_memory_record(
            task_id="task-2",
            conversation_id="chat-1",
            category="project_convention",
            claim_text="默认工作目录固定到 /repo",
            scope_kind="workspace",
            scope_ref=str(tmp_path.resolve()),
            retention_class="project_convention",
        )

        result = service.reconcile_active_records()
        refreshed_first = store.get_memory_record(first.memory_id)
        refreshed_second = store.get_memory_record(second.memory_id)

        assert result["duplicate_count"] == 1
        assert refreshed_first is not None and refreshed_first.status == "active"
        assert refreshed_second is not None and refreshed_second.status == "invalidated"
        assert refreshed_second.superseded_by_memory_id == refreshed_first.memory_id
    finally:
        store.close()


def test_memory_promotion_requires_satisfied_reconciliation(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    try:
        belief = BeliefService(store).record(
            task_id="task-guarded",
            conversation_id="chat-guarded",
            scope_kind="conversation",
            scope_ref="chat-guarded",
            category="project_convention",
            content="默认工作目录固定到 /repo",
            confidence=0.8,
            evidence_refs=[],
        )
        promoted = MemoryRecordService(store).promote_from_belief(
            belief=belief,
            conversation_id="chat-guarded",
            workspace_root=str(tmp_path),
        )

        refreshed_belief = store.get_belief(belief.belief_id)

        assert promoted is None
        assert store.list_memory_records(limit=10) == []
        assert refreshed_belief is not None
        assert refreshed_belief.promotion_candidate is False
        assert refreshed_belief.validation_basis == "promotion_blocked:reconciliation_missing"
    finally:
        store.close()
