# ruff: noqa: F403,F405
from tests.feishu_dispatcher_support import *


def test_build_approval_card_renders_structured_sections() -> None:
    card = build_approval_card(
        "准备创建定时任务 `每日巡检`。",
        "approval_sched",
        title="确认创建定时任务",
        detail="确认后，Hermit 会按这个计划自动发起任务。",
        sections=[
            {
                "title": "本次操作会做什么",
                "items": [
                    "任务名：`每日巡检`",
                    "触发时机：每隔 1 小时执行一次",
                    "Prompt 摘要：检查异常任务并回传摘要",
                ],
            },
            {
                "title": "为什么需要你确认",
                "items": [
                    "确认后，Hermit 会在未来按这个计划自动发起任务，所以需要先确认触发时机和任务内容。",
                ],
            },
        ],
        locale="zh-CN",
    )

    markdown_blocks = [
        element["content"]
        for element in card["body"]["elements"]
        if element.get("tag") == "markdown"
    ]

    assert any("为什么需要你确认" in block for block in markdown_blocks)
    assert any("本次操作会做什么" in block for block in markdown_blocks)
    assert any("触发时机：每隔 1 小时执行一次" in block for block in markdown_blocks)


def test_feishu_refresh_skips_topic_patch_for_approval_cards(monkeypatch, tmp_path) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="需要审批",
        source_channel="feishu",
        kind="respond",
    )
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "oc_chat",
                    "root_message_id": "om_approval",
                    "completion_reply_sent": False,
                    "card_mode": "approval",
                }
            }
        },
    )

    patched_topics: list[tuple[str, str]] = []
    completion_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    monkeypatch.setattr(adapter, "_schedule_topic_refresh", lambda: None)
    monkeypatch.setattr(
        adapter,
        "_patch_task_topic",
        lambda task_id, **kwargs: patched_topics.append(
            (task_id, str(kwargs.get("message_id", "")))
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_maybe_send_completion_result_message",
        lambda *args, **kwargs: completion_calls.append((args, kwargs)) or True,
    )

    adapter._refresh_task_topics()

    assert patched_topics == []
    assert completion_calls == []


def test_feishu_refresh_prunes_resolved_approval_mapping(monkeypatch, tmp_path) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="需要审批",
        source_channel="feishu",
        kind="respond",
    )
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    approval = store.create_approval(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )
    store.resolve_approval(
        approval.approval_id,
        status="denied",
        resolved_by="user",
        resolution={"status": "denied", "mode": "denied"},
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "oc_chat",
                    "root_message_id": "om_approval",
                    "completion_reply_sent": False,
                    "card_mode": "approval",
                    "approval_id": approval.approval_id,
                }
            }
        },
    )

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    monkeypatch.setattr(adapter, "_schedule_topic_refresh", lambda: None)

    patched_topics: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        adapter,
        "_patch_task_topic",
        lambda task_id, **kwargs: patched_topics.append((task_id, kwargs)) or True,
    )

    adapter._refresh_task_topics()

    conversation = store.get_conversation("oc_chat:user_1")
    assert conversation is not None
    assert dict(conversation.metadata or {}).get("feishu_task_topics", {}) == {
        ctx.task_id: {
            "chat_id": "oc_chat",
            "root_message_id": "om_approval",
            "completion_reply_sent": False,
            "card_mode": "topic",
        }
    }
    assert patched_topics == [(ctx.task_id, {"message_id": "om_approval"})]


