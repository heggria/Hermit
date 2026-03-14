from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hermit.builtin.feishu.adapter import FeishuAdapter
from hermit.builtin.feishu.reply import make_tool_step
from hermit.kernel.approval_copy import ApprovalCopy, ApprovalSection
from hermit.kernel.controller import TaskController
from hermit.kernel.store import KernelStore


@pytest.fixture(autouse=True)
def _force_feishu_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    monkeypatch.setattr("hermit.builtin.feishu.adapter.log.warning", lambda *args, **kwargs: None)


def test_feishu_adapter_task_helpers_read_terminal_result_and_notes() -> None:
    adapter = FeishuAdapter()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                list_events=lambda **_: [
                    {"event_type": "task.started", "payload": {}},
                    {"event_type": "task.note.appended", "payload": {}},
                    {"event_type": "task.completed", "payload": {"result_preview": " 最终结果 "}},
                ]
            )
        )
    )

    assert adapter._task_has_appended_notes("task-1") is True
    assert adapter._task_terminal_result_text("task-1") == "最终结果"


def test_feishu_adapter_build_pending_approval_card_uses_copy_and_detail_suffix(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._approval_copy = SimpleNamespace(
        resolve_copy=lambda requested_action, approval_id: ApprovalCopy(
            title="确认删除操作",
            summary="准备删除本地文件或目录。",
            detail="这个操作可能不可恢复。",
            sections=(ApprovalSection(title="本次操作会做什么", items=("删除 `~/tmp/demo`",)),),
        )
    )
    approval = SimpleNamespace(
        requested_action={
            "target_paths": ["/Users/beta/tmp/demo"],
            "workspace_root": "/Users/beta/work/Hermit",
            "grant_scope_dir": "/Users/beta/tmp",
            "command_preview": "rm -rf ~/tmp/demo",
        }
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_approval_card",
        lambda text, approval_id, steps, **kwargs: captured.update(
            {
                "text": text,
                "approval_id": approval_id,
                "steps": steps,
                **kwargs,
            }
        )
        or {"ok": True},
    )

    card, resolved = adapter._build_pending_approval_card(
        "approval-1",
        fallback_text="fallback",
        steps=[make_tool_step("shell", {"command": "rm -rf ~/tmp/demo"}, "", 0, locale="zh-CN")],
        detail_suffix="服务重启后重新发起此卡片。",
        approval=approval,
    )

    assert card == {"ok": True}
    assert resolved is approval
    assert captured["text"] == "准备删除本地文件或目录。"
    assert captured["title"] == "确认删除操作"
    assert "这个操作可能不可恢复。" in captured["detail"]
    assert "服务重启后重新发起此卡片。" in captured["detail"]
    assert captured["command_preview"] == "rm -rf ~/tmp/demo"
    assert captured["target_path"] == "/Users/beta/tmp/demo"
    assert captured["workspace_root"] == "/Users/beta/work/Hermit"
    assert captured["grant_scope_dir"] == "/Users/beta/tmp"


def test_feishu_adapter_maybe_send_completion_result_message_uses_terminal_fallback_and_marks_mapping(
    monkeypatch, tmp_path
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="整理结果",
        source_channel="feishu",
        kind="respond",
    )
    controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="顺便总结一下",
        prompt="顺便总结一下",
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="整理完成。",
        result_text="",
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "oc_chat",
                    "completion_reply_sent": False,
                }
            }
        },
    )

    sent_messages: list[tuple[str, str]] = []
    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_send_message",
        lambda _client, chat_id, text, **kwargs: sent_messages.append((chat_id, text)) or "om_result",
    )

    assert adapter._maybe_send_completion_result_message(ctx.task_id) is True
    assert sent_messages == [("oc_chat", "整理完成。")]

    conversation = store.get_conversation("oc_chat:user_1")
    mapping = dict(dict(conversation.metadata or {})["feishu_task_topics"][ctx.task_id])
    assert mapping["completion_reply_sent"] is True


