# ruff: noqa: F403,F405
from tests.fixtures.task_kernel_support import *


def test_enqueue_task_creates_ready_queue_records_and_claims_fifo(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    ctx1 = controller.enqueue_task(
        conversation_id="oc_1",
        goal="first",
        source_channel="scheduler",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "first prompt"},
        source_ref="schedule:job-1",
    )
    ctx2 = controller.enqueue_task(
        conversation_id="oc_2",
        goal="second",
        source_channel="scheduler",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "second prompt"},
        source_ref="schedule:job-2",
    )
    # Explicitly set started_at to guarantee FIFO ordering without relying on wall-clock time.
    # claim_next_ready_step_attempt orders by started_at ASC, so ctx1 must have an earlier value.
    with store._get_conn():
        store._get_conn().execute(
            "UPDATE step_attempts SET started_at = 1000.0 WHERE step_attempt_id = ?",
            (ctx1.step_attempt_id,),
        )
        store._get_conn().execute(
            "UPDATE step_attempts SET started_at = 2000.0 WHERE step_attempt_id = ?",
            (ctx2.step_attempt_id,),
        )

    task1 = store.get_task(ctx1.task_id)
    step1 = store.get_step(ctx1.step_id)
    attempt1 = store.get_step_attempt(ctx1.step_attempt_id)
    assert task1 is not None and task1.status == "queued"
    assert step1 is not None and step1.status == "ready"
    assert attempt1 is not None and attempt1.status == "ready"
    assert attempt1.context["ingress_metadata"]["entry_prompt"] == "first prompt"

    claimed1 = store.claim_next_ready_step_attempt()
    claimed2 = store.claim_next_ready_step_attempt()

    assert claimed1 is not None and claimed1.step_attempt_id == ctx1.step_attempt_id
    assert claimed2 is not None and claimed2.step_attempt_id == ctx2.step_attempt_id
    assert store.get_task(ctx1.task_id).status == "running"  # type: ignore[union-attr]
    assert store.get_step(ctx1.step_id).status == "running"  # type: ignore[union-attr]
    assert store.get_step_attempt(ctx1.step_attempt_id).status == "running"  # type: ignore[union-attr]


def test_enqueue_resume_requeues_blocked_attempt_with_resume_mode(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="oc_resume",
        goal="resume me",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "resume prompt"},
    )

    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    resumed = controller.enqueue_resume(ctx.step_attempt_id)
    attempt = store.get_step_attempt(ctx.step_attempt_id)

    assert resumed.step_attempt_id == ctx.step_attempt_id
    assert resumed.ingress_metadata["dispatch_mode"] == "async"
    assert store.get_task(ctx.task_id).status == "queued"  # type: ignore[union-attr]
    assert store.get_step(ctx.step_id).status == "ready"  # type: ignore[union-attr]
    assert attempt is not None and attempt.status == "ready"
    assert attempt.status_reason is None
    assert attempt.context["execution_mode"] == "resume"
    assert attempt.context["ingress_metadata"]["entry_prompt"] == "resume prompt"


def test_append_note_marks_open_attempt_input_dirty(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_dirty",
        goal="整理周报",
        source_channel="chat",
        kind="respond",
    )

    note_seq = controller.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="补充一句结论",
        prompt="补充一句结论",
        ingress_id="ingress_test_1",
    )

    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert note_seq > 0
    assert attempt is not None
    assert attempt.context["input_dirty"] is True
    assert attempt.context["latest_bound_ingress_id"] == "ingress_test_1"
    assert attempt.context["latest_note_event_seq"] == note_seq
    events = store.list_events(task_id=ctx.task_id, limit=20)
    assert any(event["event_type"] == "step_attempt.input_dirty" for event in events)


