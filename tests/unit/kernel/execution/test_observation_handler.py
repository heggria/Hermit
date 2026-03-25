from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.coordination.observation import (
    ObservationTicket,
)
from hermit.kernel.execution.executor.formatting import (
    compact_progress_text as _compact_progress_text,
)
from hermit.kernel.execution.executor.formatting import (
    format_model_content as _format_model_content,
)
from hermit.kernel.execution.executor.formatting import (
    progress_signature as _progress_signature,
)
from hermit.kernel.execution.executor.formatting import (
    progress_summary_signature as _progress_summary_signature,
)
from hermit.kernel.execution.executor.formatting import (
    truncate_middle as _truncate_middle,
)
from hermit.kernel.execution.executor.observation_handler import (
    ObservationHandler,
    _is_governed_action,
)
from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations
from hermit.runtime.capability.registry.tools import ToolSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(
    *,
    name: str = "test_tool",
    readonly: bool = False,
    action_class: str = "write_local",
    risk_hint: str | None = "low",
    requires_receipt: bool | None = None,
) -> ToolSpec:
    if readonly:
        return ToolSpec(
            name=name,
            description="test",
            input_schema={},
            handler=lambda _: None,
            readonly=True,
            action_class=action_class or "read_local",
            requires_receipt=False,
        )
    return ToolSpec(
        name=name,
        description="test",
        input_schema={},
        handler=lambda _: None,
        readonly=False,
        action_class=action_class,
        risk_hint=risk_hint or "low",
        requires_receipt=requires_receipt if requires_receipt is not None else True,
    )


def _make_policy(
    *,
    verdict: str = "allow",
    action_class: str = "write_local",
    requires_receipt: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        verdict=verdict,
        action_class=action_class,
        obligations=PolicyObligations(require_receipt=requires_receipt),
    )


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


def _make_handler(**overrides: Any) -> ObservationHandler:
    store = overrides.pop("store", MagicMock())
    registry = overrides.pop("registry", MagicMock())
    policy_engine = overrides.pop("policy_engine", MagicMock())
    receipt_service = overrides.pop("receipt_service", MagicMock())
    decision_service = overrides.pop("decision_service", MagicMock())
    capability_service = overrides.pop("capability_service", MagicMock())
    reconciliations = overrides.pop("reconciliations", MagicMock())
    snapshot = overrides.pop("_snapshot", MagicMock())
    executor = overrides.pop("executor", MagicMock())
    return ObservationHandler(
        store=store,
        registry=registry,
        policy_engine=policy_engine,
        receipt_service=receipt_service,
        decision_service=decision_service,
        capability_service=capability_service,
        reconciliations=reconciliations,
        _snapshot=snapshot,
        progress_summarizer=overrides.pop("progress_summarizer", None),
        progress_summary_keepalive_seconds=overrides.pop("progress_summary_keepalive_seconds", 0.0),
        tool_output_limit=overrides.pop("tool_output_limit", 4000),
        executor=executor,
    )


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestTruncateMiddle:
    def test_no_truncation_when_under_limit(self) -> None:
        assert _truncate_middle("hello", 10) == "hello"

    def test_exact_limit(self) -> None:
        assert _truncate_middle("hello", 5) == "hello"

    def test_zero_limit_returns_original(self) -> None:
        assert _truncate_middle("hello", 0) == "hello"

    def test_small_limit_truncates_head_only(self) -> None:
        result = _truncate_middle("abcdefghij", 5)
        assert result == "abcde"
        assert len(result) == 5

    def test_large_text_shows_head_and_tail(self) -> None:
        text = "A" * 100
        result = _truncate_middle(text, 50)
        assert "\n...\n" in result
        assert result.startswith("A")
        assert result.endswith("A")


