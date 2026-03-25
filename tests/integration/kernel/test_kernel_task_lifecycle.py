from __future__ import annotations

import concurrent.futures
import json
from types import SimpleNamespace

import pytest

from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


def test_task_controller_enqueue_resume_and_state_transitions(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    ctx = controller.enqueue_task(
        conversation_id="oc_1",
        goal="整理今天的任务",
        source_channel="feishu",
        kind="respond",
        workspace_root="/tmp/workspace",
        ingress_metadata={"notify": {"feishu": True}},
        source_ref="feishu:oc_1:om_1",
        requested_by="user-1",
    )

    task = store.get_task(ctx.task_id)
    step = store.get_step(ctx.step_id)
    attempt = store.get_step_attempt(ctx.step_attempt_id)

    assert task is not None and task.status == "queued"
    assert step is not None and step.status == "ready"
    assert attempt is not None and attempt.status == "ready"
    assert attempt.context["execution_mode"] == "run"
    assert attempt.context["workspace_root"] == "/tmp/workspace"
    assert attempt.context["ingress_metadata"]["notify"] == {"feishu": True}

    attempt_ctx = controller.context_for_attempt(ctx.step_attempt_id)
    assert attempt_ctx.task_id == ctx.task_id
    assert attempt_ctx.workspace_root == "/tmp/workspace"
    assert attempt_ctx.ingress_metadata["notify"] == {"feishu": True}

    resumed_ctx = controller.enqueue_resume(ctx.step_attempt_id)
    resumed_attempt = store.get_step_attempt(ctx.step_attempt_id)
    resumed_step = store.get_step(ctx.step_id)
    resumed_task = store.get_task(ctx.task_id)

    assert resumed_ctx.step_attempt_id == ctx.step_attempt_id
    assert resumed_attempt is not None and resumed_attempt.context["execution_mode"] == "resume"
    assert resumed_attempt.status_reason is None
    assert resumed_step is not None and resumed_step.status == "ready"
    assert resumed_task is not None and resumed_task.status == "queued"

    controller.mark_suspended(resumed_ctx, waiting_kind="awaiting_approval")
    blocked_task = store.get_task(ctx.task_id)
    blocked_attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert blocked_task is not None and blocked_task.status == "blocked"
    assert blocked_attempt is not None and blocked_attempt.status == "awaiting_approval"

    controller.pause_task(ctx.task_id)
    paused_task = store.get_task(ctx.task_id)
    assert paused_task is not None and paused_task.status == "paused"

    controller.reprioritize_task(ctx.task_id, priority="high")
    reprioritized_task = store.get_task(ctx.task_id)
    assert reprioritized_task is not None and reprioritized_task.priority == "high"

    controller.cancel_task(ctx.task_id)
    cancelled_task = store.get_task(ctx.task_id)
    assert cancelled_task is not None and cancelled_task.status == "cancelled"

    followup = controller.start_task(
        conversation_id="oc_1",
        goal="输出总结",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        followup,
        status="succeeded",
        result_preview="总结已完成",
        result_text="今天的任务总结已经整理完成。",
    )
    completed_task = store.get_task(followup.task_id)
    assert completed_task is not None and completed_task.status == "completed"


def test_task_controller_ingress_decision_and_missing_errors(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    assert controller.source_from_session("webhook-1") == "webhook"
    assert controller.source_from_session("schedule-1") == "scheduler"
    assert controller.source_from_session("cli-1") == "cli"
    assert controller.source_from_session("oc_abc") == "feishu"
    assert controller.source_from_session("oc_1:user") == "feishu"
    assert controller.source_from_session("chat-room") == "chat"

    start = controller.decide_ingress(
        conversation_id="oc_2",
        source_channel="feishu",
        raw_text="请开始",
        prompt="请开始",
    )
    assert start.mode == "start"
    assert start.intent == "start_new_task"

    ctx = controller.start_task(
        conversation_id="oc_2",
        goal="运行中的任务",
        source_channel="feishu",
        kind="respond",
    )
    decision = controller.decide_ingress(
        conversation_id="oc_2",
        source_channel="feishu",
        raw_text="补充一点说明",
        prompt="补充一点说明",
        requested_by="user-2",
    )
    assert decision.mode == "append_note"
    assert decision.intent == "continue_task"
    assert decision.task_id == ctx.task_id
    assert decision.note_event_seq is not None and decision.note_event_seq > 0

    note_event = next(
        event
        for event in reversed(store.list_events(task_id=ctx.task_id, limit=50))
        if event["event_type"] == "task.note.appended"
    )
    assert note_event["payload"]["raw_text"] == "补充一点说明"
    assert note_event["payload"]["requested_by"] == "user-2"

    with pytest.raises(KeyError):
        controller.context_for_attempt("attempt-missing")
    with pytest.raises(KeyError):
        controller.pause_task("task-missing")
    with pytest.raises(KeyError):
        controller.cancel_task("task-missing")
    with pytest.raises(KeyError):
        controller.reprioritize_task("task-missing", priority="low")


def test_task_controller_ingress_routes_chat_only_new_topic_and_followup(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    active = controller.start_task(
        conversation_id="oc_9",
        goal="制作 GPT-5.4 / Grok 3 对比文档",
        source_channel="feishu",
        kind="respond",
    )
    controller.append_note(
        task_id=active.task_id,
        source_channel="feishu",
        raw_text="加上和 Claude 4.6 的对比",
        prompt="加上和 Claude 4.6 的对比",
    )

    chat_only = controller.decide_ingress(
        conversation_id="oc_9",
        source_channel="feishu",
        raw_text="你好",
        prompt="你好",
    )
    assert chat_only.mode == "start"
    assert chat_only.intent == "chat_only"
    assert chat_only.parent_task_id is None

    new_topic = controller.decide_ingress(
        conversation_id="oc_9",
        source_channel="feishu",
        raw_text="你是什么模型",
        prompt="你是什么模型",
    )
    assert new_topic.mode == "start"
    assert new_topic.intent == "start_new_task"
    assert new_topic.parent_task_id is None

    explicit_new = controller.decide_ingress(
        conversation_id="oc_9",
        source_channel="feishu",
        raw_text="新任务：整理桌面文件",
        prompt="新任务：整理桌面文件",
    )
    assert explicit_new.mode == "start"
    assert explicit_new.intent == "start_new_task"
    assert explicit_new.reason == "explicit_new_task_marker"

    followup = controller.decide_ingress(
        conversation_id="oc_9",
        source_channel="feishu",
        raw_text="加上和 Grok 的对比",
        prompt="加上和 Grok 的对比",
    )
    assert followup.mode == "append_note"
    assert followup.intent == "continue_task"
    assert followup.task_id == active.task_id


def test_task_controller_routes_terminal_followup_to_continuation_anchor(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    weather = controller.start_task(
        conversation_id="oc_weather",
        goal="查询北京天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        weather,
        status="succeeded",
        result_preview="北京今天天气不错：晴到多云，0～12℃。",
        result_text="北京今天天气不错：晴到多云，0～12℃，微风到西南风，无明显降水。",
    )

    decision = controller.decide_ingress(
        conversation_id="oc_weather",
        source_channel="feishu",
        raw_text="你说一下你刚才查的北京天气是怎么样的",
        prompt="你说一下你刚才查的北京天气是怎么样的",
    )

    assert decision.mode == "start"
    assert decision.intent == "continue_task"
    assert decision.reason == "matched_terminal_task"
    assert decision.parent_task_id is None
    assert decision.anchor_task_id == weather.task_id
    assert decision.anchor_kind == "completed_outcome"
    assert decision.anchor_reason == "terminal_followup_marker_topic_overlap"
    assert decision.continuation_anchor is not None
    assert decision.continuation_anchor["anchor_task_id"] == weather.task_id
    assert decision.continuation_anchor["anchor_goal"] == "查询北京天气"
    assert decision.continuation_anchor["anchor_user_request"] == "查询北京天气"
    assert decision.continuation_anchor["outcome_status"] == "completed"
    assert "北京今天天气不错" in decision.continuation_anchor["outcome_summary"]


def test_task_controller_prefers_focus_task_when_multiple_open_tasks_exist(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    first = controller.start_task(
        conversation_id="oc_focus",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
    )
    second = controller.start_task(
        conversation_id="oc_focus",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
    )

    store.set_conversation_focus("oc_focus", task_id=first.task_id, reason="manual_test_focus")
    decision = controller.decide_ingress(
        conversation_id="oc_focus",
        source_channel="feishu",
        raw_text="补充一点说明",
        prompt="补充一点说明",
    )

    assert decision.mode == "append_note"
    assert decision.task_id == first.task_id
    assert decision.reason_codes == ["focus_followup_marker"]
    assert second.task_id != decision.task_id
    ingress = store.get_ingress(decision.ingress_id or "")
    assert ingress is not None
    assert ingress.rationale["actual_binding"]["chosen_task_id"] == first.task_id
    assert "shadow_binding" not in ingress.rationale


def test_task_controller_resolve_text_command_can_switch_focus_task(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    first = controller.start_task(
        conversation_id="oc_switch",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
    )
    second = controller.start_task(
        conversation_id="oc_switch",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
    )

    command = controller.resolve_text_command("oc_switch", f"切到任务 {first.task_id}")
    assert command == ("focus_task", first.task_id, "explicit_task_switch")

    controller.focus_task("oc_switch", first.task_id)
    conversation = store.get_conversation("oc_switch")
    assert conversation is not None
    assert conversation.focus_task_id == first.task_id
    assert conversation.focus_reason == "explicit_task_switch"

    decision = controller.decide_ingress(
        conversation_id="oc_switch",
        source_channel="feishu",
        raw_text="补充一点说明",
        prompt="补充一点说明",
    )
    assert decision.mode == "append_note"
    assert decision.task_id == first.task_id
    assert decision.task_id != second.task_id


def test_task_controller_explicit_task_ref_binds_deterministically(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    primary = controller.start_task(
        conversation_id="oc_explicit",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
    )
    secondary = controller.start_task(
        conversation_id="oc_explicit",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
    )

    decision = controller.decide_ingress(
        conversation_id="oc_explicit",
        source_channel="feishu",
        raw_text="改一下刚才那个任务",
        prompt="改一下刚才那个任务",
        explicit_task_ref=primary.task_id,
    )

    assert decision.mode == "append_note"
    assert decision.resolution == "append_note"
    assert decision.task_id == primary.task_id
    assert decision.task_id != secondary.task_id
    assert decision.reason_codes == ["explicit_task_ref"]


def test_task_controller_focus_task_resolves_pending_disambiguation(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    primary = controller.start_task(
        conversation_id="oc_pending",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
    )
    controller.start_task(
        conversation_id="oc_pending",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
    )
    pending = store.create_ingress(
        conversation_id="oc_pending",
        source_channel="feishu",
        raw_text="这个改一下",
        normalized_text="这个改一下",
        actor="user-1",
        prompt_ref="这个改一下",
    )
    store.update_ingress(
        pending.ingress_id,
        status="pending_disambiguation",
        resolution="pending_disambiguation",
        rationale={"reason_codes": ["ambiguous_candidate_tie"]},
    )

    resolved = controller.focus_task("oc_pending", primary.task_id)

    assert resolved is not None
    assert resolved.mode == "append_note"
    assert resolved.task_id == primary.task_id
    assert resolved.reason == "pending_disambiguation_resolved"
    assert "user_disambiguated_focus_task" in resolved.reason_codes
    assert store.count_pending_ingresses(conversation_id="oc_pending") == 0

    updated = store.get_ingress(pending.ingress_id)
    assert updated is not None
    assert updated.status == "bound"
    assert updated.chosen_task_id == primary.task_id

    note_event = next(
        event
        for event in reversed(store.list_events(task_id=primary.task_id, limit=50))
        if event["event_type"] == "task.note.appended"
    )
    assert note_event["payload"]["raw_text"] == "这个改一下"


def test_task_controller_marks_branch_followup_as_child_start(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    active = controller.start_task(
        conversation_id="oc_branch",
        goal="整理竞品分析",
        source_channel="feishu",
        kind="respond",
    )
    decision = controller.decide_ingress(
        conversation_id="oc_branch",
        source_channel="feishu",
        raw_text="顺便查一下竞品价格",
        prompt="顺便查一下竞品价格",
    )

    assert decision.mode == "start"
    assert decision.resolution == "fork_child"
    assert decision.parent_task_id == active.task_id


def test_task_controller_does_not_anchor_ambiguous_marker_without_topic_overlap(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    weather = controller.start_task(
        conversation_id="oc_weather_2",
        goal="查询北京天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        weather,
        status="succeeded",
        result_preview="北京今天天气不错：晴到多云，0～12℃。",
        result_text="北京今天天气不错：晴到多云，0～12℃，微风到西南风，无明显降水。",
    )

    decision = controller.decide_ingress(
        conversation_id="oc_weather_2",
        source_channel="feishu",
        raw_text="刚才那个，顺便帮我整理桌面文件",
        prompt="刚才那个，顺便帮我整理桌面文件",
    )

    assert decision.mode == "start"
    assert decision.intent == "start_new_task"
    assert decision.reason == "no_active_task"
    assert decision.anchor_task_id is None
    assert decision.continuation_anchor is None


def test_kernel_dispatch_service_recovers_async_attempts_and_runs_loop(monkeypatch) -> None:
    failed_attempt_updates: list[tuple[str, dict[str, object]]] = []
    failed_step_updates: list[tuple[str, dict[str, object]]] = []
    task_status_updates: list[tuple[str, str, dict[str, object]]] = []
    processed_attempts: list[str] = []
    claims = [
        SimpleNamespace(step_attempt_id="attempt-ready"),
        None,
    ]

    async_attempt = SimpleNamespace(
        step_attempt_id="attempt-running",
        step_id="step-running",
        task_id="task-running",
        status="running",
        context={"ingress_metadata": {"dispatch_mode": "async"}},
    )
    sync_attempt = SimpleNamespace(
        step_attempt_id="attempt-sync",
        step_id="step-sync",
        task_id="task-sync",
        status="running",
        context={"ingress_metadata": {"dispatch_mode": "sync"}},
    )

    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.set_result(None)

    store = SimpleNamespace(
        list_step_attempts=lambda status, limit: (
            [async_attempt, sync_attempt] if status == "running" else []
        ),
        update_step_attempt=lambda step_attempt_id, **kwargs: failed_attempt_updates.append(
            (step_attempt_id, kwargs)
        ),
        update_step=lambda step_id, **kwargs: failed_step_updates.append((step_id, kwargs)),
        update_task_status=lambda task_id, status, payload=None: task_status_updates.append(
            (task_id, status, payload or {})
        ),
        get_task=lambda task_id: None,
        claim_next_ready_step_attempt=lambda: claims.pop(0),
    )
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: processed_attempts.append(step_attempt_id),
    )
    service = KernelDispatchService(runner, worker_count=1)
    service.executor = SimpleNamespace(
        submit=lambda fn, attempt_id: processed_attempts.append(attempt_id) or future,
        shutdown=lambda **kwargs: None,
    )
    service.wake_event = SimpleNamespace(
        wait=lambda _timeout: None, clear=lambda: None, set=lambda: None
    )

    original_claim = store.claim_next_ready_step_attempt

    def claim_next_ready_step_attempt():
        attempt = original_claim()
        if attempt is None:
            service.stop_event.set()
        return attempt

    store.claim_next_ready_step_attempt = claim_next_ready_step_attempt

    service.recover_interrupted_attempts()
    service._loop()

    # Both async and sync attempts are updated; find each by attempt_id
    updates_by_id = {aid: kwargs for aid, kwargs in failed_attempt_updates}

    # async attempt → requeued as ready
    async_update = updates_by_id["attempt-running"]
    assert async_update["status"] == "ready"
    assert async_update["waiting_reason"] == "worker_interrupted_requeued"
    assert async_update["context"]["recovered_after_interrupt"] is True
    assert async_update["context"]["reentry_required"] is True
    assert async_update["context"]["reentry_boundary"] == "policy_reentry"
    assert async_update["context"]["original_status_at_interrupt"] == "running"

    # sync attempt → cancelled as orphaned
    sync_update = updates_by_id["attempt-sync"]
    assert sync_update["status"] == "cancelled"
    assert sync_update["waiting_reason"] == "worker_interrupted_sync_orphaned"
    assert sync_update["context"]["recovery_action"] == "cancelled_orphaned_sync"

    step_updates_by_id = {sid: kwargs for sid, kwargs in failed_step_updates}
    assert step_updates_by_id["step-running"] == {"status": "ready", "finished_at": None}
    assert step_updates_by_id["step-sync"]["status"] == "cancelled"

    task_updates_by_id = {tid: (s, p) for tid, s, p in task_status_updates}
    assert task_updates_by_id["task-running"] == (
        "queued",
        {
            "result_preview": "worker_interrupted_requeued",
            "result_text": "worker_interrupted_requeued",
        },
    )
    assert task_updates_by_id["task-sync"] == (
        "cancelled",
        {
            "result_preview": "worker_interrupted_sync_orphaned",
            "result_text": "worker_interrupted_sync_orphaned",
        },
    )
    assert processed_attempts == ["attempt-ready"]
    assert service.futures == {}


def test_task_controller_resume_attempt_clears_worker_interrupt_recovery_flag(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_recovery",
        goal="resume interrupted attempt",
        source_channel="chat",
        kind="respond",
    )

    store.update_step_attempt(
        ctx.step_attempt_id,
        status="blocked",
        status_reason="worker_interrupted_recovery_required",
        context={
            "workspace_root": str(tmp_path),
            "phase": "observing",
            "recovery_required": True,
            "reentry_required": True,
            "reentry_reason": "worker_interrupted",
            "reentry_boundary": "observation_resolution",
            "runtime_snapshot": {
                "schema_version": 2,
                "kind": "runtime_snapshot",
                "payload": {"suspend_kind": "observing"},
            },
        },
    )

    resumed = controller.resume_attempt(ctx.step_attempt_id)
    refreshed = store.get_step_attempt(ctx.step_attempt_id)
    events = store.list_events(task_id=ctx.task_id)

    assert resumed.step_attempt_id == ctx.step_attempt_id
    assert refreshed is not None
    assert refreshed.context["recovery_required"] is False
    assert refreshed.context["reentry_required"] is False
    assert refreshed.context["execution_mode"] == "resume"
    assert refreshed.context["phase"] == "observing"
    assert any(event["event_type"] == "step_attempt.reentry_resumed" for event in events)


def test_task_controller_resume_attempt_prefers_resume_artifact_over_context_snapshot(
    tmp_path,
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_recovery_artifact",
        goal="resume interrupted attempt",
        source_channel="chat",
        kind="respond",
    )

    snapshot_path = tmp_path / "kernel" / "runtime-snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "kind": "runtime_snapshot",
                "payload": {"suspend_kind": "observing"},
            }
        ),
        encoding="utf-8",
    )
    snapshot_artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="runtime.snapshot",
        uri=str(snapshot_path),
        content_hash="snapshot",
        producer="test",
        retention_class="audit",
        trust_tier="observed",
    )
    store.update_step_attempt(
        ctx.step_attempt_id,
        status="blocked",
        status_reason="worker_interrupted_recovery_required",
        resume_from_ref=snapshot_artifact.artifact_id,
        context={
            "workspace_root": str(tmp_path),
            "phase": "awaiting_approval",
            "recovery_required": True,
            "reentry_required": True,
            "reentry_reason": "worker_interrupted",
            "reentry_boundary": "observation_resolution",
            "runtime_snapshot": {
                "schema_version": 2,
                "kind": "runtime_snapshot",
                "payload": {"suspend_kind": "awaiting_approval"},
            },
        },
    )

    resumed = controller.resume_attempt(ctx.step_attempt_id)
    refreshed = store.get_step_attempt(ctx.step_attempt_id)

    assert resumed.step_attempt_id == ctx.step_attempt_id
    assert refreshed is not None
    assert refreshed.context["phase"] == "observing"


def test_kernel_dispatch_service_reaps_failed_futures_and_wakes(monkeypatch) -> None:
    logged: list[str] = []

    store = SimpleNamespace(
        list_step_attempts=lambda status, limit: [],
        claim_next_ready_step_attempt=lambda: None,
        get_step_attempt=lambda step_attempt_id: None,
    )
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: None,
    )
    service = KernelDispatchService(runner, worker_count=2)

    failed_future: concurrent.futures.Future[None] = concurrent.futures.Future()
    failed_future.set_exception(RuntimeError("boom"))
    service.futures[failed_future] = "attempt-failed"

    wake_calls: list[str] = []
    service.wake_event = SimpleNamespace(set=lambda: wake_calls.append("wake"))
    monkeypatch.setattr(
        "hermit.kernel.execution.coordination.dispatch.log.exception",
        lambda event, **kwargs: logged.append(f"{event}:{kwargs['step_attempt_id']}"),
    )

    assert service._capacity_available() is True
    service.wake()
    service._reap_futures()

    assert wake_calls == ["wake"]
    assert logged == ["kernel_dispatch_attempt_failed:attempt-failed"]
    assert service.futures == {}


def test_kernel_dispatch_service_start_and_stop_cover_thread_lifecycle(monkeypatch) -> None:
    store = SimpleNamespace(
        list_step_attempts=lambda status, limit: [],
        claim_next_ready_step_attempt=lambda: None,
    )
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: None,
    )
    service = KernelDispatchService(runner, worker_count=1)

    recover_calls: list[str] = []
    thread_starts: list[str] = []
    joins: list[int] = []
    shutdowns: list[tuple[bool, bool]] = []
    wake_sets: list[str] = []

    class FakeThread:
        def __init__(self, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            thread_starts.append(self.name)

        def join(self, timeout=None):
            joins.append(timeout)

    monkeypatch.setattr(
        service, "recover_interrupted_attempts", lambda: recover_calls.append("recover")
    )
    monkeypatch.setattr(
        "hermit.kernel.execution.coordination.dispatch.threading.Thread", FakeThread
    )
    service.executor = SimpleNamespace(
        shutdown=lambda wait=False, cancel_futures=True: shutdowns.append((wait, cancel_futures))
    )
    service.wake_event = SimpleNamespace(set=lambda: wake_sets.append("wake"))

    service.start()
    service.stop()

    assert recover_calls == ["recover"]
    assert thread_starts == ["kernel-dispatch-loop", "lease-reaper"]
    assert joins == [5, 5]
    assert shutdowns == [(False, True)]
    assert wake_sets == ["wake"]
