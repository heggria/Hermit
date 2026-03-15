from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import hermit.core.runner as runner_module
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
        self.enqueued: list[dict] = []
        self.resumed_attempts: list[str] = []
        self.finalized: list[tuple[object, str]] = []
        self.blocked: list[object] = []
        self.focused: list[tuple[str, str]] = []

    def resolve_text_command(self, session_id: str, text: str):
        return self.resolution

    def source_from_session(self, session_id: str) -> str:
        return "chat"

    def start_task(self, **kwargs):
        ctx = SimpleNamespace(task_id="task", step_id="step", step_attempt_id="attempt")
        self.started.append({"kwargs": kwargs, "ctx": ctx})
        return ctx

    def enqueue_task(self, **kwargs):
        attempt_id = f"attempt-{len(self.enqueued) + 1}"
        ctx = SimpleNamespace(
            conversation_id=kwargs["conversation_id"],
            source_channel=kwargs["source_channel"],
            task_id=f"task-{len(self.enqueued) + 1}",
            step_id=f"step-{len(self.enqueued) + 1}",
            step_attempt_id=attempt_id,
            ingress_metadata=dict(kwargs.get("ingress_metadata", {}) or {}),
        )
        self.enqueued.append({"kwargs": kwargs, "ctx": ctx})
        return ctx

    def enqueue_resume(self, step_attempt_id: str):
        self.resumed_attempts.append(step_attempt_id)
        return SimpleNamespace(step_attempt_id=step_attempt_id)

    def ensure_conversation(self, *_args, **_kwargs) -> None:
        return None

    def finalize_result(
        self,
        ctx,
        status: str,
        result_preview: str | None = None,
        result_text: str | None = None,
    ) -> None:
        self.finalized.append((ctx, status, result_preview, result_text))

    def mark_blocked(self, ctx) -> None:
        self.blocked.append(ctx)

    def context_for_attempt(self, step_attempt_id: str):
        return SimpleNamespace(task_id="task", step_id="step", step_attempt_id=step_attempt_id)

    def focus_task(self, conversation_id: str, task_id: str) -> None:
        self.focused.append((conversation_id, task_id))


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
    assert controller.finalized[0][3] == "answer"
    assert plugin_manager.started == ["session"]
    assert plugin_manager.post_run == ["answer"]
    assert "<session_time>" in agent.run_calls[0]["prompt"]
    assert agent.run_calls[0]["disable_tools"] is True
    assert session_manager.saved >= 1

    assert (
        runner._result_status(
            AgentResult(
                text="[Execution Requires Attention] check",
                turns=1,
                tool_calls=0,
                execution_status="",
            )
        )
        == "needs_attention"
    )
    assert (
        runner._result_status(
            AgentResult(text="[API Error] boom", turns=1, tool_calls=0, execution_status="")
        )
        == "failed"
    )
    assert (
        runner._result_status(
            AgentResult(text="ok", turns=1, tool_calls=0, execution_status="custom")
        )
        == "custom"
    )


