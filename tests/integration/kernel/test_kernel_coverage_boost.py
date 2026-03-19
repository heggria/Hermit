from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.ingress_router import IngressRouter
from hermit.kernel.task.services.planning import PlanningService, PlanningState
from hermit.kernel.task.state.outcomes import (
    build_task_outcome,
    clean_runtime_text,
    outcome_source_artifact_refs,
    trim_text,
)


def _start_task(
    controller: TaskController,
    *,
    conversation_id: str = "chat-coverage",
    goal: str = "Inspect payload",
    kind: str = "respond",
) -> object:
    return controller.start_task(
        conversation_id=conversation_id,
        goal=goal,
        source_channel="chat",
        kind=kind,
    )


class _ReceiptStore:
    def __init__(self, receipts: list[object]) -> None:
        self._receipts = receipts

    def list_receipts(self, *, task_id: str, limit: int = 50):
        return list(self._receipts)[:limit]


def test_outcome_helpers_strip_runtime_markup_and_build_terminal_summary() -> None:
    cleaned = clean_runtime_text(
        "<session_time>now</session_time>\n<feishu_msg_id>om_1</feishu_msg_id>\n\n保留正文\n"
    )
    assert cleaned == "保留正文"
    assert trim_text("abcdef", limit=1) == "a"
    assert trim_text("abcdef", limit=4) == "abc…"

    receipts = [
        type(
            "Receipt", (), {"output_refs": ["artifact-1", "artifact-1", "artifact-2", "", None]}
        )(),
        type("Receipt", (), {"output_refs": ["artifact-3"]})(),
    ]
    store = _ReceiptStore(receipts)
    assert outcome_source_artifact_refs(store, "task-1", limit=2) == ["artifact-1", "artifact-2"]

    assert build_task_outcome(store=store, task_id="task-1", status="running", events=[]) is None
    assert (
        build_task_outcome(
            store=store,
            task_id="task-1",
            status="completed",
            events=[{"event_type": "task.started", "payload": {}, "occurred_at": 0}],
        )
        is None
    )

    outcome = build_task_outcome(
        store=store,
        task_id="task-1",
        status="completed",
        events=[
            {
                "event_type": "task.created",
                "payload": {"title": "整理本周测试报告"},
                "occurred_at": 1.0,
            },
            {
                "event_type": "task.completed",
                "payload": {},
                "occurred_at": 2.0,
            },
        ],
        artifact_limit=2,
    )
    assert outcome is not None
    assert outcome["completed_at"] == 2.0
    assert outcome["outcome_summary"] == "整理本周测试报告"
    assert outcome["source_artifact_refs"] == ["artifact-1", "artifact-2"]