def test_enqueue_resume_supersedes_dirty_approval_attempt(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="oc_resume_dirty",
        goal="发邮件",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "发邮件"},
    )

    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="不要发了，改成草稿",
        prompt="不要发了，改成草稿",
        ingress_id="ingress_dirty_1",
    )
    resumed = controller.enqueue_resume(ctx.step_attempt_id)

    original = store.get_step_attempt(ctx.step_attempt_id)
    successor = store.get_step_attempt(resumed.step_attempt_id)
    assert resumed.step_attempt_id != ctx.step_attempt_id
    assert original is not None and original.status == "superseded"
    assert original.superseded_by_step_attempt_id == resumed.step_attempt_id
    assert successor is not None and successor.status == "ready"
    assert successor.context["execution_mode"] == "run"
    assert successor.context["reentered_via"] == "input_dirty_approval"
    assert successor.context["supersedes_step_attempt_id"] == ctx.step_attempt_id
    events = store.list_events(task_id=ctx.task_id, limit=50)
    assert any(event["event_type"] == "step_attempt.superseded" for event in events)


def test_kernel_dispatch_recovery_requeues_async_running_attempts(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    async_ctx = controller.enqueue_task(
        conversation_id="oc_async",
        goal="async attempt",
        source_channel="scheduler",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "queued"},
    )
    sync_ctx = controller.start_task(
        conversation_id="oc_sync",
        goal="sync attempt",
        source_channel="chat",
        kind="respond",
    )
    store.claim_next_ready_step_attempt()

    service = KernelDispatchService(SimpleNamespace(task_controller=controller), worker_count=1)
    service.recover_interrupted_attempts()

    async_attempt = store.get_step_attempt(async_ctx.step_attempt_id)
    sync_attempt = store.get_step_attempt(sync_ctx.step_attempt_id)
    async_task = store.get_task(async_ctx.task_id)

    assert async_attempt is not None and async_attempt.status == "ready"
    assert async_attempt.status_reason == "worker_interrupted_requeued"
    assert async_attempt.context["recovered_after_interrupt"] is True
    assert async_attempt.context["reentry_required"] is True
    assert async_attempt.context["reentry_boundary"] == "policy_reentry"
    assert async_task is not None and async_task.status == "queued"
    # Sync (non-async) attempts that are found in-flight during recovery are
    # failed with 'worker_interrupted_sync_orphaned', since the dispatch service
    # does not own their lifecycle.
    assert sync_attempt is not None and sync_attempt.status == "failed"
    assert sync_attempt.status_reason == "worker_interrupted_sync_orphaned"


def test_runner_process_claimed_attempt_run_emits_notify_and_records_scheduler_history(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="schedule-job",
        goal="run scheduled task",
        source_channel="scheduler",
        kind="respond",
        ingress_metadata={
            "dispatch_mode": "async",
            "entry_prompt": "rendered prompt",
            "notify": {"feishu_chat_id": "oc_schedule"},
            "source_ref": "scheduler",
            "title": "Daily summary",
            "schedule_job_id": "job_1",
            "schedule_job_name": "daily-summary",
        },
    )
    store.claim_next_ready_step_attempt()

    agent = _AsyncAgent()
    agent.run_result = AgentResult(
        text="summary complete",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": [{"type": "text", "text": "summary complete"}]}],
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
    )
    sessions = _RunnerSessionManager()
    plugin_manager = _RunnerPluginManager(tmp_path)
    fired: list[dict[str, Any]] = []
    plugin_manager.hooks.register("dispatch_result", lambda **kwargs: fired.append(kwargs))
    runner = AgentRunner(agent, sessions, plugin_manager, task_controller=controller)

    result = runner.process_claimed_attempt(ctx.step_attempt_id)

    assert result.text == "summary complete"
    assert agent.run_calls and agent.run_calls[0]["prompt"] == "rendered prompt"
    assert plugin_manager.post_run == ["summary complete"]
    assert fired and fired[0]["notify"] == {"feishu_chat_id": "oc_schedule"}
    assert fired[0]["title"] == "Daily summary"
    assert store.get_task(ctx.task_id).status == "completed"  # type: ignore[union-attr]
    history = store.list_schedule_history(job_id="job_1", limit=10)
    assert len(history) == 1
    assert history[0].result_text == "summary complete"
    assert (tmp_path / "schedules" / "history.json").exists()
    assert list((tmp_path / "schedules" / "logs").glob("*_job_1.log"))