def test_feishu_refresh_skips_progress_patch_for_terminal_topic_cards(
    monkeypatch, tmp_path
) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="结束任务",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="完成。",
        result_text="完成。",
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "oc_chat",
                    "root_message_id": "om_result",
                    "completion_reply_sent": False,
                    "card_mode": "topic",
                    "topic_signature": "existing-result-signature",
                }
            }
        },
    )

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    monkeypatch.setattr(adapter, "_schedule_topic_refresh", lambda: None)

    patched_topics: list[tuple[str, dict[str, Any]]] = []
    patched_terminal: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        adapter,
        "_patch_task_topic",
        lambda task_id, **kwargs: patched_topics.append((task_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        adapter,
        "_patch_terminal_result_card",
        lambda task_id, **kwargs: patched_terminal.append((task_id, kwargs)) or True,
    )

    adapter._refresh_task_topics()

    assert patched_topics == []
    assert patched_terminal == [(ctx.task_id, {"message_id": "om_result"})]


def test_feishu_adapter_scheduler_read_skill_sends_get_before_schedule_mutation(
    monkeypatch,
) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    reactions: list[tuple[str, str]] = []

    def fake_dispatch(**kwargs):
        on_tool_start = kwargs.get("on_tool_start")
        if on_tool_start is not None:
            on_tool_start("read_skill", {"name": "scheduler"})
            assert reactions == [("om_schedule", "Get")]
            on_tool_start(
                "schedule_create",
                {
                    "name": "每天14:10喝水提醒",
                    "cron_expr": "10 14 * * *",
                    "schedule_type": "cron",
                },
            )
        return DispatchResult(text="已进入审批。", agent_result=None)

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=None, resolve_text_command=lambda *_a, **_kw: None),
        dispatch=fake_dispatch,
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.add_reaction",
        lambda _client, message_id, emoji_type: reactions.append((message_id, emoji_type)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply",
        lambda *_a, **_kw: True,
    )

    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_schedule",
        sender_id="user-1",
        text="设置每天北京时间14:10喝水提醒",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )

    adapter._process_message(msg)

    assert reactions == [("om_schedule", "Get")]


def test_feishu_adapter_schedule_list_sends_get_once_before_delete(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    reactions: list[tuple[str, str]] = []

    def fake_dispatch(**kwargs):
        on_tool_start = kwargs.get("on_tool_start")
        if on_tool_start is not None:
            on_tool_start("schedule_list", {})
            assert reactions == [("om_schedule_delete", "Get")]
            on_tool_start("schedule_delete", {"job_id": "job_123"})
        return DispatchResult(text="已删除。", agent_result=None)

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=None, resolve_text_command=lambda *_a, **_kw: None),
        dispatch=fake_dispatch,
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.add_reaction",
        lambda _client, message_id, emoji_type: reactions.append((message_id, emoji_type)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply",
        lambda *_a, **_kw: True,
    )

    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_schedule_delete",
        sender_id="user-1",
        text="删除 每天北京时间14:10喝水提醒",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )
    adapter._process_message(msg)

    assert reactions == [("om_schedule_delete", "Get")]


def test_feishu_adapter_keeps_terminal_result_card_without_overwriting_with_topic(
    monkeypatch,
) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    patched_cards: list[dict[str, Any]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=SimpleNamespace()))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )
    monkeypatch.setattr(adapter, "_task_has_appended_notes", lambda _task_id: False)

    message_id, blocked, task_id = adapter._present_task_result(
        reply_to_message_id="om_1",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=DispatchResult(
            text="北京今天晴，最高 16°C。",
            agent_result=SimpleNamespace(
                task_id="task_1",
                blocked=False,
                suspended=False,
                execution_status="succeeded",
            ),
        ),
        steps=[
            make_tool_step(
                "web_search", {"query": "北京天气"}, {"forecast": "晴"}, 120, locale="zh-CN"
            )
        ],
    )

    assert message_id == "om_card"
    assert blocked is False
    assert task_id == "task_1"
    assert patched_cards
    assert "北京今天晴" in json.dumps(patched_cards[-1], ensure_ascii=False)


def test_feishu_adapter_guided_task_completion_patches_existing_card_to_final_result(
    monkeypatch, tmp_path
) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_1",
        goal="查询一下北京天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="重点看今天",
        prompt="重点看今天",
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="北京今天晴。",
        result_text="北京今天晴，最高 16°C，最低 8°C。",
    )

    patched_cards: list[dict[str, Any]] = []
    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )

    adapter._present_task_result(
        reply_to_message_id="om_1",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=DispatchResult(
            text="北京今天晴，最高 16°C，最低 8°C。",
            agent_result=SimpleNamespace(
                task_id=ctx.task_id,
                blocked=False,
                suspended=False,
                execution_status="succeeded",
            ),
        ),
        steps=[
            make_tool_step(
                "web_search", {"query": "北京天气"}, {"forecast": "晴"}, 120, locale="zh-CN"
            )
        ],
    )

    assert "北京今天晴，最高 16°C，最低 8°C。" in json.dumps(patched_cards[-1], ensure_ascii=False)
    assert patched_cards[-1]["body"]["elements"][-1]["tag"] == "collapsible_panel"