def test_planning_service_tracks_pending_state_and_plan_lifecycle(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    controller = TaskController(store)
    planning = PlanningService(store, artifacts)
    ctx = _start_task(controller, conversation_id="conv-plan", goal="先规划一下发布步骤")

    assert PlanningService.planning_requested("先别执行，先规划一下") is True
    assert PlanningService.planning_requested("直接做吧") is False

    assert planning.pending_for_conversation("conv-plan") is False
    planning.set_pending_for_conversation("conv-plan", enabled=True)
    assert planning.pending_for_conversation("conv-plan") is True
    planning.set_pending_for_conversation("conv-plan", enabled=False)
    assert planning.pending_for_conversation("conv-plan") is False

    entered = planning.enter_planning(ctx.task_id)
    assert entered.planning_mode is True
    assert entered.plan_status == "none"
    assert planning.enter_planning(ctx.task_id).planning_mode is True

    artifact_ref = planning.capture_plan_result(ctx, plan_text="1. 跑测试\n2. 写总结")
    assert artifact_ref
    drafted = planning.state_for_task(ctx.task_id)
    assert drafted.selected_plan_ref == artifact_ref
    assert drafted.plan_status == "selected"
    assert drafted.candidate_plan_refs == [artifact_ref]
    assert planning.load_selected_plan_text(ctx.task_id) == "1. 跑测试\n2. 写总结"
    assert planning.latest_plan_artifact_refs(ctx.task_id) == [artifact_ref]

    confirmed, decision_id = planning.confirm_selected_plan(ctx, actor="tester")
    assert decision_id
    assert confirmed.planning_mode is False
    assert confirmed.plan_status == "executing"
    assert confirmed.latest_planning_decision_id == decision_id

    deleted = store.get_artifact(artifact_ref)
    assert deleted is not None
    Path(deleted.uri).unlink()
    assert planning.load_selected_plan_text(ctx.task_id) is None


def test_planning_service_returns_none_without_selection_and_locates_latest_plan_attempt(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    controller = TaskController(store)
    planning = PlanningService(store, artifacts)
    ctx = _start_task(controller, conversation_id="conv-plan-2", goal="做个计划")

    empty_state, decision_id = planning.confirm_selected_plan(ctx, actor="tester")
    assert decision_id is None
    assert empty_state == PlanningState()
    assert planning.capture_plan_result(ctx, plan_text="   ") is None

    plan_ctx = controller.start_followup_step(
        task_id=ctx.task_id,
        kind="plan",
        status="ready",
        workspace_root="/tmp/plan-workspace",
        ingress_metadata={"source": "coverage"},
    )
    latest_attempt = planning.latest_planning_attempt(ctx.task_id)
    assert latest_attempt is not None
    assert latest_attempt.step_id == plan_ctx.step_id
    assert latest_attempt.step_attempt_id == plan_ctx.step_attempt_id
    assert latest_attempt.workspace_root == "/tmp/plan-workspace"
    assert latest_attempt.ingress_metadata == {"source": "coverage"}


def test_provider_input_compile_materializes_large_ingress_and_updates_attempt_context(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    controller = TaskController(store)
    compiler = ProviderInputCompiler(store, artifacts)
    planning = PlanningService(store, artifacts)
    ctx = _start_task(controller)

    plan_ref = planning.capture_plan_result(ctx, plan_text="先跑覆盖率，再补缺失分支")
    assert plan_ref
    extra_artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="notes",
        uri="memory://note",
        content_hash="hash-note",
        producer="test",
        retention_class="task",
        trust_tier="observed",
        metadata={"kind": "notes"},
    )
    store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="context.pack/v3",
        uri="memory://old-pack",
        content_hash="hash-pack",
        producer="test",
        retention_class="audit",
        trust_tier="derived",
        metadata={},
    )

    long_body = "\n".join(f"line {idx}" for idx in range(120))
    raw_text = f"请分析下面内容\n```py\nprint('hi')\n```\n{long_body}"
    compiled = compiler.compile(task_context=ctx, final_prompt=raw_text, raw_text=raw_text)

    assert compiled.source_mode == "compiled"
    assert compiled.context_pack_ref
    assert len(compiled.ingress_artifact_refs) == 2
    assert "<artifact_usage>" in compiled.messages[0]["content"]
    assert "<normalized_prompt>" in compiled.messages[0]["content"]

    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert attempt.context["ingress_metadata"]["detected_payload_kinds"] == [
        "code_block",
        "long_text",
    ]
    assert (
        attempt.context["ingress_metadata"]["ingress_artifact_refs"]
        == compiled.ingress_artifact_refs
    )

    related_refs = compiler._relevant_artifact_refs(ctx, compiled.ingress_artifact_refs)
    assert related_refs[:2] == compiled.ingress_artifact_refs
    assert plan_ref in related_refs
    assert extra_artifact.artifact_id in related_refs
    assert all(
        store.get_artifact(ref).kind != "context.pack/v3"
        for ref in related_refs
        if store.get_artifact(ref)
    )


def test_provider_input_compile_clears_input_dirty_and_emits_recompiled_event(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    controller = TaskController(store)
    compiler = ProviderInputCompiler(store, artifacts)
    ctx = _start_task(controller, conversation_id="conv-dirty-compile")

    controller.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="补充新输入",
        prompt="补充新输入",
        ingress_id="ingress_compile_1",
    )
    compiled = compiler.compile(task_context=ctx, final_prompt="当前请求", raw_text="当前请求")

    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert compiled.context_pack_ref
    assert attempt is not None
    assert attempt.context["input_dirty"] is False
    assert attempt.context["last_compiled_ingress_id"] == "ingress_compile_1"
    events = store.list_events(task_id=ctx.task_id, limit=50)
    assert any(event["event_type"] == "step_attempt.recompiled" for event in events)


def test_task_controller_decide_ingress_persists_raw_reply_and_quote_refs(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    ctx = _start_task(controller, conversation_id="conv-reply-evidence", goal="整理文档")
    artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="notes",
        uri="memory://artifact-evidence",
        content_hash="hash-evidence",
        producer="test",
        retention_class="task",
        trust_tier="observed",
        metadata={},
    )

    decision = controller.decide_ingress(
        conversation_id="conv-reply-evidence",
        source_channel="feishu",
        raw_text=f"继续这个，并参考 {artifact.artifact_id}",
        prompt=f"继续这个，并参考 {artifact.artifact_id}",
        reply_to_task_id=ctx.task_id,
        reply_to_ref="om_root",
        quoted_message_ref="om_quote",
    )

    ingress = store.get_ingress(decision.ingress_id or "")
    assert ingress is not None
    assert ingress.reply_to_ref == "om_root"
    assert ingress.quoted_message_ref == "om_quote"
    assert ingress.referenced_artifact_refs == [artifact.artifact_id]
    assert ingress.chosen_task_id == ctx.task_id


def test_provider_input_normalize_without_artifact_store_keeps_inline_only(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    ctx = _start_task(controller, conversation_id="conv-inline")
    compiler = ProviderInputCompiler(store, None)

    long_text = "\n".join(f"line {idx}" for idx in range(100))
    normalized = compiler.normalize_ingress(
        task_context=ctx,
        raw_text=f"```python\nprint('hello')\n```\n{long_text}",
        final_prompt="ignored",
    )
    assert normalized["ingress_artifact_refs"] == []
    assert normalized["detected_payload_kinds"] == []

    missing_attempt_ctx = replace(ctx, step_attempt_id="missing-attempt")
    compiled = compiler.compile(
        task_context=missing_attempt_ctx,
        final_prompt="plain request",
        raw_text="plain request",
    )
    assert compiled.context_pack_ref is None
    assert compiled.ingress_artifact_refs == []


def test_task_controller_handles_planning_gate_and_followup_steps(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    planning = PlanningService(store)
    ctx = _start_task(controller, conversation_id="conv-controller", goal="先规划再执行")

    planning.enter_planning(ctx.task_id)
    controller.mark_planning_ready(
        ctx,
        plan_artifact_ref="artifact-plan-1",
        result_preview="已经整理候选计划",
        result_text="计划一：补测试。计划二：补文档。",
    )
    task = store.get_task(ctx.task_id)
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert task is not None and task.status == "blocked"
    assert attempt is not None and attempt.waiting_reason == "awaiting_plan_confirmation"
    assert controller.active_task_for_conversation("conv-controller") is None

    decision = controller.decide_ingress(
        conversation_id="conv-controller",
        source_channel="chat",
        raw_text="继续执行",
        prompt="继续执行",
    )
    assert decision.mode == "append_note"
    assert decision.reason == "matched_open_task"
    assert decision.task_id == ctx.task_id

    gated = controller.decide_ingress(
        conversation_id="conv-controller",
        source_channel="chat",
        raw_text="报销预算统计",
        prompt="报销预算统计",
    )
    assert gated.mode == "start"
    assert gated.reason == "planning_confirmation_gate"
    assert gated.intent == "start_new_task"

    followup = controller.start_followup_step(
        task_id=ctx.task_id,
        kind="plan",
        status="ready",
        workspace_root="/tmp/followup",
        ingress_metadata={"resume": True},
    )
    followup_task = store.get_task(ctx.task_id)
    followup_step = store.get_step(followup.step_id)
    followup_attempt = store.get_step_attempt(followup.step_attempt_id)
    assert followup_task is not None and followup_task.status == "queued"
    assert followup_step is not None and followup_step.status == "ready"
    assert followup_attempt is not None and followup_attempt.status == "ready"
    assert followup.workspace_root == "/tmp/followup"
    assert followup.ingress_metadata == {"resume": True}

    with pytest.raises(KeyError):
        controller.start_followup_step(task_id="missing-task", kind="respond")


def test_task_controller_surfaces_missing_task_rows_for_attempt_context(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    ctx = _start_task(controller, conversation_id="conv-missing-task")

    with store._get_conn():
        store._get_conn().execute("DELETE FROM tasks WHERE task_id = ?", (ctx.task_id,))

    with pytest.raises(KeyError):
        controller.context_for_attempt(ctx.step_attempt_id)
    with pytest.raises(KeyError):
        controller.enqueue_resume(ctx.step_attempt_id)


def test_kernel_store_focus_ready_queue_and_ingress_lifecycle(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)

    store.ensure_conversation("conv-store", source_channel="chat")
    assert store.ensure_valid_focus("missing-conversation") is None
    assert store.ensure_valid_focus("conv-store") is None

    closed = _start_task(controller, conversation_id="conv-store", goal="旧任务")
    controller.finalize_result(closed, status="succeeded", result_preview="done")
    open_ctx = controller.enqueue_task(
        conversation_id="conv-store",
        goal="待执行任务",
        source_channel="chat",
        kind="respond",
        requested_by="tester",
    )
    store.set_conversation_focus("conv-store", task_id=closed.task_id, reason="manual")
    assert store.ensure_valid_focus("conv-store") == open_ctx.task_id
    conversation = store.get_conversation("conv-store")
    assert conversation is not None
    assert conversation.focus_task_id == open_ctx.task_id
    assert conversation.focus_reason == "fallback_latest_open"

    ready = store.list_ready_step_attempts(limit=10)
    assert [attempt.step_attempt_id for attempt in ready] == [open_ctx.step_attempt_id]
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None and claimed.step_attempt_id == open_ctx.step_attempt_id
    assert store.claim_next_ready_step_attempt() is None

    ingress = store.create_ingress(
        conversation_id="conv-store",
        source_channel="chat",
        raw_text="继续补覆盖率",
        normalized_text="继续补覆盖率",
        actor="tester",
        prompt_ref="prompt-1",
        reply_to_ref="reply-1",
        quoted_message_ref="quote-1",
        explicit_task_ref=closed.task_id,
        referenced_artifact_refs=["artifact-a", "artifact-b"],
    )
    assert store.count_pending_ingresses(conversation_id="conv-store") == 1
    assert store.get_ingress(ingress.ingress_id) is not None
    assert store.list_ingresses(conversation_id="conv-store")[0].ingress_id == ingress.ingress_id

    store.update_ingress(
        ingress.ingress_id,
        status="pending_disambiguation",
        resolution="needs_user_choice",
        confidence=0.45,
        margin=0.05,
        rationale={"candidates": [closed.task_id, open_ctx.task_id]},
    )
    pending = store.get_ingress(ingress.ingress_id)
    assert pending is not None and pending.status == "pending_disambiguation"
    assert store.count_pending_ingresses(conversation_id="conv-store") == 1
    assert store.list_ingresses(status="pending_disambiguation")[0].ingress_id == ingress.ingress_id

    store.update_ingress(
        ingress.ingress_id,
        status="bound",
        resolution="matched_active_task",
        chosen_task_id=open_ctx.task_id,
        parent_task_id=closed.task_id,
        confidence=0.92,
        margin=0.5,
        rationale={"winner": open_ctx.task_id},
    )
    bound = store.get_ingress(ingress.ingress_id)
    assert bound is not None
    assert bound.status == "bound"
    assert bound.chosen_task_id == open_ctx.task_id
    assert bound.parent_task_id == closed.task_id
    assert bound.rationale == {"winner": open_ctx.task_id}
    assert store.count_pending_ingresses(conversation_id="conv-store") == 0
    assert store.list_ingresses(task_id=open_ctx.task_id)[0].ingress_id == ingress.ingress_id


def test_ingress_router_handles_explicit_reply_focus_and_branch_bindings(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    router = IngressRouter(store)
    primary = _start_task(controller, conversation_id="conv-router", goal="整理覆盖率报告")
    secondary = _start_task(
        controller, conversation_id="conv-router", goal="查询北京天气", kind="respond"
    )
    conversation = store.get_conversation("conv-router")
    open_tasks = store.list_open_tasks_for_conversation(conversation_id="conv-router", limit=10)

    explicit = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="随便什么都行",
        explicit_task_ref=primary.task_id,
    )
    assert explicit.resolution == "append_note"
    assert explicit.chosen_task_id == primary.task_id

    reply = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="收到",
        reply_to_task_id=secondary.task_id,
    )
    assert reply.resolution == "append_note"
    assert reply.chosen_task_id == secondary.task_id

    approval = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="批准后直接执行草稿",
        pending_approval_task_id=primary.task_id,
    )
    assert approval.resolution == "append_note"
    assert approval.chosen_task_id == primary.task_id

    focus = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="补充说明一下",
    )
    assert focus.resolution == "append_note"
    assert focus.chosen_task_id == conversation.focus_task_id
    assert focus.reason_codes == ["focus_followup_marker"]

    branch = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="顺便再问一下报销预算",
    )
    assert branch.resolution == "fork_child"
    assert branch.parent_task_id == conversation.focus_task_id


