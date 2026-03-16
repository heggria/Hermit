# ruff: noqa: F403,F405
from tests.task_kernel_support import *


def test_builtin_tool_metadata_audit_marks_reads_and_writes_explicitly(tmp_path: Path) -> None:
    from hermit.builtin.computer_use.tools import register as register_computer
    from hermit.builtin.feishu.hooks import register as register_feishu_hooks
    from hermit.builtin.grok.tools import register as register_grok
    from hermit.builtin.image_memory.hooks import register as register_image_memory
    from hermit.builtin.scheduler.tools import register as register_scheduler
    from hermit.builtin.web_tools.tools import register as register_web_tools
    from hermit.builtin.webhook.tools import register as register_webhook

    hooks = HooksEngine()

    def _tool_map(ctx: PluginContext) -> dict[str, ToolSpec]:
        return {tool.name: tool for tool in ctx.tools}

    ctx_web = PluginContext(hooks, settings=None)
    register_web_tools(ctx_web)
    web_tools = _tool_map(ctx_web)
    assert web_tools["web_search"].readonly is True
    assert web_tools["web_search"].action_class == "network_read"
    assert web_tools["web_fetch"].requires_receipt is False

    ctx_grok = PluginContext(hooks, settings=None)
    register_grok(ctx_grok)
    grok_tools = _tool_map(ctx_grok)
    assert grok_tools["grok_search"].readonly is True
    assert grok_tools["grok_search"].action_class == "network_read"

    ctx_computer = PluginContext(hooks, settings=None)
    register_computer(ctx_computer)
    computer_tools = _tool_map(ctx_computer)
    assert computer_tools["computer_screenshot"].readonly is True
    assert computer_tools["computer_get_screen_size"].readonly is True
    assert computer_tools["computer_click"].action_class == "execute_command"
    assert computer_tools["computer_open_app"].risk_hint == "critical"

    image_settings = SimpleNamespace(
        image_memory_dir=tmp_path / "image-memory",
        image_context_limit=3,
        image_model=None,
        model="fake-model",
    )
    ctx_image = PluginContext(hooks, settings=image_settings)
    register_image_memory(ctx_image)
    image_tools = _tool_map(ctx_image)
    assert image_tools["image_search"].readonly is True
    assert image_tools["image_get"].readonly is True
    assert image_tools["image_store_from_path"].action_class == "write_local"
    assert image_tools["image_store_from_feishu"].action_class == "attachment_ingest"
    assert image_tools["image_attach_to_feishu"].action_class == "credentialed_api_call"

    ctx_webhook = PluginContext(hooks, settings=None)
    register_webhook(ctx_webhook)
    webhook_tools = _tool_map(ctx_webhook)
    assert webhook_tools["webhook_list"].readonly is True
    assert webhook_tools["webhook_add"].action_class == "write_local"
    assert webhook_tools["webhook_update"].requires_receipt is True

    ctx_scheduler = PluginContext(hooks, settings=None)
    register_scheduler(ctx_scheduler)
    scheduler_tools = _tool_map(ctx_scheduler)
    assert scheduler_tools["schedule_list"].readonly is True
    assert scheduler_tools["schedule_history"].action_class == "read_local"
    assert scheduler_tools["schedule_create"].action_class == "scheduler_mutation"
    assert scheduler_tools["schedule_create"].requires_receipt is True
    scheduler_decision = PolicyEngine().evaluate(
        ActionRequest(
            request_id="req_schedule_create",
            tool_name="schedule_create",
            tool_input={"name": "Daily", "prompt": "run", "schedule_type": "cron"},
            action_class="scheduler_mutation",
            resource_scopes=["scheduler_store"],
            risk_hint="medium",
            requires_receipt=True,
        )
    )
    assert scheduler_decision.decision == "allow_with_receipt"
    assert scheduler_decision.requires_receipt is True
    assert scheduler_decision.obligations.require_approval is False

    ctx_feishu = PluginContext(hooks, settings=None)
    register_feishu_hooks(ctx_feishu)
    feishu_tools = _tool_map(ctx_feishu)
    assert feishu_tools["feishu_react"].action_class == "ephemeral_ui_mutation"
    assert feishu_tools["feishu_react"].requires_receipt is False