def test_runner_process_claimed_attempt_resume_marks_suspended_without_post_run(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="oc_1",
        goal="resume blocked task",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "resume me"},
    )
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    controller.enqueue_resume(ctx.step_attempt_id)
    store.claim_next_ready_step_attempt()

    agent = _AsyncAgent()
    agent.resume_result = AgentResult(
        text="still waiting",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
        waiting_kind="observing",
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
    )
    sessions = _RunnerSessionManager()
    plugin_manager = _RunnerPluginManager(tmp_path)
    runner = AgentRunner(agent, sessions, plugin_manager, task_controller=controller)

    result = runner.process_claimed_attempt(ctx.step_attempt_id)

    assert result.blocked is True
    assert agent.resume_calls and agent.resume_calls[0]["step_attempt_id"] == ctx.step_attempt_id
    assert store.get_task(ctx.task_id).status == "blocked"  # type: ignore[union-attr]
    assert store.get_step_attempt(ctx.step_attempt_id).status == "observing"  # type: ignore[union-attr]
    assert plugin_manager.post_run == []


def test_runner_process_claimed_attempt_run_exception_becomes_failed_result(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="chat-1",
        goal="explode",
        source_channel="chat",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async", "entry_prompt": "explode"},
    )
    store.claim_next_ready_step_attempt()

    agent = _AsyncAgent()
    agent.raise_on_run = RuntimeError("boom")
    sessions = _RunnerSessionManager()
    plugin_manager = _RunnerPluginManager(tmp_path)
    fired: list[dict[str, Any]] = []
    plugin_manager.hooks.register("dispatch_result", lambda **kwargs: fired.append(kwargs))
    runner = AgentRunner(agent, sessions, plugin_manager, task_controller=controller)

    result = runner.process_claimed_attempt(ctx.step_attempt_id)

    assert result.execution_status == "failed"
    assert result.text == "[API Error] boom"
    assert store.get_task(ctx.task_id).status == "failed"  # type: ignore[union-attr]
    assert plugin_manager.post_run == ["[API Error] boom"]
    assert fired == []


def test_kernel_dispatch_loop_claims_attempts_and_reaps_futures(monkeypatch) -> None:
    claimed_attempts: list[str] = []
    queued = [SimpleNamespace(step_attempt_id="attempt-1"), None]
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                claim_next_ready_step_attempt=lambda: queued.pop(0) if queued else None,
                list_step_attempts=lambda status="", limit=500: [],
            )
        ),
        process_claimed_attempt=lambda attempt_id: claimed_attempts.append(attempt_id),
    )
    service = KernelDispatchService(runner, worker_count=1)
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()
    future.set_result(None)
    service.executor = SimpleNamespace(
        submit=lambda fn, attempt_id: (fn(attempt_id), future)[1],
        shutdown=lambda **_kwargs: None,
    )

    def fake_wait(_timeout: float) -> bool:
        service.stop_event.set()
        return False

    monkeypatch.setattr(service.wake_event, "wait", fake_wait)
    service._loop()

    assert claimed_attempts == ["attempt-1"]
    assert service.futures == {}


def test_kernel_dispatch_reap_futures_handles_worker_exception() -> None:
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(get_step_attempt=lambda step_attempt_id: None)
        )
    )
    service = KernelDispatchService(runner, worker_count=1)
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()
    future.set_exception(RuntimeError("boom"))
    service.futures[future] = "attempt-err"

    service._reap_futures()

    assert service.futures == {}


