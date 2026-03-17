# ruff: noqa: F403,F405
from tests.fixtures.task_kernel_support import *


def test_runner_marks_unknown_outcome_as_needs_attention(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="bash",
            description="Run shell command.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _payload: (_ for _ in ()).throw(RuntimeError("shell crash")),
            action_class="execute_command",
            resource_scope_hint=str(workspace),
            risk_hint="critical",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    runtime = AgentRuntime(
        provider=FakeProvider(
            responses=[
                ProviderResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "bash",
                            "input": {"command": "git status"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                )
            ]
        ),
        registry=registry,
        model="fake",
        tool_executor=ToolExecutor(
            registry=registry,
            store=store,
            artifact_store=artifacts,
            policy_engine=PolicyEngine(),
            approval_service=ApprovalService(store),
            receipt_service=ReceiptService(store),
            tool_output_limit=2000,
        ),
    )
    runtime.workspace_root = str(workspace)  # type: ignore[attr-defined]
    runner = AgentRunner(
        runtime,
        SessionManager(tmp_path / "sessions", store=store),
        PluginManager(),
        task_controller=TaskController(store),
    )

    result = runner.handle("chat-runner-unknown", "check git")

    task = store.get_last_task_for_conversation("chat-runner-unknown")
    assert task is not None
    attempt_id = next(
        event["entity_id"]
        for event in store.list_events(task_id=task.task_id, limit=50)
        if event["event_type"] == "step_attempt.started"
    )
    attempt = store.get_step_attempt(attempt_id)
    assert result.execution_status == "needs_attention"
    assert attempt is not None and attempt.status == "needs_attention"
    assert store.get_task(task.task_id).status == "needs_attention"


def test_kernel_store_rejects_pre_v3_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "kernel" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    try:
        KernelStore(db_path)
    except KernelSchemaError as exc:
        assert "unsupported pre-v3 schema" in str(exc)
    else:
        raise AssertionError("KernelStore should reject old schemas")


def test_event_log_uses_monotonic_event_seq(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    executor.execute(ctx, "write_file", {"path": "receipt.txt", "content": "hello\n"})

    events = store.list_events(task_id=ctx.task_id, limit=50)
    event_seq = [int(event["event_seq"]) for event in events]

    assert event_seq == sorted(event_seq)
    assert len(set(event_seq)) == len(event_seq)


def test_production_code_avoids_direct_registry_calls() -> None:
    hermit_root = Path(__file__).resolve().parents[3] / "src" / "hermit"
    offenders = [
        str(path.relative_to(hermit_root.parent))
        for path in hermit_root.rglob("*.py")
        if "registry.call(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_runner_deny_approval_persists_denial_message_in_session(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-deny",
        goal="need approval",
        source_channel="chat",
        kind="respond",
    )
    approval = store.create_approval(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )

    class FakeAgent:
        def __init__(self) -> None:
            self.resume_called = False

        def resume(self, **kwargs: Any) -> AgentResult:
            self.resume_called = True
            return AgentResult(text="should not happen", turns=1, tool_calls=0, messages=[])

    runner = AgentRunner(
        FakeAgent(),  # type: ignore[arg-type]
        SessionManager(tmp_path / "sessions", store=store),
        PluginManager(),
        task_controller=controller,
    )
    session = runner.session_manager.get_or_create("chat-deny")
    session.append_user("please continue")
    runner.session_manager.save(session)

    result = runner._resolve_approval(
        "chat-deny", action="deny", approval_id=approval.approval_id, reason="not now"
    )

    assert result.is_command is True
    assert "This approval was denied" in result.text
    assert store.get_approval(approval.approval_id).status == "denied"
    reloaded = runner.session_manager.get_or_create("chat-deny")
    assert reloaded.messages[-1]["role"] == "assistant"
    assert "start a new request" in reloaded.messages[-1]["content"][0]["text"]


def test_runner_approve_resumes_attempt_and_finalizes_task(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-approve",
        goal="resume work",
        source_channel="chat",
        kind="respond",
    )
    approval = store.create_approval(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )

    class FakeAgent:
        def resume(self, **kwargs: Any) -> AgentResult:
            return AgentResult(
                text="all done",
                turns=2,
                tool_calls=1,
                messages=[
                    {"role": "user", "content": "continue"},
                    {"role": "assistant", "content": [{"type": "text", "text": "all done"}]},
                ],
            )

    class FakePM:
        def __init__(self) -> None:
            self.post_run_calls: list[tuple[str, str]] = []

        def on_post_run(self, result: Any, **kwargs: Any) -> None:
            self.post_run_calls.append((kwargs["session_id"], result.text))

        def on_session_start(self, session_id: str) -> None:
            return None

        def on_pre_run(self, text: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
            return text, {}

        def on_session_end(self, session_id: str, messages: Any) -> None:
            return None

    pm = FakePM()
    runner = AgentRunner(
        FakeAgent(),  # type: ignore[arg-type]
        SessionManager(tmp_path / "sessions", store=store),
        pm,  # type: ignore[arg-type]
        task_controller=controller,
    )

    result = runner._resolve_approval(
        "chat-approve", action="approve", approval_id=approval.approval_id
    )

    assert result.is_command is False
    assert result.text == "all done"
    assert store.get_approval(approval.approval_id).status == "granted"
    assert store.get_task(ctx.task_id).status == "completed"
    assert store.get_step(ctx.step_id).status == "succeeded"
    assert pm.post_run_calls == [("chat-approve", "all done")]


def test_runner_dispatches_natural_language_case_and_rollback_without_slash(tmp_path: Path) -> None:
    store, _artifacts, controller, executor, ctx = _kernel_runtime(tmp_path)
    executor.execute(ctx, "write_file", {"path": "runner-nl.txt", "content": "after\n"})
    grant = store.create_capability_grant(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_ref="decision_runner",
        approval_ref=None,
        policy_ref=None,
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=[str((tmp_path / "workspace").resolve())],
        constraints={"target_paths": [str((tmp_path / "workspace").resolve())]},
        idempotency_key="runner-grant",
        expires_at=None,
    )
    job = ScheduledJob.create(
        name="RunnerJob", prompt="run", schedule_type="interval", interval_seconds=60
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

    class FakeAgent:
        def generate(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Natural-language control should not reach the agent")

    runner = AgentRunner(
        FakeAgent(),  # type: ignore[arg-type]
        SessionManager(tmp_path / "sessions", store=store),
        PluginManager(),
        task_controller=controller,
    )
    runner.agent.kernel_store = store  # type: ignore[attr-defined]

    case_result = runner.dispatch("chat-kernel", "看看这个任务")
    rollback_result = runner.dispatch("chat-kernel", "回滚这次操作")
    help_result = runner.dispatch("chat-kernel", "帮助")
    history_result = runner.dispatch("chat-kernel", "查看历史")
    list_result = runner.dispatch("chat-kernel", "任务列表")
    proof_result = runner.dispatch("chat-kernel", "查看这个任务的证明")
    grant_result = runner.dispatch("chat-kernel", "查看授权")
    schedule_result = runner.dispatch("chat-kernel", "定时任务列表")
    schedule_history_result = runner.dispatch("chat-kernel", f"查看定时历史 {job.id}")
    schedule_disable_result = runner.dispatch("chat-kernel", f"禁用定时任务 {job.id}")
    grant_revoke_result = runner.dispatch("chat-kernel", f"撤销授权 {grant.grant_id}")

    assert case_result.is_command is True
    assert json.loads(case_result.text)["task"]["task_id"] == ctx.task_id
    assert rollback_result.is_command is True
    assert json.loads(rollback_result.text)["status"] == "succeeded"
    assert help_result.is_command is True and "/task" in help_result.text
    assert history_result.is_command is True and "Current session" in history_result.text
    assert (
        list_result.is_command is True and json.loads(list_result.text)[0]["task_id"] == ctx.task_id
    )
    assert (
        proof_result.is_command is True
        and json.loads(proof_result.text)["task"]["task_id"] == ctx.task_id
    )
    assert grant_result.is_command is True
    assert any(item["grant_id"] == grant.grant_id for item in json.loads(grant_result.text))
    assert (
        schedule_result.is_command is True and json.loads(schedule_result.text)[0]["id"] == job.id
    )
    assert (
        schedule_history_result.is_command is True
        and json.loads(schedule_history_result.text)[0]["job_id"] == job.id
    )
    assert (
        schedule_disable_result.is_command is True
        and "Disabled task" in schedule_disable_result.text
    )
    assert (
        grant_revoke_result.is_command is True
        and "Revoked capability grant" in grant_revoke_result.text
    )


def test_rollback_service_restores_local_write_from_prestate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "rollback.txt"
    target.write_text("before\n", encoding="utf-8")

    store, artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    ctx.workspace_root = str(workspace)

    result = executor.execute(
        ctx,
        "write_file",
        {"path": "rollback.txt", "content": "after\n"},
    )

    receipt = store.get_receipt(result.receipt_id or "")
    assert receipt is not None
    assert receipt.rollback_supported is True
    assert target.read_text(encoding="utf-8") == "after\n"

    payload = RollbackService(store, artifacts).execute(receipt.receipt_id)

    assert payload["status"] == "succeeded"
    assert target.read_text(encoding="utf-8") == "before\n"
    assert store.get_receipt(receipt.receipt_id).rollback_status == "succeeded"


def test_executor_and_rollback_localize_core_copy(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "localized.txt"
    target.write_text("before\n", encoding="utf-8")

    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")

    store, artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    ctx.workspace_root = str(workspace)

    preview = executor._preview_text(  # type: ignore[attr-defined]
        executor.registry.get("write_file"),
        {"path": "localized.txt", "content": "after\n"},
    )
    auth_reason = executor._authorization_reason(  # type: ignore[attr-defined]
        policy=SimpleNamespace(reason=""), approval_mode="once"
    )
    success_summary = executor._successful_result_summary(  # type: ignore[attr-defined]
        tool_name="write_file", approval_mode="once"
    )

    result = executor.execute(
        ctx,
        "write_file",
        {"path": "localized.txt", "content": "after\n"},
    )
    receipt = store.get_receipt(result.receipt_id or "")
    payload = RollbackService(store, artifacts).execute(receipt.receipt_id)  # type: ignore[union-attr]

    assert "# 写入预览" in preview
    assert "路径：`localized.txt`" in preview
    assert auth_reason == "用户批准了这一次写入执行。"
    assert success_summary == "write_file 已在一次性批准后成功执行。"
    assert payload["result_summary"] == f"已恢复 {target} 的文件状态。"


def test_controller_and_executor_localize_core_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")

    _store, _artifacts, controller, executor, _ctx = _kernel_runtime(tmp_path)

    with pytest.raises(KeyError, match="未找到 step attempt：attempt-missing"):
        controller.context_for_attempt("attempt-missing")

    with pytest.raises(KeyError, match="未找到任务：task-missing"):
        controller.append_note(
            task_id="task-missing",
            source_channel="chat",
            raw_text="hi",
            prompt="hi",
        )

    with pytest.raises(KeyError, match="未找到 step attempt：attempt-missing"):
        executor.load_suspended_state("attempt-missing")

    with pytest.raises(RuntimeError, match="不支持的 runtime snapshot schema version"):
        executor._runtime_snapshot_payload(  # type: ignore[attr-defined]
            {
                "schema_version": 99,
                "kind": "runtime_snapshot",
                "expires_at": time.time() + 60,
                "payload": {},
            }
        )

    with pytest.raises(RuntimeError, match="未找到 resume messages artifact：artifact-missing"):
        executor._load_resume_messages("artifact-missing")  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="request_overrides.actor 必须是 dict"):
        executor._apply_request_overrides(  # type: ignore[attr-defined]
            ActionRequest(request_id="req-1"),
            {"actor": "user"},
        )


def test_projection_service_rebuilds_and_caches_task_case(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    executor.execute(ctx, "write_file", {"path": "projection.txt", "content": "hello\n"})

    payload = ProjectionService(store).rebuild_task(ctx.task_id)
    cached = store.get_projection_cache(ctx.task_id)

    assert payload["task"]["task_id"] == ctx.task_id
    assert payload["proof"]["chain_verification"]["valid"] is True
    assert cached is not None
    assert cached["payload"]["task"]["task_id"] == ctx.task_id


def test_projection_service_incrementally_updates_tool_history(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-projection",
        goal="长任务",
        source_channel="chat",
        kind="respond",
    )

    def add_action_event(tool_name: str, tool_input: dict[str, Any]) -> None:
        uri, content_hash = artifacts.store_json(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            }
        )
        artifact = store.create_artifact(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="action_request",
            uri=uri,
            content_hash=content_hash,
            producer="test",
            metadata={"tool_name": tool_name},
        )
        store.append_event(
            event_type="action.requested",
            entity_type="step_attempt",
            entity_id=ctx.step_attempt_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            actor="kernel",
            payload={"tool_name": tool_name, "artifact_ref": artifact.artifact_id},
        )

    add_action_event("grok_search", {"query": "topic-1"})
    add_action_event("write_file", {"path": "/tmp/topic-1.md"})

    service = ProjectionService(store)
    first = service.rebuild_task(ctx.task_id)
    assert [entry["tool_name"] for entry in first["tool_history"]] == ["grok_search", "write_file"]

    add_action_event("grok_search", {"query": "topic-2"})

    original_full_rebuild = service._full_rebuild

    def _unexpected_full_rebuild(task_id: str) -> dict[str, Any]:
        raise AssertionError(f"full rebuild should not run for {task_id}")

    service._full_rebuild = _unexpected_full_rebuild  # type: ignore[method-assign]
    try:
        second = service.rebuild_task(ctx.task_id)
    finally:
        service._full_rebuild = original_full_rebuild  # type: ignore[method-assign]

    assert [entry["tool_name"] for entry in second["tool_history"]] == [
        "grok_search",
        "write_file",
        "grok_search",
    ]
    assert second["tool_history"][-1]["key_input"] == '"topic-2"'