class TestFormatModelContent:
    def test_string_value_truncated(self) -> None:
        long_text = "x" * 200
        result = _format_model_content(long_text, 50)
        assert isinstance(result, str)
        assert len(result) <= 60  # head + separator + tail

    def test_short_string_unchanged(self) -> None:
        result = _format_model_content("short", 4000)
        assert result == "short"

    def test_dict_with_type_text_returns_list(self) -> None:
        block = {"type": "text", "text": "hello"}
        result = _format_model_content(block, 4000)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"

    def test_list_of_blocks_returned_as_is(self) -> None:
        blocks = [{"type": "text", "text": "a"}, {"type": "image", "url": "b"}]
        result = _format_model_content(blocks, 4000)
        assert isinstance(result, list)
        assert len(result) == 2


class TestCompactProgressText:
    def test_empty_input(self) -> None:
        assert _compact_progress_text("") == ""
        assert _compact_progress_text(None) == ""

    def test_whitespace_normalization(self) -> None:
        assert _compact_progress_text("  hello   world  ") == "hello world"

    def test_truncation_with_ellipsis(self) -> None:
        long_text = "a" * 300
        result = _compact_progress_text(long_text, limit=50)
        assert len(result) == 50
        assert result.endswith("\u2026")


class TestProgressSignature:
    def test_none_input_returns_none(self) -> None:
        assert _progress_signature(None) is None

    def test_empty_dict_returns_none(self) -> None:
        assert _progress_signature({}) is None

    def test_valid_progress_returns_tuple(self) -> None:
        data = {"phase": "building", "summary": "compiling", "progress_percent": 50}
        sig = _progress_signature(data)
        assert sig is not None
        assert sig[0] == "building"
        assert sig[1] == "compiling"
        assert sig[3] == 50


class TestProgressSummarySignature:
    def test_none_input_returns_none(self) -> None:
        assert _progress_summary_signature(None) is None

    def test_empty_dict_returns_none(self) -> None:
        assert _progress_summary_signature({}) is None

    def test_valid_summary_returns_tuple(self) -> None:
        data = {"summary": "building", "phase": "compiling"}
        sig = _progress_summary_signature(data)
        assert sig is not None
        assert sig[0] == "building"
        assert sig[2] == "compiling"

    def test_with_progress_percent_in_tuple(self) -> None:
        data = {"summary": "done", "phase": "final", "progress_percent": 75}
        sig = _progress_summary_signature(data)
        assert sig is not None
        assert sig[3] == 75

    def test_summary_without_phase_returns_none(self) -> None:
        # A dict with no 'summary' field should normalize to None
        data = {"phase": "compiling"}
        assert _progress_summary_signature(data) is None


class TestIsGovernedAction:
    def test_readonly_allow_is_not_governed(self) -> None:
        tool = _make_tool(readonly=True, action_class="read_local")
        policy = _make_policy(verdict="allow", action_class="read_local")
        assert _is_governed_action(tool, policy) is False

    def test_readonly_deny_not_governed_when_read_local(self) -> None:
        tool = _make_tool(readonly=True, action_class="read_local")
        policy = _make_policy(verdict="deny", action_class="read_local")
        # readonly=True + verdict="deny" still enters the second branch
        # which checks action_class "read_local" without receipt -> not governed
        assert _is_governed_action(tool, policy) is False

    def test_read_local_without_receipt_is_not_governed(self) -> None:
        tool = _make_tool(action_class="write_local")
        policy = _make_policy(action_class="read_local", requires_receipt=False)
        assert _is_governed_action(tool, policy) is False

    def test_ephemeral_ui_mutation_is_not_governed(self) -> None:
        tool = _make_tool(action_class="write_local")
        policy = _make_policy(action_class="ephemeral_ui_mutation")
        assert _is_governed_action(tool, policy) is False

    def test_write_local_with_receipt_is_governed(self) -> None:
        tool = _make_tool(action_class="write_local")
        policy = _make_policy(action_class="write_local", requires_receipt=True)
        assert _is_governed_action(tool, policy) is True


# ---------------------------------------------------------------------------
# poll_observation tests
# ---------------------------------------------------------------------------