def test_feishu_adapter_maybe_send_completion_result_message_respects_existing_reply_and_send_failure(
    monkeypatch, tmp_path
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="整理结果",
        source_channel="feishu",
        kind="respond",
    )
    controller.append_note(
        task_id=ctx.task_id,
        source_channel="feishu",
        raw_text="补充说明",
        prompt="补充说明",
    )
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="整理完成。",
        result_text="整理完成。",
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "",
                    "completion_reply_sent": True,
                }
            }
        },
    )

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    send_attempts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_send_message",
        lambda _client, chat_id, text, **kwargs: send_attempts.append((chat_id, text)) or "",
    )

    assert adapter._maybe_send_completion_result_message(ctx.task_id) is False

    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "chat_id": "",
                    "completion_reply_sent": False,
                }
            }
        },
    )
    assert adapter._maybe_send_completion_result_message(ctx.task_id) is False
    assert send_attempts == [("oc_chat", "整理完成。")]


def test_feishu_adapter_present_task_result_patches_existing_card_for_blocked_approval(monkeypatch) -> None:
    adapter = FeishuAdapter()
    adapter._client = object()

    patched: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(adapter, "_build_pending_approval_card", lambda *args, **kwargs: ({"approval": True}, None))
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, message_id, card: patched.append((message_id, card)),
    )

    message_id, blocked, task_id = adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=SimpleNamespace(
            text="等待审批",
            agent_result=SimpleNamespace(blocked=True, suspended=False, approval_id="approval-1", task_id="task-1"),
        ),
        steps=[],
    )

    assert (message_id, blocked, task_id) == ("om_card", True, "task-1")
    assert patched == [("om_card", {"approval": True})]


def test_feishu_adapter_present_task_result_replies_with_approval_card_when_blocked_without_existing_card(monkeypatch) -> None:
    adapter = FeishuAdapter()
    adapter._client = object()

    replied: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(adapter, "_build_pending_approval_card", lambda *args, **kwargs: ({"approval": True}, None))
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.reply_card_return_id",
        lambda _client, message_id, card: replied.append((message_id, card)) or "om_blocked",
    )

    message_id, blocked, task_id = adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id=None,
        chat_id="oc_1",
        result=SimpleNamespace(
            text="等待审批",
            agent_result=SimpleNamespace(blocked=True, suspended=False, approval_id="approval-1", task_id="task-1"),
        ),
        steps=[],
    )

    assert (message_id, blocked, task_id) == ("om_blocked", True, "task-1")
    assert replied == [("om_reply", {"approval": True})]


def test_feishu_adapter_present_task_result_updates_existing_progress_card(monkeypatch) -> None:
    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._task_has_appended_notes = lambda task_id: False  # type: ignore[method-assign]
    adapter._task_history_steps = lambda task_id, live_steps=None: list(live_steps or [])  # type: ignore[method-assign]

    patched: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_result_card_with_process",
        lambda text, steps, **kwargs: {"text": text, "step_count": len(steps)},
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, message_id, card: patched.append((message_id, card)),
    )

    step = make_tool_step("grok_search", {"query": "今日热点"}, {"ok": True}, 100, locale="zh-CN")
    message_id, blocked, task_id = adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=SimpleNamespace(
            text="整理完成",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[step],
    )

    assert (message_id, blocked, task_id) == ("om_card", False, "task-1")
    assert patched == [("om_card", {"text": "整理完成", "step_count": 1})]


def test_feishu_adapter_present_task_result_replies_with_result_even_when_notes_exist(monkeypatch) -> None:
    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._task_has_appended_notes = lambda task_id: True  # type: ignore[method-assign]

    replies: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply",
        lambda _client, message_id, text, **kwargs: replies.append((message_id, text)),
    )

    message_id, blocked, task_id = adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id=None,
        chat_id="oc_1",
        result=SimpleNamespace(
            text="文件已写到桌面",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[],
    )

    assert (message_id, blocked, task_id) == (None, False, "task-1")
    assert replies == [("om_reply", "文件已写到桌面")]


