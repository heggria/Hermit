"""Tests for the Feishu adapter plugin normalize + AgentRunner integration."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List

from hermit.builtin.feishu.normalize import FeishuMessage, normalize_event
from hermit.core.agent import ClaudeAgent
from hermit.core.runner import AgentRunner
from hermit.core.session import SessionManager
from hermit.core.tools import ToolRegistry
from hermit.plugin.manager import PluginManager


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


def test_normalize_event_strips_at_mention_in_group() -> None:
    event = _make_event("chat-g", "@_user_1 how are you", chat_type="group")
    msg = normalize_event(event)

    assert msg.chat_type == "group"
    assert msg.text == "how are you"


def test_normalize_event_handles_plain_text_content() -> None:
    event = {
        "message": {"chat_id": "c1", "message_id": "m1", "content": "plain text", "message_type": "text"},
        "sender": {"sender_id": {"open_id": "u1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "plain text"


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
    agent = ClaudeAgent(client=client, registry=ToolRegistry(), model="fake")
    manager = SessionManager(tmp_path / "sessions")
    pm = PluginManager()
    runner = AgentRunner(agent, manager, pm)
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
    assert roles == ["user", "assistant", "user"]


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


def test_feishu_adapter_accepts_legacy_env_names(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.setenv("FEISHU_APP_ID", "legacy-app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "legacy-app-secret")

    adapter = FeishuAdapter()

    assert adapter._app_id == "legacy-app-id"
    assert adapter._app_secret == "legacy-app-secret"


def test_feishu_adapter_prefers_hermit_env_names(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "preferred-app-id")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "preferred-app-secret")
    monkeypatch.setenv("FEISHU_APP_ID", "legacy-app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "legacy-app-secret")

    adapter = FeishuAdapter()

    assert adapter._app_id == "preferred-app-id"
    assert adapter._app_secret == "preferred-app-secret"


def test_feishu_adapter_reads_credentials_from_settings(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    settings = type(
        "Settings",
        (),
        {"feishu_app_id": "settings-app-id", "feishu_app_secret": "settings-app-secret", "feishu_thread_progress": True},
    )()
    adapter = FeishuAdapter(settings=settings)

    assert adapter._app_id == "settings-app-id"
    assert adapter._app_secret == "settings-app-secret"


def test_feishu_adapter_builds_prompt_from_images(tmp_path) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter()
    runner, _ = _make_runner(tmp_path, answer="ok")

    def image_store(_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["image_key"] == "img_v2_123"
        return {
            "image_id": "abc123",
            "summary": "一张包含流程图的截图",
            "tags": ["流程图", "产品"],
        }

    runner.agent.registry.call = image_store  # type: ignore[method-assign]
    adapter._runner = runner

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


def test_feishu_adapter_replies_with_approval_card_for_blocked_result(monkeypatch) -> None:
    from hermit.builtin.feishu.adapter import FeishuAdapter
    from hermit.core.runner import DispatchResult

    sent_cards: list[dict[str, Any]] = []
    smart_calls: list[str] = []
    done_calls: list[str] = []

    adapter = FeishuAdapter(settings=SimpleNamespace(feishu_thread_progress=False))
    adapter._client = object()
    store = SimpleNamespace(
        get_approval=lambda approval_id: SimpleNamespace(
            approval_id=approval_id,
            requested_action={
                "tool_name": "read_skill",
                "tool_input": {"name": "computer-use"},
                "risk_level": "low",
                "approval_packet": {"title": "确认读取技能说明", "summary": "准备加载 computer-use 技能说明。"},
            },
        )
    )
    adapter._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store, resolve_text_command=lambda *_a, **_kw: None),
        dispatch=lambda **_: DispatchResult(
            text="准备加载 computer-use 技能说明（审批编号：approval_123）。请使用 `/task approve approval_123`，或直接回复“批准 approval_123”继续执行。",
            agent_result=SimpleNamespace(blocked=True, approval_id="approval_123"),
        )
    )

    monkeypatch.setattr("hermit.builtin.feishu.adapter.send_ack", lambda *_a, **_kw: None)
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
    monkeypatch.setattr(
        "hermit.builtin.feishu.adapter.send_done",
        lambda *_a, **_kw: done_calls.append("done"),
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

    assert sent_cards == [{
        "text": "准备加载 computer-use 技能说明。",
        "approval_id": "approval_123",
        "steps": 0,
        "title": "确认读取技能说明",
        "detail": "风险等级：low。请确认后继续执行。",
        "command_preview": None,
    }]
    assert smart_calls == []
    assert done_calls == ["done"]


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
        lambda hint: {"hint": hint},
    )

    event = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(value={"kind": "approval", "action": "approve", "approval_id": "approval_123"}),
            context=SimpleNamespace(open_message_id="om_card_1"),
        )
    )
    response = adapter._on_card_action(event)

    assert len(submitted) == 1
    assert submitted[0][0] == adapter._handle_approval_action
    assert submitted[0][1] == ("approval_123", "approve", "om_card_1")
    assert response.toast is not None
    assert response.toast.content == "已通过，正在继续执行。"


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

    adapter._reissue_pending_approval_cards()

    assert len(sent_cards) == 1
    chat_id, card = sent_cards[0]
    assert chat_id == "oc_chat"
    body = card["body"]["elements"]
    text_blocks = [element["content"] for element in body if element.get("tag") == "markdown"]
    assert any("准备删除本地文件或目录。" in block for block in text_blocks)
    assert any("旧审批卡片的按钮可能已失效" in block for block in text_blocks)


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


# ── Emoji reaction tests ────────────────────────────────────────────────────


def test_resolve_emoji_alias() -> None:
    from hermit.builtin.feishu.reaction import resolve_emoji

    assert resolve_emoji("thumbsup") == "THUMBSUP"
    assert resolve_emoji("congrats") == "CONGRATULATIONS"
    assert resolve_emoji("fire") == "FIRE"
    assert resolve_emoji("thinking") == "THINKING_FACE"
    assert resolve_emoji("done") == "OK"


def test_resolve_emoji_passthrough_raw_type() -> None:
    from hermit.builtin.feishu.reaction import resolve_emoji

    assert resolve_emoji("THUMBSUP") == "THUMBSUP"
    assert resolve_emoji("FIRE") == "FIRE"


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


def test_send_ack_disabled_by_env(monkeypatch) -> None:
    from hermit.builtin.feishu import reaction

    monkeypatch.setenv("HERMIT_FEISHU_REACTION_ENABLED", "false")
    called: list[str] = []
    monkeypatch.setattr(reaction, "add_reaction", lambda *_a, **_kw: called.append("called"))
    reaction.send_ack(object(), "om_123")
    assert called == []


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
    adapter._runner = type("Runner", (), {"task_controller": None})()

    assert adapter._should_dispatch_raw("oc_1", "批准 approval_123") is True
    assert adapter._should_dispatch_raw("oc_1", "开始执行") is True
    assert adapter._should_dispatch_raw("oc_1", "通过") is True
    assert adapter._should_dispatch_raw("oc_1", "批准") is True
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


def test_feishu_react_tool_resolves_alias_and_calls_api(monkeypatch) -> None:
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

    result = registry.call("feishu_react", {"message_id": "om_xyz", "emoji": "thumbsup"})
    assert result["success"] is True
    assert result["emoji_type"] == "THUMBSUP"
    assert reactions == [("om_xyz", "THUMBSUP")]