def test_force_fail_attempt_marks_step_and_task_failed(tmp_path: Path) -> None:
    """When a worker crashes, _force_fail_attempt should mark the attempt, step,
    and task as failed, and propagate DAG failure to dependents."""
    store = KernelStore(tmp_path / "kernel" / "state.db")
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1", title="test", goal="test", source_channel="test"
    )
    step = store.create_step(task_id=task.task_id, kind="execute", status="ready", title="Step A")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        queue_priority=0,
        context={"ingress_metadata": {"dispatch_mode": "async"}},
    )
    store.update_task_status(task.task_id, "running")

    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    service = KernelDispatchService(runner, worker_count=1)

    service.force_fail_attempt(attempt.step_attempt_id)

    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt.status == "failed"
    assert updated_attempt.status_reason == "worker_exception"

    updated_step = store.get_step(step.step_id)
    assert updated_step.status == "failed"

    updated_task = store.get_task(task.task_id)
    assert updated_task.status == "failed"


def test_force_fail_attempt_skips_already_terminal(tmp_path: Path) -> None:
    """_force_fail_attempt should not overwrite an already-terminal attempt."""
    store = KernelStore(tmp_path / "kernel" / "state.db")
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1", title="test", goal="test", source_channel="test"
    )
    step = store.create_step(task_id=task.task_id, kind="execute", status="ready", title="Step A")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="succeeded",
        queue_priority=0,
        context={},
    )

    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    service = KernelDispatchService(runner, worker_count=1)

    service.force_fail_attempt(attempt.step_attempt_id)

    # Should remain succeeded, not overwritten
    updated_attempt = store.get_step_attempt(attempt.step_attempt_id)
    assert updated_attempt.status == "succeeded"


def test_kernel_dispatch_service_start_stop_and_wake(monkeypatch) -> None:
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=SimpleNamespace(list_step_attempts=lambda **_: []))
    )
    service = KernelDispatchService(runner, worker_count=2)
    started: list[str] = []
    stopped: list[str] = []

    class FakeThread:
        def start(self) -> None:
            started.append("thread_start")

        def join(self, timeout: float | None = None) -> None:
            stopped.append(f"join:{timeout}")

    monkeypatch.setattr(service, "recover_interrupted_attempts", lambda: started.append("recover"))
    monkeypatch.setattr(
        "hermit.kernel.execution.coordination.dispatch.threading.Thread",
        lambda **_kwargs: FakeThread(),
    )
    service.executor = SimpleNamespace(shutdown=lambda **_kwargs: stopped.append("shutdown"))

    service.start()
    service.wake()
    service.stop()

    assert started == ["recover", "thread_start"]
    assert service.wake_event.is_set() is True
    assert stopped == ["join:5", "shutdown"]


