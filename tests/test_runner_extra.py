from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.core.runner import AgentRunner, DispatchResult
from hermit.provider.runtime import AgentResult


@pytest.fixture(autouse=True)
def _force_runner_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


class _FakeSessionManager:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            messages=[{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}],
            created_at=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read_tokens=0,
            total_cache_creation_tokens=0,
        )
        self.saved = 0
        self.closed: list[str] = []

    def get_or_create(self, _session_id: str):
        return self.session

    def save(self, session) -> None:
        self.session = session
        self.saved += 1

    def close(self, session_id: str) -> None:
        self.closed.append(session_id)


class _FakePluginManager:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.ended: list[tuple[str, list]] = []
        self.post_run: list[str] = []

    def on_session_start(self, session_id: str) -> None:
        self.started.append(session_id)

    def on_session_end(self, session_id: str, messages: list) -> None:
        self.ended.append((session_id, messages))

    def on_pre_run(self, text: str, **kwargs):
        return f"processed:{text}", {"readonly_only": True, "disable_tools": True}

    def on_post_run(self, result, **kwargs) -> None:
        self.post_run.append(result.text)


class _FakeAgent:
    def __init__(self) -> None:
        self.workspace_root = "/tmp/workspace"
        self.run_calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.run_result = AgentResult(
            text="answer",
            turns=1,
            tool_calls=0,
            messages=[{"role": "assistant", "content": [{"type": "text", "text": "answer"}]}],
            input_tokens=2,
            output_tokens=3,
        )
        self.resume_result = AgentResult(
            text="resumed",
            turns=1,
            tool_calls=0,
            messages=[{"role": "assistant", "content": [{"type": "text", "text": "resumed"}]}],
            input_tokens=1,
            output_tokens=1,
        )

    def run(self, prompt: str, **kwargs):
        self.run_calls.append({"prompt": prompt, **kwargs})
        return self.run_result

    def resume(self, **kwargs):
        self.resume_calls.append(kwargs)
        return self.resume_result


class _FakeStore:
    def __init__(self, approval=None) -> None:
        self.approval = approval
        self.resolved: list[dict] = []

    def get_approval(self, approval_id: str):
        return self.approval if self.approval and approval_id == self.approval.approval_id else None

    def resolve_approval(self, approval_id: str, **kwargs) -> None:
        self.resolved.append({"approval_id": approval_id, **kwargs})


class _FakeTaskController:
    def __init__(self, approval=None) -> None:
        self.store = _FakeStore(approval)
        self.resolution = None
        self.started: list[dict] = []
        self.finalized: list[tuple[object, str]] = []
        self.blocked: list[object] = []

    def resolve_text_command(self, session_id: str, text: str):
        return self.resolution

    def source_from_session(self, session_id: str) -> str:
        return "chat"

    def start_task(self, **kwargs):
        ctx = SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt")
        self.started.append({"kwargs": kwargs, "ctx": ctx})
        return ctx

    def finalize_result(self, ctx, status: str, result_preview: str | None = None) -> None:
        self.finalized.append((ctx, status, result_preview))

    def mark_blocked(self, ctx) -> None:
        self.blocked.append(ctx)

    def context_for_attempt(self, step_attempt_id: str):
        return SimpleNamespace(task_id="task", step_id="step", step_attempt_id=step_attempt_id)


def _make_runner(approval=None):
    agent = _FakeAgent()
    session_manager = _FakeSessionManager()
    plugin_manager = _FakePluginManager()
    controller = _FakeTaskController(approval)
    runner = AgentRunner(agent, session_manager, plugin_manager, task_controller=controller)
    return runner, agent, session_manager, plugin_manager, controller


def test_runner_dispatch_commands_help_history_task_and_unknown() -> None:
    runner, _agent, session_manager, _pm, _controller = _make_runner()

    unknown = runner.dispatch("session", "/missing")
    history = runner.dispatch("session", "/history")
    help_result = runner.dispatch("session", "/help")
    task_usage = runner.dispatch("session", "/task approve")
    quit_result = runner.dispatch("session", "/quit")
    new_result = runner.dispatch("session", "/new")

    assert unknown.is_command is True and "Unknown command" in unknown.text
    assert "1 user turns, 2 messages total" in history.text
    assert "`/help`" in help_result.text and "`/quit`" in help_result.text
    assert "Usage:" in task_usage.text
    assert quit_result.should_exit is True
    assert new_result.text == "Started a new session."
    assert session_manager.closed == ["session"]


