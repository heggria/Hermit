"""Tests that verify the governance pipeline prevents premature tool execution.

The core invariant: no tool handler invocation should occur without a prior
CapabilityGrant being issued for governed actions.  Non-governed (readonly)
actions may proceed without a grant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError, CapabilityGrantService
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision, PolicyEngine, PolicyObligations
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


@pytest.fixture
def artifact_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "kernel" / "artifacts")


@pytest.fixture
def controller(store: KernelStore) -> TaskController:
    return TaskController(store)


@pytest.fixture
def attempt_ctx(controller: TaskController, tmp_path: Path) -> TaskExecutionContext:
    return controller.start_task(
        conversation_id="conv_gov_test",
        goal="test governance ordering",
        source_channel="test",
        kind="respond",
        workspace_root=str(tmp_path),
    )


def _make_write_tool(handler: Any = None) -> ToolSpec:
    """Create a governed write_file tool spec."""
    return ToolSpec(
        name="write_file",
        description="Write a file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=handler or (lambda payload: "ok"),
        action_class="write_local",
        resource_scope_hint="/workspace",
        risk_hint="high",
        requires_receipt=True,
        supports_preview=True,
    )


def _make_read_tool(handler: Any = None) -> ToolSpec:
    """Create a non-governed readonly tool spec."""
    return ToolSpec(
        name="read_file",
        description="Read a file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=handler or (lambda payload: "file contents"),
        readonly=True,
        action_class="read_local",
        resource_scope_hint="/workspace",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    )


def _build_executor(
    store: KernelStore,
    artifact_store: ArtifactStore,
    registry: ToolRegistry,
    *,
    capability_service: CapabilityGrantService | None = None,
) -> ToolExecutor:
    """Build a ToolExecutor with real kernel services."""
    return ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifact_store,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store, artifact_store),
        capability_service=capability_service or CapabilityGrantService(store),
    )


# ===========================================================================
# 1. CapabilityGrantError prevents tool handler invocation
# ===========================================================================


class TestCapabilityGrantBlocksExecution:
    """When CapabilityGrantService.enforce() raises, the tool handler must
    NOT be invoked."""

    def test_handler_not_called_when_enforce_raises(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """If enforce() raises CapabilityGrantError, invoke_tool_handler must
        not be called and the result should be denied."""
        handler = MagicMock(return_value="ok")
        write_tool = _make_write_tool(handler)
        registry = ToolRegistry()
        registry.register(write_tool)

        # Build a capability service where enforce always raises
        cap_service = CapabilityGrantService(store)

        def failing_enforce(grant_id, **kwargs):
            raise CapabilityGrantError(
                "scope_mismatch",
                f"Capability grant {grant_id} does not cover resource scope.",
            )

        cap_service.enforce = failing_enforce

        executor = _build_executor(store, artifact_store, registry, capability_service=cap_service)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )

        handler.assert_not_called()
        assert result.denied is True
        assert result.result_code == "dispatch_denied"

    def test_handler_not_called_when_grant_expired(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """If enforce() raises due to expiration, the handler must not run."""
        handler = MagicMock(return_value="ok")
        write_tool = _make_write_tool(handler)
        registry = ToolRegistry()
        registry.register(write_tool)

        cap_service = CapabilityGrantService(store, default_ttl_seconds=0)

        def expired_enforce(grant_id, **kwargs):
            raise CapabilityGrantError(
                "expired",
                f"Capability grant {grant_id} expired before dispatch.",
            )

        cap_service.enforce = expired_enforce

        executor = _build_executor(store, artifact_store, registry, capability_service=cap_service)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )

        handler.assert_not_called()
        assert result.denied is True


# ===========================================================================
# 2. Execution trace ordering: policy evaluation BEFORE tool invocation
# ===========================================================================


class TestPolicyBeforeExecution:
    """Verify that the execution trace shows policy evaluation happening
    before tool invocation by inspecting phase-changed events."""

    def test_governed_action_phase_order(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """For a governed write, policy_pending must be recorded before
        executing phase."""
        invocation_order: list[str] = []

        def tracking_handler(payload):
            invocation_order.append("handler_invoked")
            return "ok"

        write_tool = _make_write_tool(tracking_handler)
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )

        assert result.result_code == "succeeded"
        assert "handler_invoked" in invocation_order

        # Check event ordering in the ledger
        events = store.list_events(
            task_id=attempt_ctx.task_id,
            event_type="step_attempt.phase_changed",
        )
        phase_sequence = [e["payload"]["phase"] for e in events]

        # policy_pending must come before executing
        assert "policy_pending" in phase_sequence
        assert "executing" in phase_sequence
        policy_idx = phase_sequence.index("policy_pending")
        exec_idx = phase_sequence.index("executing")
        assert policy_idx < exec_idx, (
            f"policy_pending (idx={policy_idx}) must precede "
            f"executing (idx={exec_idx}) in phase sequence: {phase_sequence}"
        )

    def test_governed_action_has_capability_grant(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """A governed action that succeeds must have a capability_grant_id
        in the result."""
        write_tool = _make_write_tool(lambda payload: "ok")
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )

        assert result.result_code == "succeeded"
        assert result.capability_grant_id is not None
        assert result.policy_ref is not None

    def test_authorized_pre_exec_phase_present(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """For governed actions, authorized_pre_exec phase must appear
        between policy_pending and executing."""
        write_tool = _make_write_tool(lambda payload: "ok")
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )
        assert result.result_code == "succeeded"

        events = store.list_events(
            task_id=attempt_ctx.task_id,
            event_type="step_attempt.phase_changed",
        )
        phase_sequence = [e["payload"]["phase"] for e in events]
        assert "authorized_pre_exec" in phase_sequence
        auth_idx = phase_sequence.index("authorized_pre_exec")
        exec_idx = phase_sequence.index("executing")
        assert auth_idx < exec_idx


# ===========================================================================
# 3. Non-governed (readonly) actions bypass capability grants
# ===========================================================================


class TestReadonlyBypassesGrant:
    """Readonly tools should execute without a capability grant."""

    def test_readonly_tool_executes_without_grant(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """A readonly tool should succeed and have no capability_grant_id."""
        handler = MagicMock(return_value="file contents")
        read_tool = _make_read_tool(handler)
        registry = ToolRegistry()
        registry.register(read_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "read_file",
            {"path": "test.txt"},
        )

        handler.assert_called_once()
        assert result.result_code == "succeeded"
        assert result.capability_grant_id is None

    def test_readonly_tool_has_no_receipt(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """A readonly tool that declares requires_receipt=False should
        produce no receipt."""
        read_tool = _make_read_tool(lambda payload: "contents")
        registry = ToolRegistry()
        registry.register(read_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "read_file",
            {"path": "test.txt"},
        )

        assert result.receipt_id is None
        assert result.result_code == "succeeded"

    def test_readonly_skips_governed_phases(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """Readonly tools should not go through authorized_pre_exec or
        settling phases."""
        read_tool = _make_read_tool(lambda payload: "contents")
        registry = ToolRegistry()
        registry.register(read_tool)

        executor = _build_executor(store, artifact_store, registry)
        result = executor.execute(
            attempt_ctx,
            "read_file",
            {"path": "test.txt"},
        )
        assert result.result_code == "succeeded"

        events = store.list_events(
            task_id=attempt_ctx.task_id,
            event_type="step_attempt.phase_changed",
        )
        phase_sequence = [e["payload"]["phase"] for e in events]
        assert "authorized_pre_exec" not in phase_sequence
        assert "settling" not in phase_sequence


# ===========================================================================
# 4. Policy denial prevents tool execution entirely
# ===========================================================================


class TestPolicyDenialBlocksExecution:
    """When policy verdict is 'deny', the tool handler must not be invoked."""

    def test_denied_action_does_not_invoke_handler(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """If the policy engine returns deny, handler must not be called."""
        handler = MagicMock(return_value="ok")
        write_tool = _make_write_tool(handler)
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry)

        # Patch policy engine to always deny
        deny_decision = PolicyDecision(
            verdict="deny",
            action_class="write_local",
            obligations=PolicyObligations(require_receipt=True),
        )
        with patch.object(executor.policy_engine, "evaluate", return_value=deny_decision):
            result = executor.execute(
                attempt_ctx,
                "write_file",
                {"path": "forbidden.txt", "content": "nope"},
            )

        handler.assert_not_called()
        assert result.denied is True
        assert result.result_code == "denied"
        assert result.capability_grant_id is None

    def test_denied_action_emits_policy_denied_event(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """A policy denial must emit a policy.denied event in the ledger."""
        write_tool = _make_write_tool(lambda payload: "ok")
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry)

        deny_decision = PolicyDecision(
            verdict="deny",
            action_class="write_local",
            obligations=PolicyObligations(require_receipt=True),
        )
        with patch.object(executor.policy_engine, "evaluate", return_value=deny_decision):
            executor.execute(
                attempt_ctx,
                "write_file",
                {"path": "forbidden.txt", "content": "nope"},
            )

        denied_events = store.list_events(
            task_id=attempt_ctx.task_id,
            event_type="policy.denied",
        )
        assert len(denied_events) >= 1
        payload = denied_events[0]["payload"]
        assert payload["tool_name"] == "write_file"


# ===========================================================================
# 5. Grant issuance precedes handler invocation (end-to-end ordering)
# ===========================================================================


class TestGrantIssuancePrecedesHandler:
    """End-to-end check that grant issuance is recorded before the tool
    handler runs."""

    def test_grant_issued_before_handler_call(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        attempt_ctx: TaskExecutionContext,
    ) -> None:
        """Record timestamps to prove grant issuance happens before handler."""
        execution_log: list[str] = []

        cap_service = CapabilityGrantService(store)
        original_issue = cap_service.issue

        def tracked_issue(**kwargs):
            grant_id = original_issue(**kwargs)
            execution_log.append(f"grant_issued:{grant_id}")
            return grant_id

        cap_service.issue = tracked_issue

        def tracked_handler(payload):
            execution_log.append("handler_executed")
            return "ok"

        write_tool = _make_write_tool(tracked_handler)
        registry = ToolRegistry()
        registry.register(write_tool)

        executor = _build_executor(store, artifact_store, registry, capability_service=cap_service)
        result = executor.execute(
            attempt_ctx,
            "write_file",
            {"path": "test.txt", "content": "hello"},
        )

        assert result.result_code == "succeeded"
        assert len(execution_log) == 2
        assert execution_log[0].startswith("grant_issued:")
        assert execution_log[1] == "handler_executed"
