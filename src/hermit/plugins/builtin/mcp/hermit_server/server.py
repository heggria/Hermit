"""Hermit MCP Server — exposes kernel tools via Streamable HTTP for supervisor agents."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import uvicorn
from mcp.server.fastmcp import FastMCP

from hermit.plugins.builtin.hooks.metaloop.orchestrator import MAX_QUEUE_DEPTH

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_log = structlog.get_logger()

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

_SLIM_TASK_FIELDS = (
    "task_id",
    "title",
    "goal",
    "status",
    "priority",
    "created_at",
    "updated_at",
    "budget_tokens_used",
)

_SLIM_TASK_LIST_FIELDS = (
    "task_id",
    "title",
    "goal",
    "status",
    "priority",
    "created_at",
    "updated_at",
)

_SLIM_APPROVAL_FIELDS = (
    "approval_id",
    "task_id",
    "step_id",
    "approval_type",
    "status",
    "requested_at",
    "expires_at",
    "resolved_at",
)

_SLIM_EVENT_FIELDS = ("event_type", "entity_type", "entity_id", "occurred_at")

_GOAL_MAX_LEN = 200
_GOAL_LIST_MAX_LEN = 100
_PAYLOAD_MAX_LEN = 120


def _getval(obj: Any, key: str) -> Any:
    src = obj.__dict__ if hasattr(obj, "__dict__") else obj
    return src.get(key) if isinstance(src, dict) else getattr(obj, key, None)


def _truncate(s: str, limit: int) -> str:
    return s[:limit] + "…" if len(s) > limit else s


def _slim_task(task: Any, *, fields: tuple[str, ...] = _SLIM_TASK_FIELDS) -> dict[str, Any]:
    """TaskRecord → slim dict, skipping None values and truncating goal."""
    goal_limit = _GOAL_LIST_MAX_LEN if fields is _SLIM_TASK_LIST_FIELDS else _GOAL_MAX_LEN
    out: dict[str, Any] = {}
    for k in fields:
        v = _getval(task, k)
        if v is None:
            continue
        if k == "goal" and isinstance(v, str):
            v = _truncate(v, goal_limit)
        out[k] = v
    return out


def _action_summary(requested_action: Any) -> str | None:
    """Extract a concise action summary from an approval's requested_action."""
    if not requested_action or not isinstance(requested_action, dict):
        return None
    tool = requested_action.get("tool_name") or requested_action.get("tool", "")
    args = requested_action.get("arguments", {})
    if tool == "bash" and isinstance(args, dict):
        cmd = args.get("command", "")
        return _truncate(f"bash: {cmd}", _GOAL_MAX_LEN) if cmd else "bash"
    if tool == "write_file" and isinstance(args, dict):
        path = args.get("path", args.get("file_path", ""))
        return _truncate(f"write_file: {path}", _GOAL_MAX_LEN) if path else "write_file"
    if tool:
        summary = str(tool)
        if isinstance(args, dict) and args:
            first_val = next(iter(args.values()), "")
            if isinstance(first_val, str) and first_val:
                summary += f": {first_val}"
        return _truncate(summary, _GOAL_MAX_LEN)
    return None


def _slim_approval(approval: Any) -> dict[str, Any]:
    """ApprovalRecord → slim dict, skipping None values. Includes action_summary."""
    out = {k: v for k in _SLIM_APPROVAL_FIELDS if (v := _getval(approval, k)) is not None}
    action = _getval(approval, "requested_action")
    summary = _action_summary(action)
    if summary:
        out["action_summary"] = summary
    return out


def _slim_event(event: dict[str, Any]) -> dict[str, Any]:
    """Event dict → slim dict, payload truncated to 120 chars."""
    out = {k: v for k in _SLIM_EVENT_FIELDS if (v := event.get(k)) is not None}
    payload = event.get("payload")
    if payload is not None:
        out["payload"] = _truncate(str(payload), _PAYLOAD_MAX_LEN)
    return out


