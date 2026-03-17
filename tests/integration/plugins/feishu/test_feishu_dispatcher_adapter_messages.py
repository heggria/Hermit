# ruff: noqa: F403,F405
from tests.fixtures.feishu_dispatcher_support import *


def test_feishu_adapter_reads_hermit_env_names(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "app-secret")

    adapter = FeishuAdapter()

    assert adapter._app_id == "app-id"
    assert adapter._app_secret == "app-secret"


def test_feishu_adapter_reads_credentials_from_settings(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)

    settings = type(
        "Settings",
        (),
        {
            "feishu_app_id": "settings-app-id",
            "feishu_app_secret": "settings-app-secret",
            "feishu_thread_progress": True,
        },
    )()
    adapter = FeishuAdapter(settings=settings)

    assert adapter._app_id == "settings-app-id"
    assert adapter._app_secret == "settings-app-secret"


def test_feishu_adapter_preloads_native_feishu_skills() -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()

    assert adapter.required_skills == [
        "feishu-output-format",
        "feishu-emoji-reaction",
        "feishu-tools",
    ]


def test_feishu_adapter_builds_prompt_from_images(tmp_path) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    adapter._runner = SimpleNamespace()
    adapter._ingest_image_records = lambda _session_id, _msg: [  # type: ignore[method-assign]
        {
            "image_id": "abc123",
            "summary": "一张包含流程图的截图",
            "tags": ["流程图", "产品"],
        }
    ]

    prompt = adapter._build_image_prompt(
        "chat-1",
        FeishuMessage(
            chat_id="chat-1",
            message_id="msg-1",
            sender_id="user-1",
            text="",
            message_type="image",
            chat_type="p2p",
            image_keys=["img_v2_123"],
        ),
    )

    assert "用户发送了 1 张图片" in prompt
    assert "image_id=abc123" in prompt
    assert "流程图" in prompt


def test_feishu_adapter_ingests_images_via_kernel_executor(tmp_path) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="image_store_from_feishu",
            description="Store incoming Feishu image.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: {
                "image_id": "img_ingested",
                "summary": f"stored:{payload['image_key']}",
                "tags": ["截图", "流程图"],
            },
            action_class="attachment_ingest",
            risk_hint="high",
            requires_receipt=True,
        )
    )
    runtime = AgentRuntime(
        provider=ClaudeProvider(FakeClient(), model="fake"),
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
    runtime.workspace_root = str(tmp_path)  # type: ignore[attr-defined]
    runtime.registry.call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("registry.call should not be used")
    )  # type: ignore[method-assign]
    runner = AgentRunner(
        runtime,
        SessionManager(tmp_path / "sessions", store=store),
        PluginManager(),
        task_controller=TaskController(store),
    )

    adapter = FeishuAdapter()
    adapter._runner = runner

    record = adapter._ingest_image_record(
        session_id="oc_1", message_id="om_1", image_key="img_v2_123"
    )

    task = store.get_last_task_for_conversation("oc_1")
    assert record == {
        "image_id": "img_ingested",
        "summary": "stored:img_v2_123",
        "tags": ["截图", "流程图"],
    }
    assert task is not None
    assert task.parent_task_id is None
    assert task.requested_by_principal_id == "principal_feishu_adapter"
    assert task.status == "completed"
    receipt = store.list_receipts(task_id=task.task_id, limit=1)[0]
    assert receipt.action_type == "attachment_ingest"
    assert receipt.result_code == "succeeded"


def test_feishu_adapter_replies_with_approval_card_for_blocked_result(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter
    from hermit.runtime.control.runner.runner import DispatchResult

    sent_cards: list[dict[str, Any]] = []
    smart_calls: list[str] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    store = SimpleNamespace(
        get_approval=lambda approval_id: SimpleNamespace(
            approval_id=approval_id,
            requested_action={
                "tool_name": "read_skill",
                "tool_input": {"name": "computer-use"},
                "risk_level": "low",
                "approval_packet": {
                    "title": "确认读取技能说明",
                    "summary": "准备加载 computer-use 技能说明。",
                },
            },
        )
    )
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store, resolve_text_command=lambda *_a, **_kw: None),
        dispatch=lambda **_: DispatchResult(
            text="准备加载 computer-use 技能说明（审批编号：approval_123）。请使用 `/task approve approval_123`，或直接回复“批准 approval_123”继续执行。",
            agent_result=SimpleNamespace(blocked=True, approval_id="approval_123"),
        ),
    )

    monkeypatch.setattr(
        "hermit.plugins.builtin.adapters.feishu.adapter.build_approval_card",
        lambda text, approval_id, steps, **kwargs: {
            "text": text,
            "approval_id": approval_id,
            "steps": len(steps),
            "title": kwargs.get("title"),
            "detail": kwargs.get("detail"),
            "command_preview": kwargs.get("command_preview"),
        },
    )
    monkeypatch.setattr(
        "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id",
        lambda _client, _message_id, card: sent_cards.append(card) or "om_reply",
    )
    monkeypatch.setattr(
        "hermit.plugins.builtin.adapters.feishu.adapter.smart_reply",
        lambda *_a, **_kw: smart_calls.append("smart"),
    )

    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_1",
        sender_id="user-1",
        text="开始吧",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )
    adapter._process_message(msg)

    assert sent_cards == [
        {
            "text": "准备加载 computer-use 技能说明。",
            "approval_id": "approval_123",
            "steps": 0,
            "title": "确认读取技能说明",
            "detail": "风险等级：low。请确认后继续执行。",
            "command_preview": None,
        }
    ]
    assert smart_calls == []