def test_controller_helpers_cover_source_resolution_and_task_lifecycle(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    assert controller.source_from_session("webhook-1") == "webhook"
    assert controller.source_from_session("schedule-1") == "scheduler"
    assert controller.source_from_session("cli-1") == "cli"
    assert controller.source_from_session("oc_123") == "feishu"
    assert controller.source_from_session("group:user") == "feishu"
    assert controller.source_from_session("chat-1") == "chat"

    ctx = controller.start_task(
        conversation_id="oc_1",
        goal="hello",
        source_channel="feishu",
        kind="respond",
        workspace_root="/tmp/workspace",
    )
    assert controller.latest_task("oc_1").task_id == ctx.task_id  # type: ignore[union-attr]
    assert controller.active_task_for_conversation("oc_1").task_id == ctx.task_id  # type: ignore[union-attr]
    controller.pause_task(ctx.task_id)
    assert controller.active_task_for_conversation("oc_1") is None
    controller.reprioritize_task(ctx.task_id, priority="high")
    controller.cancel_task(ctx.task_id)
    assert store.get_task(ctx.task_id).status == "cancelled"  # type: ignore[union-attr]
    assert controller.resume_attempt(ctx.step_attempt_id).step_attempt_id == ctx.step_attempt_id


def test_controller_context_lookup_and_decide_ingress_and_append_note(
    tmp_path: Path, monkeypatch
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="oc_2",
        goal="queued task",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async"},
    )

    restored = controller.context_for_attempt(ctx.step_attempt_id)
    assert restored.task_id == ctx.task_id
    assert restored.ingress_metadata["dispatch_mode"] == "async"

    decision = controller.decide_ingress(
        conversation_id="oc_2",
        source_channel="feishu",
        raw_text="extra note",
        prompt="prompt",
    )
    assert decision.mode == "append_note"
    assert decision.task_id == ctx.task_id
    assert decision.note_event_seq is not None

    start = controller.decide_ingress(
        conversation_id="oc_3",
        source_channel="feishu",
        raw_text="new",
        prompt="prompt",
    )
    assert start.mode == "start"

    original_list_events = store.list_events

    def fake_list_events(
        *, task_id: str | None = None, after_event_seq: int | None = None, limit: int = 100
    ):
        if limit == 1:
            return []
        return original_list_events(task_id=task_id, after_event_seq=after_event_seq, limit=limit)

    monkeypatch.setattr(store, "list_events", fake_list_events)
    fallback_seq = controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="follow up",
        prompt="follow up",
    )
    assert fallback_seq > 0


