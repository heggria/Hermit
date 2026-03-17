# ruff: noqa: F403
from tests.fixtures.feishu_dispatcher_support import *


def test_add_reaction_returns_false_when_api_fails(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu.reaction import add_reaction

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
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter
    from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage

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
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter
    from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage

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
    from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter

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
    from hermit.plugins.builtin.adapters.feishu.hooks import register
    from hermit.runtime.capability.contracts.base import PluginContext
    from hermit.runtime.capability.contracts.hooks import HooksEngine
    from hermit.runtime.capability.registry.tools import ToolRegistry

    ctx = PluginContext(HooksEngine(), settings=None)
    register(ctx)
    registry = ToolRegistry()
    for tool in ctx.tools:
        registry.register(tool)
    assert registry.get("feishu_react") is not None


def test_feishu_react_tool_passes_through_emoji_type_and_calls_api(monkeypatch) -> None:
    from hermit.plugins.builtin.adapters.feishu import hooks as hooks_mod
    from hermit.plugins.builtin.adapters.feishu.hooks import register
    from hermit.runtime.capability.contracts.base import PluginContext
    from hermit.runtime.capability.contracts.hooks import HooksEngine
    from hermit.runtime.capability.registry.tools import ToolRegistry

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