def test_ingress_router_returns_new_root_when_no_open_task_or_match_is_weak(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    router = IngressRouter(store)

    no_open = router.bind(conversation=None, open_tasks=[], normalized_text="新问题")
    assert no_open.resolution == "start_new_root"
    assert no_open.reason_codes == ["no_open_tasks"]

    _start_task(controller, conversation_id="conv-router-2", goal="查询北京天气")
    conversation = store.get_conversation("conv-router-2")
    open_tasks = store.list_open_tasks_for_conversation(conversation_id="conv-router-2", limit=10)
    weak = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text="写一份完全无关的周报",
    )
    assert weak.resolution == "start_new_root"
    assert weak.reason_codes in (["weak_candidate_match"], ["no_candidate_match"])


def test_ingress_router_binds_artifact_receipt_and_workspace_signals(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    router = IngressRouter(store)

    docs = controller.start_task(
        conversation_id="conv-structural-router",
        goal="整理产品文档",
        source_channel="chat",
        kind="respond",
        workspace_root=str(tmp_path / "workspace-docs"),
    )
    report = controller.start_task(
        conversation_id="conv-structural-router",
        goal="生成测试报告",
        source_channel="chat",
        kind="respond",
        workspace_root=str(tmp_path / "workspace-report"),
    )
    artifact = store.create_artifact(
        task_id=report.task_id,
        step_id=report.step_id,
        kind="report",
        uri="memory://report-artifact",
        content_hash="hash-report",
        producer="test",
        retention_class="task",
        trust_tier="observed",
        metadata={},
    )
    receipt = store.create_receipt(
        task_id=report.task_id,
        step_id=report.step_id,
        step_attempt_id=report.step_attempt_id,
        action_type="write_local",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[artifact.artifact_id],
        result_summary="wrote report",
    )
    conversation = store.get_conversation("conv-structural-router")
    open_tasks = store.list_open_tasks_for_conversation(
        conversation_id="conv-structural-router", limit=10
    )

    artifact_decision = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text=f"请继续处理 {artifact.artifact_id}",
    )
    assert artifact_decision.resolution == "append_note"
    assert artifact_decision.chosen_task_id == report.task_id
    assert artifact_decision.reason_codes == ["artifact_ref_match"]

    receipt_decision = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text=f"把 {receipt.receipt_id} 里的产物发出来",
    )
    assert receipt_decision.resolution == "append_note"
    assert receipt_decision.chosen_task_id == report.task_id
    assert receipt_decision.reason_codes == ["receipt_ref_match"]

    workspace_decision = router.bind(
        conversation=conversation,
        open_tasks=open_tasks,
        normalized_text=f"修改 {tmp_path / 'workspace-docs' / 'README.md'} 里的文案",
    )
    assert workspace_decision.resolution == "append_note"
    assert workspace_decision.chosen_task_id == docs.task_id
    assert workspace_decision.reason_codes == ["workspace_path_match"]