def test_controller_raises_for_unknown_task_entities(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    with pytest.raises(KeyError):
        controller.context_for_attempt("missing-attempt")
    with pytest.raises(KeyError):
        controller.enqueue_resume("missing-attempt")
    with pytest.raises(KeyError):
        controller.pause_task("missing-task")
    with pytest.raises(KeyError):
        controller.cancel_task("missing-task")
    with pytest.raises(KeyError):
        controller.reprioritize_task("missing-task", priority="high")
    with pytest.raises(KeyError):
        controller.append_note(
            task_id="missing-task",
            source_channel="chat",
            raw_text="x",
            prompt="x",
        )


def test_store_task_attempt_queries_and_iter_events(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.enqueue_task(
        conversation_id="chat-iter",
        goal="iterate",
        source_channel="chat",
        kind="respond",
        ingress_metadata={"dispatch_mode": "async"},
    )

    assert len(store.list_step_attempts(task_id=ctx.task_id)) == 1
    assert len(store.list_step_attempts(step_id=ctx.step_id)) == 1
    assert len(store.list_step_attempts(status="ready")) == 1
    assert len(store.list_ready_step_attempts(limit=10)) == 1
    assert store.claim_next_ready_step_attempt() is not None
    assert store.claim_next_ready_step_attempt() is None
    store.update_step_attempt("missing-attempt", status="failed")
    events = list(store.iter_events(task_id=ctx.task_id, batch_size=1))
    assert events


def test_store_claim_next_ready_step_attempt_prefers_higher_queue_priority(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    low = controller.enqueue_task(
        conversation_id="chat-priority",
        goal="background",
        source_channel="scheduler",
        kind="respond",
    )
    high = controller.enqueue_task(
        conversation_id="chat-priority",
        goal="interactive",
        source_channel="chat",
        kind="respond",
    )
    store.update_step_attempt(low.step_attempt_id, queue_priority=10)
    store.update_step_attempt(high.step_attempt_id, queue_priority=100)

    claimed = store.claim_next_ready_step_attempt()

    assert claimed is not None
    assert claimed.step_attempt_id == high.step_attempt_id


def test_task_controller_prefers_latest_pending_approval_for_natural_language(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    first = controller.start_task(
        conversation_id="chat-approval",
        goal="first",
        source_channel="chat",
        kind="respond",
    )
    store.create_approval(
        task_id=first.task_id,
        step_id=first.step_id,
        step_attempt_id=first.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )

    second = controller.start_task(
        conversation_id="chat-approval",
        goal="second",
        source_channel="chat",
        kind="respond",
    )
    approval = store.create_approval(
        task_id=second.task_id,
        step_id=second.step_id,
        step_attempt_id=second.step_attempt_id,
        approval_type="execute_command",
        requested_action={"tool_name": "bash"},
        request_packet_ref=None,
    )

    assert store.get_task(second.task_id).parent_task_id == first.task_id
    assert controller.resolve_text_command("chat-approval", "开始执行") == (
        "approve_once",
        approval.approval_id,
        "",
    )
    assert controller.resolve_text_command("chat-approval", "通过") == (
        "approve_once",
        approval.approval_id,
        "",
    )
    assert controller.resolve_text_command("chat-approval", "批准") == (
        "approve_once",
        approval.approval_id,
        "",
    )
    assert controller.resolve_text_command("chat-approval", f"批准一次 {approval.approval_id}") == (
        "approve_once",
        approval.approval_id,
        "",
    )
    assert controller.resolve_text_command(
        "chat-approval", f"批准可变工作区 {approval.approval_id}"
    ) == ("approve_mutable_workspace", approval.approval_id, "")


def test_task_controller_resolves_natural_language_case_and_rollback(tmp_path: Path) -> None:
    _store, _artifacts, controller, executor, ctx = _kernel_runtime(tmp_path)
    result = executor.execute(
        ctx,
        "write_file",
        {"path": "nl-control.txt", "content": "hello\n"},
    )

    assert controller.resolve_text_command("chat-kernel", "看看这个任务") == (
        "case",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "回滚这次操作") == (
        "rollback",
        result.receipt_id,
        "",
    )


def test_task_controller_resolves_other_natural_language_commands(tmp_path: Path) -> None:
    store, _artifacts, controller, executor, ctx = _kernel_runtime(tmp_path)
    executor.execute(ctx, "write_file", {"path": "nl-more.txt", "content": "hello\n"})
    grant = store.create_capability_grant(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_ref="decision_nl",
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=[str((tmp_path / "workspace").resolve())],
        constraints={"target_paths": [str((tmp_path / "workspace").resolve())]},
        idempotency_key="nl-grant",
        expires_at=None,
    )
    job = ScheduledJob.create(
        name="Daily", prompt="run", schedule_type="interval", interval_seconds=60
    )
    store.create_schedule(job)
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            started_at=time.time() - 1,
            finished_at=time.time(),
            success=True,
            result_text="ok",
        )
    )

    assert controller.resolve_text_command("chat-kernel", "帮助") == ("show_help", "", "")
    assert controller.resolve_text_command("chat-kernel", "查看历史") == ("show_history", "", "")
    assert controller.resolve_text_command("chat-kernel", "任务列表") == ("task_list", "", "")
    assert controller.resolve_text_command("chat-kernel", "查看这个任务的事件") == (
        "task_events",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "查看这个任务的收据") == (
        "task_receipts",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "查看这个任务的证明") == (
        "task_proof",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "导出这个任务的证明") == (
        "task_proof_export",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "查看授权") == ("capability_list", "", "")
    assert controller.resolve_text_command("chat-kernel", f"撤销授权 {grant.grant_id}") == (
        "capability_revoke",
        grant.grant_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "定时任务列表") == (
        "schedule_list",
        "",
        "",
    )
    assert controller.resolve_text_command("chat-kernel", f"查看定时历史 {job.id}") == (
        "schedule_history",
        job.id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", f"启用定时任务 {job.id}") == (
        "schedule_enable",
        job.id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", f"禁用定时任务 {job.id}") == (
        "schedule_disable",
        job.id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", f"删除定时任务 {job.id}") == (
        "schedule_remove",
        job.id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "重建这个任务投影") == (
        "projection_rebuild",
        ctx.task_id,
        "",
    )
    assert controller.resolve_text_command("chat-kernel", "重建所有投影") == (
        "projection_rebuild_all",
        "",
        "",
    )
