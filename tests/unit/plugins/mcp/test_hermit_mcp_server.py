"""Unit tests for the Hermit MCP Server plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.plugins.builtin.mcp.hermit_server import hooks as mcp_hooks
from hermit.plugins.builtin.mcp.hermit_server.server import HermitMcpServer
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


class FakeStore:
    """Minimal mock of KernelStore for tool handler tests."""

    def __init__(self) -> None:
        self.tasks: dict[str, FakeTask] = {"task-1": FakeTask()}
        self.approvals: list[FakeApproval] = [FakeApproval()]
        self.events: list[dict[str, Any]] = [{"type": "task_created", "task_id": "task-1"}]
        self.status_updates: list[tuple[str, str, dict[str, Any] | None]] = []

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

    def update_task_status(
        self, task_id: str, status: str, *, payload: dict[str, Any] | None = None
    ) -> None:
        self.status_updates.append((task_id, status, payload))
        task = self.tasks.get(task_id)
        if task:
            task.status = status


class FakeRunner:
    """Minimal mock of AgentRunner."""

    def __init__(self, store: FakeStore | None = None) -> None:
        self.task_controller = SimpleNamespace(store=store or FakeStore())
        self.ingress_calls: list[dict[str, Any]] = []
        self.approval_calls: list[dict[str, Any]] = []

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
    ) -> None:
        self.ingress_calls.append(
            {
                "session_id": session_id,
                "text": text,
                "source_channel": source_channel,
                "source_ref": source_ref,
                "requested_by": requested_by,
                "ingress_metadata": ingress_metadata,
            }
        )

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


class TestHermitSubmitTask:
    def test_submit_task_returns_accepted(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        result = _call_tool(server, "hermit_submit_task", description="Build feature X")
        assert result["status"] == "accepted"
        assert result["session_id"].startswith("mcp-supervisor-")
        assert len(runner.ingress_calls) == 1
        assert runner.ingress_calls[0]["text"] == "Build feature X"
        assert runner.ingress_calls[0]["source_channel"] == "mcp-supervisor"

    def test_submit_task_with_priority(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_submit_task", description="Urgent fix", priority="high")
        assert result["status"] == "accepted"
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["priority"] == "high"

    def test_submit_task_no_runner_raises(self) -> None:
        srv = HermitMcpServer(host="127.0.0.1", port=0)
        with pytest.raises(Exception, match="Runner is not attached"):
            _call_tool(srv, "hermit_submit_task", description="fail")


class TestHermitTaskStatus:
    def test_task_status_returns_details(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_id="task-1")
        assert result["task"]["task_id"] == "task-1"
        assert result["is_blocked"] is True
        assert len(result["pending_approvals"]) == 1

    def test_task_status_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_status", task_id="nonexistent")
        assert result["error"] == "Task not found"


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
    def test_approve_success(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_approve", approval_id="apr-1")
        assert result["status"] == "approved"
        assert len(runner.approval_calls) == 1
        assert runner.approval_calls[0]["action"] == "approve"

    def test_approve_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_approve", approval_id="nonexistent")
        assert result["error"] == "Approval not found"


class TestHermitDeny:
    def test_deny_success(self, server: HermitMcpServer, runner: FakeRunner) -> None:
        result = _call_tool(server, "hermit_deny", approval_id="apr-1", reason="Not safe")
        assert result["status"] == "denied"
        assert runner.approval_calls[0]["action"] == "deny"
        assert runner.approval_calls[0]["reason"] == "Not safe"

    def test_deny_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_deny", approval_id="nonexistent")
        assert result["error"] == "Approval not found"


class TestHermitCancelTask:
    def test_cancel_running_task(self, server: HermitMcpServer, store: FakeStore) -> None:
        result = _call_tool(
            server, "hermit_cancel_task", task_id="task-1", reason="No longer needed"
        )
        assert result["status"] == "cancelled"
        assert store.status_updates[0] == (
            "task-1",
            "cancelled",
            {"reason": "No longer needed", "cancelled_by": "supervisor"},
        )

    def test_cancel_nonexistent_task(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_cancel_task", task_id="nonexistent")
        assert result["error"] == "Task not found"

    def test_cancel_already_completed_task(self, server: HermitMcpServer, store: FakeStore) -> None:
        store.tasks["task-1"].status = "completed"
        result = _call_tool(server, "hermit_cancel_task", task_id="task-1")
        assert "already in terminal state" in result["error"]


class TestHermitTaskProof:
    def test_task_proof_not_found(self, server: HermitMcpServer) -> None:
        result = _call_tool(server, "hermit_task_proof", task_id="nonexistent")
        assert result["error"] == "Task not found"


# ---------------------------------------------------------------------------
# Server lifecycle tests
# ---------------------------------------------------------------------------


class TestHermitSubmitTaskPolicyProfile:
    def test_submit_task_with_policy_profile(
        self, server: HermitMcpServer, runner: FakeRunner
    ) -> None:
        result = _call_tool(
            server,
            "hermit_submit_task",
            description="Supervised task",
            policy_profile="supervised",
        )
        assert result["status"] == "accepted"
        meta = runner.ingress_calls[0]["ingress_metadata"]
        assert meta["policy_profile"] == "supervised"


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
        result = _call_tool(server, "hermit_approve", approval_id="apr-orphan")
        assert result["error"] == "Task not found for approval"

    def test_deny_task_not_found(self, server: HermitMcpServer, store: FakeStore) -> None:
        """Approval exists but its task_id points to a missing task."""
        store.approvals = [FakeApproval(approval_id="apr-orphan", task_id="gone")]
        result = _call_tool(server, "hermit_deny", approval_id="apr-orphan")
        assert result["error"] == "Task not found for approval"


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

            time.sleep(0.3)
            store.tasks["task-1"].status = "completed"

        t = threading.Thread(target=complete_after_delay)
        t.start()
        try:
            result = _call_tool(server, "hermit_await_completion", task_ids=["task-1"], timeout=10)
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

            time.sleep(0.3)
            store.tasks["task-2"].status = "completed"

        t = threading.Thread(target=complete_one)
        t.start()
        try:
            result = _call_tool(
                server,
                "hermit_await_completion",
                task_ids=["task-1", "task-2"],
                timeout=10,
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
