"""Integration tests for Hermit MCP Supervisor Server with real KernelStore."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.mcp.hermit_server.server import HermitMcpServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "test.db")


@pytest.fixture
def runner(store: KernelStore) -> SimpleNamespace:
    """Fake runner with a real KernelStore attached."""
    ingress_calls: list[dict[str, Any]] = []
    approval_calls: list[dict[str, Any]] = []

    def enqueue_ingress(
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
        ingress_calls.append(
            {
                "session_id": session_id,
                "text": text,
                "source_channel": source_channel,
            }
        )

    def _resolve_approval(
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        on_tool_call: Any = None,
        on_tool_start: Any = None,
    ) -> SimpleNamespace:
        approval_calls.append({"action": action, "approval_id": approval_id, "reason": reason})
        return SimpleNamespace(text=f"{action}d {approval_id}")

    return SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        enqueue_ingress=enqueue_ingress,
        _resolve_approval=_resolve_approval,
        ingress_calls=ingress_calls,
        approval_calls=approval_calls,
    )


@pytest.fixture
def server(runner: SimpleNamespace) -> HermitMcpServer:
    srv = HermitMcpServer(host="127.0.0.1", port=0)
    srv._runner = runner
    return srv


def _call(server: HermitMcpServer, name: str, **kwargs: Any) -> dict[str, Any]:
    loop = asyncio.new_event_loop()
    try:
        raw = loop.run_until_complete(server._mcp.call_tool(name, kwargs))
    finally:
        loop.close()
    content_list = raw[0] if isinstance(raw, tuple) else raw
    for item in content_list:
        if hasattr(item, "text"):
            return json.loads(item.text)
    raise AssertionError(f"No text from {name}: {raw!r}")


# ---------------------------------------------------------------------------
# End-to-end flow: submit → status → approve → verify
# ---------------------------------------------------------------------------


class TestSupervisorFlow:
    def test_submit_task_appears_in_store(
        self, server: HermitMcpServer, store: KernelStore, runner: SimpleNamespace
    ) -> None:
        """Submit a task via MCP and verify it was dispatched to the runner."""
        result = _call(server, "hermit_submit", description="Fix the auth bug")
        assert result["status"] == "accepted"
        assert len(runner.ingress_calls) == 1
        assert runner.ingress_calls[0]["text"] == "Fix the auth bug"

    def test_task_lifecycle_with_real_store(
        self, server: HermitMcpServer, store: KernelStore
    ) -> None:
        """Create a task in the store, query it via MCP tools, then cancel it."""
        # Create task directly in store
        task = store.create_task(
            conversation_id="conv-test",
            title="Integration test task",
            goal="Test the supervisor flow",
            source_channel="mcp-supervisor",
        )

        # List tasks
        list_result = _call(server, "hermit_list_tasks")
        assert list_result["count"] >= 1
        task_ids = [t["task_id"] for t in list_result["tasks"]]
        assert task.task_id in task_ids

        # Get task status
        status_result = _call(server, "hermit_task_status", task_ids=[task.task_id])
        assert status_result["tasks"][0]["task"]["title"] == "Integration test task"
        assert status_result["tasks"][0]["is_blocked"] is False

        # Cancel task
        cancel_result = _call(server, "hermit_cancel_task", task_ids=[task.task_id])
        assert cancel_result["results"][0]["status"] == "cancelled"

        # Verify cancelled
        status_after = _call(server, "hermit_task_status", task_ids=[task.task_id])
        assert status_after["tasks"][0]["task"]["status"] == "cancelled"

    def test_approval_flow_with_real_store(
        self,
        server: HermitMcpServer,
        store: KernelStore,
        runner: SimpleNamespace,
    ) -> None:
        """Create a task + approval in the store, then approve via MCP."""
        task = store.create_task(
            conversation_id="conv-approval",
            title="Task needing approval",
            goal="Test approval",
            source_channel="test",
        )
        approval = store.create_approval(
            task_id=task.task_id,
            step_id="step-1",
            step_attempt_id="sa-1",
            approval_type="tool_execution",
            requested_action={"tool": "bash", "args": {"command": "ls"}},
            request_packet_ref=None,
        )

        # Verify it shows as pending
        pending = _call(server, "hermit_pending_approvals")
        assert pending["count"] >= 1
        approval_ids = [a["approval_id"] for a in pending["approvals"]]
        assert approval.approval_id in approval_ids

        # Task status shows blocked
        status = _call(server, "hermit_task_status", task_ids=[task.task_id])
        assert status["tasks"][0]["is_blocked"] is True

        # Approve it
        approve_result = _call(
            server, "hermit_approve", approval_ids=[approval.approval_id], reason="Looks safe"
        )
        assert approve_result["results"][0]["status"] == "approved"
        assert len(runner.approval_calls) == 1
        assert runner.approval_calls[0]["action"] == "approve"

    def test_deny_approval_with_real_store(
        self,
        server: HermitMcpServer,
        store: KernelStore,
        runner: SimpleNamespace,
    ) -> None:
        """Create an approval and deny it via MCP."""
        task = store.create_task(
            conversation_id="conv-deny",
            title="Task to deny",
            goal="Test deny",
            source_channel="test",
        )
        approval = store.create_approval(
            task_id=task.task_id,
            step_id="step-2",
            step_attempt_id="sa-2",
            approval_type="tool_execution",
            requested_action={"tool": "rm", "args": {"path": "/"}},
            request_packet_ref=None,
        )

        deny_result = _call(
            server, "hermit_deny", approval_ids=[approval.approval_id], reason="Too dangerous"
        )
        assert deny_result["results"][0]["status"] == "denied"
        assert runner.approval_calls[0]["reason"] == "Too dangerous"

    def test_task_proof_with_real_store(
        self,
        server: HermitMcpServer,
        store: KernelStore,
    ) -> None:
        """Export proof for a task in the real store."""
        task = store.create_task(
            conversation_id="conv-proof",
            title="Proof test",
            goal="Test proof export",
            source_channel="test",
        )

        proof_result = _call(server, "hermit_task_proof", task_ids=[task.task_id])
        # ProofService returns a batch dict with proofs list
        assert proof_result["count"] == 1
        assert "error" not in proof_result["proofs"][0] or "proof" in proof_result["proofs"][0]

    def test_list_tasks_with_status_filter(
        self,
        server: HermitMcpServer,
        store: KernelStore,
    ) -> None:
        """List tasks filtered by status."""
        store.create_task(
            conversation_id="conv-a",
            title="Running task",
            goal="run",
            source_channel="test",
            status="running",
        )
        store.create_task(
            conversation_id="conv-b",
            title="Completed task",
            goal="done",
            source_channel="test",
            status="completed",
        )

        running = _call(server, "hermit_list_tasks", status="running")
        completed = _call(server, "hermit_list_tasks", status="completed")

        running_titles = [t["title"] for t in running["tasks"]]
        completed_titles = [t["title"] for t in completed["tasks"]]

        assert "Running task" in running_titles
        assert "Completed task" in completed_titles
        assert "Completed task" not in running_titles
