"""Tests for plugins/builtin/mcp/hermit_server/server.py — coverage for missed lines.

Covers: HermitMcpServer._get_runner, _get_store, swap_runner,
_task_summary, lifecycle start/stop, tool registration edge cases.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.mcp.hermit_server.server import (
    HermitMcpServer,
    _task_summary,
)

# ---------------------------------------------------------------------------
# _task_summary
# ---------------------------------------------------------------------------


class TestTaskSummary:
    def test_returns_expected_fields(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t1", goal="g1", source_channel="test")
        result = _task_summary(task, store)
        assert result["status"] == task.status
        assert "task" in result
        assert "recent_events" in result


# ---------------------------------------------------------------------------
# Runner management
# ---------------------------------------------------------------------------


class TestRunnerManagement:
    def test_get_runner_no_runner_raises(self) -> None:
        server = HermitMcpServer()
        with pytest.raises(RuntimeError, match="not attached"):
            server._get_runner()

    def test_get_runner_returns_runner(self) -> None:
        server = HermitMcpServer()
        mock_runner = MagicMock()
        server._runner = mock_runner
        assert server._get_runner() is mock_runner

    def test_swap_runner(self) -> None:
        server = HermitMcpServer()
        r1 = MagicMock()
        r2 = MagicMock()
        server.swap_runner(r1)
        assert server._runner is r1
        server.swap_runner(r2)
        assert server._runner is r2


# ---------------------------------------------------------------------------
# Store retrieval
# ---------------------------------------------------------------------------


class TestGetStore:
    def test_via_task_controller(self) -> None:
        server = HermitMcpServer()
        mock_store = MagicMock()
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=mock_store))
        assert server._get_store() is mock_store

    def test_via_agent_kernel_store(self) -> None:
        server = HermitMcpServer()
        mock_store = MagicMock()
        server._runner = SimpleNamespace(
            task_controller=None,
            agent=SimpleNamespace(kernel_store=mock_store),
        )
        assert server._get_store() is mock_store

    def test_no_store_raises(self) -> None:
        server = HermitMcpServer()
        server._runner = SimpleNamespace(
            task_controller=None,
            agent=SimpleNamespace(kernel_store=None),
        )
        with pytest.raises(RuntimeError, match="not available"):
            server._get_store()

    def test_no_runner_raises(self) -> None:
        server = HermitMcpServer()
        with pytest.raises(RuntimeError, match="not attached"):
            server._get_store()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_stop_without_start(self) -> None:
        server = HermitMcpServer()
        # Should not raise
        server.stop()

    def test_stop_sets_should_exit(self) -> None:
        server = HermitMcpServer()
        mock_uv = MagicMock()
        server._uv_server = mock_uv
        server.stop()
        assert mock_uv.should_exit is True


# ---------------------------------------------------------------------------
# MCP tool wrappers — hermit_task_status, hermit_list_tasks, etc.
# ---------------------------------------------------------------------------


class TestMcpTools:
    @pytest.fixture
    def server_with_store(self, tmp_path: Path) -> tuple[HermitMcpServer, KernelStore]:
        store = KernelStore(tmp_path / "state.db")
        server = HermitMcpServer()
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(return_value=SimpleNamespace(text="ok")),
            enqueue_ingress=MagicMock(return_value=SimpleNamespace(task_id="t1")),
            agent=SimpleNamespace(workspace_root="/tmp"),
            wake_dispatcher=MagicMock(),
        )
        return server, store

    def _get_tool_fn(self, server: HermitMcpServer, tool_name: str):
        """Extract a registered tool function from FastMCP."""
        # FastMCP stores tools internally; we access via the server's _mcp
        tools = server._mcp._tool_manager._tools
        return tools[tool_name].fn

    def test_hermit_task_status_not_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_task_status")
        result = fn(task_ids=["nonexistent"])
        assert result["tasks"][0]["error"] == "Task not found"

    def test_hermit_task_status_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, store = server_with_store
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t", goal="g", source_channel="test")
        fn = self._get_tool_fn(server, "hermit_task_status")
        result = fn(task_ids=[task.task_id])
        assert result["tasks"][0]["task"]["task_id"] == task.task_id
        assert "pending_approvals" in result["tasks"][0]

    def test_hermit_list_tasks(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, store = server_with_store
        store.ensure_conversation("c1", source_channel="test")
        store.create_task(conversation_id="c1", title="t1", goal="g1", source_channel="test")
        fn = self._get_tool_fn(server, "hermit_list_tasks")
        result = fn(status="", limit=20)
        assert result["count"] >= 1

    def test_hermit_cancel_task_not_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_cancel_task")
        result = fn(task_ids=["nonexistent"])
        assert result["results"][0]["error"] == "Task not found"

    def test_hermit_cancel_task_terminal_state(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, store = server_with_store
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t1", goal="g1", source_channel="test")
        store.update_task_status(task.task_id, "completed")
        fn = self._get_tool_fn(server, "hermit_cancel_task")
        result = fn(task_ids=[task.task_id])
        assert "already in terminal" in result["results"][0]["error"]

    def test_hermit_cancel_task_success(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, store = server_with_store
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t1", goal="g1", source_channel="test")
        fn = self._get_tool_fn(server, "hermit_cancel_task")
        result = fn(task_ids=[task.task_id])
        assert result["results"][0]["status"] == "cancelled"

    def test_hermit_pending_approvals_empty(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_pending_approvals")
        result = fn(task_id="", limit=20)
        assert result["count"] == 0

    def test_hermit_approve_not_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_approve")
        result = fn(approval_ids=["nonexistent"])
        assert result["results"][0]["error"] == "Approval not found"

    def test_hermit_deny_not_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_deny")
        result = fn(approval_ids=["nonexistent"])
        assert result["results"][0]["error"] == "Approval not found"

    def test_hermit_submit(self, server_with_store: tuple[HermitMcpServer, KernelStore]) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_submit")
        result = fn(description="do something", priority="high", policy_profile="supervised")
        assert result["status"] == "accepted"
        server._runner.enqueue_ingress.assert_called_once()

    def test_hermit_task_proof_not_found(
        self, server_with_store: tuple[HermitMcpServer, KernelStore]
    ) -> None:
        server, _store = server_with_store
        fn = self._get_tool_fn(server, "hermit_task_proof")
        result = fn(task_ids=["nonexistent"])
        assert result["proofs"][0]["error"] == "Task not found"