def test_task_controller_helper_predicates_cover_followup_and_terminal_candidates(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel.db")
    controller = TaskController(store)
    respond_ctx = _start_task(controller, conversation_id="conv-helper", goal="整理 GPT-5 升级计划")
    controller.append_note(
        task_id=respond_ctx.task_id,
        source_channel="chat",
        raw_text="<session_time>ignore</session_time>\n<feishu_msg_id>x</feishu_msg_id>\n保留 GPT-5.4 时间线",
        prompt="ignored",
    )

    other_ctx = _start_task(
        controller, conversation_id="conv-helper", goal="做数据迁移", kind="analyze"
    )
    controller.finalize_result(other_ctx, status="succeeded", result_preview="迁移完成")
    controller.finalize_result(respond_ctx, status="succeeded", result_preview="升级计划已完成")

    assert (
        controller._ingress_queue_priority(source_channel="chat", requested_by=None, metadata={})
        == 100
    )
    assert (
        controller._ingress_queue_priority(
            source_channel="scheduler", requested_by=None, metadata={}
        )
        == 10
    )
    assert (
        controller._ingress_queue_priority(
            source_channel="webhook", requested_by=None, metadata={"resume_kind": "approval"}
        )
        == 90
    )
    assert (
        controller._ingress_queue_priority(source_channel="system", requested_by=None, metadata={})
        == 0
    )

    assert controller._is_chat_only_message("") is True
    assert controller._is_chat_only_message("你好") is True
    assert controller._is_chat_only_message("......") is True
    assert controller._is_chat_only_message("请整理发布计划") is False
    assert controller._has_continue_marker("继续补充一下") is True
    assert controller._looks_like_task_followup("", task_id=respond_ctx.task_id) is False
    assert controller._looks_like_task_followup("继续补充一下", task_id=respond_ctx.task_id) is True
    assert (
        controller._looks_like_task_followup("刚才那个版本计划", task_id=respond_ctx.task_id)
        is True
    )
    assert (
        controller._sanitize_context_text(
            "<session_time>ignore</session_time>\n<feishu_chat_id>oc_1</feishu_chat_id>\n保留正文"
        )
        == "保留正文"
    )

    context_texts = controller._task_context_texts(respond_ctx.task_id)
    assert any("保留 GPT-5.4 时间线" in text for text in context_texts)

    terminal_tasks = controller._terminal_continuation_tasks("conv-helper")
    assert [task.task_id for task in terminal_tasks] == [respond_ctx.task_id]

    candidate_texts = controller._continuation_candidate_texts(respond_ctx.task_id)
    assert any("升级计划已完成" in text for text in candidate_texts)
    assert controller._continuation_candidate_texts("missing-task") == []
    assert controller._continuation_anchor("missing-task", selection_reason="none") == {}

    assert (
        controller._texts_overlap("北京天气", "北京天气更新", query_tokens={"北京", "天气"}) is True
    )
    assert controller._texts_overlap("周报", "", query_tokens={"周报"}) is False
