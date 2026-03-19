from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.compiler.compiler import ContextCompiler
from hermit.kernel.context.models.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord


def _memory(
    *,
    memory_id: str,
    category: str,
    claim_text: str,
    retention_class: str,
    scope_kind: str,
    scope_ref: str,
    status: str = "active",
    expires_at: float | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        task_id="task-1",
        conversation_id="chat-1",
        category=category,
        claim_text=claim_text,
        retention_class=retention_class,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        confidence=0.9,
        trust_tier="durable",
        status=status,
        expires_at=expires_at,
        updated_at=time.time(),
    )


def test_context_compiler_selects_static_and_retrieval_memory(tmp_path: Path) -> None:
    workspace_root = str((tmp_path / "repo").resolve())
    artifacts = ArtifactStore(tmp_path / "artifacts")
    compiler = ContextCompiler(artifact_store=artifacts)
    context = TaskExecutionContext(
        conversation_id="chat-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="chat",
        workspace_root=workspace_root,
    )
    beliefs = [
        BeliefRecord(
            belief_id="belief-1",
            task_id="task-1",
            conversation_id="chat-1",
            scope_kind="conversation",
            scope_ref="chat-1",
            category="active_task",
            claim_text="当前正在清理全部定时任务",
            confidence=0.7,
        )
    ]
    memories = [
        _memory(
            memory_id="mem-pref",
            category="user_preference",
            claim_text="统一使用简体中文回复用户",
            retention_class="user_preference",
            scope_kind="global",
            scope_ref="global",
        ),
        _memory(
            memory_id="mem-project",
            category="project_convention",
            claim_text="默认在仓库根目录执行命令",
            retention_class="project_convention",
            scope_kind="workspace",
            scope_ref=workspace_root,
        ),
        _memory(
            memory_id="mem-task",
            category="active_task",
            claim_text="已清理全部定时任务，当前无任何定时任务",
            retention_class="task_state",
            scope_kind="conversation",
            scope_ref="chat-1",
        ),
        _memory(
            memory_id="mem-expired",
            category="tech_decision",
            claim_text="昨天的临时分析结论",
            retention_class="volatile_fact",
            scope_kind="conversation",
            scope_ref="chat-1",
            expires_at=time.time() - 10,
        ),
        _memory(
            memory_id="mem-sensitive",
            category="other",
            claim_text="某用户的手机号是 13800000000",
            retention_class="sensitive_fact",
            scope_kind="conversation",
            scope_ref="chat-2",
        ),
    ]

    pack = compiler.compile(
        context=context,
        working_state=WorkingStateSnapshot(goal_summary="处理定时任务状态"),
        beliefs=beliefs,
        memories=memories,
        query="检查当前定时任务状态",
    )

    assert pack.artifact_uri is not None
    assert pack.artifact_hash is not None
    assert [item["memory_id"] for item in pack.static_memory] == ["mem-pref", "mem-project"]
    assert [item["memory_id"] for item in pack.retrieval_memory] == ["mem-task"]
    assert [item["belief_id"] for item in pack.selected_beliefs] == ["belief-1"]
    assert pack.selection_reasons["mem-pref"] == "static_policy"
    assert pack.excluded_reasons["mem-expired"] == "expired"
    assert pack.excluded_reasons["mem-sensitive"] == "out_of_scope"
    assert "统一使用简体中文回复用户" in compiler.render_static_prompt(pack)
    assert "当前无任何定时任务" in compiler.render_retrieval_prompt(pack)


def test_context_compiler_skips_conversation_retrieval_for_smalltalk(tmp_path: Path) -> None:
    workspace_root = str((tmp_path / "repo").resolve())
    compiler = ContextCompiler(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    context = TaskExecutionContext(
        conversation_id="chat-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="chat",
        workspace_root=workspace_root,
    )
    memories = [
        _memory(
            memory_id="mem-task",
            category="active_task",
            claim_text="用户要求制作 GPT-5.4 vs Grok 3 对比文档，任务尚未完成。",
            retention_class="task_state",
            scope_kind="conversation",
            scope_ref="chat-1",
        ),
        _memory(
            memory_id="mem-pref",
            category="user_preference",
            claim_text="统一使用简体中文回复用户",
            retention_class="user_preference",
            scope_kind="global",
            scope_ref="global",
        ),
    ]

    pack = compiler.compile(
        context=context,
        working_state=WorkingStateSnapshot(goal_summary="你好"),
        beliefs=[],
        memories=memories,
        query="你好",
    )

    assert [item["memory_id"] for item in pack.static_memory] == ["mem-pref"]
    assert pack.retrieval_memory == []
    assert pack.excluded_reasons["mem-task"] == "smalltalk_query"


def test_context_compiler_keeps_followup_task_state_for_short_followup(tmp_path: Path) -> None:
    workspace_root = str((tmp_path / "repo").resolve())
    compiler = ContextCompiler(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    context = TaskExecutionContext(
        conversation_id="chat-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="chat",
        workspace_root=workspace_root,
    )
    memories = [
        _memory(
            memory_id="mem-task",
            category="active_task",
            claim_text="用户要求制作 GPT-5.4 vs Grok 3 对比文档，任务尚未完成。",
            retention_class="task_state",
            scope_kind="conversation",
            scope_ref="chat-1",
        )
    ]

    pack = compiler.compile(
        context=context,
        working_state=WorkingStateSnapshot(goal_summary="继续"),
        beliefs=[],
        memories=memories,
        query="继续",
    )

    assert [item["memory_id"] for item in pack.retrieval_memory] == ["mem-task"]


def test_context_compiler_excludes_quarantined_memory(tmp_path: Path) -> None:
    """Quarantined memories should be excluded with reason 'quarantined'."""
    workspace_root = str((tmp_path / "repo").resolve())
    compiler = ContextCompiler(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    context = TaskExecutionContext(
        conversation_id="chat-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="chat",
        workspace_root=workspace_root,
    )
    memories = [
        _memory(
            memory_id="mem-quarantined",
            category="user_preference",
            claim_text="Quarantined preference",
            retention_class="user_preference",
            scope_kind="global",
            scope_ref="global",
            status="quarantined",
        ),
        _memory(
            memory_id="mem-active",
            category="user_preference",
            claim_text="Active preference",
            retention_class="user_preference",
            scope_kind="global",
            scope_ref="global",
        ),
    ]

    pack = compiler.compile(
        context=context,
        working_state=WorkingStateSnapshot(goal_summary="test"),
        beliefs=[],
        memories=memories,
        query="test query",
    )

    # Lines 123-124: quarantined memory excluded with reason "quarantined"
    assert pack.excluded_reasons.get("mem-quarantined") == "quarantined"
    assert "mem-active" not in pack.excluded_reasons
