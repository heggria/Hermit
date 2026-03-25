"""Unit tests for the Hermit MCP Server plugin."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.plugins.builtin.mcp.hermit_server import hooks as mcp_hooks
from hermit.plugins.builtin.mcp.hermit_server.server import (
    _SLIM_TASK_LIST_FIELDS,
    HermitMcpServer,
    _action_summary,
    _slim_approval,
    _slim_event,
    _slim_task,
)
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Fixtures / Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeApproval:
    approval_id: str = "apr-1"
    task_id: str = "task-1"
    step_id: str = "step-1"
    step_attempt_id: str = "sa-1"
    status: str = "pending"
    approval_type: str = "tool_execution"
    requested_action: dict[str, Any] = field(default_factory=dict)
    requested_at: str = "2026-01-01T00:00:00"
    expires_at: str | None = None
    resolved_at: str | None = None
    # Extra fields that should be stripped by _slim_approval
    resolution: dict[str, Any] = field(default_factory=dict)
    request_packet_ref: str | None = None
    requested_action_ref: str | None = None
    approval_packet_ref: str | None = None


@dataclass
class FakeTask:
    task_id: str = "task-1"
    conversation_id: str = "conv-1"
    title: str = "Test task"
    goal: str = "Do something"
    status: str = "running"
    priority: str = "normal"
    owner_principal_id: str = "hermit"
    created_at: str = "2026-01-01T00:00:00"
    updated_at: str = "2026-01-01T00:00:00"
    budget_tokens_used: int = 0
    # Extra fields that should be stripped by _slim_task
    policy_profile: str = "autonomous"
    source_channel: str = "mcp-supervisor"
    parent_task_id: str | None = None
    budget_tokens_limit: int | None = None


@dataclass
class FakeReceipt:
    receipt_id: str = "rcpt-1"
    task_id: str = "task-1"
    step_id: str = "step-1"
    step_attempt_id: str = "sa-1"
    action_type: str = "bash"
    result_code: str = "succeeded"
    result_summary: str = "Ran pytest: 14 tests passed"
    observed_effect_summary: str | None = "Modified 2 files"
    rollback_supported: bool = True


class FakeStore:
    """Minimal mock of KernelStore for tool handler tests."""

    def __init__(self) -> None:
        self.tasks: dict[str, FakeTask] = {"task-1": FakeTask()}
        self.approvals: list[FakeApproval] = [FakeApproval()]
        self.events: list[dict[str, Any]] = [{"type": "task_created", "task_id": "task-1"}]
        self.receipts: list[FakeReceipt] = []
        self.status_updates: list[tuple[str, str, dict[str, Any] | None]] = []
        self.lessons: list[dict[str, Any]] = []
        # Event-driven task change notification (avoids 0.5s fallback poll)
        import threading

        self._task_change_listeners_lock = threading.Lock()
        self._task_change_listeners: dict[str, list[threading.Event]] = {}

    def get_task(self, task_id: str) -> FakeTask | None:
        return self.tasks.get(task_id)

    def list_tasks(
        self,
        *,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[FakeTask]:
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks[:limit]

    def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[FakeApproval]:
        approvals = list(self.approvals)
        if task_id:
            approvals = [a for a in approvals if a.task_id == task_id]
        if status:
            approvals = [a for a in approvals if a.status == status]
        return approvals[:limit]

    def get_approval(self, approval_id: str) -> FakeApproval | None:
        for a in self.approvals:
            if a.approval_id == approval_id:
                return a
        return None

    def list_events(self, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.events[:limit])

    def list_receipts(self, *, task_id: str | None = None, limit: int = 50) -> list[FakeReceipt]:
        receipts = list(self.receipts)
        if task_id:
            receipts = [r for r in receipts if r.task_id == task_id]
        return receipts[:limit]

    def list_lessons_learned(
        self,
        applicable_to: str | None = None,
        categories: list[str] | None = None,
        iteration_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        result = list(self.lessons)
        if applicable_to is not None:
            result = [
                item for item in result if applicable_to in str(item.get("applicable_files", ""))
            ]
        if categories:
            result = [item for item in result if item.get("category") in categories]
        if iteration_ids:
            result = [item for item in result if item.get("iteration_id") in iteration_ids]
        return result[:limit]

    def update_task_status(
        self, task_id: str, status: str, *, payload: dict[str, Any] | None = None
    ) -> None:
        self.status_updates.append((task_id, status, payload))
        task = self.tasks.get(task_id)
        if task:
            task.status = status
            self._notify_listeners(task_id)

    def register_task_change_listener(
        self, task_ids: list[str], shared_event: threading.Event
    ) -> None:
        with self._task_change_listeners_lock:
            for tid in task_ids:
                self._task_change_listeners.setdefault(tid, []).append(shared_event)
                # Immediately wake if the task is already non-running (blocked/completed/etc.)
                # This avoids waiting for a full timeout on pre-existing terminal/blocked states.
                task = self.tasks.get(tid)
                if task and task.status != "running":
                    shared_event.set()

    def deregister_task_change_listener(
        self, task_ids: list[str], shared_event: threading.Event
    ) -> None:
        with self._task_change_listeners_lock:
            for tid in task_ids:
                lst = self._task_change_listeners.get(tid)
                if lst is not None:
                    try:
                        lst.remove(shared_event)
                    except ValueError:
                        pass

    def _notify_listeners(self, task_id: str) -> None:
        with self._task_change_listeners_lock:
            for ev in self._task_change_listeners.get(task_id, []):
                ev.set()


class FakeRunner:
    """Minimal mock of AgentRunner."""

    def __init__(self, store: FakeStore | None = None) -> None:
        self.task_controller = SimpleNamespace(store=store or FakeStore())
        self.ingress_calls: list[dict[str, Any]] = []
        self.approval_calls: list[dict[str, Any]] = []
        self._ingress_counter = 0

    def wake_dispatcher(self) -> None:
        """No-op stub: async dispatch is not exercised in unit tests."""
        pass

    def enqueue_ingress(
        self,
        session_id: str,
        text: str,
        *,
        source_channel: str | None = None,
        notify: dict[str, object] | None = None,
        source_ref: str = "",
        ingress_metadata: dict[str, object] | None = None,
        requested_by: str | None = "user",
        parent_task_id: Any = None,
    ) -> SimpleNamespace:
        self._ingress_counter += 1
        task_id = f"task-ingress-{self._ingress_counter}"
        self.ingress_calls.append(
            {
                "session_id": session_id,
                "text": text,
                "source_channel": source_channel,
                "source_ref": source_ref,
                "requested_by": requested_by,
                "ingress_metadata": ingress_metadata,
                "task_id": task_id,
            }
        )
        return SimpleNamespace(task_id=task_id)

    def _resolve_approval(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        on_tool_call: Any = None,
        on_tool_start: Any = None,
    ) -> SimpleNamespace:
        self.approval_calls.append(
            {
                "session_id": session_id,
                "action": action,
                "approval_id": approval_id,
                "reason": reason,
            }
        )
        return SimpleNamespace(text=f"{action}d {approval_id}")


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def runner(store: FakeStore) -> FakeRunner:
    return FakeRunner(store)


@pytest.fixture
def server(runner: FakeRunner) -> HermitMcpServer:
    srv = HermitMcpServer(host="127.0.0.1", port=0)
    srv._runner = runner
    return srv


def _call_tool(server: HermitMcpServer, name: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke an MCP tool handler directly via the FastMCP instance."""
    import asyncio
    import json

    loop = asyncio.new_event_loop()
    try:
        raw = loop.run_until_complete(server._mcp.call_tool(name, kwargs))
    finally:
        loop.close()
    # call_tool returns (content_list, is_error) or just a list — normalise
    content_list = raw[0] if isinstance(raw, tuple) else raw

    for item in content_list:
        if hasattr(item, "text"):
            return json.loads(item.text)
    raise AssertionError(f"No text content returned from tool {name}: {raw!r}")


# ---------------------------------------------------------------------------
# Tool handler tests
# ---------------------------------------------------------------------------


