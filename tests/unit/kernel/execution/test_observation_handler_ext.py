"""Extended coverage tests for hermit.kernel.execution.executor.observation_handler.

Covers areas not tested in the existing test_observation_handler.py:
- handle_observation_submission full flow
- finalize_observation with and without pending state
- _poll_ticket for different observer_kinds
- _attempt_context_from_snapshot
- _update_runtime_snapshot
- _progress_summary_facts
- _maybe_emit_progress_summary
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.coordination.observation import (
    ObservationTicket,
)
from hermit.kernel.execution.executor.observation_handler import (
    ObservationHandler,
    _is_governed_action,
)
from hermit.kernel.policy.models.models import ActionRequest, PolicyDecision, PolicyObligations
from hermit.runtime.capability.registry.tools import ToolSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_ticket(**overrides: Any) -> ObservationTicket:
    defaults: dict[str, Any] = {
        "observer_kind": "tool_call",
        "job_id": "job-1",
        "status_ref": "ref-1",
        "poll_after_seconds": 5.0,
        "cancel_supported": False,
        "resume_token": "tok-1",
        "topic_summary": "Waiting for build",
        "tool_name": "check_status",
    }
    defaults.update(overrides)
    return ObservationTicket(**defaults)


def _make_tool(**overrides: Any) -> ToolSpec:
    readonly = overrides.get("readonly", False)
    defaults: dict[str, Any] = {
        "name": "test_tool",
        "description": "test",
        "input_schema": {},
        "handler": lambda _: {"status": "completed", "result": "done"},
        "action_class": "write_local",
    }
    if readonly:
        defaults["requires_receipt"] = False
    else:
        defaults["risk_hint"] = "low"
        defaults["requires_receipt"] = True
    defaults.update(overrides)
    return ToolSpec(**defaults)


def _make_policy(**overrides: Any) -> PolicyDecision:
    defaults: dict[str, Any] = {
        "verdict": "allow",
        "action_class": "write_local",
        "obligations": PolicyObligations(),
    }
    defaults.update(overrides)
    return PolicyDecision(**defaults)


def _make_handler(**overrides: Any) -> ObservationHandler:
    defaults: dict[str, Any] = {
        "store": MagicMock(),
        "registry": MagicMock(),
        "policy_engine": MagicMock(),
        "receipt_service": MagicMock(),
        "decision_service": MagicMock(),
        "capability_service": MagicMock(),
        "reconciliations": MagicMock(),
        "_snapshot": MagicMock(),
        "progress_summarizer": None,
        "progress_summary_keepalive_seconds": 15.0,
        "tool_output_limit": 4000,
        "executor": MagicMock(),
    }
    defaults.update(overrides)
    return ObservationHandler(**defaults)


# ---------------------------------------------------------------------------
# handle_observation_submission
# ---------------------------------------------------------------------------


class TestHandleObservationSubmission:
    def test_returns_observing_result(self) -> None:
        handler = _make_handler()
        tool = _make_tool()
        ticket = _make_ticket()
        policy = _make_policy()
        action = ActionRequest(request_id="r-1", tool_name="test", action_class="write_local")
        ctx = _make_attempt_ctx()

        result = handler.handle_observation_submission(
            tool=tool,
            tool_name="test_tool",
            tool_input={"key": "val"},
            attempt_ctx=ctx,
            observation=ticket,
            policy=policy,
            policy_ref="pol-1",
            decision_id="dec-1",
            capability_grant_id="cap-1",
            workspace_lease_id="lease-1",
            approval_ref="appr-1",
            witness_ref="wit-1",
            action_request=action,
            action_request_ref="ar-1",
            approval_packet_ref="pkt-1",
            environment_ref="env-1",
            approval_mode="once",
            rollback_plan={"supported": False, "strategy": None, "artifact_refs": []},
        )

        assert result.blocked is True
        assert result.suspended is True
        assert result.waiting_kind == "observing"
        assert result.result_code == "observation_submitted"
        assert result.execution_status == "observing"

    def test_stores_pending_execution(self) -> None:
        executor = MagicMock()
        handler = _make_handler(executor=executor)
        tool = _make_tool()
        ticket = _make_ticket()
        policy = _make_policy()
        action = ActionRequest(request_id="r-1", tool_name="test", action_class="write_local")
        ctx = _make_attempt_ctx()

        handler.handle_observation_submission(
            tool=tool,
            tool_name="test_tool",
            tool_input={},
            attempt_ctx=ctx,
            observation=ticket,
            policy=policy,
            policy_ref=None,
            decision_id=None,
            capability_grant_id=None,
            workspace_lease_id=None,
            approval_ref=None,
            witness_ref=None,
            action_request=action,
            action_request_ref=None,
            approval_packet_ref=None,
            environment_ref=None,
            approval_mode="",
            rollback_plan={},
        )

        executor._store_pending_execution.assert_called_once()
        executor._set_attempt_phase.assert_called_once_with(
            ctx, "observing", reason="observation_submitted"
        )


# ---------------------------------------------------------------------------
# finalize_observation — no pending execution
# ---------------------------------------------------------------------------


class TestFinalizeObservationNoPending:
    def test_no_pending_returns_basic_result(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = {}
        handler = _make_handler(executor=executor)
        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"data": "result"},
            is_error=False,
            summary="Done",
        )
        assert result["result_code"] == "completed"
        assert result["is_error"] is False

    def test_no_pending_with_model_content_override(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = {}
        handler = _make_handler(executor=executor)
        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result="data",
            is_error=False,
            summary="Done",
            model_content_override="Custom content",
        )
        assert result["model_content"] == "Custom content"


# ---------------------------------------------------------------------------
# finalize_observation — with pending execution
# ---------------------------------------------------------------------------


class TestFinalizeObservationWithPending:
    def test_with_pending_issues_receipt(self) -> None:
        executor = MagicMock()
        pending = {
            "tool_name": "bash",
            "tool_input": {"command": "echo hello"},
            "policy": {"verdict": "allow", "action_class": "execute_command"},
            "policy_ref": "pol-1",
            "decision_id": "dec-1",
            "capability_grant_id": "cap-1",
            "workspace_lease_id": None,
            "approval_ref": None,
            "witness_ref": None,
            "action_request_ref": "ar-1",
            "policy_result_ref": "pr-1",
            "environment_ref": None,
            "approval_mode": "",
            "rollback_plan": {"supported": False, "strategy": None, "artifact_refs": []},
            "idempotency_key": None,
        }
        executor._load_pending_execution.return_value = pending
        executor._load_contract_bundle.return_value = (None, None, None)
        executor._successful_result_summary.return_value = "Success"
        executor._authorized_effect_summary.return_value = "Effect"
        executor._issue_receipt.return_value = "receipt-1"

        registry = MagicMock()
        tool = _make_tool(name="bash", action_class="execute_command")
        registry.get.return_value = tool

        policy_engine = MagicMock()
        policy_engine.infer_action_class.return_value = "execute_command"

        handler = _make_handler(
            executor=executor,
            registry=registry,
            policy_engine=policy_engine,
        )
        # Set requires_receipt on obligations
        handler.receipt_service = MagicMock()

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"output": "hello"},
            is_error=False,
            summary="Done",
        )
        executor._clear_pending_execution.assert_called_once()
        assert result["result_code"] == "succeeded"


# ---------------------------------------------------------------------------
# _poll_ticket
# ---------------------------------------------------------------------------


class TestPollTicket:
    def test_unsupported_observer_kind(self) -> None:
        handler = _make_handler()
        ticket = _make_ticket(observer_kind="magic_kind")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "failed"
        assert "unsupported observer kind" in result["result"]["error"]

    def test_tool_call_observer(self) -> None:
        registry = MagicMock()
        status_tool = MagicMock()
        status_tool.handler.return_value = {"status": "completed", "result": "done"}
        registry.get.return_value = status_tool
        handler = _make_handler(registry=registry)
        ticket = _make_ticket(observer_kind="tool_call", status_tool_name="check_status")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "completed"

    def test_tool_call_missing_tool_name(self) -> None:
        handler = _make_handler()
        ticket = _make_ticket(observer_kind="tool_call", status_tool_name=None, tool_name=None)
        result = handler._poll_ticket(ticket)
        assert result["status"] == "failed"

    def test_local_process_no_sandbox(self) -> None:
        registry = MagicMock()
        registry._tools = {}
        handler = _make_handler(registry=registry)
        ticket = _make_ticket(observer_kind="local_process")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "failed"

    def test_local_process_with_sandbox(self) -> None:
        registry = MagicMock()
        sandbox = MagicMock()
        sandbox.poll.return_value = {"status": "completed", "result": "ok"}
        bash_tool = MagicMock()
        bash_tool.handler._sandbox = sandbox
        registry._tools = {"bash": bash_tool}
        handler = _make_handler(registry=registry)
        ticket = _make_ticket(observer_kind="local_process", job_id="job-42")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "completed"
        sandbox.poll.assert_called_once_with("job-42")


# ---------------------------------------------------------------------------
# _attempt_context_from_snapshot
# ---------------------------------------------------------------------------


class TestAttemptContextFromSnapshot:
    def test_valid_attempt(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            context={"workspace_root": "/workspace"},
        )
        task = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        store.get_step_attempt.return_value = attempt
        store.get_task.return_value = task
        handler = _make_handler(store=store)
        ctx = handler._attempt_context_from_snapshot("att-1")
        assert ctx.task_id == "task-1"
        assert ctx.workspace_root == "/workspace"

    def test_missing_attempt_raises(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        handler = _make_handler(store=store)
        with pytest.raises(KeyError):
            handler._attempt_context_from_snapshot("missing")

    def test_missing_task_raises(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            step_attempt_id="att-1",
            task_id="task-missing",
            step_id="step-1",
            context={},
        )
        store.get_task.return_value = None
        handler = _make_handler(store=store)
        with pytest.raises(KeyError):
            handler._attempt_context_from_snapshot("att-1")


# ---------------------------------------------------------------------------
# _is_governed_action (module-level)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _progress_summary_facts
# ---------------------------------------------------------------------------


class TestProgressSummaryFacts:
    def test_builds_facts_from_task_and_events(self) -> None:
        store = MagicMock()
        task = SimpleNamespace(
            title="Build project",
            goal="Compile and test",
            status="running",
            source_channel="cli",
        )
        store.get_task.return_value = task
        store.list_events.return_value = [
            {
                "event_type": "task.created",
                "payload": {"summary": "Task created"},
            },
            {
                "event_type": "tool.submitted",
                "payload": {"topic_summary": "Running build"},
            },
            {
                "event_type": "irrelevant.event",
                "payload": {},
            },
        ]
        handler = _make_handler(store=store)
        ticket = _make_ticket()
        facts = handler._progress_summary_facts(
            task_id="task-1",
            step_attempt_id="att-1",
            ticket=ticket,
            status="observing",
            progress=None,
        )
        assert facts["task"]["title"] == "Build project"
        assert facts["attempt"]["tool_name"] == "check_status"
        # Only relevant events should be included
        assert len(facts["recent_events"]) == 2

    def test_note_appended_event_uses_raw_text(self) -> None:
        store = MagicMock()
        task = SimpleNamespace(title="Task", goal="Goal", status="running", source_channel="cli")
        store.get_task.return_value = task
        store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "payload": {"raw_text": "User feedback here"},
            },
        ]
        handler = _make_handler(store=store)
        ticket = _make_ticket()
        facts = handler._progress_summary_facts(
            task_id="task-1",
            step_attempt_id="att-1",
            ticket=ticket,
            status="observing",
            progress=None,
        )
        assert len(facts["recent_events"]) == 1
        assert facts["recent_events"][0]["text"] == "User feedback here"

    def test_no_task_returns_empty_strings(self) -> None:
        store = MagicMock()
        store.get_task.return_value = None
        store.list_events.return_value = []
        handler = _make_handler(store=store)
        ticket = _make_ticket()
        facts = handler._progress_summary_facts(
            task_id="task-1",
            step_attempt_id="att-1",
            ticket=ticket,
            status="observing",
            progress=None,
        )
        assert facts["task"]["title"] == ""


# ---------------------------------------------------------------------------
# _maybe_emit_progress_summary
# ---------------------------------------------------------------------------


class TestMaybeEmitProgressSummary:
    def test_no_summarizer_noop(self) -> None:
        handler = _make_handler(progress_summarizer=None)
        ticket = _make_ticket()
        # Should not raise
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )

    def test_no_task_id_noop(self) -> None:
        summarizer = MagicMock()
        handler = _make_handler(progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id=None,
            step_id=None,
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )
        summarizer.summarize.assert_not_called()

    def test_no_progress_change_no_keepalive_noop(self) -> None:
        summarizer = MagicMock()
        handler = _make_handler(progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=False,
            now=time.time(),
        )
        summarizer.summarize.assert_not_called()

    def test_progress_changed_emits_summary(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summary = SimpleNamespace(
            summary="Building...",
            phase="building",
            progress_percent=50,
            to_dict=lambda: {"summary": "Building...", "phase": "building", "progress_percent": 50},
            signature=lambda: ("Building...", "building", None, 50),
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )
        summarizer.summarize.assert_called_once()
        store.append_event.assert_called_once()
        assert store.append_event.call_args.kwargs["event_type"] == "task.progress.summarized"

    def test_summarizer_exception_is_caught(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summarizer.summarize.side_effect = RuntimeError("LLM failure")
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        # Should not raise
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )

    def test_empty_summary_text_noop(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summary = SimpleNamespace(
            summary="   ",
            phase="",
            progress_percent=None,
            to_dict=lambda: {},
            signature=lambda: ("", None, None, None),
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )
        store.append_event.assert_not_called()

    def test_phase_fallback_from_progress(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summary = SimpleNamespace(
            summary="Working on it",
            phase="",  # Empty phase triggers fallback
            progress_percent=None,
            to_dict=lambda: {
                "summary": "Working on it",
                "phase": "building",
                "progress_percent": 50,
            },
            signature=lambda: ("Working on it", "building", None, 50),
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()

        from hermit.kernel.execution.coordination.observation import ObservationProgress

        progress = ObservationProgress(phase="building", summary="Building...")
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=progress,
            progress_changed=True,
            now=time.time(),
        )
        assert summary.phase == "building"

    def test_phase_fallback_from_status(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summary = SimpleNamespace(
            summary="Working on it",
            phase="",  # Empty phase triggers fallback
            progress_percent=None,
            to_dict=lambda: {
                "summary": "Working on it",
                "phase": "observing",
                "progress_percent": None,
            },
            signature=lambda: ("Working on it", "observing", None, None),
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )
        assert summary.phase == "observing"

    def test_progress_percent_fallback(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summary = SimpleNamespace(
            summary="Working on it",
            phase="building",
            progress_percent=None,  # Triggers fallback from progress
            to_dict=lambda: {
                "summary": "Working on it",
                "phase": "building",
                "progress_percent": 75,
            },
            signature=lambda: ("Working on it", "building", None, 75),
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()

        from hermit.kernel.execution.coordination.observation import ObservationProgress

        progress = ObservationProgress(phase="building", summary="Building...", progress_percent=75)
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=progress,
            progress_changed=True,
            now=time.time(),
        )
        assert summary.progress_percent == 75

    def test_duplicate_signature_skips_event(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        sig = ("Working on it", "building", None, 50)
        summary = SimpleNamespace(
            summary="Working on it",
            phase="building",
            progress_percent=50,
            to_dict=lambda: {
                "summary": "Working on it",
                "phase": "building",
                "progress_percent": 50,
            },
            signature=lambda: sig,
        )
        summarizer.summarize.return_value = summary
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        # Set previous progress_summary with same signature
        ticket.progress_summary = {
            "summary": "Working on it",
            "phase": "building",
            "progress_percent": 50,
        }

        # Patch to return matching signature
        with patch(
            "hermit.kernel.execution.executor.observation_handler._progress_summary_signature",
            return_value=sig,
        ):
            handler._maybe_emit_progress_summary(
                step_attempt_id="att-1",
                task_id="task-1",
                step_id="step-1",
                ticket=ticket,
                status="observing",
                progress=None,
                progress_changed=True,
                now=time.time(),
            )
        store.append_event.assert_not_called()

    def test_none_summary_noop(self) -> None:
        store = MagicMock()
        store.get_task.return_value = SimpleNamespace(
            title="T", goal="G", status="running", source_channel="cli"
        )
        store.list_events.return_value = []
        summarizer = MagicMock()
        summarizer.summarize.return_value = None
        handler = _make_handler(store=store, progress_summarizer=summarizer)
        ticket = _make_ticket()
        handler._maybe_emit_progress_summary(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            ticket=ticket,
            status="observing",
            progress=None,
            progress_changed=True,
            now=time.time(),
        )
        store.append_event.assert_not_called()


# ---------------------------------------------------------------------------
# _update_runtime_snapshot
# ---------------------------------------------------------------------------


class TestUpdateRuntimeSnapshot:
    def test_updates_step_attempt_context(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            context={},
            status="observing",
        )
        store.get_step_attempt.return_value = attempt
        task = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        store.get_task.return_value = task
        executor = MagicMock()
        executor._runtime_snapshot_envelope.return_value = {"schema_version": 2}
        executor._store_runtime_snapshot_artifact.return_value = "snap-ref-1"
        handler = _make_handler(store=store, executor=executor)
        handler._update_runtime_snapshot("att-1", {"suspend_kind": "observing"})
        store.update_step_attempt.assert_called_once()

    def test_null_attempt_returns_early(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        executor = MagicMock()
        executor._runtime_snapshot_envelope.return_value = {"schema_version": 2}
        handler = _make_handler(store=store, executor=executor)
        # Should not raise, should return early
        handler._update_runtime_snapshot("att-1", {"suspend_kind": "observing"})
        store.update_step_attempt.assert_not_called()

    def test_note_cursor_event_seq_preserved(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(
            step_attempt_id="att-1",
            task_id="task-1",
            step_id="step-1",
            context={"some": "ctx"},
            status="observing",
        )
        store.get_step_attempt.return_value = attempt
        task = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        store.get_task.return_value = task
        executor = MagicMock()
        executor._runtime_snapshot_envelope.return_value = {"schema_version": 2}
        executor._store_runtime_snapshot_artifact.return_value = "snap-ref-1"
        handler = _make_handler(store=store, executor=executor)
        handler._update_runtime_snapshot(
            "att-1", {"suspend_kind": "observing", "note_cursor_event_seq": 5}
        )
        update_call = store.update_step_attempt.call_args
        ctx = update_call.kwargs.get("context", {})
        assert ctx.get("note_cursor_event_seq") == 5


# ---------------------------------------------------------------------------
# _is_governed_action (module-level)
# ---------------------------------------------------------------------------


class TestIsGovernedAction:
    def test_readonly_allow_not_governed(self) -> None:
        tool = _make_tool(readonly=True, action_class="read_local")
        policy = _make_policy(verdict="allow", action_class="read_local")
        assert _is_governed_action(tool, policy) is False

    def test_readonly_deny_read_local_not_governed(self) -> None:
        # readonly=True but deny: first check fails (verdict != allow),
        # then read_local without receipt -> not governed
        tool = _make_tool(readonly=True, action_class="read_local")
        policy = _make_policy(verdict="deny", action_class="read_local")
        assert _is_governed_action(tool, policy) is False

    def test_readonly_deny_write_local_is_governed(self) -> None:
        # readonly=True but deny + write_local action_class -> governed
        tool = _make_tool(readonly=True, action_class="write_local")
        policy = _make_policy(verdict="deny", action_class="write_local")
        assert _is_governed_action(tool, policy) is True

    def test_ephemeral_ui_not_governed(self) -> None:
        tool = _make_tool(action_class="ephemeral_ui_mutation")
        policy = _make_policy(action_class="ephemeral_ui_mutation")
        assert _is_governed_action(tool, policy) is False

    def test_write_local_is_governed(self) -> None:
        tool = _make_tool(action_class="write_local")
        policy = _make_policy(action_class="write_local")
        assert _is_governed_action(tool, policy) is True

    def test_network_read_no_receipt_not_governed(self) -> None:
        tool = _make_tool(action_class="network_read")
        policy = _make_policy(action_class="network_read")
        assert _is_governed_action(tool, policy) is False

    def test_network_read_with_receipt_governed(self) -> None:
        tool = _make_tool(action_class="network_read")
        policy = _make_policy(
            action_class="network_read",
            obligations=PolicyObligations(require_receipt=True),
        )
        assert _is_governed_action(tool, policy) is True