class TestPollObservation:
    def test_returns_none_when_suspend_kind_not_observing(self) -> None:
        handler = _make_handler()
        handler._executor.load_suspended_state.return_value = {"suspend_kind": "approval"}
        result = handler.poll_observation("attempt-1")
        assert result is None

    def test_returns_none_when_observation_missing(self) -> None:
        handler = _make_handler()
        handler._executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": "not-a-dict",
        }
        result = handler.poll_observation("attempt-1")
        assert result is None

    def test_skips_poll_when_before_next_poll_at(self) -> None:
        ticket = _make_ticket(next_poll_at=9999999999.0)
        handler = _make_handler()
        handler._executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": ticket.to_dict(),
        }
        result = handler.poll_observation("attempt-1", now=1000.0)
        assert result is not None
        assert result.should_resume is False

    def test_completed_status_triggers_resume(self) -> None:
        ticket = _make_ticket(next_poll_at=0.0)
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            status="observing",
            context={"workspace_root": "/tmp"},
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        registry = MagicMock()
        tool_mock = MagicMock()
        tool_mock.handler.return_value = {"status": "completed", "result": {"ok": True}}
        registry.get.return_value = tool_mock

        executor = MagicMock()
        executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": ticket.to_dict(),
        }
        executor._load_pending_execution.return_value = None
        executor._runtime_snapshot_envelope.return_value = {"snapshot": True}

        handler = _make_handler(store=store, registry=registry, executor=executor)
        result = handler.poll_observation("attempt-1", now=2000.0)

        assert result is not None
        assert result.should_resume is True
        assert result.ticket.terminal_status == "completed"

    def test_still_observing_reschedules_poll(self) -> None:
        ticket = _make_ticket(next_poll_at=0.0, poll_after_seconds=10.0)
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            status="observing",
            context={},
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        registry = MagicMock()
        tool_mock = MagicMock()
        tool_mock.handler.return_value = {
            "status": "observing",
            "topic_summary": "Still building",
        }
        registry.get.return_value = tool_mock

        executor = MagicMock()
        executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": ticket.to_dict(),
        }
        executor._runtime_snapshot_envelope.return_value = {"snapshot": True}

        handler = _make_handler(store=store, registry=registry, executor=executor)
        result = handler.poll_observation("attempt-1", now=1000.0)

        assert result is not None
        assert result.should_resume is False
        assert result.ticket.next_poll_at is not None
        assert result.ticket.next_poll_at >= 1000.0

    def test_failed_status_triggers_resume_with_error(self) -> None:
        ticket = _make_ticket(next_poll_at=0.0)
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            status="observing",
            context={"workspace_root": "/tmp"},
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        registry = MagicMock()
        tool_mock = MagicMock()
        tool_mock.handler.return_value = {
            "status": "failed",
            "result": {"error": "build broke"},
            "is_error": True,
            "topic_summary": "Build failed",
        }
        registry.get.return_value = tool_mock

        executor = MagicMock()
        executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": ticket.to_dict(),
        }
        executor._load_pending_execution.return_value = None
        executor._runtime_snapshot_envelope.return_value = {"snapshot": True}

        handler = _make_handler(store=store, registry=registry, executor=executor)
        result = handler.poll_observation("attempt-1", now=2000.0)

        assert result is not None
        assert result.should_resume is True
        assert result.ticket.terminal_status == "failed"
        assert result.ticket.final_is_error is True

    def test_ready_return_triggers_early_finalization(self) -> None:
        ticket = _make_ticket(next_poll_at=0.0, ready_return=True)
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            status="observing",
            context={"workspace_root": "/tmp"},
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
        )
        registry = MagicMock()
        tool_mock = MagicMock()
        tool_mock.handler.return_value = {
            "status": "observing",
            "progress": {
                "phase": "done",
                "summary": "Ready",
                "ready": True,
            },
        }
        registry.get.return_value = tool_mock

        executor = MagicMock()
        executor.load_suspended_state.return_value = {
            "suspend_kind": "observing",
            "observation": ticket.to_dict(),
        }
        executor._load_pending_execution.return_value = None
        executor._runtime_snapshot_envelope.return_value = {"snapshot": True}

        handler = _make_handler(store=store, registry=registry, executor=executor)
        result = handler.poll_observation("attempt-1", now=2000.0)

        assert result is not None
        assert result.should_resume is True
        assert result.ticket.terminal_status == "completed"


