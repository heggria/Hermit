"""Tests for the Feishu adapter plugin normalize + AgentRunner integration."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hermit.builtin.feishu.normalize import FeishuMessage, normalize_event
from hermit.builtin.feishu.reply import build_approval_card, build_task_topic_card, make_tool_step
from hermit.core.runner import AgentRunner
from hermit.core.session import SessionManager
from hermit.core.tools import ToolRegistry, ToolSpec
from hermit.kernel.approvals import ApprovalService
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.controller import TaskController
from hermit.kernel.executor import ToolExecutor
from hermit.kernel.policy import PolicyEngine
from hermit.kernel.receipts import ReceiptService
from hermit.kernel.store import KernelStore
from hermit.plugin.manager import PluginManager
from hermit.provider.providers.claude import ClaudeProvider
from hermit.provider.runtime import AgentRuntime


@pytest.fixture(autouse=True)
def _force_feishu_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")


@dataclass
class FakeResponse:
    content: list
    stop_reason: str = "end_turn"


class FakeMessagesAPI:
    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(copy.deepcopy(kwargs))
        return FakeResponse(content=[{"type": "text", "text": self.answer}])


class FakeClient:
    def __init__(self, answer: str = "ok") -> None:
        self.messages = FakeMessagesAPI(answer)


def _make_event(chat_id: str, text: str, chat_type: str = "p2p") -> dict:
    return {
        "message": {
            "chat_id": chat_id,
            "message_id": f"om_{chat_id}",
            "content": json.dumps({"text": text}),
            "message_type": "text",
            "chat_type": chat_type,
        },
        "sender": {"sender_id": {"open_id": "user-1"}},
    }


# ---- normalize_event tests ----


def test_normalize_event_extracts_fields() -> None:
    event = _make_event("chat-1", "hello")
    msg = normalize_event(event)

    assert msg.chat_id == "chat-1"
    assert msg.text == "hello"
    assert msg.sender_id == "user-1"
    assert msg.message_type == "text"
    assert msg.chat_type == "p2p"
    assert msg.image_keys == []
    assert msg.reply_to_message_id == ""
    assert msg.quoted_message_id == ""


def test_normalize_event_strips_at_mention_in_group() -> None:
    event = _make_event("chat-g", "@_user_1 how are you", chat_type="group")
    msg = normalize_event(event)

    assert msg.chat_type == "group"
    assert msg.text == "how are you"


def test_normalize_event_handles_plain_text_content() -> None:
    event = {
        "message": {
            "chat_id": "c1",
            "message_id": "m1",
            "content": "plain text",
            "message_type": "text",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "plain text"


def test_normalize_event_extracts_reply_and_quote_message_ids() -> None:
    event = {
        "message": {
            "chat_id": "c1",
            "message_id": "m1",
            "content": json.dumps({"text": "继续这个"}),
            "message_type": "text",
            "reply_to_message_id": "om_parent",
            "quoted_message_id": "om_quote",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.reply_to_message_id == "om_parent"
    assert msg.quoted_message_id == "om_quote"


def test_normalize_event_empty_fields() -> None:
    msg = normalize_event({"message": {}, "sender": {}})
    assert msg.chat_id == ""
    assert msg.text == ""
    assert msg.image_keys == []


def test_normalize_event_extracts_image_key() -> None:
    event = {
        "message": {
            "chat_id": "chat-img",
            "message_id": "m-img",
            "content": json.dumps({"image_key": "img_v2_123"}),
            "message_type": "image",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.text == ""
    assert msg.message_type == "image"
    assert msg.image_keys == ["img_v2_123"]


def test_normalize_event_collects_nested_image_key_for_post() -> None:
    event = {
        "message": {
            "chat_id": "chat-post",
            "message_id": "m-post",
            "message_type": "post",
            "chat_type": "p2p",
            "content": json.dumps(
                {
                    "zh_cn": {
                        "title": "这是什么",
                        "content": [
                            [
                                {"tag": "at", "user_name": "ZClaw"},
                                {"tag": "text", "text": " 这个是啥"},
                            ],
                            [{"tag": "img", "image_key": "img_nested_1"}],
                        ],
                    }
                }
            ),
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.message_type == "post"
    assert msg.text == "这是什么\n@ZClaw这个是啥"
    assert msg.image_keys == ["img_nested_1"]


# ---- AgentRunner tests ----


def _make_runner(tmp_path, answer: str = "reply") -> tuple[AgentRunner, FakeClient]:
    client = FakeClient(answer=answer)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    agent = AgentRuntime(
        provider=ClaudeProvider(client, model="fake"),
        registry=ToolRegistry(),
        model="fake",
    )
    manager = SessionManager(tmp_path / "sessions", store=store)
    pm = PluginManager()
    runner = AgentRunner(agent, manager, pm, task_controller=TaskController(store))
    return runner, client


def test_runner_creates_session_and_returns_result(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="reply-1")
    result = runner.handle("chat-x", "hi")

    assert result.text == "reply-1"
    session = runner.session_manager.get_or_create("chat-x")
    assert len(session.messages) == 2


def test_runner_preserves_history_across_messages(tmp_path) -> None:
    runner, client = _make_runner(tmp_path, answer="turn-2")

    runner.handle("chat-y", "first")
    runner.handle("chat-y", "second")

    assert len(client.messages.calls) == 2
    second_call_messages = client.messages.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["user"]
    assert "<conversation_projection>" in second_call_messages[0]["content"]
    assert "<context_pack>" in second_call_messages[0]["content"]


def test_runner_isolates_sessions(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="response")

    runner.handle("chat-a", "msg-a")
    runner.handle("chat-b", "msg-b")

    session_a = runner.session_manager.get_or_create("chat-a")
    session_b = runner.session_manager.get_or_create("chat-b")
    assert session_a.messages != session_b.messages


def test_runner_reset_session(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="r1")

    runner.handle("s1", "hello")
    session_before = runner.session_manager.get_or_create("s1")
    assert len(session_before.messages) == 2

    runner.reset_session("s1")
    session_after = runner.session_manager.get_or_create("s1")
    assert len(session_after.messages) == 0


def test_runner_close_session(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="ok")

    runner.handle("s2", "hi")
    runner.close_session("s2")

    fresh = runner.session_manager.get_or_create("s2")
    assert len(fresh.messages) == 0


def test_build_task_topic_card_renders_current_phase_and_recent_milestones() -> None:
    card = build_task_topic_card(
        {
            "status": "running",
            "current_hint": "dev server 已就绪，下一步会继续 smoke test。",
            "current_phase": "ready",
            "current_progress_percent": 100,
            "items": [
                {
                    "kind": "tool.progressed",
                    "text": "Booting dev server",
                    "phase": "starting",
                    "progress_percent": 10,
                },
                {
                    "kind": "task.progress.summarized",
                    "text": "dev server 已就绪，下一步会继续 smoke test。\n服务已经可以访问。",
                    "phase": "ready",
                    "progress_percent": 100,
                },
            ],
        },
        title="Dev Task",
        locale="zh-CN",
    )

    elements = card["body"]["elements"]
    assert elements[0]["content"].startswith("**已就绪 · 100%**")
    assert "下一步会继续 smoke test" in elements[0]["content"]
    assert "服务已经可以访问" in elements[1]["content"]


def test_build_task_topic_card_hides_duplicate_terminal_summary_and_start_item() -> None:
    card = build_task_topic_card(
        {
            "status": "completed",
            "current_hint": "你好！有什么可以帮你的吗？",
            "current_phase": "completed",
            "current_progress_percent": 100,
            "items": [
                {"kind": "task.started", "text": "你好"},
                {
                    "kind": "task.completed",
                    "text": "你好！有什么可以帮你的吗？",
                    "phase": "completed",
                    "progress_percent": 100,
                },
            ],
        },
        title="Greeting",
        locale="zh-CN",
    )

    elements = card["body"]["elements"]
    assert len(elements) == 1
    assert elements[0]["content"].startswith("**已完成 · 100%**")
    assert "有什么可以帮你的吗" in elements[0]["content"]


def test_feishu_adapter_reads_hermit_env_names(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "app-secret")

    adapter = FeishuAdapter()

    assert adapter._app_id == "app-id"
    assert adapter._app_secret == "app-secret"


def test_feishu_adapter_reads_credentials_from_settings(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()

    assert adapter.required_skills == [
        "feishu-output-format",
        "feishu-emoji-reaction",
        "feishu-tools",
    ]


def test_feishu_adapter_builds_prompt_from_images(tmp_path) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

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
        "hermit.builtin.feishu.adapter.build_approval_card",
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
        "hermit.builtin.feishu.adapter.reply_card_return_id",
        lambda _client, _message_id, card: sent_cards.append(card) or "om_reply",
    )
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.smart_reply",
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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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
        "hermit.builtin.feishu.adapter.smart_reply", lambda *_a, **_kw: smart_calls.append("smart")
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
    from hermit.builtin.feishu.adapter import FeishuAdapter

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


def test_feishu_adapter_stop_shuts_down_background_resources(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    shutdown_called: list[bool] = []
    join_called: list[float] = []
    flush_called: list[bool] = []

    class FakeTimer:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[bool, bool]] = []

        def shutdown(self, wait: bool, cancel_futures: bool) -> None:
            self.calls.append((wait, cancel_futures))

    async def fake_shutdown_ws() -> None:
        shutdown_called.append(True)

    def fake_join_ws_thread(timeout_seconds: float = 2.0) -> None:
        join_called.append(timeout_seconds)

    def fake_flush_all_sessions() -> None:
        flush_called.append(True)

    timer = FakeTimer()
    executor = FakeExecutor()
    adapter._sweep_timer = timer
    adapter._executor = executor  # type: ignore[assignment]
    adapter._shutdown_ws = fake_shutdown_ws  # type: ignore[method-assign]
    adapter._join_ws_thread = fake_join_ws_thread  # type: ignore[method-assign]
    adapter._flush_all_sessions = fake_flush_all_sessions  # type: ignore[method-assign]

    asyncio.run(adapter.stop())

    assert adapter._stopped is True
    assert timer.cancelled is True
    assert adapter._sweep_timer is None
    assert shutdown_called == [True]
    assert executor.calls == [(False, True)]
    assert join_called == [2.0]
    assert flush_called == [True]


def test_feishu_adapter_start_raises_when_ws_thread_crashes() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    adapter._app_id = "app-id"
    adapter._app_secret = "app-secret"

    def fake_run_ws_client() -> None:
        adapter._ws_error = ValueError("boom")
        adapter._ws_exited.set()

    adapter._run_ws_client = fake_run_ws_client  # type: ignore[method-assign]

    try:
        asyncio.run(adapter.start(runner=object()))  # type: ignore[arg-type]
    except RuntimeError as exc:
        assert str(exc) == "Feishu adapter stopped unexpectedly"
        assert isinstance(exc.__cause__, ValueError)
    else:
        raise AssertionError("adapter.start() should propagate WebSocket thread failures")


def test_feishu_adapter_receive_loop_treats_normal_close_as_graceful(monkeypatch) -> None:
    import hermit.builtin.feishu.adapter as adapter_module
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    adapter._stopped = True
    error_logs: list[str] = []
    disconnect_calls: list[bool] = []
    reconnect_calls: list[bool] = []
    loop_tasks: list[str] = []
    info_logs: list[str] = []

    class FakeConnectionClosed(Exception):
        pass

    class FakeConn:
        async def recv(self) -> str:
            raise FakeConnectionClosed("sent 1000 (OK); then received 1000 (OK) bye")

    class FakeClient:
        def __init__(self) -> None:
            self._conn = FakeConn()
            self._auto_reconnect = False

        async def _disconnect(self) -> None:
            disconnect_calls.append(True)

        async def _reconnect(self) -> None:
            reconnect_calls.append(True)

        async def _handle_message(self, msg: str) -> None:
            loop_tasks.append(msg)

        def _fmt_log(self, template: str, exc: BaseException) -> str:
            return template.format(exc)

    class FakeLoop:
        def create_task(self, task: Any) -> Any:
            loop_tasks.append("created")
            return task

    class FakeLogger:
        def error(self, message: str) -> None:
            error_logs.append(message)

    class FakeClientClass:
        pass

    fake_module = SimpleNamespace(
        Client=FakeClientClass,
        loop=FakeLoop(),
        logger=FakeLogger(),
        ConnectionClosedException=FakeConnectionClosed,
    )

    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.log.info", lambda message: info_logs.append(message)
    )

    monkeypatch.setattr(adapter_module, "_LARK_RECEIVE_LOOP_PATCHED", False)
    adapter_module._patch_lark_receive_loop(fake_module)

    client = FakeClient()
    client._hermit_adapter_ref = adapter

    asyncio.run(fake_module.Client._receive_message_loop(client))

    assert disconnect_calls == [True]
    assert reconnect_calls == []
    assert error_logs == []
    assert any("closed cleanly" in message for message in info_logs)


def test_feishu_adapter_cancel_ws_receive_task_cancels_pending_task() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    cancelled: list[bool] = []

    async def fake_receive_loop() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def run() -> None:
        task = asyncio.create_task(fake_receive_loop())
        adapter._ws_client = SimpleNamespace(_hermit_receive_task=task)
        await asyncio.sleep(0)
        await adapter._cancel_ws_receive_task()
        assert task.cancelled()

    asyncio.run(run())

    assert cancelled == [True]


def test_feishu_adapter_connect_patch_tracks_receive_task_and_consumes_graceful_close(
    monkeypatch,
) -> None:
    import hermit.builtin.feishu.adapter as adapter_module

    info_logs: list[str] = []
    unhandled_contexts: list[dict[str, Any]] = []

    class FakeConnectionClosed(Exception):
        pass

    class FakeInvalidStatusCode(Exception):
        pass

    class FakeLogger:
        def info(self, _message: str) -> None:
            return None

    async def fake_connect(_url: str) -> object:
        return object()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled_contexts.append(context))

        try:
            fake_module = SimpleNamespace(
                Client=type("FakeClientClass", (), {}),
                loop=loop,
                logger=FakeLogger(),
                websockets=SimpleNamespace(
                    connect=fake_connect,
                    InvalidStatusCode=FakeInvalidStatusCode,
                ),
                _parse_ws_conn_exception=lambda exc: None,
                urlparse=lambda url: SimpleNamespace(query="device_id=dev-1&service_id=svc-1"),
                parse_qs=lambda query: {"device_id": ["dev-1"], "service_id": ["svc-1"]},
                DEVICE_ID="device_id",
                SERVICE_ID="service_id",
            )

            monkeypatch.setattr(adapter_module, "_LARK_CONNECT_PATCHED", False)
            monkeypatch.setattr(
                "hermit.builtin.feishu.adapter.log.info", lambda message: info_logs.append(message)
            )

            adapter_module._patch_lark_connect(fake_module)

            class FakeClient:
                def __init__(self) -> None:
                    self._lock = asyncio.Lock()
                    self._conn = None
                    self._conn_url = ""
                    self._conn_id = ""
                    self._service_id = ""
                    self._hermit_adapter_ref = SimpleNamespace(_stopped=True)

                def _get_conn_url(self) -> str:
                    return "wss://example.test/ws?device_id=dev-1&service_id=svc-1"

                def _fmt_log(self, template: str, value: object) -> str:
                    return template.format(value)

                async def _receive_message_loop(self) -> None:
                    raise FakeConnectionClosed("sent 1000 (OK); then received 1000 (OK) bye")

            client = FakeClient()
            client._connect = fake_module.Client._connect.__get__(client, type(client))

            await client._connect()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert client._hermit_receive_task.done()
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())

    assert unhandled_contexts == []
    assert any("receive task exception during shutdown" in message for message in info_logs)


# ── Emoji reaction tests ────────────────────────────────────────────────────


def test_add_reaction_returns_false_when_api_fails(monkeypatch) -> None:
    from hermit.builtin.feishu.reaction import add_reaction

    class FakeResp:
        def success(self):
            return False

        code = 99
        msg = "forbidden"

    class FakeReaction:
        def create(self, _):
            return FakeResp()

    class FakeIm:
        v1 = type("v1", (), {"message_reaction": FakeReaction()})()

    class FakeClient:
        im = FakeIm()

    result = add_reaction(FakeClient(), "om_123", "THUMBSUP")
    assert result is False


def test_build_prompt_injects_message_id() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.builtin.feishu.normalize import FeishuMessage

    adapter = FeishuAdapter()
    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_abc",
        sender_id="u1",
        text="你好",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )
    prompt = adapter._build_prompt("session-1", msg)
    assert "<feishu_msg_id>om_abc</feishu_msg_id>" in prompt
    assert "你好" in prompt


def test_build_prompt_without_message_id() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.builtin.feishu.normalize import FeishuMessage

    adapter = FeishuAdapter()
    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="",
        sender_id="u1",
        text="测试",
        message_type="text",
        chat_type="p2p",
        image_keys=[],
    )
    prompt = adapter._build_prompt("session-1", msg)
    assert "<feishu_msg_id>" not in prompt
    assert "<feishu_chat_id>oc_1</feishu_chat_id>" in prompt
    assert "测试" in prompt


def test_feishu_control_messages_bypass_prompt_wrapping() -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    task_controller = type(
        "TaskController",
        (),
        {
            "resolve_text_command": staticmethod(
                lambda _session_id, text: (
                    ("case", "task_123", "") if text in {"看看这个任务", "定时任务列表"} else None
                )
            )
        },
    )()
    adapter._runner = type("Runner", (), {"task_controller": task_controller})()

    assert adapter._should_dispatch_raw("oc_1", "批准 approval_123") is True
    assert adapter._should_dispatch_raw("oc_1", "开始执行") is True
    assert adapter._should_dispatch_raw("oc_1", "通过") is True
    assert adapter._should_dispatch_raw("oc_1", "批准") is True
    assert adapter._should_dispatch_raw("oc_1", "看看这个任务") is True
    assert adapter._should_dispatch_raw("oc_1", "定时任务列表") is True
    assert adapter._should_dispatch_raw("oc_1", "普通问题") is False


def test_feishu_react_tool_registered(monkeypatch) -> None:
    from hermit.builtin.feishu.hooks import register
    from hermit.core.tools import ToolRegistry
    from hermit.plugin.base import PluginContext
    from hermit.plugin.hooks import HooksEngine

    ctx = PluginContext(HooksEngine(), settings=None)
    register(ctx)
    registry = ToolRegistry()
    for tool in ctx.tools:
        registry.register(tool)
    assert registry.get("feishu_react") is not None


def test_feishu_react_tool_passes_through_emoji_type_and_calls_api(monkeypatch) -> None:
    from hermit.builtin.feishu import hooks as hooks_mod
    from hermit.builtin.feishu.hooks import register
    from hermit.core.tools import ToolRegistry
    from hermit.plugin.base import PluginContext
    from hermit.plugin.hooks import HooksEngine

    reactions: list[tuple[str, str]] = []

    def fake_add_reaction(client, message_id, emoji_type):
        reactions.append((message_id, emoji_type))
        return True

    monkeypatch.setattr(hooks_mod, "add_reaction", fake_add_reaction)

    class FakeClient:
        pass

    monkeypatch.setattr(hooks_mod, "build_lark_client", lambda: FakeClient())

    ctx = PluginContext(HooksEngine(), settings=None)
    register(ctx)
    registry = ToolRegistry()
    for tool in ctx.tools:
        registry.register(tool)

    result = registry.call("feishu_react", {"message_id": "om_xyz", "emoji_type": "get"})
    assert result["success"] is True
    assert result["emoji_type"] == "get"
    assert reactions == [("om_xyz", "get")]