def _task_summary(task: Any, store: Any) -> dict[str, Any]:
    """Build a concise summary for a terminal-state task."""
    events = store.list_events(task_id=task.task_id, limit=5)
    return {
        "status": task.status,
        "task": _slim_task(task),
        "recent_events": [_slim_event(e) if isinstance(e, dict) else e for e in events],
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
                "When status is 'blocked' with pending approvals, use hermit_approve "
                "or hermit_deny to resolve them.\n\n"
                "OPTIMAL CALL PATTERNS (prefer fewer round-trips):\n"
                "- Single task: hermit_submit(description='...', await_completion=120) "
                "(1 call = submit + wait)\n"
                "- Multiple independent tasks: hermit_submit(tasks=[{description:'A'},"
                "{description:'B'}]) → hermit_await_completion(task_ids=..., mode='all')"
                " (2 calls)\n"
                "- Complex DAG: hermit_submit_dag_task → hermit_await_completion (2 calls)\n"
                "- All submit tools return task_ids — no need to call hermit_list_tasks "
                "after.\n"
                "- Approvals: hermit_task_status returns pending_approvals inline.\n"
                "- Metrics: hermit_metrics(kind='health'|'governance'|'task')\n"
                "- Use mode='all' with hermit_await_completion to wait for all tasks at once.\n\n"
                "Self-iteration tools (meta-loop):\n"
                "- hermit_submit_iteration: Submit iteration goals for self-improvement. "
                "Each iteration goes through research->spec->decompose->implement->review->benchmark->learn.\n"
                "- hermit_spec_queue: Manage the spec backlog queue (list/add/remove/reprioritize).\n"
                "- hermit_iteration_status: Get status of iterations including findings and spec.\n"
                "- hermit_benchmark_results: Retrieve benchmark results for iterations or specs.\n"
                "- hermit_lessons_learned: Query lessons learned from past iterations."
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

    def _submit_single(
        self,
        runner: AgentRunner,
        description: str,
        priority: str,
        policy_profile: str,
    ) -> tuple[str | None, str]:
        """Submit one task via enqueue_ingress. Returns (task_id, session_id)."""
        session_id = f"mcp-supervisor-{uuid4().hex[:8]}"
        metadata: dict[str, object] = {
            "source": "mcp-supervisor",
            "priority": priority,
        }
        if policy_profile:
            metadata["policy_profile"] = policy_profile
        ctx = runner.enqueue_ingress(
            session_id,
            description,
            source_channel="mcp-supervisor",
            source_ref="mcp-supervisor",
            requested_by="supervisor",
            ingress_metadata=metadata,
        )
        task_id = getattr(ctx, "task_id", None) if ctx else None
        return task_id, session_id

    def _await_single_task(
        self,
        task_id: str,
        timeout: int,
    ) -> dict[str, Any]:
        """Block until a single task reaches terminal/blocked state or times out."""
        timeout = min(max(timeout, 1), 300)
        store = self._get_store()
        deadline = time.monotonic() + timeout

        _register = getattr(store, "register_task_change_listener", None)
        _deregister = getattr(store, "deregister_task_change_listener", None)
        _FALLBACK_POLL = 0.5

        while time.monotonic() < deadline:
            task = store.get_task(task_id)
            if task is None:
                return {"task_id": task_id, "status": "not_found", "error": "Task disappeared"}

            if task.status in _TERMINAL_STATUSES:
                return {"task_id": task_id, **_task_summary(task, store)}

            if task.status == "blocked":
                approvals = store.list_approvals(
                    task_id=task_id,
                    status="pending",
                    limit=5,
                )
                return {
                    "task_id": task_id,
                    "status": "blocked",
                    "task": _slim_task(task),
                    "pending_approvals": [_slim_approval(a) for a in approvals],
                    "message": "Task blocked on approvals. Use hermit_approve to unblock.",
                }

            if _register is not None and _deregister is not None:
                ev = threading.Event()
                _register([task_id], ev)
                try:
                    ev.wait(timeout=max(0.0, deadline - time.monotonic()))
                finally:
                    _deregister([task_id], ev)
            else:
                time.sleep(_FALLBACK_POLL)

        task = store.get_task(task_id)
        return {
            "task_id": task_id,
            "status": task.status if task else "unknown",
            "timed_out": True,
            "message": (
                f"Task still running after {timeout}s. "
                "Use hermit_await_completion to continue waiting."
            ),
        }

    def _register_tools(self) -> None:
        @self._mcp.tool()
        def hermit_submit(  # pyright: ignore[reportUnusedFunction]
            description: str = "",
            tasks: list[dict[str, Any]] | None = None,
            priority: str = "normal",
            policy_profile: str = "autonomous",
            await_completion: int = 0,
        ) -> dict[str, Any]:
            """Submit one or more tasks to the Hermit kernel for governed execution.

            Modes (auto-detected):
            - SINGLE: provide description only → submits one task.
            - BATCH: provide tasks list → submits multiple independent tasks in parallel.
            - Any mode + await_completion > 0 → blocks until done or timed out.

            Args:
                description: What the task should accomplish (single mode).
                tasks: List of task dicts for batch mode. Each must contain:
                    - description (str): What the task should accomplish.
                    Optional: priority (str), policy_profile (str).
                priority: Default priority — "low", "normal", or "high".
                policy_profile: Default policy profile (default "autonomous").
                await_completion: If > 0, wait this many seconds for results.
                    Default 0 (fire-and-forget).
            """
            has_tasks = tasks is not None and len(tasks) > 0

            if not description and not has_tasks:
                return {"error": "Provide 'description' (single task) or 'tasks' (batch)."}

            runner = self._get_runner()

            # ── BATCH mode ──
            if has_tasks:
                assert tasks is not None  # for type narrowing
                results: list[dict[str, Any]] = []
                task_ids: list[str] = []

                for i, task_def in enumerate(tasks):
                    desc = task_def.get("description")
                    if not desc:
                        results.append({"index": i, "error": "Missing 'description' field"})
                        continue

                    t_priority = task_def.get("priority", priority)
                    t_policy = task_def.get("policy_profile", policy_profile)
                    tid, sid = self._submit_single(runner, desc, t_priority, t_policy)
                    if tid:
                        task_ids.append(tid)
                    results.append(
                        {
                            "index": i,
                            "task_id": tid,
                            "session_id": sid,
                            "status": "accepted",
                        }
                    )

                out: dict[str, Any] = {
                    "task_ids": task_ids,
                    "results": results,
                    "submitted": len(task_ids),
                    "status": "accepted",
                    "message": (
                        f"{len(task_ids)} tasks submitted. "
                        "Use hermit_await_completion(task_ids=...) to wait."
                    ),
                }

                if await_completion > 0 and task_ids:
                    await_result = hermit_await_completion(
                        task_ids=task_ids,
                        timeout=min(max(await_completion, 1), 300),
                        mode="all",
                    )
                    out.update(
                        {
                            "completed": await_result.get("completed", {}),
                            "pending": await_result.get("pending", []),
                            "timed_out": await_result.get("timed_out", False),
                        }
                    )

                return out

            # ── SINGLE mode ──
            task_id, session_id = self._submit_single(
                runner,
                description,
                priority,
                policy_profile,
            )

            if await_completion > 0 and task_id is not None:
                result = self._await_single_task(task_id, await_completion)
                result["task_ids"] = [task_id]
                return result

            return {
                "task_ids": [task_id] if task_id else [],
                "task_id": task_id,
                "session_id": session_id,
                "status": "accepted",
                "message": "Task submitted. Use hermit_await_completion to wait for results.",
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
        def hermit_task_status(task_ids: list[str]) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Get detailed status of one or more tasks including events and pending approvals.

            Args:
                task_ids: List of task IDs to query.
            """
            store = self._get_store()
            tasks_out: list[dict[str, Any]] = []

            for task_id in task_ids:
                task = store.get_task(task_id)
                if task is None:
                    tasks_out.append({"task_id": task_id, "error": "Task not found"})
                    continue

                approvals = store.list_approvals(task_id=task_id, status="pending", limit=10)
                events = store.list_events(task_id=task_id, limit=10)
                tasks_out.append(
                    {
                        "task_id": task_id,
                        "task": _slim_task(task),
                        "pending_approvals": [_slim_approval(a) for a in approvals],
                        "recent_events": [
                            _slim_event(e) if isinstance(e, dict) else e for e in events[-10:]
                        ],
                        "is_blocked": len(approvals) > 0,
                    }
                )

            return {"tasks": tasks_out, "count": len(tasks_out)}

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
            return {
                "tasks": [_slim_task(t, fields=_SLIM_TASK_LIST_FIELDS) for t in tasks],
                "count": len(tasks),
            }

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
            return {"approvals": [_slim_approval(a) for a in approvals], "count": len(approvals)}

        @self._mcp.tool()
        def hermit_approve(  # pyright: ignore[reportUnusedFunction]
            approval_ids: list[str],
            reason: str = "",
            await_after: int = 0,
        ) -> dict[str, Any]:
            """Approve one or more pending approval requests.

            Args:
                approval_ids: List of approval IDs to approve.
                reason: Optional reason for the approval.
                await_after: If > 0, wait this many seconds for affected tasks to
                    reach a terminal state after approval. Saves a separate
                    hermit_await_completion call. Default 0 (no wait).
            """
            runner = self._get_runner()
            store = self._get_store()
            results: list[dict[str, Any]] = []
            approved = 0
            errors = 0
            affected_task_ids: set[str] = set()

            for approval_id in approval_ids:
                approval = store.get_approval(approval_id)
                if approval is None:
                    results.append(
                        {
                            "approval_id": approval_id,
                            "status": "error",
                            "error": "Approval not found",
                        }
                    )
                    errors += 1
                    continue

                task = store.get_task(approval.task_id)
                if task is None:
                    results.append(
                        {
                            "approval_id": approval_id,
                            "status": "error",
                            "error": "Task not found for approval",
                        }
                    )
                    errors += 1
                    continue

                try:
                    result = runner._resolve_approval(  # type: ignore[attr-defined]
                        task.conversation_id,
                        action="approve",
                        approval_id=approval_id,
                        reason=reason,
                    )
                    results.append(
                        {"approval_id": approval_id, "status": "approved", "text": result.text}
                    )
                    approved += 1
                    affected_task_ids.add(approval.task_id)
                except Exception as exc:
                    results.append(
                        {"approval_id": approval_id, "status": "error", "error": str(exc)}
                    )
                    errors += 1

            out: dict[str, Any] = {"results": results, "approved": approved, "errors": errors}

            # Optional: wait for affected tasks after approval
            if await_after > 0 and affected_task_ids:
                await_timeout = min(max(await_after, 1), 300)
                task_results = hermit_await_completion(
                    task_ids=list(affected_task_ids),
                    timeout=await_timeout,
                    mode="all",
                )
                out["task_status"] = task_results

            return out

        @self._mcp.tool()
        def hermit_deny(  # pyright: ignore[reportUnusedFunction]
            approval_ids: list[str],
            reason: str = "",
            await_after: int = 0,
        ) -> dict[str, Any]:
            """Deny one or more pending approval requests.

            Args:
                approval_ids: List of approval IDs to deny.
                reason: Reason for denial.
                await_after: If > 0, wait this many seconds for affected tasks to
                    reach a terminal state after denial. Default 0 (no wait).
            """
            runner = self._get_runner()
            store = self._get_store()
            results: list[dict[str, Any]] = []
            denied = 0
            errors = 0
            affected_task_ids: set[str] = set()

            for approval_id in approval_ids:
                approval = store.get_approval(approval_id)
                if approval is None:
                    results.append(
                        {
                            "approval_id": approval_id,
                            "status": "error",
                            "error": "Approval not found",
                        }
                    )
                    errors += 1
                    continue

                task = store.get_task(approval.task_id)
                if task is None:
                    results.append(
                        {
                            "approval_id": approval_id,
                            "status": "error",
                            "error": "Task not found for approval",
                        }
                    )
                    errors += 1
                    continue

                try:
                    result = runner._resolve_approval(  # type: ignore[attr-defined]
                        task.conversation_id,
                        action="deny",
                        approval_id=approval_id,
                        reason=reason,
                    )
                    results.append(
                        {"approval_id": approval_id, "status": "denied", "text": result.text}
                    )
                    denied += 1
                    affected_task_ids.add(approval.task_id)
                except Exception as exc:
                    results.append(
                        {"approval_id": approval_id, "status": "error", "error": str(exc)}
                    )
                    errors += 1

            out: dict[str, Any] = {"results": results, "denied": denied, "errors": errors}

            if await_after > 0 and affected_task_ids:
                await_timeout = min(max(await_after, 1), 300)
                task_results = hermit_await_completion(
                    task_ids=list(affected_task_ids),
                    timeout=await_timeout,
                    mode="all",
                )
                out["task_status"] = task_results

            return out

        @self._mcp.tool()
        def hermit_cancel_task(task_ids: list[str], reason: str = "") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Cancel one or more running tasks.

            Args:
                task_ids: List of task IDs to cancel.
                reason: Reason for cancellation.
            """
            store = self._get_store()
            results: list[dict[str, Any]] = []
            cancelled = 0
            skipped = 0
            errors = 0

            for task_id in task_ids:
                task = store.get_task(task_id)
                if task is None:
                    results.append(
                        {"task_id": task_id, "status": "error", "error": "Task not found"}
                    )
                    errors += 1
                    continue
                if task.status in ("completed", "failed", "cancelled"):
                    results.append(
                        {
                            "task_id": task_id,
                            "status": "skipped",
                            "error": f"Task already in terminal state: {task.status}",
                        }
                    )
                    skipped += 1
                    continue

                store.update_task_status(
                    task_id,
                    "cancelled",
                    payload={"reason": reason, "cancelled_by": "supervisor"},
                )
                results.append({"task_id": task_id, "status": "cancelled"})
                cancelled += 1

            return {
                "results": results,
                "cancelled": cancelled,
                "skipped": skipped,
                "errors": errors,
            }

        @self._mcp.tool()
        def hermit_task_output(  # pyright: ignore[reportUnusedFunction]
            task_ids: list[str],
            include_receipts: bool = True,
            limit_per_task: int = 20,
        ) -> dict[str, Any]:
            """Get execution output summary for completed tasks.

            Returns what each task actually did: actions taken, results, and effects.
            Use this after a task completes to understand its output without reading files.

            Args:
                task_ids: List of task IDs to get output for.
                include_receipts: Include receipt details (action_type, result, effects).
                    Default True.
                limit_per_task: Max receipts per task. Default 20.
            """
            store = self._get_store()
            _list_receipts = getattr(store, "list_receipts", None)
            outputs: list[dict[str, Any]] = []

            for task_id in task_ids:
                task = store.get_task(task_id)
                if task is None:
                    outputs.append({"task_id": task_id, "error": "Task not found"})
                    continue

                out: dict[str, Any] = {
                    "task_id": task_id,
                    "status": task.status,
                    "title": getattr(task, "title", None),
                }

                if include_receipts and _list_receipts is not None:
                    try:
                        receipts = _list_receipts(
                            task_id=task_id,
                            limit=min(limit_per_task, 50),
                        )
                        receipt_summaries: list[dict[str, Any]] = []
                        for r in receipts:
                            entry: dict[str, Any] = {
                                "action_type": getattr(r, "action_type", None),
                                "result_code": getattr(r, "result_code", None),
                            }
                            result_summary = getattr(r, "result_summary", None)
                            if result_summary:
                                entry["result_summary"] = _truncate(
                                    str(result_summary),
                                    _GOAL_MAX_LEN,
                                )
                            effect = getattr(r, "observed_effect_summary", None)
                            if effect:
                                entry["effect"] = _truncate(str(effect), _GOAL_MAX_LEN)
                            rollback = getattr(r, "rollback_supported", None)
                            if rollback is not None:
                                entry["rollback_supported"] = rollback
                            receipt_summaries.append(entry)
                        out["receipts"] = receipt_summaries
                        out["total_actions"] = len(receipt_summaries)
                        # Aggregate result codes
                        codes = [
                            r.get("result_code") for r in receipt_summaries if r.get("result_code")
                        ]
                        out["result_codes"] = dict.fromkeys(codes)  # unique, ordered
                    except Exception as exc:
                        out["receipts_error"] = str(exc)

                # Include recent events as fallback context
                events = store.list_events(task_id=task_id, limit=5)
                out["recent_events"] = [
                    _slim_event(e) if isinstance(e, dict) else e for e in events
                ]
                outputs.append(out)

            return {"outputs": outputs, "count": len(outputs)}

        @self._mcp.tool()
        def hermit_task_proof(task_ids: list[str], detail: str = "summary") -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Export proof bundles for one or more completed tasks.

            Args:
                task_ids: List of task IDs to export proofs for.
                detail: Verbosity level — "summary" (default, ~5-20 KB):
                    core verification, chain status, refs only.
                    "standard" (~50-200 KB): adds full governance records.
                    "full" (can be MBs): adds receipt bundles, context
                    manifests, artifact index, and Merkle proofs.
            """
            from hermit.kernel.verification.proofs.proofs import ProofService

            store = self._get_store()
            proof_service: ProofService | None = None
            proofs: list[dict[str, Any]] = []

            for task_id in task_ids:
                task = store.get_task(task_id)
                if task is None:
                    proofs.append({"task_id": task_id, "error": "Task not found"})
                    continue

                try:
                    if proof_service is None:
                        proof_service = ProofService(store)
                    proof = proof_service.export_task_proof(task_id, detail=detail)
                    proofs.append({"task_id": task_id, "proof": proof})
                except Exception as exc:
                    proofs.append({"task_id": task_id, "error": str(exc)})

            return {"proofs": proofs, "count": len(proofs)}

        @self._mcp.tool()
        def hermit_await_completion(  # pyright: ignore[reportUnusedFunction]
            task_ids: list[str],
            timeout: int = 120,
            mode: str = "any",
        ) -> dict[str, Any]:
            """Block until one or more tasks reach a terminal state (completed/failed/cancelled).

            Returns immediately for tasks already in a terminal state. For running tasks,
            blocks server-side and returns as soon as any task finishes or the timeout expires.
            This eliminates the need for client-side polling.

            Args:
                task_ids: List of task IDs to wait for.
                timeout: Maximum wait time in seconds (default 120, max 300).
                mode: "any" (default) returns when any task finishes.
                    "all" waits until every task reaches a terminal/blocked state.
            """
            wait_all = mode == "all"
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

            # Block until task(s) finish or timeout
            newly_done: dict[str, dict[str, Any]] = {}
            still_pending: list[str] = list(pending_ids)

            _register = getattr(store, "register_task_change_listener", None)
            _deregister = getattr(store, "deregister_task_change_listener", None)
            _FALLBACK_POLL = 0.5  # seconds – used only when store lacks event support

            while time.monotonic() < deadline and still_pending:
                # Wait for any status change, then scan all still-pending tasks.
                if _register is not None and _deregister is not None:
                    shared_ev = threading.Event()
                    _register(still_pending, shared_ev)
                    try:
                        shared_ev.wait(timeout=max(0.0, deadline - time.monotonic()))
                    finally:
                        _deregister(still_pending, shared_ev)
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
                            "task": _slim_task(task),
                            "pending_approvals": [_slim_approval(a) for a in approvals],
                        }
                    else:
                        remaining.append(tid)
                still_pending = remaining

                # In "any" mode, return as soon as we have any newly finished task.
                # In "all" mode, keep waiting until still_pending is empty.
                if newly_done and not wait_all:
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
        def hermit_metrics(  # pyright: ignore[reportUnusedFunction]
            kind: str = "health",
            task_ids: list[str] | None = None,
            window_hours: float = 24.0,
            include_step_timings: bool = False,
            stale_threshold_minutes: float = 10.0,
            limit: int = 500,
        ) -> dict[str, Any]:
            """Unified kernel metrics and health monitoring.

            Args:
                kind: Metric type — "health" (default), "governance", or "task".
                task_ids: Task IDs for kind="task" (required) or kind="governance"
                    (optional, uses first ID to scope). Ignored for kind="health".
                window_hours: Look-back window in hours (kind="health"/"governance").
                include_step_timings: Per-step timing breakdown (kind="task" only).
                stale_threshold_minutes: Stale task threshold (kind="health" only).
                limit: Max records per entity type (kind="governance" only).
            """
            store = self._get_store()

            if kind == "health":
                from hermit.kernel.analytics.health.monitor import TaskHealthMonitor

                monitor = TaskHealthMonitor(store)
                report = monitor.check_health(
                    stale_threshold_seconds=max(0.0, stale_threshold_minutes) * 60.0,
                    window_seconds=max(0.0, window_hours) * 3600.0,
                )
                return {
                    "kind": "health",
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
                        for s in sorted(
                            report.stale_tasks,
                            key=lambda s: s.idle_seconds,
                            reverse=True,
                        )[:10]
                    ],
                    "throughput": {
                        "completed_tasks": report.throughput.completed_tasks,
                        "failed_tasks": report.throughput.failed_tasks,
                        "throughput_per_hour": round(
                            report.throughput.throughput_per_hour,
                            2,
                        ),
                        "failure_rate": round(report.throughput.failure_rate, 3),
                        "window_hours": window_hours,
                    }
                    if report.throughput
                    else None,
                    "notes": report.notes,
                    "scored_at": report.scored_at,
                }

            if kind == "governance":
                import time as _time

                from hermit.kernel.analytics.engine import AnalyticsEngine

                engine = AnalyticsEngine(store)
                window_end = _time.time()
                window_start = window_end - max(0.0, window_hours) * 3600.0
                scoped_id = task_ids[0] if task_ids else None

                metrics = engine.compute_metrics(
                    window_start=window_start,
                    window_end=window_end,
                    task_id=scoped_id,
                    limit=min(limit, 2000),
                )
                return {
                    "kind": "governance",
                    "window_start": metrics.window_start,
                    "window_end": metrics.window_end,
                    "window_hours": window_hours,
                    "task_id": scoped_id,
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
                        for e in metrics.risk_entries[:50]
                    ],
                    "risk_summary": {
                        level: sum(1 for e in metrics.risk_entries if e.risk_level == level)
                        for level in ("high", "medium", "low")
                    },
                    "risk_entries_total": len(metrics.risk_entries),
                }

            if kind == "task":
                from hermit.kernel.analytics.task_metrics import TaskMetricsService

                if not task_ids:
                    return {"kind": "task", "error": "task_ids required for kind='task'."}

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
                    "kind": "task",
                    "tasks": [_serialize_metrics(m) for m in summary.tasks],
                    "total_tasks": summary.total_tasks,
                    "tasks_with_timing": summary.tasks_with_timing,
                }

            return {"error": "Unknown kind. Use 'health', 'governance', or 'task'."}

        # --------------------------------------------------------------
        # Self-iteration (meta-loop) tools
        # --------------------------------------------------------------

        @self._mcp.tool()
        def hermit_submit_iteration(  # pyright: ignore[reportUnusedFunction]
            iterations: list[dict[str, Any]],
            policy_profile: str = "autonomous",
        ) -> dict[str, Any]:
            """Submit iteration goals for self-improvement. Each iteration goes through research->spec->decompose->implement->review->benchmark->learn.

            Args:
                iterations: List of iteration dicts. Each must contain:
                    - goal (str): What the iteration should accomplish.
                    Optional:
                    - priority (str): "low", "normal", or "high". Default "normal".
                    - trust_zone (str): Trust zone for execution. Default "".
                    - research_hints (list[str]): Hints to guide the research phase.
                policy_profile: Policy profile for all iterations. Default "autonomous".
            """
            store = self._get_store()
            if not hasattr(store, "create_spec_entry"):
                return {"error": "Self-iterate schema not available"}

            # Fix 4: queue depth check before creating iterations
            max_queue = MAX_QUEUE_DEPTH
            if hasattr(store, "count_active_specs"):
                active_count = store.count_active_specs()
                if active_count >= max_queue:
                    return {
                        "error": "queue_depth_exceeded",
                        "message": (
                            f"Spec backlog is full ({active_count}/{max_queue} active specs). "
                            "Wait for existing iterations to complete or remove entries "
                            "with hermit_spec_queue(action='remove')."
                        ),
                        "active_specs": active_count,
                        "limit": max_queue,
                    }

            results: list[dict[str, Any]] = []
            for i, entry in enumerate(iterations):
                goal = entry.get("goal", "")
                if not goal:
                    results.append({"index": i, "status": "error", "error": "Missing goal"})
                    continue

                # Re-check queue depth before each creation
                if hasattr(store, "count_active_specs") and store.count_active_specs() >= max_queue:
                    results.append(
                        {
                            "index": i,
                            "status": "error",
                            "error": f"Queue full ({max_queue} active specs)",
                        }
                    )
                    continue

                spec_id = f"iter-{uuid4().hex[:12]}"
                priority = entry.get("priority", "normal")
                trust_zone = entry.get("trust_zone", "")
                research_hints = entry.get("research_hints", [])
                try:
                    store.create_spec_entry(
                        spec_id=spec_id,
                        goal=goal,
                        priority=priority,
                        trust_zone=trust_zone,
                        research_hints=research_hints,
                        metadata={"policy_profile": policy_profile},
                    )
                    results.append(
                        {
                            "index": i,
                            "iteration_id": spec_id,
                            "phase": "pending",
                            "status": "ok",
                        }
                    )
                except Exception as exc:
                    results.append({"index": i, "status": "error", "error": str(exc)})

            return {
                "results": results,
                "submitted": sum(1 for r in results if r.get("status") == "ok"),
                "errors": sum(1 for r in results if r.get("status") == "error"),
            }

        @self._mcp.tool()
        def hermit_spec_queue(  # pyright: ignore[reportUnusedFunction]
            action: str,
            entries: list[dict[str, Any]] | None = None,
            filters: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Manage the spec backlog queue for self-iteration.

            Args:
                action: Operation to perform — "list", "add", "remove", or "reprioritize".
                entries: List of entry dicts. Required for "add", "remove", "reprioritize".
                    For "add": each must contain {goal, priority?}.
                    For "remove": each must contain {spec_id}.
                    For "reprioritize": each must contain {spec_id, priority}.
                filters: Optional filters for "list" action. Supports:
                    - status (str): Filter by status (e.g. "pending", "in_progress").
                    - priority (str): Filter by priority.
                    - limit (int): Max results. Default 20.
            """
            store = self._get_store()
            if not hasattr(store, "list_spec_backlog"):
                return {"error": "Self-iterate schema not available"}

            entries = entries or []
            filters = filters or {}

            if action == "list":
                try:
                    specs = store.list_spec_backlog(
                        status=filters.get("status"),
                        priority=filters.get("priority"),
                        limit=min(filters.get("limit", 20), 100),
                    )
                    return {
                        "action": "list",
                        "specs": [s if isinstance(s, dict) else s.__dict__ for s in specs],
                        "count": len(specs),
                    }
                except Exception as exc:
                    return {"action": "list", "error": str(exc)}

            if action == "add":
                # Fix 4/11: queue depth check with structured error
                max_queue = MAX_QUEUE_DEPTH
                if hasattr(store, "count_active_specs"):
                    active_count = store.count_active_specs()
                    if active_count >= max_queue:
                        return {
                            "action": "add",
                            "error": "queue_depth_exceeded",
                            "message": (
                                f"Spec backlog is full ({active_count}/{max_queue} active specs). "
                                "Wait for existing specs to complete or remove entries."
                            ),
                            "active_specs": active_count,
                            "limit": max_queue,
                        }

                results: list[dict[str, Any]] = []
                for i, entry in enumerate(entries):
                    goal = entry.get("goal", "")
                    if not goal:
                        results.append({"index": i, "status": "error", "error": "Missing goal"})
                        continue

                    if (
                        hasattr(store, "count_active_specs")
                        and store.count_active_specs() >= max_queue
                    ):
                        results.append(
                            {
                                "index": i,
                                "status": "error",
                                "error": f"Queue full ({max_queue} active specs)",
                            }
                        )
                        continue

                    spec_id = f"spec-{uuid4().hex[:12]}"
                    try:
                        store.create_spec_entry(
                            spec_id=spec_id,
                            goal=goal,
                            priority=entry.get("priority", "normal"),
                        )
                        results.append({"index": i, "spec_id": spec_id, "status": "ok"})
                    except Exception as exc:
                        results.append({"index": i, "status": "error", "error": str(exc)})
                return {"action": "add", "results": results}

            if action == "remove":
                if not hasattr(store, "remove_spec_entry"):
                    return {"error": "Self-iterate schema not available"}
                results = []
                for i, entry in enumerate(entries):
                    spec_id = entry.get("spec_id", "")
                    if not spec_id:
                        results.append({"index": i, "status": "error", "error": "Missing spec_id"})
                        continue
                    try:
                        store.remove_spec_entry(spec_id=spec_id)
                        results.append({"index": i, "spec_id": spec_id, "status": "ok"})
                    except Exception as exc:
                        results.append({"index": i, "status": "error", "error": str(exc)})
                return {"action": "remove", "results": results}

            if action == "reprioritize":
                if not hasattr(store, "reprioritize_spec_entry"):
                    return {"error": "Self-iterate schema not available"}
                results = []
                for i, entry in enumerate(entries):
                    spec_id = entry.get("spec_id", "")
                    new_priority = entry.get("priority", "")
                    if not spec_id or not new_priority:
                        results.append(
                            {"index": i, "status": "error", "error": "Missing spec_id or priority"}
                        )
                        continue
                    try:
                        store.reprioritize_spec_entry(spec_id=spec_id, priority=new_priority)
                        results.append({"index": i, "spec_id": spec_id, "status": "ok"})
                    except Exception as exc:
                        results.append({"index": i, "status": "error", "error": str(exc)})
                return {"action": "reprioritize", "results": results}

            return {
                "error": f"Unknown action '{action}'. Use 'list', 'add', 'remove', or 'reprioritize'."
            }

        @self._mcp.tool()
        def hermit_iteration_status(  # pyright: ignore[reportUnusedFunction]
            iteration_ids: list[str],
            include_findings: bool = True,
            include_spec: bool = True,
        ) -> dict[str, Any]:
            """Get status of one or more self-improvement iterations including findings and spec.

            Args:
                iteration_ids: List of iteration/spec IDs to query.
                include_findings: Include research findings in response. Default True.
                include_spec: Include generated spec details. Default True.
            """
            store = self._get_store()
            if not hasattr(store, "get_spec_entry"):
                return {"error": "Self-iterate schema not available"}

            iterations: list[dict[str, Any]] = []
            for iter_id in iteration_ids:
                try:
                    entry = store.get_spec_entry(spec_id=iter_id)
                    if entry is None:
                        iterations.append({"iteration_id": iter_id, "error": "Not found"})
                        continue
                    out = entry if isinstance(entry, dict) else entry.__dict__
                    result: dict[str, Any] = {
                        "iteration_id": iter_id,
                        "status": out.get("status", "unknown"),
                        "phase": out.get("phase", out.get("status", "unknown")),
                        "goal": out.get("goal", ""),
                        "priority": out.get("priority", "normal"),
                        "created_at": out.get("created_at"),
                        "updated_at": out.get("updated_at"),
                    }
                    if include_findings and hasattr(store, "get_iteration_findings"):
                        try:
                            findings = store.get_iteration_findings(spec_id=iter_id)
                            result["findings"] = findings
                        except Exception:
                            result["findings"] = None
                    if include_spec and "spec" in out:
                        result["spec"] = out["spec"]
                    # Include DAG task reference if present
                    dag_task_id = out.get("dag_task_id")
                    if dag_task_id:
                        result["dag_task_id"] = dag_task_id
                    iterations.append(result)
                except Exception as exc:
                    iterations.append({"iteration_id": iter_id, "error": str(exc)})

            return {"iterations": iterations, "count": len(iterations)}

        @self._mcp.tool()
        def hermit_benchmark_results(  # pyright: ignore[reportUnusedFunction]
            iteration_ids: list[str] | None = None,
            spec_ids: list[str] | None = None,
            limit: int = 20,
            include_clade_score: bool = False,
        ) -> dict[str, Any]:
            """Retrieve benchmark results for iterations or specs.

            Args:
                iteration_ids: Filter by iteration IDs.
                spec_ids: Filter by spec IDs.
                limit: Max results to return. Default 20.
                include_clade_score: Include clade score breakdown. Default False.
            """
            store = self._get_store()
            if not hasattr(store, "list_benchmark_results"):
                return {"error": "Self-iterate schema not available"}

            try:
                results = store.list_benchmark_results(
                    iteration_ids=iteration_ids or [],
                    spec_ids=spec_ids or [],
                    limit=min(limit, 100),
                )
                out_results: list[dict[str, Any]] = []
                for r in results:
                    entry = r if isinstance(r, dict) else r.__dict__
                    out: dict[str, Any] = {
                        "spec_id": entry.get("spec_id"),
                        "iteration_id": entry.get("iteration_id", entry.get("spec_id")),
                        "benchmark_type": entry.get("benchmark_type"),
                        "score": entry.get("score"),
                        "passed": entry.get("passed"),
                        "created_at": entry.get("created_at"),
                    }
                    if include_clade_score and "clade_score" in entry:
                        out["clade_score"] = entry["clade_score"]
                    out_results.append(out)
                return {"benchmarks": out_results, "count": len(out_results)}
            except Exception as exc:
                return {"error": str(exc)}

        @self._mcp.tool()
        def hermit_lessons_learned(  # pyright: ignore[reportUnusedFunction]
            applicable_to: list[str] | None = None,
            categories: list[str] | None = None,
            iteration_ids: list[str] | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Query lessons learned from past self-improvement iterations.

            Args:
                applicable_to: Filter lessons applicable to specific domains or tools.
                categories: Filter by lesson categories (e.g. "performance", "reliability").
                iteration_ids: Filter lessons from specific iterations.
                limit: Max lessons to return. Default 20.
            """
            store = self._get_store()
            if not hasattr(store, "list_lessons_learned"):
                return {"error": "Self-iterate schema not available"}

            try:
                capped_limit = min(limit, 100)
                if applicable_to:
                    # store.list_lessons_learned expects applicable_to: str | None,
                    # but the MCP tool accepts list[str]. Query per domain and merge.
                    all_lessons: list[Any] = []
                    seen_ids: set[str] = set()
                    for domain in applicable_to:
                        batch = store.list_lessons_learned(
                            applicable_to=domain,
                            categories=categories if categories else None,
                            iteration_ids=iteration_ids if iteration_ids else None,
                            limit=capped_limit,
                        )
                        for lesson in batch:
                            entry = lesson if isinstance(lesson, dict) else lesson.__dict__
                            lid = entry.get("lesson_id", "")
                            if lid not in seen_ids:
                                seen_ids.add(lid)
                                all_lessons.append(lesson)
                    lessons = all_lessons[:capped_limit]
                else:
                    lessons = store.list_lessons_learned(
                        applicable_to=None,
                        categories=categories if categories else None,
                        iteration_ids=iteration_ids if iteration_ids else None,
                        limit=capped_limit,
                    )
                out_lessons: list[dict[str, Any]] = []
                for lesson in lessons:
                    entry = lesson if isinstance(lesson, dict) else lesson.__dict__
                    out_lessons.append(
                        {
                            "lesson_id": entry.get("lesson_id"),
                            "iteration_id": entry.get("iteration_id", entry.get("spec_id")),
                            "category": entry.get("category"),
                            "summary": entry.get("summary"),
                            "applicable_to": entry.get("applicable_to", []),
                            "confidence": entry.get("confidence"),
                            "created_at": entry.get("created_at"),
                        }
                    )
                return {"lessons": out_lessons, "count": len(out_lessons)}
            except Exception as exc:
                return {"error": str(exc)}

        # --------------------------------------------------------------
        # Assurance tools
        # --------------------------------------------------------------

        @self._mcp.tool()
        def hermit_assurance_replay_task(  # pyright: ignore[reportUnusedFunction]
            task_id: str,
            attribution_mode: str = "post_run",
        ) -> dict[str, Any]:
            """Replay a task's governance trace through the assurance system.

            Runs invariant checks, contract checks, and failure attribution
            against the recorded trace. Returns an assurance report with first
            violation, root cause, and evidence refs.

            Args:
                task_id: The task ID to replay.
                attribution_mode: Whether to run failure attribution. Default: post_run.
            """
            from hermit.kernel.verification.assurance.lab import AssuranceLab
            from hermit.kernel.verification.assurance.mcp_tools import handle_assurance_tool
            from hermit.kernel.verification.assurance.recorder import TraceRecorder

            try:
                store = self._get_store()
                recorder = TraceRecorder(store=store)
                lab = AssuranceLab()
                lab.recorder = recorder
                return handle_assurance_tool(
                    "hermit_assurance_replay_task",
                    {"task_id": task_id, "attribution_mode": attribution_mode},
                    lab=lab,
                )
            except Exception as exc:
                return {"error": str(exc)}

        @self._mcp.tool()
        def hermit_assurance_check_trace(  # pyright: ignore[reportUnusedFunction]
            task_id: str,
        ) -> dict[str, Any]:
            """Run assurance checks (invariants + contracts) against a task's recorded trace.

            Lighter weight than replay -- just validates the trace without full replay.

            Args:
                task_id: The task ID whose trace to check.
            """
            from hermit.kernel.verification.assurance.lab import AssuranceLab
            from hermit.kernel.verification.assurance.mcp_tools import handle_assurance_tool
            from hermit.kernel.verification.assurance.recorder import TraceRecorder

            try:
                store = self._get_store()
                recorder = TraceRecorder(store=store)
                lab = AssuranceLab()
                lab.recorder = recorder
                return handle_assurance_tool(
                    "hermit_assurance_check_trace",
                    {"task_id": task_id},
                    lab=lab,
                )
            except Exception as exc:
                return {"error": str(exc)}

        @self._mcp.tool()
        def hermit_assurance_report(  # pyright: ignore[reportUnusedFunction]
            task_id: str,
            format: str = "json",
        ) -> dict[str, Any]:
            """Get the latest assurance report for a task, or generate one if none exists.

            Args:
                task_id: The task ID to get the assurance report for.
                format: Output format — "json" (default) or "markdown".
            """
            from hermit.kernel.verification.assurance.lab import AssuranceLab
            from hermit.kernel.verification.assurance.mcp_tools import handle_assurance_tool
            from hermit.kernel.verification.assurance.recorder import TraceRecorder

            try:
                store = self._get_store()
                recorder = TraceRecorder(store=store)
                lab = AssuranceLab()
                lab.recorder = recorder
                return handle_assurance_tool(
                    "hermit_assurance_report",
                    {"task_id": task_id, "format": format},
                    lab=lab,
                )
            except Exception as exc:
                return {"error": str(exc)}

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
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                _log.warning("mcp_server_thread_still_alive")  # type: ignore[call-arg]
            self._thread = None
        _log.info("mcp_server_stopped")  # type: ignore[call-arg]