# ---------------------------------------------------------------------------
# finalize_observation tests
# ---------------------------------------------------------------------------


class TestFinalizeObservation:
    def test_no_pending_returns_basic_result(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = None
        handler = _make_handler(executor=executor)

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"ok": True},
            is_error=False,
            summary="Done",
        )

        # Without pending execution data, terminal_status passes through as-is
        assert result["result_code"] == "completed"
        assert result["is_error"] is False

    def test_no_pending_with_model_content_override(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = None
        handler = _make_handler(executor=executor)

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"ok": True},
            is_error=False,
            summary="Done",
            model_content_override="Custom output",
        )

        assert result["model_content"] == "Custom output"

    def test_failed_terminal_status_propagates(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = None
        handler = _make_handler(executor=executor)

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="failed",
            raw_result={"error": "boom"},
            is_error=True,
            summary="Build failed",
        )

        assert result["result_code"] == "failed"
        assert result["is_error"] is True

    def test_timeout_terminal_status(self) -> None:
        executor = MagicMock()
        executor._load_pending_execution.return_value = None
        handler = _make_handler(executor=executor)

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="timeout",
            raw_result=None,
            is_error=True,
            summary="Timed out",
        )

        assert result["result_code"] == "timeout"

    def test_with_pending_issues_receipt_when_required(self) -> None:
        tool = _make_tool(action_class="write_local", requires_receipt=True)
        policy = _make_policy(
            action_class="write_local",
            requires_receipt=True,
        )
        pending = {
            "tool_name": "test_tool",
            "tool_input": {"path": "/tmp/file"},
            "policy": policy.to_dict(),
            "policy_ref": "pol-1",
            "decision_id": "dec-1",
            "capability_grant_id": "cap-1",
            "workspace_lease_id": None,
            "approval_ref": "apr-1",
            "witness_ref": "wit-1",
            "action_request_ref": "areq-1",
            "policy_result_ref": "pol-1",
            "environment_ref": None,
            "approval_mode": "auto",
            "rollback_plan": {"supported": False},
            "idempotency_key": "idem-1",
        }

        executor = MagicMock()
        executor._load_pending_execution.return_value = pending
        executor._load_contract_bundle.return_value = (None, None, None)
        executor._issue_receipt.return_value = "receipt-1"
        executor._successful_result_summary.return_value = "ok"

        registry = MagicMock()
        registry.get.return_value = tool

        policy_engine = MagicMock()
        policy_engine.infer_action_class.return_value = "write_local"

        capability_service = MagicMock()

        handler = _make_handler(
            executor=executor,
            registry=registry,
            policy_engine=policy_engine,
            capability_service=capability_service,
        )

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"ok": True},
            is_error=False,
            summary="Done",
        )

        assert result["result_code"] == "succeeded"
        executor._issue_receipt.assert_called_once()
        capability_service.consume.assert_called_once_with("cap-1")

    def test_error_result_prefixed_in_model_content(self) -> None:
        pending = {
            "tool_name": "test_tool",
            "tool_input": {},
            "policy": _make_policy().to_dict(),
            "policy_ref": None,
            "decision_id": None,
            "capability_grant_id": None,
            "workspace_lease_id": None,
            "approval_ref": None,
            "witness_ref": None,
            "action_request_ref": None,
            "policy_result_ref": None,
            "environment_ref": None,
            "approval_mode": "",
            "rollback_plan": {},
            "idempotency_key": None,
        }
        tool = _make_tool(action_class="write_local", requires_receipt=False)
        executor = MagicMock()
        executor._load_pending_execution.return_value = pending

        registry = MagicMock()
        registry.get.return_value = tool

        policy_engine = MagicMock()
        policy_engine.infer_action_class.return_value = "write_local"

        handler = _make_handler(
            executor=executor,
            registry=registry,
            policy_engine=policy_engine,
        )

        ctx = _make_attempt_ctx()
        result = handler.finalize_observation(
            ctx,
            terminal_status="failed",
            raw_result={"error": "oops"},
            is_error=True,
            summary="Something went wrong",
        )

        assert result["model_content"] == "Error: Something went wrong"