def test_feishu_adapter_present_task_result_uses_reply_or_chat_fallback(monkeypatch) -> None:
    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._task_has_appended_notes = lambda task_id: False  # type: ignore[method-assign]

    replies: list[tuple[str, str]] = []
    sends: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply",
        lambda _client, message_id, text, **kwargs: replies.append((message_id, text)),
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_send_message",
        lambda _client, chat_id, text, **kwargs: sends.append((chat_id, text)) or "om_sent",
    )

    adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id=None,
        chat_id="oc_1",
        result=SimpleNamespace(
            text="这是直接回复",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[],
    )
    adapter._present_task_result(
        reply_to_message_id=None,
        existing_card_message_id=None,
        chat_id="oc_1",
        result=SimpleNamespace(
            text="这是群聊发送",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[],
    )

    assert replies == [("om_reply", "这是直接回复")]
    assert sends == [("oc_1", "这是群聊发送")]


def test_feishu_adapter_present_task_result_returns_early_without_client_or_text() -> None:
    adapter = FeishuAdapter()

    assert adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=SimpleNamespace(
            text="整理完成",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[],
    ) == ("om_card", False, "task-1")

    adapter._client = object()
    assert adapter._present_task_result(
        reply_to_message_id="om_reply",
        existing_card_message_id="om_card",
        chat_id="oc_1",
        result=SimpleNamespace(
            text="",
            agent_result=SimpleNamespace(blocked=False, suspended=False, approval_id="", task_id="task-1"),
        ),
        steps=[],
    ) == ("om_card", False, "task-1")


def test_feishu_adapter_patch_task_topic_updates_signature_once(monkeypatch, tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="长任务",
        source_channel="feishu",
        kind="respond",
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "root_message_id": "om_root",
                }
            }
        },
    )

    patched: list[tuple[str, dict[str, Any]]] = []
    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.ProjectionService",
        lambda _store: SimpleNamespace(
            ensure_task_projection=lambda task_id: {
                "topic": {"status": "running", "current_hint": "继续执行"},
                "task": {"title": "一个非常非常长的任务标题，用来测试 patch topic 时的标题截断"},
            }
        ),
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_progress_card",
        lambda steps, current_hint, **kwargs: {"steps": len(steps), "current_hint": current_hint},
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, message_id, card: patched.append((message_id, card)) or True,
    )

    assert adapter._patch_task_topic(ctx.task_id) is True
    assert adapter._patch_task_topic(ctx.task_id) is False
    assert patched == [("om_root", {"steps": 0, "current_hint": "思考中..."})]

    conversation = store.get_conversation("oc_chat:user_1")
    mapping = dict(dict(conversation.metadata or {})["feishu_task_topics"][ctx.task_id])
    assert mapping["topic_signature"]


def test_feishu_adapter_patch_task_topic_hides_initial_started_state(monkeypatch, tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="你好",
        source_channel="feishu",
        kind="respond",
    )
    store.update_conversation_metadata(
        "oc_chat:user_1",
        {
            "feishu_task_topics": {
                ctx.task_id: {
                    "root_message_id": "om_root",
                }
            }
        },
    )

    patched: list[tuple[str, dict[str, Any]]] = []
    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.ProjectionService",
        lambda _store: SimpleNamespace(
            ensure_task_projection=lambda task_id: {
                "topic": {
                    "status": "running",
                    "current_hint": "你好",
                    "current_phase": "started",
                    "items": [{"kind": "task.started", "text": "你好"}],
                }
            }
        ),
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.build_progress_card",
        lambda steps, current_hint, **kwargs: {"steps": len(steps), "current_hint": current_hint},
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, message_id, card: patched.append((message_id, card)) or True,
    )

    assert adapter._patch_task_topic(ctx.task_id) is True
    assert patched == [("om_root", {"steps": 0, "current_hint": "思考中..."})]