def test_feishu_adapter_guided_completion_without_progress_replies_with_final_text(
    monkeypatch, tmp_path
) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_1",
        goal="搜索今天最 hot 的话题",
        source_channel="feishu",
        kind="respond",
    )
    controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="总结为文档放到我的桌面",
        prompt="总结为文档放到我的桌面",
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="文件已写到桌面。",
        result_text="文件已写到桌面：`今日热门话题_20260313.md`",
    )

    smart_calls: list[str] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply", lambda *_a, **_kw: smart_calls.append("smart")
    )

    adapter._present_task_result(
        reply_to_message_id="om_1",
        existing_card_message_id=None,
        chat_id="oc_1",
        result=DispatchResult(
            text="文件已写到桌面：`今日热门话题_20260313.md`",
            agent_result=SimpleNamespace(
                task_id=ctx.task_id,
                blocked=False,
                suspended=False,
                execution_status="succeeded",
            ),
        ),
        steps=[],
    )

    assert smart_calls == ["smart"]


def test_feishu_adapter_card_action_submits_approval_job(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    submitted: list[tuple[Any, tuple[Any, ...]]] = []

    class FakeExecutor:
        def submit(self, fn, *args):
            submitted.append((fn, args))
            return None

    store = SimpleNamespace(
        get_approval=lambda approval_id: SimpleNamespace(approval_id=approval_id, status="pending")
    )
    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    adapter = FeishuAdapter()
    adapter._runner = runner  # type: ignore[assignment]
    adapter._client = object()
    adapter._executor = FakeExecutor()  # type: ignore[assignment]

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_thinking_card",
        lambda hint, **kwargs: {"hint": hint, "locale": kwargs.get("locale")},
    )

    event = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={"kind": "approval", "action": "approve_once", "approval_id": "approval_123"}
            ),
            context=SimpleNamespace(open_message_id="om_card_1"),
        )
    )
    response = adapter._on_card_action(event)

    assert len(submitted) == 1
    assert submitted[0][0] == adapter._handle_approval_action
    assert submitted[0][1] == ("approval_123", "approve_once", "om_card_1")
    assert response.toast is not None
    assert response.toast.content == "已通过，正在继续执行。"
    assert response.card is not None
    assert response.card.type == "raw"
    assert response.card.data == {"hint": "已通过，正在继续执行。", "locale": "zh-CN"}


def test_feishu_adapter_approval_action_deny_unbinds_mapping(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    task = SimpleNamespace(task_id="task_1", conversation_id="oc_1", source_channel="feishu")
    approval = SimpleNamespace(approval_id="approval_1", task_id="task_1", requested_action={})
    store = SimpleNamespace(
        get_approval=lambda approval_id: approval if approval_id == "approval_1" else None,
        get_task=lambda task_id: task if task_id == "task_1" else None,
    )

    patched_cards: list[dict[str, Any]] = []
    unbind_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        enqueue_approval_resume=lambda *args, **kwargs: SimpleNamespace(
            text="本次审批已拒绝，当前操作不会继续。"
        ),
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card) or True,
    )
    monkeypatch.setattr(
        adapter, "_unbind_task_topic", lambda *args, **kwargs: unbind_calls.append((args, kwargs))
    )

    adapter._handle_approval_action("approval_1", "deny", "om_card_1")

    assert patched_cards[-1]["header"]["title"]["content"] == "未通过"
    assert unbind_calls == [(("oc_1", "task_1"), {})]


def test_feishu_adapter_approval_action_switches_back_to_topic_card(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    task = SimpleNamespace(task_id="task_1", conversation_id="oc_1", source_channel="feishu")
    approval = SimpleNamespace(approval_id="approval_1", task_id="task_1", requested_action={})
    store = SimpleNamespace(
        get_approval=lambda approval_id: approval if approval_id == "approval_1" else None,
        get_task=lambda task_id: task if task_id == "task_1" else None,
    )

    patched_cards: list[dict[str, Any]] = []
    mapping_updates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    topic_patch_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        enqueue_approval_resume=lambda *args, **kwargs: SimpleNamespace(text="正在继续执行"),
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_thinking_card",
        lambda hint, **kwargs: {"hint": hint, "locale": kwargs.get("locale")},
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )
    monkeypatch.setattr(
        adapter,
        "_update_task_topic_mapping",
        lambda *args, **kwargs: mapping_updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        adapter,
        "_patch_task_topic",
        lambda *args, **kwargs: topic_patch_calls.append((args, kwargs)),
    )

    adapter._handle_approval_action("approval_1", "approve_once", "om_card_1")

    assert patched_cards == [{"hint": "正在继续执行", "locale": "zh-CN"}]
    assert mapping_updates == [
        (
            ("oc_1", "task_1"),
            {"card_mode": "topic", "approval_id": ""},
        )
    ]
    assert topic_patch_calls == [(("task_1",), {"message_id": "om_card_1"})]