# ---------------------------------------------------------------------------
# _poll_ticket tests
# ---------------------------------------------------------------------------


class TestPollTicket:
    def test_unsupported_observer_kind_returns_error(self) -> None:
        handler = _make_handler()
        ticket = _make_ticket(observer_kind="unknown_kind")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "failed"
        assert "unsupported observer kind" in result["result"]["error"]

    def test_local_process_without_sandbox_returns_error(self) -> None:
        registry = MagicMock()
        registry._tools = {}
        handler = _make_handler(registry=registry)
        ticket = _make_ticket(observer_kind="local_process")
        result = handler._poll_ticket(ticket)
        assert result["status"] == "failed"
        assert "unavailable" in result["topic_summary"]

    def test_tool_call_without_tool_name_returns_error(self) -> None:
        handler = _make_handler()
        ticket = _make_ticket(
            observer_kind="tool_call",
            status_tool_name=None,
            tool_name="",
        )
        result = handler._poll_tool_call_observation(ticket)
        assert result["status"] == "failed"
        assert result["is_error"] is True

    def test_tool_call_delegates_to_status_tool(self) -> None:
        registry = MagicMock()
        status_tool = MagicMock()
        status_tool.handler.return_value = {"status": "completed", "result": {"v": 1}}
        registry.get.return_value = status_tool

        handler = _make_handler(registry=registry)
        ticket = _make_ticket(
            observer_kind="tool_call",
            status_tool_name="check_build",
            status_tool_input={"project": "hermit"},
        )
        result = handler._poll_tool_call_observation(ticket)

        assert result["status"] == "completed"
        registry.get.assert_called_with("check_build")
        call_args = status_tool.handler.call_args[0][0]
        assert call_args["project"] == "hermit"
        assert "job_id" in call_args

    def test_tool_call_returns_nested_observation(self) -> None:
        nested_ticket_data = {
            "_hermit_observation": {
                "observer_kind": "tool_call",
                "job_id": "job-2",
                "status_ref": "ref-2",
                "poll_after_seconds": 15.0,
                "cancel_supported": False,
                "resume_token": "tok-2",
                "topic_summary": "Still building",
                "progress": {"phase": "compile", "summary": "compiling"},
            }
        }
        registry = MagicMock()
        status_tool = MagicMock()
        status_tool.handler.return_value = nested_ticket_data
        registry.get.return_value = status_tool

        handler = _make_handler(registry=registry)
        ticket = _make_ticket(observer_kind="tool_call", status_tool_name="check_build")
        result = handler._poll_tool_call_observation(ticket)

        assert result["status"] == "observing"
        assert result["topic_summary"] == "Still building"
        assert result["poll_after_seconds"] == 15.0


# ---------------------------------------------------------------------------
# _attempt_context_from_snapshot tests
# ---------------------------------------------------------------------------


class TestAttemptContextFromSnapshot:
    def test_raises_on_unknown_attempt(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        handler = _make_handler(store=store)

        with pytest.raises(KeyError, match="Unknown step attempt"):
            handler._attempt_context_from_snapshot("missing-id")

    def test_raises_on_unknown_task(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-missing",
            step_id="step-1",
            step_attempt_id="attempt-1",
            context={"workspace_root": "/tmp"},
        )
        store.get_task.return_value = None
        handler = _make_handler(store=store)

        with pytest.raises(KeyError, match="Unknown task"):
            handler._attempt_context_from_snapshot("attempt-1")

    def test_builds_context_from_store(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            context={"workspace_root": "/home/user/project"},
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            conversation_id="conv-1",
            source_channel="feishu",
            policy_profile="strict",
        )
        handler = _make_handler(store=store)

        ctx = handler._attempt_context_from_snapshot("attempt-1")

        assert ctx.task_id == "task-1"
        assert ctx.conversation_id == "conv-1"
        assert ctx.source_channel == "feishu"
        assert ctx.policy_profile == "strict"
        assert ctx.workspace_root == "/home/user/project"