def test_feishu_adapter_patch_task_topic_returns_false_without_message_id(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="oc_chat:user_1",
        goal="长任务",
        source_channel="feishu",
        kind="respond",
    )

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))

    assert adapter._patch_task_topic(ctx.task_id) is False


def test_feishu_adapter_reissues_only_feishu_oc_approval_cards(monkeypatch) -> None:
    approvals = [
        SimpleNamespace(approval_id="approval-skip-source", task_id="task-skip-source"),
        SimpleNamespace(approval_id="approval-skip-chat", task_id="task-skip-chat"),
        SimpleNamespace(approval_id="approval-ok", task_id="task-ok"),
    ]
    tasks = {
        "task-skip-source": SimpleNamespace(task_id="task-skip-source", source_channel="cli", conversation_id="oc_cli:user"),
        "task-skip-chat": SimpleNamespace(task_id="task-skip-chat", source_channel="feishu", conversation_id="group:user"),
        "task-ok": SimpleNamespace(task_id="task-ok", source_channel="feishu", conversation_id="oc_chat:user"),
    }

    sent_cards: list[tuple[str, dict[str, Any]]] = []
    bound: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                list_approvals=lambda **_: approvals,
                get_task=lambda task_id: tasks.get(task_id),
            )
        )
    )

    monkeypatch.setattr(adapter, "_chat_id_from_conversation_id", lambda conversation_id: conversation_id.split(":")[0])
    monkeypatch.setattr(adapter, "_build_pending_approval_card", lambda *args, **kwargs: ({"approval": True}, None))
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.send_card",
        lambda _client, chat_id, card: sent_cards.append((chat_id, card)) or "om_card",
    )
    monkeypatch.setattr(adapter, "_bind_task_topic", lambda *args, **kwargs: bound.append((args, kwargs)))

    adapter._reissue_pending_approval_cards()

    assert sent_cards == [("oc_chat", {"approval": True})]
    assert bound == [
        (
            ("oc_chat:user", "task-ok"),
            {
                "chat_id": "oc_chat",
                "root_message_id": "om_card",
                "card_mode": "approval",
                "approval_id": "approval-ok",
            },
        )
    ]


def test_feishu_adapter_ingest_image_record_handles_missing_kernel_and_tool_errors() -> None:
    adapter = FeishuAdapter()
    adapter._runner = SimpleNamespace(task_controller=None, agent=SimpleNamespace(tool_executor=None))

    assert adapter._ingest_image_record(session_id="oc_1", message_id="om_1", image_key="img_1") is None

    finalized: list[str] = []

    class FakeTaskController:
        def __init__(self) -> None:
            self.store = SimpleNamespace(resolve_approval=lambda *args, **kwargs: None)

        def start_task(self, **kwargs: Any) -> Any:
            return SimpleNamespace(task_id="task-1")

        def finalize_result(self, ctx: Any, *, status: str, **kwargs: Any) -> None:
            finalized.append(status)

    controller = FakeTaskController()
    adapter._runner = SimpleNamespace(
        task_controller=controller,
        agent=SimpleNamespace(
            workspace_root="/Users/beta/work/Hermit",
            tool_executor=SimpleNamespace(execute=lambda *args, **kwargs: (_ for _ in ()).throw(KeyError("missing"))),
        ),
    )

    assert adapter._ingest_image_record(session_id="oc_1", message_id="om_1", image_key="img_1") is None
    assert finalized == ["failed"]