def test_feishu_adapter_approval_action_resume_uses_task_conversation(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    task = SimpleNamespace(task_id="task_1", conversation_id="oc_1", source_channel="feishu")
    approval = SimpleNamespace(approval_id="approval_1", task_id="task_1", requested_action={})
    store = SimpleNamespace(
        get_approval=lambda approval_id: approval if approval_id == "approval_1" else None,
        get_task=lambda task_id: task if task_id == "task_1" else None,
    )

    resume_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        enqueue_approval_resume=lambda *args, **kwargs: (
            resume_calls.append((args, kwargs)) or SimpleNamespace(text="继续执行")
        ),
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_thinking_card",
        lambda hint, **kwargs: {"hint": hint, "locale": kwargs.get("locale")},
    )
    monkeypatch.setattr("hermit.builtin.feishu.adapter.patch_card", lambda *_a, **_kw: True)
    monkeypatch.setattr(adapter, "_update_task_topic_mapping", lambda *_a, **_kw: None)
    monkeypatch.setattr(adapter, "_patch_task_topic", lambda *_a, **_kw: None)

    adapter._handle_approval_action("approval_1", "approve_once", "om_card_1")

    assert resume_calls == [(("oc_1",), {"action": "approve_once", "approval_id": "approval_1"})]


def test_feishu_adapter_reissues_pending_approval_cards_on_startup(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    sent_cards: list[tuple[str, dict[str, Any]]] = []
    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                list_approvals=lambda **kwargs: [
                    SimpleNamespace(
                        approval_id="approval_123",
                        task_id="task_1",
                        requested_action={
                            "tool_name": "bash",
                            "command_preview": "rm -rf ~/tmp/demo",
                            "display_copy": {
                                "title": "确认删除操作",
                                "summary": "准备删除本地文件或目录。",
                                "detail": "这个操作可能不可恢复，建议先确认删除范围；原始命令可在详情中查看。",
                            },
                        },
                    )
                ],
                get_task=lambda task_id: SimpleNamespace(
                    task_id=task_id,
                    conversation_id="oc_chat:user_1",
                    source_channel="feishu",
                ),
            )
        )
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.send_card",
        lambda _client, chat_id, card: sent_cards.append((chat_id, card)) or "om_new",
    )
    monkeypatch.setattr(adapter, "_bind_task_topic", lambda *args, **kwargs: None)

    adapter._reissue_pending_approval_cards()

    assert len(sent_cards) == 1
    chat_id, card = sent_cards[0]
    assert chat_id == "oc_chat"
    body = card["body"]["elements"]
    text_blocks = [element["content"] for element in body if element.get("tag") == "markdown"]
    assert any("准备删除本地文件或目录。" in block for block in text_blocks)
    assert any("旧审批卡片的按钮可能已失效" in block for block in text_blocks)


def test_feishu_adapter_card_action_uses_english_locale(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    submitted: list[tuple[Any, tuple[Any, ...]]] = []

    class FakeExecutor:
        def submit(self, fn, *args):
            submitted.append((fn, args))
            return None

    store = SimpleNamespace(
        get_approval=lambda approval_id: SimpleNamespace(approval_id=approval_id, status="pending")
    )
    runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
    adapter = FeishuAdapter(settings=SimpleNamespace(locale="en-US"))
    adapter._runner = runner  # type: ignore[assignment]
    adapter._client = object()
    adapter._executor = FakeExecutor()  # type: ignore[assignment]

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_thinking_card",
        lambda hint, **kwargs: {"hint": hint, "locale": kwargs.get("locale")},
    )

    event = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={"kind": "approval", "action": "approve_once", "approval_id": "approval_123"}
            ),
            context=SimpleNamespace(open_message_id="om_card_1"),
        )
    )

    response = adapter._on_card_action(event)

    assert submitted[0][1] == ("approval_123", "approve_once", "om_card_1")
    assert response.toast.content == "Approved. Continuing execution."
    assert response.card is not None
    assert response.card.type == "raw"
    assert response.card.data == {"hint": "Approved. Continuing execution.", "locale": "en-US"}
