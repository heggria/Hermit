"""E2E: Conversation routing — ingress decision, focus, disambiguation, and notes.

Exercises multi-task conversation scenarios where the ingress router must decide
how to route incoming messages: start new tasks, append notes, disambiguate, and
handle terminal followups.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


def test_single_task_append_note_and_followup(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """When one task is active, follow-up messages append as notes."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-routing-1",
        goal="整理项目文档",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Follow-up with continue marker appends note
    decision = controller.decide_ingress(
        conversation_id="e2e-routing-1",
        source_channel="feishu",
        raw_text="补充一点 API 参考文档",
        prompt="补充一点 API 参考文档",
    )
    assert decision.mode == "append_note"
    assert decision.task_id == ctx.task_id

    # Verify note event
    events = store.list_events(task_id=ctx.task_id)
    assert any(
        e["event_type"] == "task.note.appended"
        and e["payload"]["raw_text"] == "补充一点 API 参考文档"
        for e in events
    )


def test_chat_only_message_starts_without_task_binding(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Simple greetings are routed as chat_only, not bound to existing tasks."""
    _store, _artifacts, controller, _executor, workspace = e2e_runtime

    controller.start_task(
        conversation_id="e2e-routing-chat",
        goal="Active background task",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    decision = controller.decide_ingress(
        conversation_id="e2e-routing-chat",
        source_channel="feishu",
        raw_text="你好",
        prompt="你好",
    )
    assert decision.mode == "start"
    assert decision.intent == "chat_only"


def test_explicit_new_task_marker_creates_new_task(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Messages with explicit new task markers create new tasks regardless of active ones."""
    _store, _artifacts, controller, _executor, workspace = e2e_runtime

    controller.start_task(
        conversation_id="e2e-routing-explicit",
        goal="Existing task",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    decision = controller.decide_ingress(
        conversation_id="e2e-routing-explicit",
        source_channel="feishu",
        raw_text="新任务：整理桌面文件",
        prompt="新任务：整理桌面文件",
    )
    assert decision.mode == "start"
    assert decision.intent == "start_new_task"
    assert decision.reason == "explicit_new_task_marker"


def test_focus_task_and_disambiguation_resolution(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Focus task mechanism and disambiguation resolution work end-to-end."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    # Create two tasks in same conversation
    first = controller.start_task(
        conversation_id="e2e-routing-focus",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )
    second = controller.start_task(
        conversation_id="e2e-routing-focus",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Set focus to first task
    controller.focus_task("e2e-routing-focus", first.task_id)
    conversation = store.get_conversation("e2e-routing-focus")
    assert conversation is not None
    assert conversation.focus_task_id == first.task_id

    # Follow-up goes to focused task (use continue marker)
    decision = controller.decide_ingress(
        conversation_id="e2e-routing-focus",
        source_channel="feishu",
        raw_text="补充一点说明",
        prompt="补充一点说明",
    )
    assert decision.mode == "append_note"
    assert decision.task_id == first.task_id

    # Create pending disambiguation
    pending = store.create_ingress(
        conversation_id="e2e-routing-focus",
        source_channel="feishu",
        raw_text="这个改一下",
        normalized_text="这个改一下",
        actor="user",
        prompt_ref="这个改一下",
    )
    store.update_ingress(
        pending.ingress_id,
        status="pending_disambiguation",
        resolution="pending_disambiguation",
        rationale={"reason_codes": ["ambiguous_candidate_tie"]},
    )
    assert store.count_pending_ingresses(conversation_id="e2e-routing-focus") == 1

    # Focus resolves disambiguation
    resolved = controller.focus_task("e2e-routing-focus", second.task_id)
    assert resolved is not None
    assert resolved.task_id == second.task_id
    assert store.count_pending_ingresses(conversation_id="e2e-routing-focus") == 0


def test_terminal_task_followup_anchors_continuation(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """When a completed task has relevant follow-up, it anchors to the terminal task."""
    _store, _artifacts, controller, _executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-routing-terminal",
        goal="查询北京天气",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="北京今天天气不错：晴到多云，0～12℃。",
        result_text="北京今天天气不错：晴到多云，0～12℃，微风。",
    )

    decision = controller.decide_ingress(
        conversation_id="e2e-routing-terminal",
        source_channel="feishu",
        raw_text="你说一下北京天气详情",
        prompt="你说一下北京天气详情",
    )
    assert decision.mode == "start"
    assert decision.intent == "continue_task"
    assert decision.anchor_task_id == ctx.task_id
    assert decision.continuation_anchor is not None
    assert "北京今天天气不错" in decision.continuation_anchor["outcome_summary"]


def test_branch_followup_forks_child_task(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """A branching follow-up creates a child task with parent reference."""
    _store, _artifacts, controller, _executor, workspace = e2e_runtime

    active = controller.start_task(
        conversation_id="e2e-routing-branch",
        goal="分析竞品数据",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    decision = controller.decide_ingress(
        conversation_id="e2e-routing-branch",
        source_channel="feishu",
        raw_text="顺便查一下竞品价格",
        prompt="顺便查一下竞品价格",
    )
    assert decision.mode == "start"
    assert decision.resolution == "fork_child"
    assert decision.parent_task_id == active.task_id


def test_explicit_task_ref_binds_deterministically(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """When an explicit task reference is given, binding is deterministic."""
    _store, _artifacts, controller, _executor, workspace = e2e_runtime

    first = controller.start_task(
        conversation_id="e2e-routing-explicit-ref",
        goal="Task A",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )
    controller.start_task(
        conversation_id="e2e-routing-explicit-ref",
        goal="Task B",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )

    decision = controller.decide_ingress(
        conversation_id="e2e-routing-explicit-ref",
        source_channel="feishu",
        raw_text="改一下任务内容",
        prompt="改一下任务内容",
        explicit_task_ref=first.task_id,
    )
    assert decision.mode == "append_note"
    assert decision.task_id == first.task_id
    assert decision.reason_codes == ["explicit_task_ref"]