def test_feishu_adapter_ingest_image_record_auto_denies_blocked_results() -> None:
    finalized: list[str] = []
    resolved: list[tuple[str, dict[str, Any]]] = []

    class FakeTaskController:
        def __init__(self) -> None:
            self.store = SimpleNamespace(
                resolve_approval=lambda approval_id, **kwargs: resolved.append((approval_id, kwargs))
            )

        def start_task(self, **kwargs: Any) -> Any:
            return SimpleNamespace(task_id="task-1")

        def finalize_result(self, ctx: Any, *, status: str, **kwargs: Any) -> None:
            finalized.append(status)

    adapter = FeishuAdapter()
    adapter._runner = SimpleNamespace(
        task_controller=FakeTaskController(),
        agent=SimpleNamespace(
            workspace_root="/Users/beta/work/Hermit",
            tool_executor=SimpleNamespace(
                execute=lambda *args, **kwargs: SimpleNamespace(
                    blocked=True,
                    approval_id="approval-1",
                    execution_status="blocked",
                    raw_result=None,
                    result_code="blocked",
                )
            ),
        ),
    )

    assert adapter._ingest_image_record(session_id="oc_1", message_id="om_1", image_key="img_1") is None
    assert resolved == [
        (
            "approval-1",
            {
                "status": "denied",
                "resolved_by": "feishu_adapter",
                "resolution": {"reason": "adapter ingress does not support interactive approval"},
            },
        )
    ]
    assert finalized == ["failed"]


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            SimpleNamespace(
                blocked=False,
                approval_id="",
                execution_status="succeeded",
                raw_result="not-a-dict",
                result_code="succeeded",
            ),
            "failed",
        ),
        (
            SimpleNamespace(
                blocked=False,
                approval_id="",
                execution_status="failed",
                raw_result=None,
                result_code="failed",
            ),
            "failed",
        ),
        (
            SimpleNamespace(
                blocked=False,
                approval_id="",
                execution_status="degraded",
                raw_result=None,
                result_code="partial",
            ),
            None,
        ),
    ],
)
def test_feishu_adapter_ingest_image_record_handles_invalid_and_degraded_results(result: Any, expected_status: str | None) -> None:
    finalized: list[str] = []

    class FakeTaskController:
        def __init__(self) -> None:
            self.store = SimpleNamespace(resolve_approval=lambda *args, **kwargs: None)

        def start_task(self, **kwargs: Any) -> Any:
            return SimpleNamespace(task_id="task-1")

        def finalize_result(self, ctx: Any, *, status: str, **kwargs: Any) -> None:
            finalized.append(status)

    adapter = FeishuAdapter()
    adapter._runner = SimpleNamespace(
        task_controller=FakeTaskController(),
        agent=SimpleNamespace(
            workspace_root="/Users/beta/work/Hermit",
            tool_executor=SimpleNamespace(execute=lambda *args, **kwargs: result),
        ),
    )

    assert adapter._ingest_image_record(session_id="oc_1", message_id="om_1", image_key="img_1") is None
    assert finalized == ([expected_status] if expected_status is not None else [])


def test_feishu_adapter_ingest_image_records_filters_out_failed_items(monkeypatch) -> None:
    adapter = FeishuAdapter()
    monkeypatch.setattr(
        adapter,
        "_ingest_image_record",
        lambda **kwargs: {"image_id": kwargs["image_key"]} if kwargs["image_key"] == "img_ok" else None,
    )

    records = adapter._ingest_image_records(
        "oc_1",
        SimpleNamespace(message_id="om_1", image_keys=["img_skip", "img_ok"]),
    )

    assert records == [{"image_id": "img_ok"}]


def test_feishu_adapter_completion_message_requires_feishu_terminal_task_with_notes(monkeypatch) -> None:
    adapter = FeishuAdapter()
    adapter._client = object()
    adapter._task_has_appended_notes = lambda task_id: False  # type: ignore[method-assign]
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                get_task=lambda task_id: SimpleNamespace(
                    task_id=task_id,
                    conversation_id="oc_1:user",
                    source_channel="cli",
                    status="running",
                )
            )
        )
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_send_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    assert adapter._maybe_send_completion_result_message("task-1") is False