def test_runner_time_context_uses_current_message_time(monkeypatch) -> None:
    runner, agent, _session_manager, _plugin_manager, _controller = _make_runner()

    frozen_now = dt.datetime(2026, 3, 14, 22, 40, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    class _FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(runner_module.datetime, "datetime", _FrozenDateTime)

    runner.handle("session", "1分钟后提醒我喝水")

    prompt = agent.run_calls[0]["prompt"]
    assert "current_time=2026-03-14T22:40:00+08:00" in prompt
    assert "timezone=UTC+08:00" in prompt
    assert "relative_time_base=current_time" in prompt
    assert "session_started_at=" not in prompt


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

    denied = runner._resolve_approval(
        "session", action="deny", approval_id="approval-1", reason="nope"
    )
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
    approved = runner._resolve_approval(
        "session", action="approve_mutable_workspace", approval_id="approval-1"
    )
    assert approved.text == "approved"
    assert controller.store.resolved[-1]["resolution"]["mode"] == "mutable_workspace"
    assert controller.finalized[-1][1] == "failed"
    assert controller.finalized[-1][2] == "approved"
    assert controller.finalized[-1][3] == "approved"
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


def test_runner_enqueue_ingress_queues_async_task_and_wakes_dispatcher() -> None:
    runner, agent, _session_manager, plugin_manager, controller = _make_runner()
    wake_calls: list[str] = []
    runner._dispatch_service = SimpleNamespace(wake=lambda: wake_calls.append("wake"))

    ctx = runner.enqueue_ingress(
        "session",
        "hello",
        source_channel="webhook",
        notify={"feishu_chat_id": "oc_1"},
        source_ref="webhook/test",
        ingress_metadata={"webhook_route": "test"},
    )

    assert ctx.task_id == "task-1"
    queued = controller.enqueued[0]["kwargs"]
    assert queued["source_channel"] == "webhook"
    assert queued["kind"] == "plan"
    assert queued["policy_profile"] == "readonly"
    assert queued["source_ref"] == "webhook/test"
    assert queued["ingress_metadata"]["dispatch_mode"] == "async"
    assert queued["ingress_metadata"]["notify"] == {"feishu_chat_id": "oc_1"}
    assert queued["ingress_metadata"]["webhook_route"] == "test"
    assert "<session_time>" in queued["ingress_metadata"]["entry_prompt"]
    assert "processed:hello" in queued["ingress_metadata"]["entry_prompt"]
    assert queued["workspace_root"] == agent.workspace_root
    assert wake_calls == ["wake"]
    assert plugin_manager.started == ["session"]


def test_runner_enqueue_approval_resume_queues_resume_without_inline_resume() -> None:
    approval = SimpleNamespace(approval_id="approval-1", step_attempt_id="attempt-1")
    runner, agent, session_manager, _plugin_manager, controller = _make_runner(approval)

    result = runner.enqueue_approval_resume(
        "session", action="approve_once", approval_id="approval-1"
    )

    assert result.is_command is True
    assert "queued" in result.text.lower()
    assert controller.store.resolved[-1]["resolution"]["mode"] == "once"
    assert controller.resumed_attempts == ["attempt-1"]
    assert agent.resume_calls == []
    assert session_manager.saved >= 1


def test_runner_add_command_and_register_command_decorator() -> None:
    runner, *_ = _make_runner()

    @AgentRunner.register_command("/extra-test", "Extra command")
    def _extra(runner: AgentRunner, session_id: str, text: str) -> DispatchResult:
        return DispatchResult("extra", is_command=True)

    runner.add_command(
        "/plugin", lambda *_args: DispatchResult("plugin", is_command=True), "Plugin command"
    )

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


def test_runner_background_services_start_stop_and_wake(monkeypatch) -> None:
    runner, _agent, _session_manager, plugin_manager, _controller = _make_runner()
    started: list[str] = []
    stopped: list[str] = []
    wakes: list[str] = []

    class FakeObservationService:
        def __init__(self, _runner) -> None:
            started.append("obs_init")

        def start(self) -> None:
            started.append("obs_start")

        def stop(self) -> None:
            stopped.append("obs_stop")

    class FakeDispatchService:
        def __init__(self, _runner, *, worker_count: int) -> None:
            started.append(f"dispatch_init:{worker_count}")

        def start(self) -> None:
            started.append("dispatch_start")

        def stop(self) -> None:
            stopped.append("dispatch_stop")

        def wake(self) -> None:
            wakes.append("wake")

    monkeypatch.setattr(runner_module, "ObservationService", FakeObservationService)
    monkeypatch.setattr("hermit.kernel.dispatch.KernelDispatchService", FakeDispatchService)

    runner.start_background_services()
    runner.wake_dispatcher()
    runner.stop_background_services()

    assert started == ["obs_init", "obs_start", "dispatch_init:4", "dispatch_start"]
    assert wakes == ["wake"]
    assert stopped == ["dispatch_stop", "obs_stop"]
    assert runner._dispatch_service is None
    assert runner._observation_service is None


def test_runner_resume_attempt_handles_terminal_and_blocked_paths() -> None:
    approval = SimpleNamespace(approval_id="approval-1", step_attempt_id="attempt-1")
    runner, agent, _session_manager, plugin_manager, controller = _make_runner(approval)
    controller.resume_attempt = lambda step_attempt_id: SimpleNamespace(
        conversation_id="session",
        task_id="task",
        step_id="step",
        step_attempt_id=step_attempt_id,
    )

    agent.resume_result = AgentResult(
        text="resume ok",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": [{"type": "text", "text": "resume ok"}]}],
    )
    terminal = runner.resume_attempt("attempt-1")
    assert terminal.text == "resume ok"
    assert controller.finalized[-1][1] == "succeeded"
    assert plugin_manager.post_run[-1] == "resume ok"

    agent.resume_result = AgentResult(
        text="need approval",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
    )
    blocked = runner.resume_attempt("attempt-1")
    assert blocked.blocked is True
    assert controller.blocked


def test_runner_handle_respects_ingress_parent_override() -> None:
    runner, _agent, _session_manager, _plugin_manager, controller = _make_runner()

    controller.decide_ingress = lambda **_kwargs: SimpleNamespace(
        mode="start", intent="chat_only", parent_task_id=None
    )  # type: ignore[method-assign]

    runner.handle("chat-1", "你好")

    assert controller.started[-1]["kwargs"]["parent_task_id"] is None


def test_runner_handle_returns_pending_disambiguation_without_starting_task(monkeypatch) -> None:
    runner, _agent, _session_manager, _plugin_manager, controller = _make_runner()
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")

    controller.decide_ingress = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        mode="start",
        resolution="pending_disambiguation",
        candidates=[{"task_id": "task-1"}, {"task_id": "task-2"}],
    )

    result = runner.handle("chat-1", "这个改一下")

    assert result.execution_status == "pending_disambiguation"
    assert "切到任务 task-1" in result.text
    assert controller.started == []


def test_runner_dispatch_control_action_can_focus_task() -> None:
    runner, _agent, _session_manager, _plugin_manager, controller = _make_runner()

    result = runner._dispatch_control_action("chat-1", action="focus_task", target_id="task-2")

    assert result.is_command is True
    assert "task-2" in result.text
    assert controller.focused == [("chat-1", "task-2")]


def test_runner_dispatch_control_action_mentions_resolved_pending_ingress() -> None:
    runner, _agent, _session_manager, _plugin_manager, controller = _make_runner()

    def _focus_task(_conversation_id: str, _task_id: str):
        controller.focused.append((_conversation_id, _task_id))
        return SimpleNamespace(note_event_seq=12)

    controller.focus_task = _focus_task  # type: ignore[method-assign]

    result = runner._dispatch_control_action("chat-1", action="focus_task", target_id="task-9")

    assert result.is_command is True
    assert "task-9" in result.text
    assert "pending message was attached" in result.text