class TestHermitSubmit:
    """Tests for the unified hermit_submit MCP tool."""

    # ── SINGLE mode ──

    def test_single_task_returns_accepted_with_task_id(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        result = _call_tool(server, "hermit_submit", description="Build feature X")
        assert result["status"] == "accepted"
        assert result["session_id"].startswith("mcp-supervisor-")
        assert result["task_id"] == "task-ingress-1"
        assert result["task_ids"] == ["task-ingress-1"]
        assert len(runner.ingress_calls) == 1
        assert runner.ingress_calls[0]["text"] == "Build feature X"
        assert runner.ingress_calls[0]["source_channel"] == "mcp-supervisor"

    def test_single_with_priority(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(
            server,
            "hermit_submit",
            description="Urgent fix",
            priority="high",
        )
        assert result["status"] == "accepted"
        assert result["task_id"] is not None
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["priority"] == "high"

    def test_single_with_policy_profile(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(
            server,
            "hermit_submit",
            description="Supervised task",
            policy_profile="supervised",
        )
        assert result["status"] == "accepted"
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["policy_profile"] == "supervised"

    def test_single_no_runner_raises(self) -> None:
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        with pytest.raises(Exception, match="Runner is not attached"):
            _call_tool(srv, "hermit_submit", description="fail")

    def test_error_no_description_no_tasks(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_submit")
        assert "error" in result

    def test_task_ids_always_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_submit", description="Single")
        assert isinstance(result["task_ids"], list)

    def test_empty_tasks_with_description_is_single(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        result = _call_tool(
            server,
            "hermit_submit",
            description="Single mode",
            tasks=[],
        )
        # Empty tasks list → single mode (description takes precedence)
        assert result["status"] == "accepted"
        assert result["task_id"] == "task-ingress-1"

    # ── BATCH mode ──

    def test_batch_multiple_tasks(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(
            server,
            "hermit_submit",
            tasks=[
                {"description": "Task A"},
                {"description": "Task B", "priority": "high"},
                {"description": "Task C", "policy_profile": "supervised"},
            ],
        )
        assert result["submitted"] == 3
        assert len(result["task_ids"]) == 3
        assert len(result["results"]) == 3
        assert all(r["status"] == "accepted" for r in result["results"])
        assert len(runner.ingress_calls) == 3
        assert runner.ingress_calls[0]["text"] == "Task A"
        assert runner.ingress_calls[1]["ingress_metadata"]["priority"] == "high"
        assert runner.ingress_calls[2]["ingress_metadata"]["policy_profile"] == "supervised"

    def test_batch_missing_description(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(
            server,
            "hermit_submit",
            tasks=[
                {"description": "Good task"},
                {"priority": "high"},  # missing description
            ],
        )
        assert result["submitted"] == 1
        assert len(result["task_ids"]) == 1
        assert result["results"][1]["error"] == "Missing 'description' field"

    def test_batch_empty_list_with_description(self, server: HermitMcpServer) -> None:
        """Empty tasks + description → single mode, not batch error."""
        result = _call_tool(
            server,
            "hermit_submit",
            description="Fallback",
            tasks=[],
        )
        assert result["status"] == "accepted"

    def test_batch_default_policy_is_autonomous(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        _call_tool(
            server,
            "hermit_submit",
            tasks=[{"description": "Default policy task"}],
        )
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["policy_profile"] == "autonomous"

    def test_batch_step_overrides(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        """Per-step priority/policy_profile override the defaults."""
        _call_tool(
            server,
            "hermit_submit",
            priority="low",
            policy_profile="supervised",
            tasks=[
                {"description": "Override", "priority": "high", "policy_profile": "autonomous"},
            ],
        )
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["priority"] == "high"
        assert meta["policy_profile"] == "autonomous"

    # ── AWAIT mode ──

    def test_await_single_completed(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """Single task with await_completion returns terminal result."""
        import threading

        store.tasks["task-ingress-1"] = FakeTask(
            task_id="task-ingress-1",
            status="running",
        )

        def complete_task() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-ingress-1"].status = "completed"
            store._notify_listeners("task-ingress-1")

        t = threading.Thread(target=complete_task)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_submit",
                description="Quick task",
                await_completion=3,
            )
            assert result["task_ids"] == ["task-ingress-1"]
            assert result["status"] == "completed"
            assert "timed_out" not in result
        finally:
            t.join(timeout=3)

    def test_await_single_timeout(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """Task doesn't complete within await_completion timeout."""
        store.tasks["task-ingress-1"] = FakeTask(
            task_id="task-ingress-1",
            status="running",
        )
        result = _call_tool(
            server,
            "hermit_submit",
            description="Slow task",
            await_completion=1,
        )
        assert result["timed_out"] is True

    def test_await_single_blocked(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """Task becomes blocked — returns immediately with approvals."""
        store.tasks["task-ingress-1"] = FakeTask(
            task_id="task-ingress-1",
            status="blocked",
        )
        store.approvals.append(
            FakeApproval(approval_id="apr-block", task_id="task-ingress-1"),
        )
        result = _call_tool(
            server,
            "hermit_submit",
            description="Blocked task",
            await_completion=2,
        )
        assert result["status"] == "blocked"
        assert len(result["pending_approvals"]) >= 1

    def test_await_batch_all(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """Batch with await_completion waits for all tasks."""
        import threading

        # Pre-seed tasks that will be created by enqueue_ingress
        store.tasks["task-ingress-1"] = FakeTask(
            task_id="task-ingress-1",
            status="running",
        )
        store.tasks["task-ingress-2"] = FakeTask(
            task_id="task-ingress-2",
            status="running",
        )

        def complete_tasks() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-ingress-1"].status = "completed"
            store._notify_listeners("task-ingress-1")
            time.sleep(0.02)
            store.tasks["task-ingress-2"].status = "completed"
            store._notify_listeners("task-ingress-2")

        t = threading.Thread(target=complete_tasks)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_submit",
                tasks=[{"description": "A"}, {"description": "B"}],
                await_completion=3,
            )
            assert result["submitted"] == 2
            assert result["timed_out"] is False
            assert "task-ingress-1" in result["completed"]
            assert "task-ingress-2" in result["completed"]
        finally:
            t.join(timeout=3)


class TestHermitTaskStatus:
    def test_task_status_single(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_ids=["task-1"])
        assert result["count"] == 1
        assert result["tasks"][0]["task_id"] == "task-1"
        assert result["tasks"][0]["task"]["task_id"] == "task-1"
        assert result["tasks"][0]["is_blocked"] is True
        assert len(result["tasks"][0]["pending_approvals"]) == 1

    def test_task_status_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_ids=["nonexistent"])
        assert result["count"] == 1
        assert result["tasks"][0]["error"] == "Task not found"

    def test_task_status_batch_mixed(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_ids=["task-1", "nonexistent"])
        assert result["count"] == 2
        assert result["tasks"][0]["task"]["task_id"] == "task-1"
        assert result["tasks"][1]["error"] == "Task not found"

    def test_task_status_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_ids=[])
        assert result["count"] == 0
        assert result["tasks"] == []


class TestHermitListTasks:
    def test_list_tasks_returns_all(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_list_tasks")
        assert result["count"] == 1
        assert result["tasks"][0]["task_id"] == "task-1"

    def test_list_tasks_with_status_filter(self, server: HermitMcpServer, store: FakeStore) -> None:
        result = _call_tool(server, "hermit_list_tasks", status="completed")
        assert result["count"] == 0

    def test_list_tasks_limit_capped_at_50(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_list_tasks", limit=100)
        # Should not error — limit is internally capped
        assert "tasks" in result


class TestHermitPendingApprovals:
    def test_pending_approvals_returns_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_pending_approvals")
        assert result["count"] == 1
        assert result["approvals"][0]["approval_id"] == "apr-1"

    def test_pending_approvals_filtered_by_task(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_pending_approvals", task_id="nonexistent")
        assert result["count"] == 0


class TestHermitApprove:
    def test_approve_single(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_approve", approval_ids=["apr-1"])
        assert result["approved"] == 1
        assert result["errors"] == 0
        assert result["results"][0]["status"] == "approved"
        assert len(runner.approval_calls) == 1
        assert runner.approval_calls[0]["action"] == "approve"

    def test_approve_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_approve", approval_ids=["nonexistent"])
        assert result["approved"] == 0
        assert result["errors"] == 1
        assert result["results"][0]["error"] == "Approval not found"

    def test_approve_batch_mixed(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_approve", approval_ids=["apr-1", "nonexistent"])
        assert result["approved"] == 1
        assert result["errors"] == 1
        assert result["results"][0]["status"] == "approved"
        assert result["results"][1]["error"] == "Approval not found"

    def test_approve_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_approve", approval_ids=[])
        assert result["approved"] == 0
        assert result["errors"] == 0
        assert result["results"] == []


class TestHermitDeny:
    def test_deny_single(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_deny", approval_ids=["apr-1"], reason="Not safe")
        assert result["denied"] == 1
        assert result["errors"] == 0
        assert result["results"][0]["status"] == "denied"
        assert runner.approval_calls[0]["action"] == "deny"
        assert runner.approval_calls[0]["reason"] == "Not safe"

    def test_deny_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_deny", approval_ids=["nonexistent"])
        assert result["denied"] == 0
        assert result["errors"] == 1
        assert result["results"][0]["error"] == "Approval not found"

    def test_deny_batch_mixed(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_deny", approval_ids=["apr-1", "nonexistent"])
        assert result["denied"] == 1
        assert result["errors"] == 1

    def test_deny_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_deny", approval_ids=[])
        assert result["denied"] == 0
        assert result["errors"] == 0


class TestHermitCancelTask:
    def test_cancel_single_running_task(self, server: HermitMcpServer, store: FakeStore) -> None:
        result = _call_tool(
            server, "hermit_cancel_task", task_ids=["task-1"], reason="No longer needed"
        )
        assert result["cancelled"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["results"][0]["status"] == "cancelled"
        assert store.status_updates[0] == (
            "task-1",
            "cancelled",
            {"reason": "No longer needed", "cancelled_by": "supervisor"},
        )

    def test_cancel_nonexistent_task(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_cancel_task", task_ids=["nonexistent"])
        assert result["errors"] == 1
        assert result["cancelled"] == 0
        assert result["results"][0]["error"] == "Task not found"

    def test_cancel_already_completed_task(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-1"].status = "completed"
        result = _call_tool(server, "hermit_cancel_task", task_ids=["task-1"])
        assert result["skipped"] == 1
        assert result["cancelled"] == 0
        assert "already in terminal state" in result["results"][0]["error"]

    def test_cancel_batch_mixed(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-2"] = FakeTask(task_id="task-2", status="completed")
        result = _call_tool(
            server,
            "hermit_cancel_task",
            task_ids=["task-1", "task-2", "nonexistent"],
            reason="Batch cleanup",
        )
        assert result["cancelled"] == 1
        assert result["skipped"] == 1
        assert result["errors"] == 1

    def test_cancel_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_cancel_task", task_ids=[])
        assert result["cancelled"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["results"] == []


class TestHermitTaskProof:
    def test_task_proof_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_proof", task_ids=["nonexistent"])
        assert result["count"] == 1
        assert result["proofs"][0]["error"] == "Task not found"

    def test_task_proof_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_proof", task_ids=[])
        assert result["count"] == 0
        assert result["proofs"] == []


# ---------------------------------------------------------------------------
# Server lifecycle tests
# ---------------------------------------------------------------------------


class TestHermitMetrics:
    """Tests for the unified hermit_metrics MCP tool."""

    def test_health_default(self, server: HermitMcpServer, monkeypatch: pytest.MonkeyPatch) -> None:
        """kind='health' dispatches to TaskHealthMonitor."""
        from types import SimpleNamespace as NS

        health_report = NS(
            health_level=NS(value="healthy"),
            health_score=95,
            total_active_tasks=3,
            total_stale_tasks=0,
            failure_rate=0.0,
            stale_tasks=[],
            throughput=NS(
                completed_tasks=10,
                failed_tasks=0,
                throughput_per_hour=2.5,
                failure_rate=0.0,
            ),
            notes=[],
            scored_at="2026-01-01T00:00:00",
        )

        def fake_check_health(*, stale_threshold_seconds, window_seconds):
            return health_report

        import hermit.kernel.analytics.health.monitor as hmod

        monkeypatch.setattr(
            hmod, "TaskHealthMonitor", lambda store: NS(check_health=fake_check_health)
        )

        result = _call_tool(server, "hermit_metrics", kind="health")
        assert result["kind"] == "health"
        assert result["health_score"] == 95
        assert result["health_level"] == "healthy"

    def test_governance_basic(
        self, server: HermitMcpServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kind='governance' dispatches to AnalyticsEngine."""
        from types import SimpleNamespace as NS

        metrics_result = NS(
            window_start=1000.0,
            window_end=2000.0,
            task_throughput=5,
            approval_rate=0.8,
            avg_approval_latency=1.5,
            rollback_rate=0.1,
            evidence_sufficiency_avg=0.9,
            tool_usage_counts={"bash": 10},
            action_class_distribution={"shell": 10},
            risk_entries=[],
        )

        def fake_compute(*, window_start, window_end, task_id, limit):
            return metrics_result

        import hermit.kernel.analytics.engine as emod

        monkeypatch.setattr(emod, "AnalyticsEngine", lambda store: NS(compute_metrics=fake_compute))

        result = _call_tool(server, "hermit_metrics", kind="governance")
        assert result["kind"] == "governance"
        assert result["task_throughput"] == 5
        assert result["approval_rate"] == 0.8

    def test_task_metrics_basic(
        self, server: HermitMcpServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kind='task' dispatches to TaskMetricsService."""
        from types import SimpleNamespace as NS

        summary = NS(
            tasks=[
                NS(
                    task_id="task-1",
                    task_status="completed",
                    total_steps=3,
                    completed_steps=3,
                    failed_steps=0,
                    skipped_steps=0,
                    total_duration_seconds=10.0,
                    avg_step_duration_seconds=3.3,
                    min_step_duration_seconds=2.0,
                    max_step_duration_seconds=5.0,
                    step_timings=[],
                ),
            ],
            total_tasks=1,
            tasks_with_timing=1,
        )

        def fake_compute(task_ids, *, include_step_timings=False):
            return summary

        import hermit.kernel.analytics.task_metrics as tmod

        monkeypatch.setattr(
            tmod, "TaskMetricsService", lambda store: NS(compute_multi_task_metrics=fake_compute)
        )

        result = _call_tool(
            server,
            "hermit_metrics",
            kind="task",
            task_ids=["task-1"],
        )
        assert result["kind"] == "task"
        assert result["total_tasks"] == 1
        assert result["tasks"][0]["task_id"] == "task-1"

    def test_unknown_kind_error(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_metrics", kind="unknown")
        assert "error" in result
        assert "Unknown kind" in result["error"]

    def test_governance_with_task_id(
        self, server: HermitMcpServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kind='governance' scopes to first task_id."""
        from types import SimpleNamespace as NS

        captured: list[str | None] = []

        def fake_compute(*, window_start, window_end, task_id, limit):
            captured.append(task_id)
            return NS(
                window_start=window_start,
                window_end=window_end,
                task_throughput=0,
                approval_rate=0,
                avg_approval_latency=0,
                rollback_rate=0,
                evidence_sufficiency_avg=0,
                tool_usage_counts={},
                action_class_distribution={},
                risk_entries=[],
            )

        import hermit.kernel.analytics.engine as emod

        monkeypatch.setattr(emod, "AnalyticsEngine", lambda store: NS(compute_metrics=fake_compute))

        _call_tool(
            server,
            "hermit_metrics",
            kind="governance",
            task_ids=["task-1"],
        )
        assert captured == ["task-1"]

    def test_task_missing_task_ids(self, server: HermitMcpServer) -> None:
        """kind='task' without task_ids returns error."""
        result = _call_tool(server, "hermit_metrics", kind="task")
        assert result["kind"] == "task"
        assert "error" in result


class TestHermitAwaitCompletionModeAll:
    """Tests for hermit_await_completion mode='all'."""

    def test_await_all_waits_for_every_task(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """mode='all' waits until all tasks finish, not just the first."""
        import threading

        store.tasks["task-1"].status = "running"
        store.tasks["task-2"] = FakeTask(task_id="task-2", status="running")

        def complete_sequentially() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "completed"
            store._notify_listeners("task-1")
            time.sleep(0.02)
            store.tasks["task-2"].status = "completed"
            store._notify_listeners("task-2")

        t = threading.Thread(target=complete_sequentially)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_await_completion",
                task_ids=["task-1", "task-2"],
                timeout=3,
                mode="all",
            )
            assert result["timed_out"] is False
            assert "task-1" in result["completed"]
            assert "task-2" in result["completed"]
            assert result["pending"] == []
        finally:
            t.join(timeout=3)

    def test_await_all_timeout_returns_partial(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """mode='all' with timeout returns partial results."""
        import threading

        store.tasks["task-1"].status = "running"
        store.tasks["task-2"] = FakeTask(task_id="task-2", status="running")

        def complete_one() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "completed"
            store._notify_listeners("task-1")
            # task-2 stays running

        t = threading.Thread(target=complete_one)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_await_completion",
                task_ids=["task-1", "task-2"],
                timeout=1,
                mode="all",
            )
            assert result["timed_out"] is True
            assert "task-1" in result["completed"]
            pending_ids = [p["task_id"] for p in result["pending"]]
            assert "task-2" in pending_ids
        finally:
            t.join(timeout=3)

    def test_await_any_still_returns_on_first(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """Default mode='any' still returns on first completion."""
        import threading

        store.tasks["task-1"].status = "running"
        store.tasks["task-2"] = FakeTask(task_id="task-2", status="running")

        def complete_one() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "completed"
            store._notify_listeners("task-1")

        t = threading.Thread(target=complete_one)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_await_completion",
                task_ids=["task-1", "task-2"],
                timeout=3,
                mode="any",
            )
            assert result["timed_out"] is False
            assert "task-1" in result["completed"]
            pending_ids = [p["task_id"] for p in result["pending"]]
            assert "task-2" in pending_ids
        finally:
            t.join(timeout=3)


class TestHermitSubmitDagTask:
    """Tests for the hermit_submit_dag_task MCP tool."""

    def test_submit_diamond_dag(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        """Diamond DAG: A → {B, C} → D creates correct topology."""
        from dataclasses import dataclass
        from typing import Any as _Any

        # Build a fake TaskController with start_dag_task
        @dataclass
        class FakeDAGDef:
            roots: list[str]
            leaves: list[str]
            topological_order: list[str]

        call_log: list[dict[str, _Any]] = []

        def fake_start_dag_task(
            *, conversation_id, goal, source_channel, nodes, policy_profile, requested_by, **kw
        ):
            call_log.append(
                {
                    "goal": goal,
                    "nodes": nodes,
                    "policy_profile": policy_profile,
                    "source_channel": source_channel,
                }
            )
            ctx = SimpleNamespace(task_id="dag-task-1")
            dag = FakeDAGDef(
                roots=["research"],
                leaves=["review"],
                topological_order=["research", "frontend", "backend", "review"],
            )
            key_map = {
                "research": "step-1",
                "frontend": "step-2",
                "backend": "step-3",
                "review": "step-4",
            }
            root_ctxs = [ctx]
            return ctx, dag, key_map, root_ctxs

        runner.task_controller.start_dag_task = fake_start_dag_task

        result = _call_tool(
            server,
            "hermit_submit_dag_task",
            goal="Build full-stack feature",
            nodes=[
                {"key": "research", "kind": "research", "title": "Research"},
                {
                    "key": "frontend",
                    "kind": "code",
                    "title": "Frontend",
                    "depends_on": ["research"],
                },
                {"key": "backend", "kind": "code", "title": "Backend", "depends_on": ["research"]},
                {
                    "key": "review",
                    "kind": "review",
                    "title": "Review",
                    "depends_on": ["frontend", "backend"],
                },
            ],
        )

        assert result["status"] == "queued"
        assert result["task_id"] == "dag-task-1"
        assert result["dag_topology"]["roots"] == ["research"]
        assert result["dag_topology"]["leaves"] == ["review"]
        assert result["dag_topology"]["total_steps"] == 4
        assert result["step_ids"]["frontend"] == "step-2"
        assert len(call_log) == 1
        assert call_log[0]["policy_profile"] == "autonomous"

    def test_submit_dag_passes_workspace_root(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        """workspace_root from runner.agent is forwarded to start_dag_task."""
        from dataclasses import dataclass
        from typing import Any as _Any

        @dataclass
        class FakeDAGDef:
            roots: list[str]
            leaves: list[str]
            topological_order: list[str]

        call_log: list[dict[str, _Any]] = []

        def fake_start_dag_task(
            *,
            conversation_id,
            goal,
            source_channel,
            nodes,
            policy_profile,
            requested_by,
            workspace_root="",
            **kw,
        ):
            call_log.append({"workspace_root": workspace_root})
            ctx = SimpleNamespace(task_id="dag-ws-1")
            dag = FakeDAGDef(roots=["a"], leaves=["a"], topological_order=["a"])
            return ctx, dag, {"a": "step-1"}, [ctx]

        runner.task_controller.start_dag_task = fake_start_dag_task
        # Attach a fake agent with workspace_root
        runner.agent = SimpleNamespace(workspace_root="/Users/test/project")

        _call_tool(
            server,
            "hermit_submit_dag_task",
            goal="Test workspace root",
            nodes=[{"key": "a", "kind": "execute", "title": "A"}],
        )

        assert len(call_log) == 1
        assert call_log[0]["workspace_root"] == "/Users/test/project"

    def test_submit_dag_workspace_root_empty_without_agent(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        """Without agent attr, workspace_root defaults to empty string."""
        from dataclasses import dataclass
        from typing import Any as _Any

        @dataclass
        class FakeDAGDef:
            roots: list[str]
            leaves: list[str]
            topological_order: list[str]

        call_log: list[dict[str, _Any]] = []

        def fake_start_dag_task(
            *,
            conversation_id,
            goal,
            source_channel,
            nodes,
            policy_profile,
            requested_by,
            workspace_root="",
            **kw,
        ):
            call_log.append({"workspace_root": workspace_root})
            ctx = SimpleNamespace(task_id="dag-ws-2")
            dag = FakeDAGDef(roots=["a"], leaves=["a"], topological_order=["a"])
            return ctx, dag, {"a": "step-1"}, [ctx]

        runner.task_controller.start_dag_task = fake_start_dag_task
        # No agent attr on runner

        _call_tool(
            server,
            "hermit_submit_dag_task",
            goal="Test no agent",
            nodes=[{"key": "a", "kind": "execute", "title": "A"}],
        )

        assert len(call_log) == 1
        assert call_log[0]["workspace_root"] == ""

    def test_submit_dag_missing_node_field(self, server: HermitMcpServer) -> None:
        """Node missing required 'kind' field returns error."""
        result = _call_tool(
            server,
            "hermit_submit_dag_task",
            goal="Bad DAG",
            nodes=[{"key": "a", "title": "Only key and title"}],
        )
        assert "error" in result
        assert "missing required field" in result["error"]

    def test_submit_dag_validation_error(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        """Cycle in DAG returns validation error."""

        def fake_start_dag_task(**kw):
            raise ValueError("Cycle detected in step DAG")

        runner.task_controller.start_dag_task = fake_start_dag_task

        result = _call_tool(
            server,
            "hermit_submit_dag_task",
            goal="Cyclic DAG",
            nodes=[
                {"key": "a", "kind": "execute", "title": "A", "depends_on": ["b"]},
                {"key": "b", "kind": "execute", "title": "B", "depends_on": ["a"]},
            ],
        )
        assert "error" in result
        assert "Cycle" in result["error"]

    def test_submit_dag_no_task_controller(self) -> None:
        """When runner has no task_controller, returns error."""
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        srv._runner = SimpleNamespace(
            task_controller=None, agent=SimpleNamespace(kernel_store=None)
        )

        result = _call_tool(
            srv,
            "hermit_submit_dag_task",
            goal="No controller",
            nodes=[{"key": "a", "kind": "execute", "title": "A"}],
        )
        assert "error" in result
        assert "TaskController" in result["error"]


class TestHermitApproveTaskNotFound:
    def test_approve_task_not_found(self, server: HermitMcpServer, store: FakeStore) -> None:
        """Approval exists but its task_id points to a missing task."""
        store.approvals = [FakeApproval(approval_id="apr-orphan", task_id="gone")]
        result = _call_tool(server, "hermit_approve", approval_ids=["apr-orphan"])
        assert result["errors"] == 1
        assert result["results"][0]["error"] == "Task not found for approval"

    def test_deny_task_not_found(self, server: HermitMcpServer, store: FakeStore) -> None:
        """Approval exists but its task_id points to a missing task."""
        store.approvals = [FakeApproval(approval_id="apr-orphan", task_id="gone")]
        result = _call_tool(server, "hermit_deny", approval_ids=["apr-orphan"])
        assert result["errors"] == 1
        assert result["results"][0]["error"] == "Task not found for approval"


class TestHermitAwaitCompletion:
    """Tests for the hermit_await_completion long-poll tool."""

    def test_await_already_completed_returns_immediately(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        store.tasks["task-1"].status = "completed"
        result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=5)
        assert result["timed_out"] is False
        assert result["pending"] == []
        assert "task-1" in result["completed"]
        assert result["completed"]["task-1"]["status"] == "completed"

    def test_await_not_found_task(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_await_completion", task_ids=["nonexistent"], timeout=5)
        assert result["timed_out"] is False
        assert result["completed"]["nonexistent"]["status"] == "not_found"

    def test_await_mixed_terminal_and_not_found(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        store.tasks["task-1"].status = "failed"
        result = _call_tool(
            server,
            "hermit_await_completion",
            task_ids=["task-1", "nonexistent"],
            timeout=5,
        )
        assert result["timed_out"] is False
        assert result["completed"]["task-1"]["status"] == "failed"
        assert result["completed"]["nonexistent"]["status"] == "not_found"

    def test_await_running_task_becomes_completed(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """Simulate a task completing after a short delay."""
        import threading

        store.tasks["task-1"].status = "running"

        def complete_after_delay():
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "completed"
            store._notify_listeners("task-1")

        t = threading.Thread(target=complete_after_delay)
        t.start()
        try:
            result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=3)
            assert result["timed_out"] is False
            assert "task-1" in result["completed"]
            assert result["completed"]["task-1"]["status"] == "completed"
        finally:
            t.join(timeout=5)

    def test_await_timeout_returns_pending(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-1"].status = "running"
        result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=1)
        assert result["timed_out"] is True
        assert len(result["pending"]) == 1
        assert result["pending"][0]["task_id"] == "task-1"
        assert result["pending"][0]["status"] == "running"

    def test_await_blocked_task_reports_approvals(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        store.tasks["task-1"].status = "blocked"
        result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=2)
        assert "task-1" in result["completed"]
        assert result["completed"]["task-1"]["status"] == "blocked"
        assert "pending_approvals" in result["completed"]["task-1"]

    def test_await_multiple_tasks_returns_on_first_completion(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """With multiple running tasks, returns as soon as any one finishes."""
        import threading

        store.tasks["task-1"].status = "running"
        store.tasks["task-2"] = FakeTask(task_id="task-2", status="running")

        def complete_one():
            import time

            time.sleep(0.02)
            store.tasks["task-2"].status = "completed"
            store._notify_listeners("task-2")

        t = threading.Thread(target=complete_one)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_await_completion",
                task_ids=["task-1", "task-2"],
                timeout=3,
            )
            assert result["timed_out"] is False
            assert "task-2" in result["completed"]
            assert result["completed"]["task-2"]["status"] == "completed"
            # task-1 still pending
            pending_ids = [p["task_id"] for p in result["pending"]]
            assert "task-1" in pending_ids
        finally:
            t.join(timeout=5)

    def test_await_timeout_clamped(self, server: HermitMcpServer, store: FakeStore) -> None:
        """Timeout values are clamped to [1, 300]."""
        store.tasks["task-1"].status = "completed"
        # Negative timeout clamped to 1
        result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=-5)
        assert result["timed_out"] is False

    def test_await_empty_task_ids(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_await_completion", task_ids=[], timeout=5)
        assert result["timed_out"] is False
        assert result["completed"] == {}
        assert result["pending"] == []


class TestServerLifecycle:
    def test_swap_runner(self, server: HermitMcpServer) -> None:
        new_runner = FakeRunner()
        server.swap_runner(new_runner)
        assert server._runner is new_runner

    def test_get_runner_raises_when_none(self) -> None:
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        with pytest.raises(RuntimeError, match="Runner is not attached"):
            srv._get_runner()

    def test_get_store_via_task_controller(self, server: HermitMcpServer, store: FakeStore) -> None:
        result = server._get_store()
        assert result is store

    def test_get_store_fallback_via_agent_kernel_store(self) -> None:
        """When task_controller is absent, fall back to runner.agent.kernel_store."""
        fake_store = FakeStore()
        runner = SimpleNamespace(agent=SimpleNamespace(kernel_store=fake_store))
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        srv._runner = runner
        assert srv._get_store() is fake_store

    def test_get_store_fallback_raises_when_no_store(self) -> None:
        """When neither task_controller nor agent.kernel_store exists, raise."""
        runner = SimpleNamespace(agent=SimpleNamespace(kernel_store=None))
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        srv._runner = runner
        with pytest.raises(RuntimeError, match="Kernel store is not available"):
            srv._get_store()

    def test_start_and_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercise the real start/stop lifecycle (mock uvicorn)."""
        import uvicorn

        run_calls: list[bool] = []

        class FakeUvicornServer:
            def __init__(self, config: Any) -> None:
                self.config = config
                self.should_exit = False

            def run(self) -> None:
                run_calls.append(True)

        monkeypatch.setattr(uvicorn, "Server", FakeUvicornServer)

        srv = HermitMcpServer(host="127.0.0.1", port=0)
        fake_runner = FakeRunner()
        srv.start(fake_runner)

        assert srv._runner is fake_runner
        assert srv._uv_server is not None
        assert srv._thread is not None
        # Wait for the thread to finish (it's a fake, returns immediately)
        srv._thread.join(timeout=2)
        assert run_calls == [True]

        srv.stop()
        assert srv._uv_server.should_exit is True


# ---------------------------------------------------------------------------
# Hooks lifecycle tests
# ---------------------------------------------------------------------------


class TestMcpHooksLifecycle:
    def test_hooks_register_and_start_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        started: list[object] = []
        stopped: list[bool] = []

        class FakeServer:
            def __init__(self, *, host: str, port: int) -> None:
                self.host = host
                self.port = port

            def start(self, runner: Any) -> None:
                started.append(runner)

            def stop(self) -> None:
                stopped.append(True)

            def swap_runner(self, runner: Any) -> None:
                started.append(("swap", runner))

        monkeypatch.setattr(
            "hermit.plugins.builtin.mcp.hermit_server.server.HermitMcpServer",
            FakeServer,
        )

        # Reset module-level state
        mcp_hooks._server = None

        ctx = PluginContext(HooksEngine())
        mcp_hooks.register(ctx)

        # Disabled by default
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(mcp_server_enabled=False),
            runner="runner1",
        )
        assert len(started) == 0

        # Enabled
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(
                mcp_server_enabled=True, mcp_server_host="0.0.0.0", mcp_server_port=9999
            ),
            runner="runner1",
        )
        assert started == ["runner1"]

        # Reload mode → hot-swap
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(mcp_server_enabled=True),
            runner="runner2",
            reload_mode=True,
        )
        assert started[-1] == ("swap", "runner2")

        # Stop
        ctx._hooks.fire(HookEvent.SERVE_STOP)
        assert stopped == [True]
        assert mcp_hooks._server is None

    def test_hooks_stop_skipped_during_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stopped: list[bool] = []

        class FakeServer:
            def __init__(self, **kw: Any) -> None:
                pass

            def start(self, runner: Any) -> None:
                pass

            def stop(self) -> None:
                stopped.append(True)

        monkeypatch.setattr(
            "hermit.plugins.builtin.mcp.hermit_server.server.HermitMcpServer",
            FakeServer,
        )
        mcp_hooks._server = None

        ctx = PluginContext(HooksEngine())
        mcp_hooks.register(ctx)

        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(mcp_server_enabled=True),
            runner="runner",
        )
        ctx._hooks.fire(HookEvent.SERVE_STOP, reload_mode=True)
        assert stopped == []  # Should NOT stop during reload


# ---------------------------------------------------------------------------
# Event-driven hermit_await_completion (KernelStore integration)
# ---------------------------------------------------------------------------


class TestHermitAwaitCompletionEventDriven:
    """Validate that hermit_await_completion wakes up via threading.Event
    rather than sleeping for a full poll interval."""

    def _make_store_with_running_task(self) -> tuple[Any, str]:
        """Return (KernelStore, task_id) with one running task seeded."""
        from pathlib import Path

        from hermit.kernel.ledger.journal.store import KernelStore

        store = KernelStore(Path(":memory:"))
        store.ensure_conversation("conv-ev", source_channel="test")
        task = store.create_task(
            conversation_id="conv-ev",
            title="Event task",
            goal="Test event wakeup",
            source_channel="test",
            status="running",
        )
        return store, task.task_id

    def test_event_driven_wakes_immediately_on_status_change(self) -> None:
        """With KernelStore, the event fires well under 0.5 s (the old poll interval)
        after update_task_status is called."""
        import threading
        import time

        store, task_id = self._make_store_with_running_task()

        ev = store.get_or_create_task_event(task_id)
        assert not ev.is_set()

        DELAY = 0.05  # 50 ms — far below the 500 ms poll interval

        def change_status() -> None:
            time.sleep(DELAY)
            store.update_task_status(task_id, "completed")

        t = threading.Thread(target=change_status, daemon=True)
        t.start()

        t0 = time.monotonic()
        triggered = ev.wait(timeout=2.0)
        elapsed = time.monotonic() - t0
        t.join(timeout=5)

        assert triggered, "Event was never fired after update_task_status"
        # Should have been woken up close to DELAY, not after a full poll interval
        assert elapsed < 0.4, f"Event-driven wait took {elapsed:.3f}s, expected < 0.4s"

    def test_notify_task_changed_fires_event(self) -> None:
        """notify_task_changed fires without error and is idempotent."""
        store, task_id = self._make_store_with_running_task()
        # First call creates the event and immediately sets+clears it
        store.notify_task_changed(task_id)
        # Subsequent calls on the same task are idempotent
        store.notify_task_changed(task_id)
        # Calling on a task with no event registered is safe
        store.notify_task_changed("nonexistent-task-id")

    def test_get_or_create_task_event_returns_same_event(self) -> None:
        """get_or_create_task_event is idempotent for the same task_id."""
        from pathlib import Path

        from hermit.kernel.ledger.journal.store import KernelStore

        store = KernelStore(Path(":memory:"))
        ev1 = store.get_or_create_task_event("task-x")
        ev2 = store.get_or_create_task_event("task-x")
        assert ev1 is ev2

    def test_update_task_status_fires_event_for_all_terminal_statuses(self) -> None:
        """Every terminal status transition fires the per-task event."""
        import threading
        import time
        from pathlib import Path

        from hermit.kernel.ledger.journal.store import KernelStore

        for terminal in ("completed", "failed", "cancelled"):
            store = KernelStore(Path(":memory:"))
            store.ensure_conversation("c", source_channel="test")
            task = store.create_task(
                conversation_id="c",
                title="t",
                goal="g",
                source_channel="test",
                status="running",
            )
            ev = store.get_or_create_task_event(task.task_id)

            fired: list[bool] = []

            def _wait(event: Any = ev, out: list[bool] = fired) -> None:
                out.append(event.wait(timeout=1.0))

            watcher = threading.Thread(target=_wait, daemon=True)
            watcher.start()
            time.sleep(0.02)
            store.update_task_status(task.task_id, terminal)
            watcher.join(timeout=2.0)

            assert fired == [True], f"Event not fired for status={terminal!r}"


# ---------------------------------------------------------------------------
# Slim serialization unit tests
# ---------------------------------------------------------------------------


class TestSlimTask:
    def test_slim_task_keeps_expected_fields(self) -> None:
        task = FakeTask()
        result = _slim_task(task)
        # budget_tokens_used=0 is not None, so it stays
        assert set(result.keys()) == {
            "task_id",
            "title",
            "goal",
            "status",
            "priority",
            "created_at",
            "updated_at",
            "budget_tokens_used",
        }
        assert result["task_id"] == "task-1"
        assert result["title"] == "Test task"

    def test_slim_task_drops_none_values(self) -> None:
        task = FakeTask(parent_task_id=None, budget_tokens_limit=None)
        result = _slim_task(task)
        # None fields from the allowed set are also dropped
        assert "parent_task_id" not in result  # not in field list anyway
        assert "budget_tokens_limit" not in result  # not in field list anyway

    def test_slim_task_strips_internal_fields(self) -> None:
        task = FakeTask()
        result = _slim_task(task)
        assert "conversation_id" not in result
        assert "owner_principal_id" not in result
        assert "policy_profile" not in result
        assert "source_channel" not in result

    def test_slim_task_truncates_long_goal(self) -> None:
        task = FakeTask(goal="x" * 500)
        result = _slim_task(task)
        assert len(result["goal"]) == 201  # 200 + ellipsis
        assert result["goal"].endswith("…")

    def test_slim_task_list_fields_omits_budget(self) -> None:
        task = FakeTask(budget_tokens_used=5000)
        result = _slim_task(task, fields=_SLIM_TASK_LIST_FIELDS)
        assert "budget_tokens_used" not in result
        assert result["task_id"] == "task-1"
        assert result["title"] == "Test task"

    def test_slim_task_list_truncates_goal_at_100(self) -> None:
        task = FakeTask(goal="a" * 300)
        result = _slim_task(task, fields=_SLIM_TASK_LIST_FIELDS)
        assert len(result["goal"]) == 101  # 100 + ellipsis
        assert result["goal"].endswith("…")


class TestSlimApproval:
    def test_slim_approval_keeps_non_none_fields(self) -> None:
        approval = FakeApproval()
        result = _slim_approval(approval)
        # expires_at=None and resolved_at=None are dropped
        assert set(result.keys()) == {
            "approval_id",
            "task_id",
            "step_id",
            "approval_type",
            "status",
            "requested_at",
        }
        assert result["approval_id"] == "apr-1"
        assert result["status"] == "pending"

    def test_slim_approval_includes_non_none_timestamps(self) -> None:
        approval = FakeApproval(
            expires_at="2026-01-02T00:00:00",
            resolved_at="2026-01-02T01:00:00",
        )
        result = _slim_approval(approval)
        assert result["expires_at"] == "2026-01-02T00:00:00"
        assert result["resolved_at"] == "2026-01-02T01:00:00"

    def test_slim_approval_strips_nested_dicts_but_adds_action_summary(self) -> None:
        approval = FakeApproval(
            requested_action={"tool_name": "bash", "arguments": {"command": "ls -la"}}
        )
        result = _slim_approval(approval)
        assert "requested_action" not in result
        assert "resolution" not in result
        assert "request_packet_ref" not in result
        assert result["action_summary"] == "bash: ls -la"

    def test_slim_approval_no_action_summary_when_empty(self) -> None:
        approval = FakeApproval(requested_action={})
        result = _slim_approval(approval)
        assert "action_summary" not in result

    def test_slim_approval_action_summary_write_file(self) -> None:
        approval = FakeApproval(
            requested_action={"tool_name": "write_file", "arguments": {"path": "/tmp/foo.py"}}
        )
        result = _slim_approval(approval)
        assert result["action_summary"] == "write_file: /tmp/foo.py"

    def test_slim_approval_action_summary_generic_tool(self) -> None:
        approval = FakeApproval(
            requested_action={"tool_name": "read_file", "arguments": {"path": "/etc/hosts"}}
        )
        result = _slim_approval(approval)
        assert result["action_summary"] == "read_file: /etc/hosts"

    def test_slim_approval_action_summary_truncates_long_command(self) -> None:
        approval = FakeApproval(
            requested_action={"tool_name": "bash", "arguments": {"command": "x" * 500}}
        )
        result = _slim_approval(approval)
        assert len(result["action_summary"]) <= 201  # 200 + ellipsis


class TestSlimEvent:
    def test_slim_event_keeps_5_fields(self) -> None:
        event = {
            "event_type": "task_created",
            "entity_type": "task",
            "entity_id": "task-1",
            "occurred_at": "2026-01-01T00:00:00",
            "payload": {"key": "value"},
            "event_id": "evt-1",
            "task_id": "task-1",
            "step_id": "step-1",
            "actor_principal_id": "hermit",
            "actor": "system",
            "event_hash": "abc123",
            "prev_event_hash": "def456",
            "hash_chain_algo": "sha256",
        }
        result = _slim_event(event)
        assert set(result.keys()) == {
            "event_type",
            "entity_type",
            "entity_id",
            "occurred_at",
            "payload",
        }
        assert result["event_type"] == "task_created"
        assert "event_hash" not in result
        assert "actor" not in result

    def test_slim_event_truncates_long_payload(self) -> None:
        event = {
            "event_type": "step_completed",
            "entity_type": "step",
            "entity_id": "step-1",
            "occurred_at": "2026-01-01T00:00:00",
            "payload": {"data": "x" * 500},
        }
        result = _slim_event(event)
        assert len(result["payload"]) <= 121  # 120 + ellipsis char
        assert result["payload"].endswith("…")

    def test_slim_event_no_payload(self) -> None:
        event = {
            "event_type": "task_created",
            "entity_type": "task",
            "entity_id": "task-1",
            "occurred_at": "2026-01-01T00:00:00",
        }
        result = _slim_event(event)
        assert "payload" not in result


# ---------------------------------------------------------------------------
# hermit_task_output tests
# ---------------------------------------------------------------------------


class TestHermitTaskOutput:
    def test_task_output_with_receipts(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-1"].status = "completed"
        store.receipts = [
            FakeReceipt(
                receipt_id="rcpt-1",
                task_id="task-1",
                action_type="bash",
                result_code="succeeded",
                result_summary="Ran pytest: 14 tests passed",
                observed_effect_summary="Modified 2 files",
            ),
            FakeReceipt(
                receipt_id="rcpt-2",
                task_id="task-1",
                action_type="write_file",
                result_code="succeeded",
                result_summary="Wrote src/foo.py",
                observed_effect_summary=None,
                rollback_supported=True,
            ),
        ]
        result = _call_tool(server, "hermit_task_output", task_ids=["task-1"])
        assert result["count"] == 1
        out = result["outputs"][0]
        assert out["task_id"] == "task-1"
        assert out["status"] == "completed"
        assert out["total_actions"] == 2
        assert out["receipts"][0]["action_type"] == "bash"
        assert out["receipts"][0]["result_summary"] == "Ran pytest: 14 tests passed"
        assert out["receipts"][0]["effect"] == "Modified 2 files"
        assert out["receipts"][1]["action_type"] == "write_file"
        assert "effect" not in out["receipts"][1]  # None effect is omitted

    def test_task_output_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_output", task_ids=["nonexistent"])
        assert result["count"] == 1
        assert result["outputs"][0]["error"] == "Task not found"

    def test_task_output_no_receipts(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-1"].status = "completed"
        store.receipts = []
        result = _call_tool(server, "hermit_task_output", task_ids=["task-1"])
        out = result["outputs"][0]
        assert out["total_actions"] == 0
        assert out["receipts"] == []

    def test_task_output_empty_list(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_output", task_ids=[])
        assert result["count"] == 0
        assert result["outputs"] == []

    def test_task_output_truncates_long_summary(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        store.tasks["task-1"].status = "completed"
        store.receipts = [
            FakeReceipt(
                task_id="task-1",
                result_summary="x" * 500,
                observed_effect_summary="y" * 500,
            ),
        ]
        result = _call_tool(server, "hermit_task_output", task_ids=["task-1"])
        receipt = result["outputs"][0]["receipts"][0]
        assert len(receipt["result_summary"]) <= 201
        assert len(receipt["effect"]) <= 201


# ---------------------------------------------------------------------------
# hermit_approve/deny await_after tests
# ---------------------------------------------------------------------------


class TestHermitApproveAwaitAfter:
    def test_approve_with_await_after(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """approve with await_after waits for task to complete."""
        import threading

        store.tasks["task-1"].status = "blocked"

        def complete_task() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "completed"
            store._notify_listeners("task-1")

        t = threading.Thread(target=complete_task)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_approve",
                approval_ids=["apr-1"],
                await_after=3,
            )
            assert result["approved"] == 1
            assert "task_status" in result
            assert "task-1" in result["task_status"]["completed"]
        finally:
            t.join(timeout=3)

    def test_approve_without_await_after(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        """Default await_after=0 returns immediately without task_status."""
        result = _call_tool(server, "hermit_approve", approval_ids=["apr-1"])
        assert result["approved"] == 1
        assert "task_status" not in result

    def test_deny_with_await_after(
        self, server: HermitMcpServer, runner: FakeRunner, store: FakeStore
    ) -> None:
        """deny with await_after waits for task to reach terminal state."""
        import threading

        store.tasks["task-1"].status = "blocked"

        def fail_task() -> None:
            import time

            time.sleep(0.02)
            store.tasks["task-1"].status = "failed"
            store._notify_listeners("task-1")

        t = threading.Thread(target=fail_task)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_deny",
                approval_ids=["apr-1"],
                reason="Unsafe",
                await_after=3,
            )
            assert result["denied"] == 1
            assert "task_status" in result
            assert "task-1" in result["task_status"]["completed"]
        finally:
            t.join(timeout=3)


# ---------------------------------------------------------------------------
# action_summary unit tests
# ---------------------------------------------------------------------------


class TestActionSummary:
    def test_bash_command(self) -> None:
        assert (
            _action_summary({"tool_name": "bash", "arguments": {"command": "ls -la"}})
            == "bash: ls -la"
        )

    def test_write_file_path(self) -> None:
        assert (
            _action_summary({"tool_name": "write_file", "arguments": {"path": "/tmp/f.py"}})
            == "write_file: /tmp/f.py"
        )

    def test_write_file_file_path_key(self) -> None:
        assert (
            _action_summary({"tool_name": "write_file", "arguments": {"file_path": "/a/b.txt"}})
            == "write_file: /a/b.txt"
        )

    def test_generic_tool(self) -> None:
        result = _action_summary({"tool_name": "read_file", "arguments": {"path": "/etc/hosts"}})
        assert result == "read_file: /etc/hosts"

    def test_tool_no_args(self) -> None:
        assert _action_summary({"tool_name": "list_files", "arguments": {}}) == "list_files"

    def test_empty_dict(self) -> None:
        assert _action_summary({}) is None

    def test_none_input(self) -> None:
        assert _action_summary(None) is None

    def test_truncation(self) -> None:
        result = _action_summary({"tool_name": "bash", "arguments": {"command": "x" * 500}})
        assert result is not None
        assert len(result) <= 201


# ---------------------------------------------------------------------------
# Response size benchmark
# ---------------------------------------------------------------------------


class TestResponseSizeBenchmark:
    """Measure JSON byte sizes before/after slim serialization.

    Uses production-scale data: real field counts (TaskRecord=18, ApprovalRecord=22,
    Event=13), realistic UUIDs, nested dicts matching actual tool call payloads,
    and long goal descriptions that trigger truncation.
    """

    @staticmethod
    def _uuid(prefix: str, i: int) -> str:
        return f"{prefix}-{i:04d}-abcd-1234-567890abcdef"

    @classmethod
    def _make_full_task(cls, i: int) -> SimpleNamespace:
        """Simulate a real TaskRecord with all 18 fields."""
        # Production goals are often 300-800 chars (full task descriptions from Claude)
        goal = (
            f"Refactor the authentication middleware in src/hermit/kernel/policy/ to support "
            f"workspace-scoped capability grants. This involves: (1) updating the "
            f"AuthorizationPlanRecord to include workspace_lease_ref validation, "
            f"(2) modifying PolicyEngine.evaluate() to check workspace boundaries before "
            f"granting tool execution permits, (3) adding integration tests that verify "
            f"cross-workspace access is denied, (4) updating the operator summary format "
            f"to include workspace context in approval prompts. Ensure backwards "
            f"compatibility with existing single-workspace deployments. Task {i}."
        )
        return SimpleNamespace(
            task_id=cls._uuid("task", i),
            conversation_id=cls._uuid("conv", i),
            title=f"Refactor auth middleware for workspace-scoped grants (batch {i})",
            goal=goal,
            status=["running", "completed", "failed", "queued", "blocked"][i % 5],
            priority=["low", "normal", "high"][i % 3],
            owner_principal_id=cls._uuid("principal", i),
            policy_profile="autonomous",
            source_channel="mcp-supervisor",
            parent_task_id=cls._uuid("task", i - 1) if i % 3 == 0 else None,
            task_contract_ref=cls._uuid("contract", i) if i % 2 == 0 else None,
            created_at=1711234567.890 + i * 60,
            updated_at=1711234567.890 + i * 60 + 30,
            requested_by_principal_id=cls._uuid("principal", 0),
            child_result_refs=[cls._uuid("artifact", j) for j in range(i % 4)],
            budget_tokens_used=i * 2500 + 1200,
            budget_tokens_limit=100000,
        )

    @classmethod
    def _make_full_approval(cls, i: int) -> SimpleNamespace:
        """Simulate a real ApprovalRecord with all 22 fields + nested dicts."""
        return SimpleNamespace(
            approval_id=cls._uuid("apr", i),
            task_id=cls._uuid("task", i),
            step_id=cls._uuid("step", i),
            step_attempt_id=cls._uuid("sa", i),
            status="pending",
            approval_type="tool_execution",
            # Production requested_action: full tool call with args
            requested_action={
                "tool_name": "bash",
                "arguments": {
                    "command": (
                        "cd /Users/beta/work/Hermit && "
                        "uv run pytest tests/unit/kernel/test_policy_engine.py "
                        "-k 'test_workspace_grant_scope' -v --tb=short 2>&1 | head -100"
                    ),
                },
                "metadata": {
                    "risk_level": "medium",
                    "action_class": "shell_execution",
                    "reversible": True,
                    "workspace_root": "/Users/beta/work/Hermit",
                    "estimated_duration_ms": 5000,
                },
                "policy_context": {
                    "profile": "autonomous",
                    "step_kind": "execute",
                    "attempt": 1,
                    "contract_objective": f"Run unit tests for workspace grant scope (step {i})",
                },
            },
            request_packet_ref=cls._uuid("pkt-req", i),
            requested_action_ref=cls._uuid("action", i),
            approval_packet_ref=cls._uuid("pkt-apr", i),
            policy_result_ref=cls._uuid("policy", i),
            requested_contract_ref=cls._uuid("contract", i),
            authorization_plan_ref=cls._uuid("authplan", i),
            evidence_case_ref=cls._uuid("evidence", i),
            drift_expiry=1711234567.890 + 3600 if i % 2 == 0 else None,
            fallback_contract_refs=[cls._uuid("fallback", j) for j in range(i % 3)],
            decision_ref=cls._uuid("decision", i) if i % 4 == 0 else None,
            state_witness_ref=cls._uuid("witness", i),
            requested_at=1711234567.890 + i * 10,
            expires_at=1711234567.890 + i * 10 + 300,
            resolved_at=None,
            resolved_by_principal_id=None,
            resolution={
                "outcome": "pending",
                "policy_evaluation": {
                    "risk_score": 0.3 + i * 0.05,
                    "auto_approve_eligible": True,
                    "evaluation_path": "autonomous_policy_v2",
                    "contributing_factors": [
                        "low_risk_action_class",
                        "trusted_workspace",
                        "known_tool_pattern",
                    ],
                },
            },
        )

    @classmethod
    def _make_full_event(cls, i: int) -> dict[str, Any]:
        """Simulate a real kernel event with all 13 fields + realistic payload."""
        event_types = [
            "task_created",
            "step_started",
            "step_completed",
            "approval_requested",
            "receipt_issued",
            "tool_executed",
        ]
        # Production payloads: tool execution results, policy evaluations, etc.
        payloads = [
            {
                "tool_name": "bash",
                "result_code": "succeeded",
                "output_summary": f"Step {i} executed: 14 tests passed, 0 failed. "
                f"Coverage: 87.3%. Duration: 2.4s. "
                f"Modified files: src/hermit/kernel/policy/evaluators/workspace.py, "
                f"tests/unit/kernel/test_workspace_policy.py",
                "artifacts_produced": [cls._uuid("artifact", i)],
                "receipt_ref": cls._uuid("receipt", i),
            },
            {
                "policy_evaluation": {
                    "profile": "autonomous",
                    "risk_level": "low",
                    "auto_approved": True,
                    "evaluation_chain": [
                        "action_class_check",
                        "workspace_boundary_check",
                        "tool_allowlist_check",
                    ],
                },
                "authorization_plan_ref": cls._uuid("authplan", i),
            },
            {
                "status_transition": {"from": "running", "to": "completed"},
                "final_state_witness_ref": cls._uuid("witness", i),
                "reconciliation_result": "verified_consistent",
                "budget_summary": {"tokens_used": 4500 + i * 100, "limit": 100000},
            },
        ]
        return {
            "event_type": event_types[i % len(event_types)],
            "entity_type": ["task", "step", "approval"][i % 3],
            "entity_id": cls._uuid("entity", i),
            "occurred_at": 1711234567.890 + i * 5,
            "payload": payloads[i % len(payloads)],
            "event_seq": i,
            "event_id": cls._uuid("evt", i),
            "task_id": cls._uuid("task", i // 5),
            "step_id": cls._uuid("step", i),
            "actor_principal_id": cls._uuid("principal", 0),
            "actor": "kernel",
            "event_hash": f"sha256:{i:04d}" + "a" * 60,
            "prev_event_hash": f"sha256:{i - 1:04d}" + "b" * 60,
            "hash_chain_algo": "sha256",
        }

    def test_benchmark_production_scale(self) -> None:
        """Production-scale benchmark: 50 tasks, 20 approvals, 100 events."""
        import json

        tasks = [self._make_full_task(i) for i in range(50)]
        approvals = [self._make_full_approval(i) for i in range(20)]
        events = [self._make_full_event(i) for i in range(100)]

        rows: list[tuple[str, int, int]] = []

        # --- hermit_list_tasks (50 tasks, max limit) ---
        before = json.dumps({"tasks": [t.__dict__ for t in tasks]}).encode()
        after = json.dumps(
            {"tasks": [_slim_task(t, fields=_SLIM_TASK_LIST_FIELDS) for t in tasks]}
        ).encode()
        rows.append(("list_tasks (50)", len(before), len(after)))

        # --- hermit_pending_approvals (20 approvals) ---
        before = json.dumps({"approvals": [a.__dict__ for a in approvals]}).encode()
        after = json.dumps({"approvals": [_slim_approval(a) for a in approvals]}).encode()
        rows.append(("pending_approvals (20)", len(before), len(after)))

        # --- hermit_task_status (batch of 5 tasks) ---
        before_items = []
        after_items = []
        for t in tasks[:5]:
            task_approvals = [a for a in approvals if a.task_id == t.task_id]
            task_events = events[:10]
            before_items.append(
                {
                    "task_id": t.task_id,
                    "task": t.__dict__,
                    "pending_approvals": [a.__dict__ for a in task_approvals],
                    "recent_events": task_events,
                    "is_blocked": len(task_approvals) > 0,
                }
            )
            after_items.append(
                {
                    "task_id": t.task_id,
                    "task": _slim_task(t),
                    "pending_approvals": [_slim_approval(a) for a in task_approvals],
                    "recent_events": [_slim_event(e) for e in task_events],
                    "is_blocked": len(task_approvals) > 0,
                }
            )
        before = json.dumps({"tasks": before_items, "count": 5}).encode()
        after = json.dumps({"tasks": after_items, "count": 5}).encode()
        rows.append(("task_status (5 tasks)", len(before), len(after)))

        # --- hermit_await_completion (3 completed + 2 blocked) ---
        completed_results_before: dict[str, Any] = {}
        completed_results_after: dict[str, Any] = {}
        for t in tasks[:3]:
            t_copy = SimpleNamespace(**t.__dict__)
            t_copy.status = "completed"
            completed_results_before[t.task_id] = {
                "status": "completed",
                "task": t_copy.__dict__,
                "recent_events": events[:5],
            }
            completed_results_after[t.task_id] = {
                "status": "completed",
                "task": _slim_task(t_copy),
                "recent_events": [_slim_event(e) for e in events[:5]],
            }
        for t in tasks[3:5]:
            task_approvals = [a for a in approvals if a.task_id == t.task_id][:3]
            completed_results_before[t.task_id] = {
                "status": "blocked",
                "task": t.__dict__,
                "pending_approvals": [a.__dict__ for a in task_approvals],
            }
            completed_results_after[t.task_id] = {
                "status": "blocked",
                "task": _slim_task(t),
                "pending_approvals": [_slim_approval(a) for a in task_approvals],
            }
        before = json.dumps({"completed": completed_results_before}).encode()
        after = json.dumps({"completed": completed_results_after}).encode()
        rows.append(("await_completion (5)", len(before), len(after)))

        # --- events batch (100 events) ---
        before = json.dumps(events).encode()
        after = json.dumps([_slim_event(e) for e in events]).encode()
        rows.append(("events (100)", len(before), len(after)))

        # Print table
        total_before = sum(b for _, b, _ in rows)
        total_after = sum(a for _, _, a in rows)
        print("\n" + "=" * 75)
        print(f"{'Scenario':<30} {'Before (B)':>12} {'After (B)':>12} {'Reduction':>10}")
        print("-" * 75)
        for name, b, a in rows:
            pct = round((1 - a / b) * 100) if b > 0 else 0
            print(f"{name:<30} {b:>12,} {a:>12,} {pct:>9}%")
        print("-" * 75)
        total_pct = round((1 - total_after / total_before) * 100)
        print(f"{'TOTAL':<30} {total_before:>12,} {total_after:>12,} {total_pct:>9}%")
        print("=" * 75)

        # Verify meaningful reduction
        for name, b, a in rows:
            assert a < b, f"{name}: after ({a}) should be smaller than before ({b})"
            reduction = 1 - a / b
            assert reduction >= 0.3, f"{name}: only {reduction:.0%} reduction, expected >=30%"


# ---------------------------------------------------------------------------
# hermit_lessons_learned tests
# ---------------------------------------------------------------------------


class TestHermitLessonsLearned:
    """Tests for the hermit_lessons_learned MCP tool — type mismatch fix."""

    def test_no_filter_returns_all(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.lessons = [
            {"lesson_id": "l1", "iteration_id": "i1", "category": "perf", "summary": "A"},
            {"lesson_id": "l2", "iteration_id": "i2", "category": "rel", "summary": "B"},
        ]
        result = _call_tool(server, "hermit_lessons_learned")
        assert result["count"] == 2

    def test_applicable_to_single_domain(self, server: HermitMcpServer, store: FakeStore) -> None:
        """applicable_to as list[str] with one element correctly filters."""
        store.lessons = [
            {
                "lesson_id": "l1",
                "iteration_id": "i1",
                "category": "perf",
                "summary": "Speed up DB",
                "applicable_files": '["src/db.py"]',
            },
            {
                "lesson_id": "l2",
                "iteration_id": "i2",
                "category": "rel",
                "summary": "Fix auth",
                "applicable_files": '["src/auth.py"]',
            },
        ]
        result = _call_tool(server, "hermit_lessons_learned", applicable_to=["src/db.py"])
        assert result["count"] == 1
        assert result["lessons"][0]["lesson_id"] == "l1"

    def test_applicable_to_multiple_domains(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """applicable_to with multiple items merges results from each domain."""
        store.lessons = [
            {
                "lesson_id": "l1",
                "iteration_id": "i1",
                "category": "perf",
                "summary": "A",
                "applicable_files": '["src/db.py"]',
            },
            {
                "lesson_id": "l2",
                "iteration_id": "i2",
                "category": "rel",
                "summary": "B",
                "applicable_files": '["src/auth.py"]',
            },
            {
                "lesson_id": "l3",
                "iteration_id": "i3",
                "category": "sec",
                "summary": "C",
                "applicable_files": '["src/other.py"]',
            },
        ]
        result = _call_tool(
            server,
            "hermit_lessons_learned",
            applicable_to=["src/db.py", "src/auth.py"],
        )
        assert result["count"] == 2
        ids = {item["lesson_id"] for item in result["lessons"]}
        assert ids == {"l1", "l2"}

    def test_applicable_to_deduplicates(self, server: HermitMcpServer, store: FakeStore) -> None:
        """Lesson matching multiple domains is only returned once."""
        store.lessons = [
            {
                "lesson_id": "l1",
                "iteration_id": "i1",
                "category": "perf",
                "summary": "Multi",
                "applicable_files": '["src/db.py", "src/auth.py"]',
            },
        ]
        result = _call_tool(
            server,
            "hermit_lessons_learned",
            applicable_to=["src/db.py", "src/auth.py"],
        )
        assert result["count"] == 1

    def test_applicable_to_empty_list_returns_all(
        self, server: HermitMcpServer, store: FakeStore
    ) -> None:
        """Empty applicable_to list is treated as no filter."""
        store.lessons = [
            {"lesson_id": "l1", "iteration_id": "i1", "category": "perf", "summary": "A"},
        ]
        result = _call_tool(server, "hermit_lessons_learned", applicable_to=[])
        assert result["count"] == 1

    def test_schema_not_available(self) -> None:
        """Returns error when store lacks list_lessons_learned."""
        bare_store = SimpleNamespace()
        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=bare_store),
        )
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        srv._runner = runner
        result = _call_tool(srv, "hermit_lessons_learned")
        assert "error" in result
        assert "not available" in result["error"]
