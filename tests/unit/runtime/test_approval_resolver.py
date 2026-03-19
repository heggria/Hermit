"""Tests for ApprovalResolver — especially async/DAG approval path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.runtime.control.runner.approval_resolver import ApprovalResolver


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
    attempt: int = 1
    approval_id: str | None = None
    waiting_reason: str | None = None


@dataclass
class _FakeTaskCtx:
    conversation_id: str = "conv_1"
    task_id: str = "task_1"
    step_id: str = "step_1"
    step_attempt_id: str = "attempt_1"
    source_channel: str = "test"
    policy_profile: str = "autonomous"
    workspace_root: str = ""
    ingress_metadata: dict[str, Any] = field(default_factory=dict)


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    store.get_approval.return_value = _FakeApproval()
    store.get_step_attempt.return_value = _FakeStepAttempt()
    return store


@pytest.fixture()
def mock_controller() -> MagicMock:
    controller = MagicMock()
    controller.context_for_attempt.return_value = _FakeTaskCtx()
    controller.enqueue_resume.return_value = _FakeTaskCtx()
    return controller


@pytest.fixture()
def resolver(mock_store: MagicMock, mock_controller: MagicMock) -> ApprovalResolver:
    return ApprovalResolver(store=mock_store, task_controller=mock_controller)


class TestIsAsyncDispatch:
    def test_async_dispatch_detected(
        self, resolver: ApprovalResolver, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "async"}}
        )
        assert resolver._is_async_dispatch("attempt_1") is True

    def test_sync_dispatch_detected(
        self, resolver: ApprovalResolver, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "sync"}}
        )
        assert resolver._is_async_dispatch("attempt_1") is False

    def test_no_dispatch_mode_is_sync(
        self, resolver: ApprovalResolver, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        assert resolver._is_async_dispatch("attempt_1") is False

    def test_missing_attempt_is_sync(
        self, resolver: ApprovalResolver, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = None
        assert resolver._is_async_dispatch("attempt_1") is False

    def test_empty_context_is_sync(self, resolver: ApprovalResolver, mock_store: MagicMock) -> None:
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {}}
        )
        assert resolver._is_async_dispatch("attempt_1") is False


class TestResolveApprovalAsyncPath:
    """The core bug fix: async/DAG approvals must use enqueue_resume, not agent.resume."""

    def test_async_approval_uses_enqueue_resume(
        self,
        resolver: ApprovalResolver,
        mock_store: MagicMock,
        mock_controller: MagicMock,
    ) -> None:
        # Set up async dispatch step
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(
            context={"ingress_metadata": {"dispatch_mode": "async"}}
        )
        mock_agent = MagicMock()
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_or_create.return_value = MagicMock(messages=[])

        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = resolver.resolve_approval(
                "session_1",
                action="approve",
                approval_id="approval_1",
                session_manager=mock_session_mgr,
                agent=mock_agent,
                pm=MagicMock(),
                result_status_fn=lambda r: "succeeded",
            )

        # enqueue_resume MUST be called instead of agent.resume
        mock_controller.enqueue_resume.assert_called_once_with("attempt_1")
        # agent.resume MUST NOT be called
        mock_agent.resume.assert_not_called()
        # finalize_result MUST NOT be called (dispatch loop handles it)
        mock_controller.finalize_result.assert_not_called()
        # Result should indicate the step was re-queued
        assert result.is_command is True

    def test_sync_approval_uses_agent_resume(
        self,
        resolver: ApprovalResolver,
        mock_store: MagicMock,
        mock_controller: MagicMock,
    ) -> None:
        # Set up sync dispatch step (no dispatch_mode)
        mock_store.get_step_attempt.return_value = _FakeStepAttempt(context={})
        mock_agent = MagicMock()
        mock_result = MagicMock(
            suspended=False,
            blocked=False,
            status_managed_by_kernel=False,
            text="done",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            messages=[],
        )
        mock_agent.resume.return_value = mock_result
        mock_session = MagicMock(
            messages=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read_tokens=0,
            total_cache_creation_tokens=0,
        )
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_or_create.return_value = mock_session

        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = resolver.resolve_approval(
                "session_1",
                action="approve",
                approval_id="approval_1",
                session_manager=mock_session_mgr,
                agent=mock_agent,
                pm=MagicMock(),
                result_status_fn=lambda r: "succeeded",
            )

        # Sync path: agent.resume IS called
        mock_agent.resume.assert_called_once()
        # enqueue_resume is NOT called
        mock_controller.enqueue_resume.assert_not_called()
        # Result is not a command (it's an agent result)
        assert result.is_command is False

    def test_deny_action_unchanged(
        self,
        resolver: ApprovalResolver,
        mock_store: MagicMock,
        mock_controller: MagicMock,
    ) -> None:
        mock_session = MagicMock(messages=[])
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_or_create.return_value = mock_session

        with patch(
            "hermit.kernel.policy.approvals.approvals.ApprovalService"
        ) as MockApprovalService:
            result = resolver.resolve_approval(
                "session_1",
                action="deny",
                approval_id="approval_1",
                session_manager=mock_session_mgr,
                agent=MagicMock(),
                pm=MagicMock(),
                result_status_fn=lambda r: "succeeded",
            )

        # Deny path is unchanged
        MockApprovalService.return_value.deny.assert_called_once()
        mock_controller.enqueue_resume.assert_not_called()
        assert result.is_command is True
