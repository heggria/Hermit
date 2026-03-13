from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.core.runner import AgentRunner, DispatchResult, _result_preview, _strip_internal_markup
from hermit.provider.runtime import AgentResult


@pytest.fixture(autouse=True)
def _force_runner_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


class _SessionManager:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            messages=[{"role": "user", "content": "hello"}],
            created_at=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read_tokens=0,
            total_cache_creation_tokens=0,
        )
        self.saved = 0

    def get_or_create(self, _session_id: str):
        return self.session

    def save(self, session) -> None:
        self.session = session
        self.saved += 1

    def close(self, _session_id: str) -> None:
        return None


class _Hooks:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fire(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


class _PluginManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.settings = SimpleNamespace(base_dir=str(base_dir) if base_dir else "/tmp/hermit", kernel_dispatch_worker_count=2)
        self.hooks = _Hooks()
        self.started: list[str] = []
        self.post_run: list[str] = []
        self.ended: list[tuple[str, list[dict[str, object]]]] = []

    def on_session_start(self, session_id: str) -> None:
        self.started.append(session_id)

    def on_session_end(self, session_id: str, messages: list[dict[str, object]]) -> None:
        self.ended.append((session_id, messages))

    def on_pre_run(self, text: str, **kwargs):
        return f"prepared:{text}", {"readonly_only": True, "disable_tools": True}

    def on_post_run(self, result, **kwargs) -> None:
        self.post_run.append(result.text)


class _Agent:
    def __init__(self) -> None:
        self.workspace_root = "/tmp/workspace"
        self.run_calls: list[dict[str, object]] = []
        self.resume_calls: list[dict[str, object]] = []
        self.run_result = AgentResult(
            text="done",
            turns=1,
            tool_calls=1,
            messages=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
        )
        self.resume_result = AgentResult(
            text="resumed",
            turns=1,
            tool_calls=0,
            messages=[{"role": "assistant", "content": [{"type": "text", "text": "resumed"}]}],
        )

    def run(self, prompt: str, **kwargs):
        self.run_calls.append({"prompt": prompt, **kwargs})
        result = self.run_result
        if isinstance(result, Exception):
            raise result
        return result

    def resume(self, **kwargs):
        self.resume_calls.append(kwargs)
        result = self.resume_result
        if isinstance(result, Exception):
            raise result
        return result


class _Store:
    def __init__(self) -> None:
        self.approvals: dict[str, object] = {}
        self.resolved: list[tuple[str, dict[str, object]]] = []
        self.appended_history: list[object] = []
        self.schedule_history: list[object] = []
        self.step_attempt = SimpleNamespace(context={"execution_mode": "run"})

    def get_approval(self, approval_id: str):
        return self.approvals.get(approval_id)

    def resolve_approval(self, approval_id: str, **kwargs) -> None:
        self.resolved.append((approval_id, kwargs))

    def get_step_attempt(self, _step_attempt_id: str):
        return self.step_attempt

    def append_schedule_history(self, record) -> None:
        self.appended_history.append(record)
        self.schedule_history.insert(0, record)

    def list_schedule_history(self, **kwargs):
        return list(self.schedule_history)


class _TaskController:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.enqueued: list[dict[str, object]] = []
        self.resumed: list[str] = []
        self.finalized: list[tuple[str, str, str | None, str | None]] = []
        self.suspended: list[tuple[str, str]] = []
        self.ensure_calls: list[tuple[str, str]] = []
        self.decisions: list[tuple[str, str, str, str | None]] = []

    def ensure_conversation(self, conversation_id: str, *, source_channel: str | None = None) -> None:
        self.ensure_calls.append((conversation_id, source_channel or ""))

    def source_from_session(self, session_id: str) -> str:
        return "feishu" if session_id.startswith("oc_") else "chat"

    def enqueue_task(self, **kwargs):
        self.enqueued.append(kwargs)
        return SimpleNamespace(task_id="task-1", step_id="step-1", step_attempt_id="attempt-1")

    def enqueue_resume(self, step_attempt_id: str):
        self.resumed.append(step_attempt_id)
        return SimpleNamespace(step_attempt_id=step_attempt_id)

    def context_for_attempt(self, step_attempt_id: str):
        return SimpleNamespace(
            conversation_id="oc_1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id=step_attempt_id,
            source_channel="scheduler",
            ingress_metadata={
                "notify": {"feishu": True},
                "source_ref": "schedule:job-1",
                "title": "Nightly summary",
                "schedule_job_id": "job-1",
                "schedule_job_name": "Nightly summary",
            },
        )

    def finalize_result(self, ctx, *, status: str, result_preview: str | None = None, result_text: str | None = None) -> None:
        self.finalized.append((ctx.step_attempt_id, status, result_preview, result_text))

    def mark_suspended(self, ctx, *, waiting_kind: str) -> None:
        self.suspended.append((ctx.step_attempt_id, waiting_kind))

    def resolve_text_command(self, session_id: str, text: str):
        return None

    def decide_ingress(self, *, conversation_id: str, source_channel: str, raw_text: str, prompt: str, requested_by: str | None = "user"):
        self.decisions.append((conversation_id, source_channel, raw_text, requested_by))
        return SimpleNamespace(mode="start")

    def start_task(self, **kwargs):
        return SimpleNamespace(task_id="task-run", step_id="step-run", step_attempt_id="attempt-run")


def _make_runner(base_dir: Path | None = None):
    store = _Store()
    controller = _TaskController(store)
    agent = _Agent()
    session_manager = _SessionManager()
    pm = _PluginManager(base_dir)
    runner = AgentRunner(agent, session_manager, pm, task_controller=controller)
    return runner, agent, session_manager, pm, controller, store


def test_runner_start_stop_background_services_and_wake(monkeypatch) -> None:
    runner, _agent, _session_manager, pm, _controller, _store = _make_runner()

    observation_calls: list[str] = []
    dispatch_calls: list[tuple[str, int | None]] = []

    class FakeObservationService:
        def __init__(self, attached_runner) -> None:
            assert attached_runner is runner

        def start(self) -> None:
            observation_calls.append("start")

        def stop(self) -> None:
            observation_calls.append("stop")

    class FakeDispatchService:
        def __init__(self, attached_runner, worker_count: int = 4) -> None:
            assert attached_runner is runner
            dispatch_calls.append(("init", worker_count))

        def start(self) -> None:
            dispatch_calls.append(("start", None))

        def stop(self) -> None:
            dispatch_calls.append(("stop", None))

        def wake(self) -> None:
            dispatch_calls.append(("wake", None))

    monkeypatch.setattr("hermit.core.runner.ObservationService", FakeObservationService)
    monkeypatch.setattr("hermit.kernel.dispatch.KernelDispatchService", FakeDispatchService)

    runner.start_background_services()
    runner.start_background_services()
    runner.wake_dispatcher()
    runner.stop_background_services()

    assert observation_calls == ["start", "start", "stop"]
    assert dispatch_calls == [("init", 2), ("start", None), ("wake", None), ("stop", None)]


def test_runner_enqueue_ingress_sets_async_metadata_and_wakes() -> None:
    runner, agent, _session_manager, _pm, controller, _store = _make_runner()
    wake_calls: list[str] = []
    runner.wake_dispatcher = lambda: wake_calls.append("wake")  # type: ignore[method-assign]

    ctx = runner.enqueue_ingress(
        "oc_1",
        "整理今天的站会结论",
        notify={"feishu": True},
        source_ref="feishu:oc_1:om_1",
        requested_by="user-1",
    )

    assert ctx.task_id == "task-1"
    enqueued = controller.enqueued[-1]
    metadata = enqueued["ingress_metadata"]
    assert enqueued["conversation_id"] == "oc_1"
    assert enqueued["source_channel"] == "feishu"
    assert enqueued["kind"] == "plan"
    assert enqueued["policy_profile"] == "readonly"
    assert enqueued["workspace_root"] == agent.workspace_root
    assert metadata["dispatch_mode"] == "async"
    assert metadata["notify"] == {"feishu": True}
    assert metadata["source_ref"] == "feishu:oc_1:om_1"
    assert metadata["disable_tools"] is True
    assert metadata["readonly_only"] is True
    assert "prepared:整理今天的站会结论" in metadata["entry_prompt"]
    assert wake_calls == ["wake"]


def test_runner_enqueue_approval_resume_handles_missing_deny_and_grant() -> None:
    runner, _agent, session_manager, _pm, controller, store = _make_runner()

    missing = runner.enqueue_approval_resume("oc_1", action="approve_once", approval_id="missing")
    assert missing.is_command is True
    assert "Approval not found" in missing.text

    approval = SimpleNamespace(approval_id="approval-1", step_attempt_id="attempt-1")
    store.approvals["approval-1"] = approval
    wake_calls: list[str] = []
    runner.wake_dispatcher = lambda: wake_calls.append("wake")  # type: ignore[method-assign]

    denied = runner.enqueue_approval_resume("oc_1", action="deny", approval_id="approval-1", reason="not-safe")
    granted = runner.enqueue_approval_resume("oc_1", action="approve_always_directory", approval_id="approval-1")

    assert denied.is_command is True
    assert "denied" in denied.text.lower()
    assert granted.is_command is True
    assert "queued to resume" in granted.text
    assert store.resolved == [
        (
            "approval-1",
            {
                "status": "denied",
                "resolved_by": "user",
                "resolution": {"status": "denied", "mode": "denied", "reason": "not-safe"},
            },
        ),
        (
            "approval-1",
            {
                "status": "granted",
                "resolved_by": "user",
                "resolution": {"status": "granted", "mode": "always_directory"},
            },
        ),
    ]
    assert controller.resumed == ["attempt-1"]
    assert wake_calls == ["wake"]
    assert session_manager.saved >= 2


def test_runner_process_claimed_attempt_emits_notify_and_scheduler_artifacts(tmp_path: Path) -> None:
    runner, agent, session_manager, pm, controller, store = _make_runner(tmp_path)
    store.step_attempt = SimpleNamespace(context={"execution_mode": "run"})

    result = runner.process_claimed_attempt("attempt-1")

    assert result.text == "done"
    assert controller.finalized == [("attempt-1", "succeeded", "done", "done")]
    assert pm.post_run == ["done"]
    assert pm.hooks.calls and pm.hooks.calls[0][0][0] == "dispatch_result"
    assert pm.hooks.calls[0][1]["success"] is True
    assert store.appended_history and store.appended_history[0].job_id == "job-1"

    history_path = tmp_path / "schedules" / "history.json"
    assert history_path.exists()
    records = json.loads(history_path.read_text(encoding="utf-8"))
    assert records["records"][0]["job_id"] == "job-1"

    log_dir = tmp_path / "schedules" / "logs"
    log_files = list(log_dir.glob("*.log"))
    assert log_files
    assert "Job: Nightly summary (job-1)" in log_files[0].read_text(encoding="utf-8")
    assert session_manager.saved >= 1
    assert agent.run_calls and agent.run_calls[0]["readonly_only"] is False


def test_runner_process_claimed_attempt_resume_and_error_paths() -> None:
    runner, agent, _session_manager, pm, controller, store = _make_runner()

    store.step_attempt = SimpleNamespace(context={"execution_mode": "resume"})
    agent.resume_result = AgentResult(
        text="waiting",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
        waiting_kind="awaiting_input",
    )
    resumed = runner.process_claimed_attempt("attempt-2")

    assert resumed.blocked is True
    assert controller.suspended == [("attempt-2", "awaiting_input")]
    assert pm.post_run == []

    store.step_attempt = SimpleNamespace(context={"execution_mode": "run"})
    agent.run_result = RuntimeError("boom")
    errored = runner.process_claimed_attempt("attempt-3")

    assert errored.execution_status == "failed"
    assert controller.finalized[-1][0] == "attempt-3"
    assert controller.finalized[-1][1] == "failed"
    assert controller.finalized[-1][2].startswith("[API Error]")


def test_runner_dispatch_control_action_covers_kernel_introspection_paths(monkeypatch) -> None:
    runner, _agent, _session_manager, _pm, _controller, _store = _make_runner()

    class TaskRecord:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            self.status = "completed"

    class ReceiptRecord:
        def __init__(self, receipt_id: str) -> None:
            self.receipt_id = receipt_id

    grant = SimpleNamespace(grant_id="grant-1", status="active")
    job = SimpleNamespace(id="job-1", to_dict=lambda: {"id": "job-1", "enabled": True})
    record = SimpleNamespace(to_dict=lambda: {"job_id": "job-1", "success": True})
    store = SimpleNamespace(
        list_tasks=lambda limit=20: [TaskRecord("task-1")],
        list_events=lambda task_id, limit=100: [{"event_type": "task.completed"}],
        list_receipts=lambda task_id, limit=50: [ReceiptRecord("receipt-1")],
        list_path_grants=lambda **kwargs: [grant],
        get_path_grant=lambda grant_id: grant if grant_id == "grant-1" else None,
        update_path_grant=lambda *args, **kwargs: None,
        list_schedules=lambda: [job],
        list_schedule_history=lambda **kwargs: [record],
        update_schedule=lambda job_id, enabled: job if job_id == "job-1" else None,
        delete_schedule=lambda job_id: job_id == "job-1",
    )
    runner.agent.kernel_store = store  # type: ignore[attr-defined]

    class FakeSupervisionService:
        def __init__(self, attached_store) -> None:
            assert attached_store is store

        def build_task_case(self, target_id: str):
            return {"task_id": target_id, "case": True}

    class FakeProofService:
        def __init__(self, attached_store) -> None:
            assert attached_store is store

        def build_proof_summary(self, target_id: str):
            return {"task_id": target_id, "proof": True}

        def export_task_proof(self, target_id: str):
            return {"task_id": target_id, "exported": True}

    class FakeRollbackService:
        def __init__(self, attached_store) -> None:
            assert attached_store is store

        def execute(self, target_id: str):
            return {"rollback": target_id}

    class FakeProjectionService:
        def __init__(self, attached_store) -> None:
            assert attached_store is store

        def rebuild_task(self, target_id: str):
            return {"task_id": target_id, "rebuilt": True}

        def rebuild_all(self):
            return {"rebuilt_all": True}

    monkeypatch.setattr("hermit.kernel.supervision.SupervisionService", FakeSupervisionService)
    monkeypatch.setattr("hermit.kernel.proofs.ProofService", FakeProofService)
    monkeypatch.setattr("hermit.kernel.rollbacks.RollbackService", FakeRollbackService)
    monkeypatch.setattr("hermit.kernel.projections.ProjectionService", FakeProjectionService)

    assert '"task_id": "task-1"' in runner._dispatch_control_action("oc_1", action="task_list", target_id="").text
    assert '"case": true' in runner._dispatch_control_action("oc_1", action="case", target_id="task-1").text
    assert '"event_type": "task.completed"' in runner._dispatch_control_action("oc_1", action="task_events", target_id="task-1").text
    assert '"receipt_id": "receipt-1"' in runner._dispatch_control_action("oc_1", action="task_receipts", target_id="task-1").text
    assert '"proof": true' in runner._dispatch_control_action("oc_1", action="task_proof", target_id="task-1").text
    assert '"exported": true' in runner._dispatch_control_action("oc_1", action="task_proof_export", target_id="task-1").text
    assert '"rollback": "task-1"' in runner._dispatch_control_action("oc_1", action="rollback", target_id="task-1").text
    assert '"rebuilt": true' in runner._dispatch_control_action("oc_1", action="projection_rebuild", target_id="task-1").text
    assert '"rebuilt_all": true' in runner._dispatch_control_action("oc_1", action="projection_rebuild_all", target_id="").text
    assert '"grant_id": "grant-1"' in runner._dispatch_control_action("oc_1", action="grant_list", target_id="").text
    assert "Revoked grant" in runner._dispatch_control_action("oc_1", action="grant_revoke", target_id="grant-1").text
    assert '"id": "job-1"' in runner._dispatch_control_action("oc_1", action="schedule_list", target_id="").text
    assert '"success": true' in runner._dispatch_control_action("oc_1", action="schedule_history", target_id="job-1").text
    assert "Enabled" in runner._dispatch_control_action("oc_1", action="schedule_enable", target_id="job-1").text
    assert "Disabled" in runner._dispatch_control_action("oc_1", action="schedule_disable", target_id="job-1").text
    assert "Removed" in runner._dispatch_control_action("oc_1", action="schedule_remove", target_id="job-1").text
    assert "not found" in runner._dispatch_control_action("oc_1", action="grant_revoke", target_id="missing").text.lower()
    assert "Unsupported control action" in runner._dispatch_control_action("oc_1", action="mystery", target_id="").text

    runner.agent.kernel_store = None  # type: ignore[attr-defined]
    unavailable = runner._dispatch_control_action("oc_1", action="task_list", target_id="")
    assert unavailable.is_command is True
    assert "Task kernel" in unavailable.text and "available" in unavailable.text

    runner.reset_session("oc_1")
    help_result = runner._dispatch_control_action("oc_1", action="show_help", target_id="")
    history_result = runner._dispatch_control_action("oc_1", action="show_history", target_id="")
    new_session_result = runner._dispatch_control_action("oc_1", action="new_session", target_id="")

    assert help_result.is_command is True and "/help" in help_result.text
    assert history_result.is_command is True and "user turns" in history_result.text
    assert new_session_result.is_command is True and "Started a new session." in new_session_result.text


def test_runner_close_session_and_resume_attempt_cover_finalize_and_blocked_fallback() -> None:
    runner, agent, session_manager, pm, controller, store = _make_runner()
    runner._session_started.add("oc_1")

    runner.close_session("oc_1")
    assert pm.ended and pm.ended[0][0] == "oc_1"
    assert "oc_1" not in runner._session_started

    agent.resume_result = AgentResult(
        text="resume ok",
        turns=1,
        tool_calls=0,
        messages=[{"role": "assistant", "content": [{"type": "text", "text": "resume ok"}]}],
    )
    resumed = runner.resume_attempt("attempt-1")
    assert resumed.text == "resume ok"
    assert controller.finalized[-1] == ("attempt-1", "succeeded", "resume ok", "resume ok")
    assert pm.post_run[-1] == "resume ok"

    class FallbackController:
        def __init__(self, store: _Store) -> None:
            self.store = store
            self.blocked: list[str] = []

        def resume_attempt(self, step_attempt_id: str):
            return SimpleNamespace(
                conversation_id="oc_1",
                task_id="task-1",
                step_id="step-1",
                step_attempt_id=step_attempt_id,
            )

        def mark_blocked(self, ctx) -> None:
            self.blocked.append(ctx.step_attempt_id)

    fallback_store = _Store()
    fallback_controller = FallbackController(fallback_store)
    fallback_runner = AgentRunner(_Agent(), _SessionManager(), _PluginManager(), task_controller=fallback_controller)  # type: ignore[arg-type]
    fallback_runner.agent.resume_result = AgentResult(
        text="wait",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
        waiting_kind="",
    )
    blocked = fallback_runner.resume_attempt("attempt-2")
    assert blocked.blocked is True
    assert fallback_controller.blocked == ["attempt-2"]


def test_runner_async_helpers_return_early_without_notify_or_schedule(tmp_path: Path) -> None:
    runner, _agent, _session_manager, pm, _controller, _store = _make_runner(tmp_path)
    task_ctx = SimpleNamespace(
        task_id="task-1",
        step_attempt_id="attempt-1",
        source_channel="scheduler",
        ingress_metadata={},
    )
    result = AgentResult(text="noop", turns=1, tool_calls=0, messages=[])

    runner._emit_async_dispatch_result(task_ctx, result, started_at=1.0)
    runner._record_scheduler_execution(task_ctx, result, started_at=1.0)

    assert pm.hooks.calls == []


def test_runner_helper_functions_and_constructor_guard() -> None:
    assert _strip_internal_markup("") == ""
    assert _strip_internal_markup("<session_time>x</session_time>\n<feishu_msg_id>om_1</feishu_msg_id>\nHello") == "Hello"
    assert _result_preview("") == ""
    assert _result_preview("word " * 100, limit=12).endswith("…")

    with pytest.raises(ValueError):
        AgentRunner(_Agent(), _SessionManager(), _PluginManager(), task_controller=None)


def test_runner_handle_marks_blocked_without_mark_suspended_and_respects_kernel_managed() -> None:
    class BlockOnlyController:
        def __init__(self) -> None:
            self.blocked: list[str] = []

        def source_from_session(self, session_id: str) -> str:
            return "chat"

        def ensure_conversation(self, conversation_id: str, *, source_channel: str | None = None) -> None:
            return None

        def resolve_text_command(self, session_id: str, text: str):
            return None

        def decide_ingress(self, **kwargs):
            return SimpleNamespace(mode="start")

        def start_task(self, **kwargs):
            return SimpleNamespace(task_id="task-1", step_id="step-1", step_attempt_id="attempt-1")

        def mark_blocked(self, ctx) -> None:
            self.blocked.append(ctx.step_attempt_id)

    controller = BlockOnlyController()
    runner = AgentRunner(_Agent(), _SessionManager(), _PluginManager(), task_controller=controller)  # type: ignore[arg-type]

    runner.agent.run_result = AgentResult(text="blocked", turns=1, tool_calls=0, messages=[], blocked=True)
    blocked = runner.handle("chat-1", "Need approval")
    assert blocked.blocked is True
    assert controller.blocked == ["attempt-1"]

    runner.agent.run_result = AgentResult(
        text="still blocked",
        turns=1,
        tool_calls=0,
        messages=[],
        blocked=True,
        status_managed_by_kernel=True,
    )
    kernel_managed = runner.handle("chat-1", "Need approval")
    assert kernel_managed.status_managed_by_kernel is True


def test_runner_prepare_prompt_context_sanitizes_session_and_command_mapping(monkeypatch) -> None:
    runner, _agent, session_manager, _pm, _controller, _store = _make_runner()
    session_manager.session.messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tool-1", "name": "search", "input": {}},
            ],
        }
    ]

    session, prompt, run_opts, task_goal = runner._prepare_prompt_context(
        "oc_1",
        "整理输出",
        source_channel="feishu",
    )

    assert session_manager.saved >= 1
    assert session.messages[-1]["role"] == "user"
    assert "<session_time>" in prompt
    assert run_opts["readonly_only"] is True
    assert task_goal == "整理输出"

    runner.serve_mode = True
    help_result = runner.dispatch("oc_1", "/help")
    assert "/quit" not in help_result.text

    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_dispatch_control_action",
        lambda session_id, action, target_id, **kwargs: dispatched.append((action, target_id)) or DispatchResult("ok", is_command=True),
    )
    runner.dispatch("oc_1", "/task rollback receipt-1")
    assert dispatched == [("rollback", "receipt-1")]