def test_agent_runtime_blocks_then_resumes_same_step_attempt(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    runtime = AgentRuntime(
        provider=FakeProvider(
            responses=[
                ProviderResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "write_file",
                            "input": {"path": ".env", "content": "kernel\n"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                ),
                ProviderResponse(
                    content=[{"type": "text", "text": "done"}],
                    stop_reason="end_turn",
                    usage=UsageMetrics(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        registry=_write_registry(Path(ctx.workspace_root)),
        model="fake",
        tool_executor=executor,
    )

    blocked = runtime.run("update draft", task_context=ctx)

    assert blocked.blocked is True
    assert blocked.approval_id is not None
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert "runtime_snapshot" in attempt.context
    snapshot = attempt.context["runtime_snapshot"]
    assert snapshot["schema_version"] == 2
    assert snapshot["kind"] == "runtime_snapshot"
    assert "messages" not in snapshot["payload"]
    resume_messages_ref = snapshot["payload"]["resume_messages_ref"]
    resume_messages = store.get_artifact(resume_messages_ref)
    assert resume_messages is not None
    assert snapshot["payload"]["pending_tool_blocks"][0]["name"] == "write_file"

    ApprovalService(store).approve(blocked.approval_id)
    resumed = runtime.resume(step_attempt_id=ctx.step_attempt_id, task_context=ctx)

    assert resumed.blocked is False
    assert resumed.text == "done"
    assert resumed.tool_calls == 1
    assert (Path(ctx.workspace_root) / ".env").read_text(encoding="utf-8") == "kernel\n"
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert "runtime_snapshot" not in attempt.context
    assert store.list_receipts(task_id=ctx.task_id, limit=10)[0].action_type == "write_local"


def test_agent_runtime_resume_supports_legacy_v1_runtime_snapshot(tmp_path: Path) -> None:
    store, _artifacts, _controller, executor, ctx = _kernel_runtime(tmp_path)
    runtime = AgentRuntime(
        provider=FakeProvider(
            responses=[
                ProviderResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "write_file",
                            "input": {"path": ".env", "content": "legacy\n"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                ),
                ProviderResponse(
                    content=[{"type": "text", "text": "done"}],
                    stop_reason="end_turn",
                    usage=UsageMetrics(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        registry=_write_registry(Path(ctx.workspace_root)),
        model="fake",
        tool_executor=executor,
    )

    blocked = runtime.run("update draft", task_context=ctx)
    assert blocked.approval_id is not None
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    snapshot = dict(attempt.context["runtime_snapshot"])
    v2_payload = dict(snapshot["payload"])
    attempt.context["runtime_snapshot"] = {
        "schema_version": 1,
        "kind": "runtime_snapshot",
        "expires_at": snapshot["expires_at"],
        "payload": {
            "messages": [
                {"role": "user", "content": "update draft"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "write_file",
                            "input": {"path": ".env", "content": "legacy\n"},
                        }
                    ],
                },
            ],
            "pending_tool_blocks": v2_payload["pending_tool_blocks"],
            "tool_result_blocks": v2_payload["tool_result_blocks"],
            "next_turn": v2_payload["next_turn"],
            "disable_tools": v2_payload["disable_tools"],
            "readonly_only": v2_payload["readonly_only"],
        },
    }
    store.update_step_attempt(ctx.step_attempt_id, context=attempt.context)

    ApprovalService(store).approve(blocked.approval_id)
    resumed = runtime.resume(step_attempt_id=ctx.step_attempt_id, task_context=ctx)

    assert resumed.blocked is False
    assert resumed.text == "done"
    assert (Path(ctx.workspace_root) / ".env").read_text(encoding="utf-8") == "legacy\n"


def test_observation_progress_events_are_deduped_and_ready_return_resumes_attempt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-observation",
        goal="Watch a long search",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    responses = [
        {
            "status": "observing",
            "topic_summary": "Checking first source",
            "progress": {
                "phase": "probing",
                "summary": "Checking first source",
                "progress_percent": 15,
            },
        },
        {
            "status": "observing",
            "topic_summary": "Checking first source",
            "progress": {
                "phase": "probing",
                "summary": "Checking first source",
                "progress_percent": 15,
            },
        },
        {
            "status": "observing",
            "topic_summary": "Search context is ready",
            "progress": {
                "phase": "ready",
                "summary": "Search context is ready",
                "progress_percent": 100,
                "ready": True,
            },
            "result": {"ready": True, "source_count": 3},
        },
    ]
    registry = _observation_registry(responses)
    summarizer = _FakeProgressSummarizer()
    executor = ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        progress_summarizer=summarizer,
        tool_output_limit=2000,
    )
    runtime = AgentRuntime(
        provider=FakeProvider(
            responses=[
                ProviderResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "observe_start",
                            "input": {},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=UsageMetrics(input_tokens=2, output_tokens=1),
                ),
                ProviderResponse(
                    content=[{"type": "text", "text": "done"}],
                    stop_reason="end_turn",
                    usage=UsageMetrics(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        registry=registry,
        model="fake",
        tool_executor=executor,
    )

    blocked = runtime.run("watch it", task_context=ctx)

    assert blocked.suspended is True
    assert blocked.waiting_kind == "observing"
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert attempt.pending_execution_ref is not None
    stripped_context = dict(attempt.context)
    stripped_context.pop("runtime_snapshot", None)
    stripped_context.pop("pending_observation_execution", None)
    store.update_step_attempt(ctx.step_attempt_id, context=stripped_context)

    first_poll = executor.poll_observation(ctx.step_attempt_id, now=time.time())
    second_poll = executor.poll_observation(ctx.step_attempt_id, now=time.time())
    third_poll = executor.poll_observation(ctx.step_attempt_id, now=time.time())

    assert first_poll is not None and first_poll.should_resume is False
    assert second_poll is not None and second_poll.should_resume is False
    assert third_poll is not None and third_poll.should_resume is True

    progress_events = [
        event
        for event in store.list_events(task_id=ctx.task_id, limit=50)
        if event["event_type"] == "tool.progressed"
    ]
    summary_events = [
        event
        for event in store.list_events(task_id=ctx.task_id, limit=50)
        if event["event_type"] == "task.progress.summarized"
    ]
    assert len(progress_events) == 2
    assert len(summary_events) == 2
    assert progress_events[0]["payload"]["summary"] == "Checking first source"
    assert progress_events[1]["payload"]["ready"] is True
    assert "正在收敛上下文" in summary_events[0]["payload"]["summary"]
    assert "现在可以继续后续步骤了" in summary_events[1]["payload"]["summary"]
    assert len(summarizer.calls) == 2

    resumed = runtime.resume(step_attempt_id=ctx.step_attempt_id, task_context=ctx)

    assert resumed.text == "done"
    assert resumed.blocked is False
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None
    assert "runtime_snapshot" not in attempt.context
    assert attempt.pending_execution_ref is None


def test_task_topic_projection_prefers_progress_milestones() -> None:
    topic = build_task_topic(
        [
            {
                "event_seq": 1,
                "event_type": "task.created",
                "payload": {
                    "title": "<session_time>ts</session_time>\n<feishu_msg_id>om_1</feishu_msg_id>\nRun dev server",
                },
            },
            {
                "event_seq": 2,
                "event_type": "tool.submitted",
                "payload": {"topic_summary": "Submitting dev server"},
            },
            {
                "event_seq": 3,
                "event_type": "tool.progressed",
                "payload": {
                    "phase": "starting",
                    "summary": "Booting dev server",
                    "progress_percent": 10,
                },
            },
            {
                "event_seq": 4,
                "event_type": "tool.status.changed",
                "payload": {"status": "observing", "topic_summary": "Booting dev server"},
            },
            {
                "event_seq": 5,
                "event_type": "task.progress.summarized",
                "payload": {
                    "phase": "starting",
                    "summary": "正在启动 dev server，并等待首个 ready 信号。",
                    "detail": "暂时没有阻塞。",
                    "progress_percent": 10,
                },
            },
            {
                "event_seq": 6,
                "event_type": "tool.progressed",
                "payload": {
                    "phase": "ready",
                    "summary": "Dev server ready",
                    "detail": "READY http://127.0.0.1:3000",
                    "progress_percent": 100,
                    "ready": True,
                },
            },
            {
                "event_seq": 7,
                "event_type": "task.progress.summarized",
                "payload": {
                    "phase": "ready",
                    "summary": "dev server 已就绪，接下来可以继续 smoke test。",
                    "detail": "服务已经可访问。",
                    "progress_percent": 100,
                },
            },
            {
                "event_seq": 8,
                "event_type": "task.completed",
                "payload": {"result_preview": "北京今天晴，最高 16°C。"},
            },
        ]
    )

    assert topic["status"] == "completed"
    assert topic["current_hint"] == "北京今天晴，最高 16°C。"
    assert topic["current_phase"] == "completed"
    assert topic["current_progress_percent"] == 100
    assert topic["items"][0]["text"] == "Run dev server"
    assert topic["items"][-1]["text"] == "北京今天晴，最高 16°C。"
    assert topic["items"][-1]["kind"] == "task.completed"