def test_runner_handle_and_status_paths() -> None:
    runner, agent, session_manager, plugin_manager, controller = _make_runner()

    result = runner.dispatch("session", "hello")

    assert result.text == "answer"
    assert controller.started[0]["kwargs"]["policy_profile"] == "readonly"
    assert controller.finalized[0][1] == "succeeded"
    assert controller.finalized[0][2] == "answer"
    assert plugin_manager.started == ["session"]
    assert plugin_manager.post_run == ["answer"]
    assert "<session_time>" in agent.run_calls[0]["prompt"]
    assert agent.run_calls[0]["disable_tools"] is True
    assert session_manager.saved >= 1

    assert runner._result_status(
        AgentResult(text="[Execution Requires Attention] check", turns=1, tool_calls=0, execution_status="")
    ) == "needs_attention"
    assert runner._result_status(
        AgentResult(text="[API Error] boom", turns=1, tool_calls=0, execution_status="")
    ) == "failed"
    assert runner._result_status(AgentResult(text="ok", turns=1, tool_calls=0, execution_status="custom")) == "custom"


def test_runner_uses_clean_task_goal_for_kernel_records() -> None:
    runner, _agent, _session_manager, _plugin_manager, controller = _make_runner()

    runner.handle(
        "session",
        "<feishu_msg_id>om_1</feishu_msg_id>\n<session_time>ts</session_time>\n查询一下北京天气",
    )

    assert controller.started[0]["kwargs"]["goal"] == "查询一下北京天气"


def test_runner_resolve_approval_handles_missing_deny_and_approve_paths() -> None:
    missing_runner, *_ = _make_runner()
    missing = missing_runner._resolve_approval("session", action="approve", approval_id="missing")
    assert missing.text == "Approval not found: missing"

    approval = SimpleNamespace(approval_id="approval-1", step_attempt_id="attempt-1")
    runner, agent, session_manager, plugin_manager, controller = _make_runner(approval)

    denied = runner._resolve_approval("session", action="deny", approval_id="approval-1", reason="nope")
    assert denied.is_command is True
    assert "This approval was denied" in denied.text
    assert controller.store.resolved[0]["resolution"]["reason"] == "nope"

    agent.resume_result = AgentResult(
        text="approved",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": [{"type": "text", "text": "approved"}]}],
        execution_status="failed",
    )
    approved = runner._resolve_approval("session", action="approve_always_directory", approval_id="approval-1")
    assert approved.text == "approved"
    assert controller.store.resolved[-1]["resolution"]["mode"] == "always_directory"
    assert controller.finalized[-1][1] == "failed"
    assert controller.finalized[-1][2] == "approved"
    assert plugin_manager.post_run[-1] == "approved"

    agent.resume_result = AgentResult(
        text="blocked",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
    )
    runner._resolve_approval("session", action="approve", approval_id="approval-1")
    assert controller.blocked


def test_runner_add_command_and_register_command_decorator() -> None:
    runner, *_ = _make_runner()

    @AgentRunner.register_command("/extra-test", "Extra command")
    def _extra(runner: AgentRunner, session_id: str, text: str) -> DispatchResult:
        return DispatchResult("extra", is_command=True)

    runner.add_command("/plugin", lambda *_args: DispatchResult("plugin", is_command=True), "Plugin command")

    assert runner.dispatch("session", "/plugin").text == "plugin"
    fresh_runner, *_ = _make_runner()
    assert fresh_runner.dispatch("session", "/extra-test").text == "extra"


def test_runner_messages_can_render_zh_cn(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    runner, *_ = _make_runner()

    unknown = runner.dispatch("session", "/missing")
    history = runner.dispatch("session", "/history")
    task_usage = runner.dispatch("session", "/task approve")
    new_result = runner.dispatch("session", "/new")

    assert "未知命令" in unknown.text
    assert "1 轮用户消息，共 2 条记录" in history.text
    assert "用法：" in task_usage.text
    assert new_result.text == "已开启新会话。"
