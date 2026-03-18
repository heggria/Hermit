"""Hermit MCP Server — exposes kernel tools via Streamable HTTP for supervisor agents."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import uvicorn
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_log = structlog.get_logger()


class HermitMcpServer:
    """MCP server that exposes Hermit kernel operations as tools.

    Designed for supervisor agents (e.g. Claude Code) to submit tasks,
    monitor status, and manage approvals without direct code access.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8322) -> None:
        self._host = host
        self._port = port
        self._runner: AgentRunner | None = None
        self._runner_lock = threading.Lock()
        self._uv_server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        self._mcp = FastMCP(
            "Hermit Kernel",
            instructions=(
                "Hermit is a governed agent kernel. Use these tools to submit tasks, "
                "monitor execution, and manage approvals. Poll hermit_task_status to "
                "track progress. When status is 'blocked' with pending approvals, "
                "use hermit_approve or hermit_deny to resolve them."
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------
    # Runner management
    # ------------------------------------------------------------------

    def swap_runner(self, new_runner: AgentRunner) -> None:
        """Atomically swap the runner reference for hot-reload."""
        with self._runner_lock:
            self._runner = new_runner
        _log.info("mcp_server_runner_swapped")  # type: ignore[call-arg]

    def _get_runner(self) -> AgentRunner:
        with self._runner_lock:
            runner = self._runner
        if runner is None:
            raise RuntimeError("Runner is not attached")
        return runner

    def _get_store(self) -> Any:
        runner = self._get_runner()
        task_controller = getattr(runner, "task_controller", None)
        if task_controller is not None:
            return task_controller.store
        store = getattr(getattr(runner, "agent", None), "kernel_store", None)
        if store is None:
            raise RuntimeError("Kernel store is not available")
        return store

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        @self._mcp.tool()
        def hermit_submit_task(  # pyright: ignore[reportUnusedFunction]
            description: str,
            priority: str = "normal",
            policy_profile: str = "",
        ) -> dict[str, Any]:
            """Submit a task to the Hermit kernel for governed execution.

            Args:
                description: What the task should accomplish.
                priority: Task priority — "low", "normal", or "high".
                policy_profile: Optional policy profile to apply (e.g. "supervised").
            """
            runner = self._get_runner()
            session_id = f"mcp-supervisor-{uuid4().hex[:8]}"

            metadata: dict[str, object] = {
                "source": "mcp-supervisor",
                "priority": priority,
            }
            if policy_profile:
                metadata["policy_profile"] = policy_profile

            runner.enqueue_ingress(
                session_id,
                description,
                source_channel="mcp-supervisor",
                source_ref="mcp-supervisor",
                requested_by="supervisor",
                ingress_metadata=metadata,
            )

            return {
                "session_id": session_id,
                "status": "accepted",
                "message": "Task submitted. Use hermit_task_status or hermit_list_tasks to track.",
            }

        @self._mcp.tool()
        def hermit_task_status(task_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Get detailed status of a task including events and pending approvals.

            Args:
                task_id: The task ID to query.
            """
            store = self._get_store()
            task = store.get_task(task_id)
            if task is None:
                return {"error": "Task not found", "task_id": task_id}

            approvals = store.list_approvals(task_id=task_id, status="pending", limit=10)
            events = store.list_events(task_id=task_id, limit=20)

            return {
                "task": task.__dict__,
                "pending_approvals": [a.__dict__ for a in approvals],
                "recent_events": events[-10:] if len(events) > 10 else events,
                "is_blocked": len(approvals) > 0,
            }

        @self._mcp.tool()
        def hermit_list_tasks(  # pyright: ignore[reportUnusedFunction]
            status: str = "",
            limit: int = 20,
        ) -> dict[str, Any]:
            """List recent tasks from the kernel.

            Args:
                status: Filter by status (e.g. "running", "completed", "failed"). Empty for all.
                limit: Maximum number of tasks to return.
            """
            store = self._get_store()
            tasks = store.list_tasks(
                status=status if status else None,
                limit=min(limit, 50),
            )
            return {"tasks": [t.__dict__ for t in tasks], "count": len(tasks)}

        @self._mcp.tool()
        def hermit_pending_approvals(  # pyright: ignore[reportUnusedFunction]
            task_id: str = "",
            limit: int = 20,
        ) -> dict[str, Any]:
            """List all pending approvals across tasks.

            Args:
                task_id: Optional filter by task ID. Empty for all tasks.
                limit: Maximum number of approvals to return.
            """
            store = self._get_store()
            approvals = store.list_approvals(
                task_id=task_id if task_id else None,
                status="pending",
                limit=min(limit, 50),
            )
            return {"approvals": [a.__dict__ for a in approvals], "count": len(approvals)}

        @self._mcp.tool()
        def hermit_approve(approval_id: str, reason: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Approve a pending approval request.

            Args:
                approval_id: The approval ID to approve.
                reason: Optional reason for the approval.
            """
            runner = self._get_runner()
            store = self._get_store()

            approval = store.get_approval(approval_id)
            if approval is None:
                return {"error": "Approval not found", "approval_id": approval_id}

            task = store.get_task(approval.task_id)
            if task is None:
                return {"error": "Task not found for approval", "approval_id": approval_id}

            result = runner._resolve_approval(  # type: ignore[attr-defined]
                task.conversation_id,
                action="approve",
                approval_id=approval_id,
                reason=reason,
            )
            return {
                "status": "approved",
                "approval_id": approval_id,
                "text": result.text,
            }

        @self._mcp.tool()
        def hermit_deny(approval_id: str, reason: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Deny a pending approval request.

            Args:
                approval_id: The approval ID to deny.
                reason: Reason for denial.
            """
            runner = self._get_runner()
            store = self._get_store()

            approval = store.get_approval(approval_id)
            if approval is None:
                return {"error": "Approval not found", "approval_id": approval_id}

            task = store.get_task(approval.task_id)
            if task is None:
                return {"error": "Task not found for approval", "approval_id": approval_id}

            result = runner._resolve_approval(  # type: ignore[attr-defined]
                task.conversation_id,
                action="deny",
                approval_id=approval_id,
                reason=reason,
            )
            return {
                "status": "denied",
                "approval_id": approval_id,
                "text": result.text,
            }

        @self._mcp.tool()
        def hermit_cancel_task(task_id: str, reason: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Cancel a running task.

            Args:
                task_id: The task ID to cancel.
                reason: Reason for cancellation.
            """
            store = self._get_store()
            task = store.get_task(task_id)
            if task is None:
                return {"error": "Task not found", "task_id": task_id}
            if task.status in ("completed", "failed", "cancelled"):
                return {
                    "error": f"Task already in terminal state: {task.status}",
                    "task_id": task_id,
                }

            store.update_task_status(
                task_id,
                "cancelled",
                payload={"reason": reason, "cancelled_by": "supervisor"},
            )
            return {"status": "cancelled", "task_id": task_id}

        @self._mcp.tool()
        def hermit_task_proof(task_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Export the proof bundle for a completed task.

            Args:
                task_id: The task ID to export proof for.
            """
            from hermit.kernel.verification.proofs.proofs import ProofService

            store = self._get_store()
            task = store.get_task(task_id)
            if task is None:
                return {"error": "Task not found", "task_id": task_id}

            return ProofService(store).export_task_proof(task_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, runner: AgentRunner) -> None:
        with self._runner_lock:
            self._runner = runner

        app = self._mcp.streamable_http_app()
        uv_config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._uv_server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._uv_server.run,
            name="hermit-mcp-server",
            daemon=True,
        )
        self._thread.start()
        _log.info(  # type: ignore[call-arg]
            "mcp_server_started",
            host=self._host,
            port=self._port,
        )

    def stop(self) -> None:
        if self._uv_server is not None:
            self._uv_server.should_exit = True
        _log.info("mcp_server_stopped")  # type: ignore[call-arg]