def test_feishu_adapter_process_message_routes_short_start_phrase_through_sync_dispatch(
    monkeypatch,
) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    dispatch_calls: list[dict[str, Any]] = []
    bind_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=None,
            resolve_text_command=lambda *_a, **_kw: None,
            decide_ingress=lambda **_kw: SimpleNamespace(
                mode="start_task", intent="start_new_task", task_id=None
            ),
        ),
        dispatch=lambda **_: None,
        enqueue_ingress=lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_approval")
        ),
    )

    monkeypatch.setattr(
        adapter,
        "_dispatch_message_sync_compat",
        lambda **kwargs: dispatch_calls.append(kwargs),
    )
    monkeypatch.setattr(
        adapter, "_bind_task_topic", lambda *args, **kwargs: bind_calls.append((args, kwargs))
    )

    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_1",
        sender_id="user-1",
        text="开始吧",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )
    adapter._process_message(msg)

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["enable_progress_card"] is False
    assert bind_calls == []
    assert enqueue_calls == []


def test_feishu_adapter_process_message_routes_chat_only_through_sync_dispatch(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    dispatch_calls: list[dict[str, Any]] = []
    enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_a, **_kw: None,
            decide_ingress=lambda **_kw: SimpleNamespace(
                mode="start", intent="chat_only", task_id=None
            ),
        ),
        enqueue_ingress=lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_chat")
        ),
    )

    monkeypatch.setattr(
        adapter,
        "_dispatch_message_sync_compat",
        lambda **kwargs: dispatch_calls.append(kwargs),
    )

    adapter._process_message(
        FeishuMessage(
            chat_id="oc_1",
            message_id="om_hello",
            sender_id="user-1",
            text="你好",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["session_id"] == "oc_1"
    assert dispatch_calls[0]["dispatch_text"]
    assert enqueue_calls == []


def test_feishu_adapter_process_message_routes_low_signal_punctuation_through_sync_dispatch(
    monkeypatch,
) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    dispatch_calls: list[dict[str, Any]] = []
    enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_a, **_kw: None,
            decide_ingress=lambda **_kw: SimpleNamespace(mode="start_task", task_id=None),
        ),
        enqueue_ingress=lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_noise")
        ),
    )

    monkeypatch.setattr(
        adapter,
        "_dispatch_message_sync_compat",
        lambda **kwargs: dispatch_calls.append(kwargs),
    )

    adapter._process_message(
        FeishuMessage(
            chat_id="oc_1",
            message_id="om_noise",
            sender_id="user-1",
            text="？",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["enable_progress_card"] is False
    assert enqueue_calls == []


def test_feishu_adapter_process_message_routes_short_text_through_sync_dispatch_without_progress(
    monkeypatch,
) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    dispatch_calls: list[dict[str, Any]] = []
    enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_a, **_kw: None,
            decide_ingress=lambda **_kw: SimpleNamespace(
                mode="start_task", intent="start_new_task", task_id=None
            ),
        ),
        enqueue_ingress=lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_short")
        ),
    )

    monkeypatch.setattr(
        adapter,
        "_dispatch_message_sync_compat",
        lambda **kwargs: dispatch_calls.append(kwargs),
    )

    adapter._process_message(
        FeishuMessage(
            chat_id="oc_1",
            message_id="om_short",
            sender_id="user-1",
            text="帮我看看",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["enable_progress_card"] is False
    assert enqueue_calls == []


def test_feishu_adapter_process_message_replies_on_pending_disambiguation(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    smart_calls: list[str] = []
    enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_a, **_kw: None,
            decide_ingress=lambda **_kw: SimpleNamespace(
                mode="start",
                resolution="pending_disambiguation",
                candidates=[{"task_id": "task-1"}, {"task_id": "task-2"}],
            ),
        ),
        _pending_disambiguation_text=lambda ingress: (
            f"请先切到任务 {ingress.candidates[0]['task_id']}"
        ),
        enqueue_ingress=lambda *args, **kwargs: (
            enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task-x")
        ),
    )

    monkeypatch.setattr(
        "hermit.plugins.builtin.adapters.feishu.adapter.smart_reply",
        lambda *_a, **_kw: smart_calls.append("smart"),
    )

    adapter._process_message(
        FeishuMessage(
            chat_id="oc_1",
            message_id="om_pending",
            sender_id="user-1",
            text="这个改一下",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert smart_calls == ["smart"]
    assert enqueue_calls == []


def test_feishu_adapter_process_message_passes_reply_bound_task_id(monkeypatch, tmp_path) -> None:
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

    runner, _client = _make_runner(tmp_path, answer="reply")
    task = runner.task_controller.start_task(
        conversation_id="oc_1",
        goal="整理产品文档",
        source_channel="feishu",
        kind="respond",
    )

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = runner
    adapter._bind_task_topic("oc_1", task.task_id, chat_id="oc_1", root_message_id="om_root")

    captured: list[dict[str, Any]] = []

    def _decide_ingress(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(mode="append_note", task_id=task.task_id, resolution="append_note")

    monkeypatch.setattr(runner.task_controller, "decide_ingress", _decide_ingress)
    monkeypatch.setattr(adapter, "_patch_task_topic", lambda *_a, **_kw: True)

    adapter._process_message(
        FeishuMessage(
            chat_id="oc_1",
            message_id="om_pending",
            sender_id="user-1",
            text="继续这个",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
            reply_to_message_id="om_root",
        )
    )

    assert captured and captured[0]["reply_to_task_id"] == task.task_id
    assert captured[0]["reply_to_ref"] == "om_root"
    assert captured[0]["quoted_message_ref"] is None
