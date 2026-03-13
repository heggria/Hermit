from __future__ import annotations

import concurrent.futures
from types import SimpleNamespace

import pytest

from hermit.kernel.controller import TaskController
from hermit.kernel.dispatch import KernelDispatchService
from hermit.kernel.store import KernelStore


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
    assert resumed_attempt.waiting_reason is None
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
    assert decision.task_id == ctx.task_id
    assert decision.note_event_seq is not None and decision.note_event_seq > 0

    event = store.list_events(task_id=ctx.task_id, limit=50)[-1]
    assert event["event_type"] == "task.note.appended"
    assert event["payload"]["raw_text"] == "补充一点说明"
    assert event["payload"]["requested_by"] == "user-2"

    with pytest.raises(KeyError):
        controller.context_for_attempt("attempt-missing")
    with pytest.raises(KeyError):
        controller.pause_task("task-missing")
    with pytest.raises(KeyError):
        controller.cancel_task("task-missing")
    with pytest.raises(KeyError):
        controller.reprioritize_task("task-missing", priority="low")


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
        context={"ingress_metadata": {"dispatch_mode": "async"}},
    )
    sync_attempt = SimpleNamespace(
        step_attempt_id="attempt-sync",
        step_id="step-sync",
        task_id="task-sync",
        context={"ingress_metadata": {"dispatch_mode": "sync"}},
    )

    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.set_result(None)

    store = SimpleNamespace(
        list_step_attempts=lambda status, limit: [async_attempt, sync_attempt],
        update_step_attempt=lambda step_attempt_id, **kwargs: failed_attempt_updates.append((step_attempt_id, kwargs)),
        update_step=lambda step_id, **kwargs: failed_step_updates.append((step_id, kwargs)),
        update_task_status=lambda task_id, status, payload=None: task_status_updates.append((task_id, status, payload or {})),
        claim_next_ready_step_attempt=lambda: claims.pop(0),
    )
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: processed_attempts.append(step_attempt_id),
    )
    service = KernelDispatchService(runner, worker_count=1)
    service._executor = SimpleNamespace(
        submit=lambda fn, attempt_id: processed_attempts.append(attempt_id) or future,
        shutdown=lambda **kwargs: None,
    )
    service._wake = SimpleNamespace(wait=lambda _timeout: None, clear=lambda: None, set=lambda: None)

    original_claim = store.claim_next_ready_step_attempt

    def claim_next_ready_step_attempt():
        attempt = original_claim()
        if attempt is None:
            service._stop.set()
        return attempt

    store.claim_next_ready_step_attempt = claim_next_ready_step_attempt

    service._recover_interrupted_attempts()
    service._loop()

    assert failed_attempt_updates[0][0] == "attempt-running"
    assert failed_attempt_updates[0][1]["status"] == "failed"
    assert failed_step_updates == [("step-running", {"status": "failed", "finished_at": failed_step_updates[0][1]["finished_at"]})]
    assert task_status_updates == [
        (
            "task-running",
            "failed",
            {"result_preview": "worker_interrupted", "result_text": "worker_interrupted"},
        )
    ]
    assert processed_attempts == ["attempt-ready"]
    assert service._futures == {}


def test_kernel_dispatch_service_reaps_failed_futures_and_wakes(monkeypatch) -> None:
    logged: list[str] = []

    store = SimpleNamespace(
        list_step_attempts=lambda status, limit: [],
        claim_next_ready_step_attempt=lambda: None,
    )
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: None,
    )
    service = KernelDispatchService(runner, worker_count=2)

    failed_future: concurrent.futures.Future[None] = concurrent.futures.Future()
    failed_future.set_exception(RuntimeError("boom"))
    service._futures[failed_future] = "attempt-failed"

    wake_calls: list[str] = []
    service._wake = SimpleNamespace(set=lambda: wake_calls.append("wake"))
    monkeypatch.setattr("hermit.kernel.dispatch.log.exception", lambda event, **kwargs: logged.append(f"{event}:{kwargs['step_attempt_id']}"))

    assert service._capacity_available() is True
    service.wake()
    service._reap_futures()

    assert wake_calls == ["wake"]
    assert logged == ["kernel_dispatch_attempt_failed:attempt-failed"]
    assert service._futures == {}


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

    monkeypatch.setattr(service, "_recover_interrupted_attempts", lambda: recover_calls.append("recover"))
    monkeypatch.setattr("hermit.kernel.dispatch.threading.Thread", FakeThread)
    service._executor = SimpleNamespace(shutdown=lambda wait=False, cancel_futures=True: shutdowns.append((wait, cancel_futures)))
    service._wake = SimpleNamespace(set=lambda: wake_sets.append("wake"))

    service.start()
    service.stop()

    assert recover_calls == ["recover"]
    assert thread_starts == ["kernel-dispatch-loop"]
    assert joins == [5]
    assert shutdowns == [(False, True)]
    assert wake_sets == ["wake"]
