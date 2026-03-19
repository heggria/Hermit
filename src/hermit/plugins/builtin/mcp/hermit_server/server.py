"""Hermit MCP Server — exposes kernel tools via Streamable HTTP for supervisor agents."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import uvicorn
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_log = structlog.get_logger()

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _task_summary(task: Any, store: Any) -> dict[str, Any]:
    """Build a concise summary for a terminal-state task."""
    events = store.list_events(task_id=task.task_id, limit=5)
    return {
        "status": task.status,
        "task": task.__dict__,
        "recent_events": events,
    }


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
                "Hermit is a governed agent kernel that AUTONOMOUSLY EXECUTES code "
                "modifications, shell commands, and multi-step tasks. When you submit "
                "a task, Hermit compiles context, calls its own LLM provider, runs "
                "tools (write_file, bash, etc.) under governed policy, and produces "
                "receipts and proofs — all without Claude needing to intervene. "
                "Intermediate statuses like 'reconciling' are normal and transient; "
                "the task will reach 'completed' or 'failed' on its own. "
                "Use hermit_await_completion to block until tasks finish instead of "
                "polling hermit_task_status. When status is 'blocked' with pending "
                "approvals, use hermit_approve or hermit_deny to resolve them."
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
        def hermit_submit_dag_task(  # pyright: ignore[reportUnusedFunction]
            goal: str,
            nodes: list[dict[str, Any]],
            policy_profile: str = "autonomous",
        ) -> dict[str, Any]:
            """Submit a DAG task with parallel and dependent steps for governed execution.

            Steps run concurrently when independent; dependent steps wait for
            upstream completion. Hermit handles scheduling, join barriers, data
            flow, failure cascade, and proof generation automatically.

            Args:
                goal: High-level goal for the entire DAG task.
                nodes: List of step definitions. Each dict must contain:
                    - key (str): Unique step identifier.
                    - kind (str): Step type — "execute", "research", "code", "review", etc.
                    - title (str): Human-readable step name.
                    Optional fields:
                    - depends_on (list[str]): Keys of upstream dependencies. Default [].
                    - join_strategy (str): "all_required" (default), "any_sufficient",
                      "majority", or "best_effort".
                    - input_bindings (dict): Maps local names to "upstream_key.output_ref".
                    - max_attempts (int): Max retry attempts. Default 1.
                    - metadata (dict): Arbitrary metadata for the step.
                policy_profile: Policy profile — "autonomous" (default, high autonomy),
                    "default" (medium), "supervised" (low), or "readonly" (none).
            """
            from hermit.kernel.task.services.dag_builder import StepNode

            runner = self._get_runner()
            task_controller = getattr(runner, "task_controller", None)
            if task_controller is None:
                return {"error": "TaskController is not available on runner"}

            parsed_nodes: list[StepNode] = []
            for i, raw in enumerate(nodes):
                key = raw.get("key")
                kind = raw.get("kind")
                title = raw.get("title")
                if not key or not kind or not title:
                    return {
                        "error": f"Node at index {i} missing required field (key, kind, title)",
                        "node": raw,
                    }
                parsed_nodes.append(
                    StepNode(
                        key=key,
                        kind=kind,
                        title=title,
                        depends_on=raw.get("depends_on", []),
                        join_strategy=raw.get("join_strategy", "all_required"),
                        input_bindings=raw.get("input_bindings", {}),
                        max_attempts=raw.get("max_attempts", 1),
                        metadata=raw.get("metadata", {}),
                    )
                )

            conversation_id = f"mcp-dag-{uuid4().hex[:8]}"
            workspace_root = str(
                getattr(getattr(runner, "agent", None), "workspace_root", "") or ""
            )
            try:
                ctx, dag, key_to_step_id, _root_contexts = task_controller.start_dag_task(
                    conversation_id=conversation_id,
                    goal=goal,
                    source_channel="mcp-supervisor",
                    nodes=parsed_nodes,
                    policy_profile=policy_profile,
                    requested_by="supervisor",
                    workspace_root=workspace_root,
                    ingress_metadata={
                        "source": "mcp-supervisor",
                        "dispatch_mode": "async",
                        "policy_profile": policy_profile,
                    },
                )
            except ValueError as exc:
                return {"error": str(exc)}

            # Wake the dispatch service so root steps are claimed immediately.
            runner.wake_dispatcher()

            return {
                "task_id": ctx.task_id,
                "status": "queued",
                "dag_topology": {
                    "roots": dag.roots,
                    "leaves": dag.leaves,
                    "total_steps": len(dag.topological_order),
                    "topological_order": dag.topological_order,
                },
                "step_ids": key_to_step_id,
                "message": (
                    f"DAG task created with {len(dag.topological_order)} steps. "
                    f"Root steps ({', '.join(dag.roots)}) are ready for execution. "
                    "Use hermit_task_status to monitor progress."
                ),
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
        def hermit_task_proof(task_id: str, detail: str = "summary") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Export the proof bundle for a completed task.

            Args:
                task_id: The task ID to export proof for.
                detail: Verbosity level — "summary" (default, ~5-20 KB):
                    core verification, chain status, refs only.
                    "standard" (~50-200 KB): adds full governance records.
                    "full" (can be MBs): adds receipt bundles, context
                    manifests, artifact index, and Merkle proofs.
            """
            from hermit.kernel.verification.proofs.proofs import ProofService

            store = self._get_store()
            task = store.get_task(task_id)
            if task is None:
                return {"error": "Task not found", "task_id": task_id}

            return ProofService(store).export_task_proof(task_id, detail=detail)

        @self._mcp.tool()
        def hermit_await_completion(  # pyright: ignore[reportUnusedFunction]
            task_ids: list[str],
            timeout: int = 120,
        ) -> dict[str, Any]:
            """Block until one or more tasks reach a terminal state (completed/failed/cancelled).

            Returns immediately for tasks already in a terminal state. For running tasks,
            blocks server-side and returns as soon as any task finishes or the timeout expires.
            This eliminates the need for client-side polling.

            Args:
                task_ids: List of task IDs to wait for.
                timeout: Maximum wait time in seconds (default 120, max 300).
            """
            timeout = min(max(timeout, 1), 300)
            store = self._get_store()
            deadline = time.monotonic() + timeout

            # Build initial snapshot — return immediately if all already terminal
            results: dict[str, dict[str, Any]] = {}
            pending_ids: list[str] = []

            for tid in task_ids:
                task = store.get_task(tid)
                if task is None:
                    results[tid] = {"status": "not_found", "error": "Task not found"}
                elif task.status in _TERMINAL_STATUSES:
                    results[tid] = _task_summary(task, store)
                else:
                    pending_ids.append(tid)

            if not pending_ids:
                return {"completed": results, "pending": [], "timed_out": False}

            # Block until at least one pending task finishes or timeout
            newly_done: dict[str, dict[str, Any]] = {}
            still_pending: list[str] = list(pending_ids)

            # Subscribe to status-change events for all pending tasks.
            # get_or_create_task_event is only available on KernelStore (not the
            # generic store protocol), so we fall back to a short poll interval
            # when the store doesn't support events.
            _get_event = getattr(store, "get_or_create_task_event", None)
            _FALLBACK_POLL = 0.5  # seconds – used only when store lacks event support

            while time.monotonic() < deadline and still_pending:
                # Wait for any status change, then scan all still-pending tasks.
                if _get_event is not None:
                    # Collect the union of all per-task events so that any single
                    # status change wakes us up.  We subscribe *before* checking
                    # status to avoid a race where the event fires between the
                    # status-read and the wait.
                    events = [_get_event(tid) for tid in still_pending]
                    wait_secs = max(0.0, deadline - time.monotonic())
                    # Wait for any single event (we check all tasks afterwards).
                    for ev in events:
                        ev.wait(timeout=wait_secs)
                        if time.monotonic() >= deadline:
                            break
                        if ev.is_set():
                            break
                else:
                    time.sleep(_FALLBACK_POLL)

                remaining: list[str] = []
                for tid in still_pending:
                    task = store.get_task(tid)
                    if task is None:
                        newly_done[tid] = {"status": "not_found", "error": "Task not found"}
                    elif task.status in _TERMINAL_STATUSES:
                        newly_done[tid] = _task_summary(task, store)
                    elif task.status == "blocked":
                        # Report blocked tasks with approval info
                        approvals = store.list_approvals(task_id=tid, status="pending", limit=5)
                        newly_done[tid] = {
                            "status": "blocked",
                            "task": task.__dict__,
                            "pending_approvals": [a.__dict__ for a in approvals],
                        }
                    else:
                        remaining.append(tid)
                still_pending = remaining

                # Return as soon as we have any newly finished task
                if newly_done:
                    break

            results.update(newly_done)
            timed_out = bool(still_pending) and time.monotonic() >= deadline

            # For still-pending tasks, include current status snapshot
            pending_snapshots: list[dict[str, Any]] = []
            for tid in still_pending:
                task = store.get_task(tid)
                if task is not None:
                    pending_snapshots.append({"task_id": tid, "status": task.status})
                else:
                    pending_snapshots.append({"task_id": tid, "status": "not_found"})

            return {
                "completed": results,
                "pending": pending_snapshots,
                "timed_out": timed_out,
            }

        @self._mcp.tool()
        def hermit_compute_metrics(  # pyright: ignore[reportUnusedFunction]
            window_hours: float = 24.0,
            task_id: str = "",
            limit: int = 500,
        ) -> dict[str, Any]:
            """Compute kernel governance metrics for a time window.

            Returns aggregated statistics including task throughput, approval rate,
            rollback rate, evidence sufficiency, tool usage, and action risk entries.

            Args:
                window_hours: Size of the look-back window in hours (default 24).
                task_id: Optional task ID to scope metrics to a single task.
                limit: Maximum records to query per entity type (default 500).
            """
            import time as _time

            from hermit.kernel.analytics.engine import AnalyticsEngine

            store = self._get_store()
            engine = AnalyticsEngine(store)

            window_end = _time.time()
            window_start = window_end - max(0.0, window_hours) * 3600.0

            metrics = engine.compute_metrics(
                window_start=window_start,
                window_end=window_end,
                task_id=task_id if task_id else None,
                limit=min(limit, 2000),
            )

            return {
                "window_start": metrics.window_start,
                "window_end": metrics.window_end,
                "window_hours": window_hours,
                "task_id": task_id or None,
                "task_throughput": metrics.task_throughput,
                "approval_rate": metrics.approval_rate,
                "avg_approval_latency": metrics.avg_approval_latency,
                "rollback_rate": metrics.rollback_rate,
                "evidence_sufficiency_avg": metrics.evidence_sufficiency_avg,
                "tool_usage_counts": metrics.tool_usage_counts,
                "action_class_distribution": metrics.action_class_distribution,
                "risk_entries": [
                    {
                        "action_type": e.action_type,
                        "risk_level": e.risk_level,
                        "result_code": e.result_code,
                        "receipt_id": e.receipt_id,
                        "rollback_supported": e.rollback_supported,
                    }
                    for e in metrics.risk_entries
                ],
            }

        @self._mcp.tool()
        def hermit_task_metrics(  # pyright: ignore[reportUnusedFunction]
            task_ids: list[str],
            include_step_timings: bool = False,
        ) -> dict[str, Any]:
            """Return execution timing metrics for one or more tasks.

            Aggregates per-step timing from StepRecord started_at/finished_at fields,
            falling back to StepAttemptRecord timestamps when step-level timing is absent.

            Args:
                task_ids: List of task IDs to compute metrics for.
                include_step_timings: Include per-step timing breakdown in response.
            """
            from hermit.kernel.analytics.task_metrics import TaskMetricsService

            store = self._get_store()
            service = TaskMetricsService(store)

            summary = service.compute_multi_task_metrics(
                task_ids,
                include_step_timings=include_step_timings,
            )

            def _serialize_metrics(m: Any) -> dict[str, Any]:
                out: dict[str, Any] = {
                    "task_id": m.task_id,
                    "task_status": m.task_status,
                    "total_steps": m.total_steps,
                    "completed_steps": m.completed_steps,
                    "failed_steps": m.failed_steps,
                    "skipped_steps": m.skipped_steps,
                    "total_duration_seconds": m.total_duration_seconds,
                    "avg_step_duration_seconds": m.avg_step_duration_seconds,
                    "min_step_duration_seconds": m.min_step_duration_seconds,
                    "max_step_duration_seconds": m.max_step_duration_seconds,
                }
                if include_step_timings:
                    out["step_timings"] = [
                        {
                            "step_id": t.step_id,
                            "kind": t.kind,
                            "status": t.status,
                            "duration_seconds": t.duration_seconds,
                            "started_at": t.started_at,
                            "finished_at": t.finished_at,
                        }
                        for t in m.step_timings
                    ]
                return out

            return {
                "tasks": [_serialize_metrics(m) for m in summary.tasks],
                "total_tasks": summary.total_tasks,
                "tasks_with_timing": summary.tasks_with_timing,
            }

        @self._mcp.tool()
        def hermit_health_check(  # pyright: ignore[reportUnusedFunction]
            stale_threshold_minutes: float = 10.0,
            window_hours: float = 24.0,
        ) -> dict[str, Any]:
            """Check kernel health: stale tasks, failure rate, throughput, and health score.

            Returns an aggregate health report with a numeric score (0-100) and level
            (healthy/degraded/unhealthy). Use this to monitor kernel operational status.

            Args:
                stale_threshold_minutes: Tasks idle longer than this are flagged stale (default 10).
                window_hours: Look-back window for throughput and failure metrics (default 24).
            """
            from hermit.kernel.analytics.health.monitor import TaskHealthMonitor

            store = self._get_store()
            monitor = TaskHealthMonitor(store)
            report = monitor.check_health(
                stale_threshold_seconds=max(0.0, stale_threshold_minutes) * 60.0,
                window_seconds=max(0.0, window_hours) * 3600.0,
            )

            return {
                "health_level": report.health_level.value,
                "health_score": report.health_score,
                "total_active_tasks": report.total_active_tasks,
                "total_stale_tasks": report.total_stale_tasks,
                "failure_rate": report.failure_rate,
                "stale_tasks": [
                    {
                        "task_id": s.task_id,
                        "title": s.title,
                        "status": s.status,
                        "idle_seconds": round(s.idle_seconds, 1),
                    }
                    for s in report.stale_tasks
                ],
                "throughput": {
                    "completed_tasks": report.throughput.completed_tasks,
                    "failed_tasks": report.throughput.failed_tasks,
                    "throughput_per_hour": round(report.throughput.throughput_per_hour, 2),
                    "failure_rate": round(report.throughput.failure_rate, 3),
                    "window_hours": window_hours,
                }
                if report.throughput
                else None,
                "notes": report.notes,
                "scored_at": report.scored_at,
            }

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
