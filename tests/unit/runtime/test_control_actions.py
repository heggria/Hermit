"""Tests for hermit.runtime.control.runner.control_actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.runtime.control.runner.control_actions import (
    ControlActionDispatcher,
    _resolve_help_text,
)
from hermit.runtime.control.runner.utils import DispatchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeApproval:
    approval_id: str = "approval_1"
    task_id: str = "task_1"
    step_id: str = "step_1"
    step_attempt_id: str = "attempt_1"
    status: str = "pending"


@dataclass
class _FakeStepAttempt:
    step_attempt_id: str = "attempt_1"
    task_id: str = "task_1"
    step_id: str = "step_1"
    status: str = "blocked"
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeTaskCtx:
    conversation_id: str = "conv_1"
    task_id: str = "task_1"
    step_id: str = "step_1"
    step_attempt_id: str = "attempt_1"
    source_channel: str = "test"
    policy_profile: str = "default"
    workspace_root: str = ""
    ingress_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeTaskRecord:
    task_id: str = "task_1"
    conversation_id: str = "conv_1"
    goal: str = "test"
    status: str = "running"


def _make_session(session_id: str = "sess_1"):
    from hermit.runtime.control.lifecycle.session import Session

    return Session(session_id=session_id)


@pytest.fixture()
def mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.serve_mode = False
    runner._commands = {
        "/help": (MagicMock(), "help text", False),
        "/quit": (MagicMock(), "quit text", True),
    }
    runner.session_manager = MagicMock()
    runner.session_manager.get_or_create.return_value = _make_session()
    runner.agent = MagicMock()
    runner.agent.kernel_store = MagicMock()
    runner.agent.artifact_store = MagicMock()
    runner._max_session_messages.return_value = 100
    runner.pm = MagicMock()
    runner.pm.settings = SimpleNamespace(locale="en-US")
    return runner


@pytest.fixture()
def mock_task_controller() -> MagicMock:
    tc = MagicMock()
    tc.store = MagicMock()
    tc.store.get_approval.return_value = _FakeApproval()
    tc.store.get_step_attempt.return_value = _FakeStepAttempt()
    tc.store.get_last_task_for_conversation.return_value = _FakeTaskRecord()
    tc.focus_task.return_value = None
    tc.context_for_attempt.return_value = _FakeTaskCtx()
    return tc


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = SimpleNamespace(locale="en-US")
    return pm


@pytest.fixture()
def dispatcher(
    mock_runner: MagicMock,
    mock_task_controller: MagicMock,
    mock_pm: MagicMock,
) -> ControlActionDispatcher:
    return ControlActionDispatcher(
        runner=mock_runner,
        task_controller=mock_task_controller,
        pm=mock_pm,
    )


# ---------------------------------------------------------------------------
# _resolve_help_text
# ---------------------------------------------------------------------------


class TestResolveHelpText:
    def test_returns_string(self) -> None:
        result = _resolve_help_text("some help text")
        assert isinstance(result, str)

    def test_with_runner(self, mock_runner: MagicMock) -> None:
        result = _resolve_help_text("help text", runner=mock_runner)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# dispatch — simple actions
# ---------------------------------------------------------------------------


class TestDispatchSimpleActions:
    def test_new_session(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="new_session", target_id="")
        assert isinstance(result, DispatchResult)
        assert result.is_command is True
        dispatcher._runner.reset_session.assert_called_once_with("sess_1")

    def test_focus_task(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        result = dispatcher.dispatch("sess_1", action="focus_task", target_id="task_42")
        assert result.is_command is True
        mock_task_controller.focus_task.assert_called_once_with("sess_1", "task_42")

    def test_focus_task_with_note(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        resolved = MagicMock()
        resolved.note_event_seq = 5
        mock_task_controller.focus_task.return_value = resolved
        result = dispatcher.dispatch("sess_1", action="focus_task", target_id="task_42")
        assert "pending message" in result.text

    def test_show_history(self, dispatcher: ControlActionDispatcher) -> None:
        session = _make_session()
        session.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        dispatcher._runner.session_manager.get_or_create.return_value = session
        result = dispatcher.dispatch("sess_1", action="show_history", target_id="")
        assert result.is_command is True

    def test_show_help(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="show_help", target_id="")
        assert result.is_command is True

    def test_show_help_hides_cli_only_in_serve_mode(
        self, dispatcher: ControlActionDispatcher
    ) -> None:
        dispatcher._runner.serve_mode = True
        result = dispatcher.dispatch("sess_1", action="show_help", target_id="")
        assert "/quit" not in result.text


# ---------------------------------------------------------------------------
# dispatch — kernel actions
# ---------------------------------------------------------------------------


class TestDispatchKernelActions:
    def test_task_list(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_tasks.return_value = []
        result = dispatcher.dispatch("sess_1", action="task_list", target_id="")
        assert result.is_command is True

    def test_task_events(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_events.return_value = []
        result = dispatcher.dispatch("sess_1", action="task_events", target_id="task_1")
        assert result.is_command is True

    def test_task_receipts(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_receipts.return_value = []
        result = dispatcher.dispatch("sess_1", action="task_receipts", target_id="task_1")
        assert result.is_command is True

    def test_task_proof(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.verification.proofs.proofs.ProofService") as MockPS:
            MockPS.return_value.build_proof_summary.return_value = {"proof": True}
            result = dispatcher.dispatch("sess_1", action="task_proof", target_id="task_1")
            assert result.is_command is True

    def test_task_proof_export(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.verification.proofs.proofs.ProofService") as MockPS:
            MockPS.return_value.export_task_proof.return_value = {"export": True}
            result = dispatcher.dispatch("sess_1", action="task_proof_export", target_id="task_1")
            assert result.is_command is True

    def test_rollback(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.verification.rollbacks.rollbacks.RollbackService") as MockRS:
            MockRS.return_value.execute.return_value = {"rolled_back": True}
            result = dispatcher.dispatch("sess_1", action="rollback", target_id="task_1")
            assert result.is_command is True

    def test_projection_rebuild(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.task.projections.projections.ProjectionService") as MockPS:
            MockPS.return_value.rebuild_task.return_value = {"rebuilt": True}
            result = dispatcher.dispatch("sess_1", action="projection_rebuild", target_id="task_1")
            assert result.is_command is True

    def test_projection_rebuild_all(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.task.projections.projections.ProjectionService") as MockPS:
            MockPS.return_value.rebuild_all.return_value = {"rebuilt": True}
            result = dispatcher.dispatch("sess_1", action="projection_rebuild_all", target_id="")
            assert result.is_command is True

    def test_capability_list(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_capability_grants.return_value = []
        result = dispatcher.dispatch("sess_1", action="capability_list", target_id="")
        assert result.is_command is True

    def test_capability_revoke_found(self, dispatcher: ControlActionDispatcher) -> None:
        grant = MagicMock()
        dispatcher._runner.agent.kernel_store.get_capability_grant.return_value = grant
        result = dispatcher.dispatch("sess_1", action="capability_revoke", target_id="grant_1")
        assert result.is_command is True

    def test_capability_revoke_not_found(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.get_capability_grant.return_value = None
        result = dispatcher.dispatch("sess_1", action="capability_revoke", target_id="grant_1")
        assert result.is_command is True

    def test_schedule_list(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_schedules.return_value = []
        result = dispatcher.dispatch("sess_1", action="schedule_list", target_id="")
        assert result.is_command is True

    def test_schedule_history(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.list_schedule_history.return_value = []
        result = dispatcher.dispatch("sess_1", action="schedule_history", target_id="")
        assert result.is_command is True

    def test_schedule_enable(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.update_schedule.return_value = MagicMock()
        result = dispatcher.dispatch("sess_1", action="schedule_enable", target_id="job_1")
        assert result.is_command is True

    def test_schedule_enable_not_found(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.update_schedule.return_value = None
        result = dispatcher.dispatch("sess_1", action="schedule_enable", target_id="job_1")
        assert result.is_command is True

    def test_schedule_disable(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.update_schedule.return_value = MagicMock()
        result = dispatcher.dispatch("sess_1", action="schedule_disable", target_id="job_1")
        assert result.is_command is True

    def test_schedule_remove_found(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.delete_schedule.return_value = True
        result = dispatcher.dispatch("sess_1", action="schedule_remove", target_id="job_1")
        assert result.is_command is True

    def test_schedule_remove_not_found(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store.delete_schedule.return_value = False
        result = dispatcher.dispatch("sess_1", action="schedule_remove", target_id="job_1")
        assert result.is_command is True

    def test_plan_enter_via_dispatch(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="plan_enter", target_id="task_1")
        assert result.is_command is True

    def test_plan_exit_via_dispatch(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="plan_exit", target_id="task_1")
        assert result.is_command is True

    def test_plan_confirm_via_dispatch(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="plan_confirm", target_id="")
        assert result.is_command is True

    def test_unsupported_action(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher.dispatch("sess_1", action="unknown_action", target_id="")
        assert result.is_command is True

    def test_case_action(self, dispatcher: ControlActionDispatcher) -> None:
        with patch("hermit.kernel.execution.controller.supervision.SupervisionService") as MockSS:
            MockSS.return_value.build_task_case.return_value = {"case": True}
            result = dispatcher.dispatch("sess_1", action="case", target_id="task_1")
            assert result.is_command is True


# ---------------------------------------------------------------------------
# dispatch — no kernel store
# ---------------------------------------------------------------------------


class TestDispatchNoKernelStore:
    def test_returns_unavailable_when_no_store(self, dispatcher: ControlActionDispatcher) -> None:
        dispatcher._runner.agent.kernel_store = None
        result = dispatcher.dispatch("sess_1", action="task_list", target_id="")
        assert result.is_command is True


# ---------------------------------------------------------------------------
# dispatch — approval actions
# ---------------------------------------------------------------------------


class TestDispatchApprovalActions:
    def test_approve_once_delegates(self, dispatcher: ControlActionDispatcher) -> None:
        with patch.object(dispatcher, "_resolve_approval") as mock_resolve:
            mock_resolve.return_value = DispatchResult("approved", is_command=True)
            result = dispatcher.dispatch("sess_1", action="approve_once", target_id="approval_1")
            mock_resolve.assert_called_once()
            assert result.is_command is True

    def test_deny_delegates(self, dispatcher: ControlActionDispatcher) -> None:
        with patch.object(dispatcher, "_resolve_approval") as mock_resolve:
            mock_resolve.return_value = DispatchResult("denied", is_command=True)
            dispatcher.dispatch("sess_1", action="deny", target_id="approval_1", reason="bad")
            mock_resolve.assert_called_once()

    def test_approve_mutable_workspace_delegates(self, dispatcher: ControlActionDispatcher) -> None:
        with patch.object(dispatcher, "_resolve_approval") as mock_resolve:
            mock_resolve.return_value = DispatchResult("approved", is_command=True)
            dispatcher.dispatch(
                "sess_1", action="approve_mutable_workspace", target_id="approval_1"
            )
            mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_approval
# ---------------------------------------------------------------------------


class TestResolveApproval:
    def test_not_found(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = None
        result = dispatcher._resolve_approval(
            "sess_1", action="approve_once", approval_id="missing"
        )
        assert result.is_command is True

    def test_deny_action(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = _FakeApproval()
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = dispatcher._resolve_approval(
                "sess_1", action="deny", approval_id="approval_1", reason="not needed"
            )
        assert result.is_command is True

    def test_approve_once_sync_dispatch(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = _FakeApproval()
        # sync dispatch — no async ingress metadata
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        mock_runner = dispatcher._runner
        mock_runner.agent.resume.return_value = MagicMock(
            text="resumed",
            input_tokens=5,
            output_tokens=10,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            messages=[],
            suspended=False,
            blocked=False,
            status_managed_by_kernel=False,
        )
        mock_runner._max_session_messages.return_value = 100
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = dispatcher._resolve_approval(
                "sess_1", action="approve_once", approval_id="approval_1"
            )
        assert result.is_command is False

    def test_approve_mutable_workspace_sync_dispatch(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = _FakeApproval()
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        mock_runner = dispatcher._runner
        mock_runner.agent.resume.return_value = MagicMock(
            text="resumed",
            input_tokens=5,
            output_tokens=10,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            messages=[],
            suspended=False,
            blocked=False,
            status_managed_by_kernel=False,
        )
        mock_runner._max_session_messages.return_value = 100
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService") as MockAS:
            dispatcher._resolve_approval(
                "sess_1", action="approve_mutable_workspace", approval_id="approval_1"
            )
            MockAS.return_value.approve_mutable_workspace.assert_called_once()

    def test_approve_async_dispatch_enqueues_resume(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = _FakeApproval()
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "async"}}
        )
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = dispatcher._resolve_approval(
                "sess_1", action="approve_once", approval_id="approval_1"
            )
        assert result.is_command is True
        mock_task_controller.enqueue_resume.assert_called_once()

    def test_approve_sync_suspended_result(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_approval.return_value = _FakeApproval()
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        mock_runner = dispatcher._runner
        mock_runner.agent.resume.return_value = MagicMock(
            text="blocked",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            messages=[],
            suspended=True,
            blocked=False,
            status_managed_by_kernel=False,
            waiting_kind="awaiting_approval",
        )
        mock_runner._max_session_messages.return_value = 100
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = dispatcher._resolve_approval(
                "sess_1", action="approve_once", approval_id="approval_1"
            )
        assert result.is_command is False


# ---------------------------------------------------------------------------
# _is_async_dispatch
# ---------------------------------------------------------------------------


class TestIsAsyncDispatch:
    def test_returns_true_for_async(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "async"}}
        )
        assert dispatcher._is_async_dispatch("attempt_1") is True

    def test_returns_false_for_sync(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "sync"}}
        )
        assert dispatcher._is_async_dispatch("attempt_1") is False

    def test_returns_false_when_attempt_not_found(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_step_attempt.return_value = None
        assert dispatcher._is_async_dispatch("missing") is False

    def test_returns_false_when_no_ingress_metadata(
        self, dispatcher: ControlActionDispatcher, mock_task_controller: MagicMock
    ) -> None:
        mock_task_controller.store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        assert dispatcher._is_async_dispatch("attempt_1") is False


# ---------------------------------------------------------------------------
# Planning helpers
# ---------------------------------------------------------------------------


class TestPlanningHelpers:
    def test_plan_enter_no_planning(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher._plan_enter(None, "task_1", "sess_1")
        assert result.is_command is True

    def test_plan_enter_with_target(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        result = dispatcher._plan_enter(planning, "task_1", "sess_1")
        assert result.is_command is True
        planning.enter_planning.assert_called_once_with("task_1")

    def test_plan_enter_without_target(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        result = dispatcher._plan_enter(planning, "", "sess_1")
        assert result.is_command is True
        planning.set_pending_for_conversation.assert_called_once_with("sess_1", enabled=True)

    def test_plan_exit_no_planning(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher._plan_exit(None, "task_1", "sess_1")
        assert result.is_command is True

    def test_plan_exit_with_target(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        result = dispatcher._plan_exit(planning, "task_1", "sess_1")
        assert result.is_command is True
        planning.exit_planning.assert_called_once_with("task_1")
        planning.set_pending_for_conversation.assert_called_once_with("sess_1", enabled=False)

    def test_plan_exit_without_target(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        result = dispatcher._plan_exit(planning, "", "sess_1")
        assert result.is_command is True
        planning.set_pending_for_conversation.assert_called_once_with("sess_1", enabled=False)

    def test_plan_confirm_no_planning(self, dispatcher: ControlActionDispatcher) -> None:
        result = dispatcher._plan_confirm(None, "task_1")
        assert result.is_command is True

    def test_plan_confirm_no_target(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        result = dispatcher._plan_confirm(planning, "")
        assert result.is_command is True

    def test_plan_confirm_no_plan_text(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        planning.load_selected_plan_text.return_value = ""
        planning.latest_planning_attempt.return_value = None
        result = dispatcher._plan_confirm(planning, "task_1")
        assert result.is_command is True

    def test_plan_confirm_no_plan_ctx(self, dispatcher: ControlActionDispatcher) -> None:
        planning = MagicMock()
        planning.load_selected_plan_text.return_value = "Some plan text"
        planning.latest_planning_attempt.return_value = None
        result = dispatcher._plan_confirm(planning, "task_1")
        assert result.is_command is True

    def test_plan_confirm_executes_plan(
        self, dispatcher: ControlActionDispatcher, mock_runner: MagicMock
    ) -> None:
        planning = MagicMock()
        planning.load_selected_plan_text.return_value = "Do step 1, then step 2"
        plan_ctx = MagicMock()
        plan_ctx.step_attempt_id = "attempt_plan"
        plan_ctx.step_id = "step_plan"
        plan_ctx.workspace_root = "/tmp"
        planning.latest_planning_attempt.return_value = plan_ctx
        planning.state_for_task.return_value = MagicMock(selected_plan_ref="ref_1")

        mock_runner.agent.kernel_store = MagicMock()
        attempt = MagicMock()
        attempt.status = "awaiting_plan_confirmation"
        mock_runner.agent.kernel_store.get_step_attempt.return_value = attempt

        dispatcher._task_controller.store.get_step_attempt.return_value = attempt
        dispatcher._task_controller.start_followup_step.return_value = _FakeTaskCtx()

        mock_runner._run_existing_task.return_value = MagicMock(text="Executed!")

        result = dispatcher._plan_confirm(planning, "task_1")
        assert result.is_command is False
        planning.confirm_selected_plan.assert_called_once()