def test_feishu_adapter_process_message_reports_dispatch_failure_without_progress_card(monkeypatch) -> None:
    sent_replies: list[tuple[str, str]] = []
    patched_cards: list[tuple[str, dict[str, Any]]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True, locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_args, **_kwargs: None,
            decide_ingress=lambda **kwargs: SimpleNamespace(mode="start_task", task_id=None),
        ),
        enqueue_ingress=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    monkeypatch.setattr("hermit.builtin.feishu.adapter.send_ack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.send_text_reply",
        lambda _client, message_id, text: sent_replies.append((message_id, text)),
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, message_id, card: patched_cards.append((message_id, card)),
    )

    adapter._process_message(
        SimpleNamespace(
            chat_id="oc_1",
            message_id="om_1",
            sender_id="user-1",
            text="帮我看看",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert patched_cards == []
    assert sent_replies == [("om_1", "[错误] Agent 处理失败。")]


def test_feishu_adapter_process_message_note_appended_refreshes_topic(monkeypatch) -> None:
    patched_topics: list[str] = []
    done_calls: list[str] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=True, locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            resolve_text_command=lambda *_args, **_kwargs: None,
            decide_ingress=lambda **kwargs: SimpleNamespace(mode="append_note", task_id="task-1"),
        ),
    )

    monkeypatch.setattr("hermit.builtin.feishu.adapter.send_ack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_patch_task_topic", lambda task_id: patched_topics.append(task_id) or True)
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.send_done",
        lambda _client, message_id, _settings: done_calls.append(message_id),
    )

    adapter._process_message(
        SimpleNamespace(
            chat_id="oc_1",
            message_id="om_1",
            sender_id="user-1",
            text="继续",
            message_type="text",
            chat_type="p2p",
            image_keys=[],
        )
    )

    assert patched_topics == ["task-1"]
    assert done_calls == ["om_1"]


def test_feishu_adapter_approval_action_patches_error_when_approval_missing(monkeypatch) -> None:
    patched_cards: list[dict[str, Any]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(task_controller=SimpleNamespace(store=SimpleNamespace(get_approval=lambda approval_id: None)))

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )

    adapter._handle_approval_action("approval-1", "approve_once", "om_card_1")

    assert patched_cards[-1]["header"]["title"]["content"] == "处理失败"
    assert "approval-1" in patched_cards[-1]["body"]["elements"][0]["content"]


def test_feishu_adapter_approval_action_patches_error_when_task_missing(monkeypatch) -> None:
    patched_cards: list[dict[str, Any]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                get_approval=lambda approval_id: SimpleNamespace(approval_id=approval_id, task_id="task-1"),
                get_task=lambda task_id: None,
            )
        )
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )

    adapter._handle_approval_action("approval-1", "approve_once", "om_card_1")

    assert patched_cards[-1]["header"]["title"]["content"] == "处理失败"
    assert "task-1" in patched_cards[-1]["body"]["elements"][0]["content"]


def test_feishu_adapter_approval_action_patches_error_when_resolution_fails(monkeypatch) -> None:
    patched_cards: list[dict[str, Any]] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(locale="zh-CN"))
    adapter._client = object()
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(
            store=SimpleNamespace(
                get_approval=lambda approval_id: SimpleNamespace(approval_id=approval_id, task_id="task-1", requested_action={}),
                get_task=lambda task_id: SimpleNamespace(task_id=task_id, conversation_id="oc_1", source_channel="feishu"),
            )
        ),
        _resolve_approval=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.patch_card",
        lambda _client, _message_id, card: patched_cards.append(card),
    )

    adapter._handle_approval_action("approval-1", "approve_once", "om_card_1")

    assert patched_cards[-1]["header"]["title"]["content"] == "处理失败"


def test_feishu_adapter_card_action_response_normalizes_level_and_embeds_card() -> None:
    adapter = FeishuAdapter()

    response = adapter._card_action_response(
        "处理中",
        level="warn",
        card={"ok": True},
    )
    success_response = adapter._card_action_response("完成", level="success")

    assert response.toast is not None
    assert response.toast.type == "info"
    assert response.toast.content == "处理中"
    assert response.card is not None
    assert response.card.type == "raw"
    assert response.card.data == {"ok": True}

    assert success_response.toast is not None
    assert success_response.toast.type == "success"
    assert success_response.toast.content == "完成"
    assert success_response.card is None
