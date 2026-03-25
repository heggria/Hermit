# MCP Integration Guide

In a traditional OS, user programs interact with the kernel through system calls.
In Hermit, supervisor agents (Claude Code, Cursor, etc.) interact with the kernel
through MCP -- the Model Context Protocol. MCP is Hermit's system call interface.

Hermit exposes its governed execution kernel as an MCP (Model Context Protocol) server,
allowing any MCP-compatible supervisor agent to submit tasks, monitor execution, manage
approvals, and export cryptographic proof bundles -- all without direct code access.

This is Hermit's key differentiator: instead of executing shell commands and file writes
directly, a supervisor agent delegates work to Hermit, which enforces policy evaluation,
scoped capability grants, receipt issuance, and hash-chained proof generation on every
action. The supervisor stays in a decision-making role while Hermit handles governed
execution autonomously.

---

## 1. What Is Hermit's MCP Interface?

Hermit runs a Streamable HTTP MCP server (powered by FastMCP + Uvicorn) that exposes
kernel operations as MCP tools. When `hermit serve` starts with the MCP server enabled,
it binds to `127.0.0.1:8322` (configurable) and accepts tool calls from any MCP client.

**Architecture:**

```
Supervisor Agent (Claude Code, Cursor, custom client)
    |
    | MCP Streamable HTTP
    v
Hermit MCP Server (port 8322)
    |
    v
Hermit Kernel
    Task Controller -> Policy Engine -> Executor -> Receipts -> Proofs
    |                                                              |
    v                                                              v
SQLite Ledger (event-sourced)                        Hash-chained proof bundles
```

**What happens when you submit a task:**

1. The MCP server receives the tool call and enqueues it via the kernel's governed ingress.
2. Hermit compiles context from artifacts, memory, and workspace state.
3. Hermit calls its own LLM provider to plan and execute actions.
4. Every action (file write, shell command, etc.) passes through the policy engine.
5. Actions that exceed the policy profile's trust threshold trigger approval requests.
6. Approved actions execute under scoped capability grants, producing signed receipts.
7. On completion, the task produces a hash-chained proof bundle for auditability.

The supervisor never touches tools directly. It submits goals and handles approvals.

---

## 2. Available Tools

### Task Tools

> **OS analogy:** Process management syscalls (`fork`, `exec`, `wait`, `kill`).

| Tool | Description |
|------|-------------|
| `hermit_submit` | Submit one or more tasks for governed execution. Supports single mode (provide `description`) and batch mode (provide `tasks` list). Optional `await_completion` parameter blocks until done. |
| `hermit_submit_dag_task` | Submit a DAG (directed acyclic graph) of dependent steps. Independent steps run concurrently; dependent steps wait for upstream completion. Hermit handles scheduling, join barriers, and failure cascading. |
| `hermit_task_status` | Get detailed status of one or more tasks, including recent events and pending approvals. |
| `hermit_list_tasks` | List recent tasks from the kernel. Filter by status (`running`, `completed`, `failed`, etc.). |
| `hermit_await_completion` | Block server-side until tasks reach a terminal state (`completed`, `failed`, `cancelled`) or `blocked`. Eliminates client-side polling. Supports `mode="any"` (return on first finish) and `mode="all"` (wait for all). |
| `hermit_cancel_task` | Cancel one or more running tasks. |
| `hermit_task_output` | Get execution output for completed tasks: actions taken, receipt summaries, result codes, and observed effects. |
| `hermit_task_proof` | Export cryptographic proof bundles at three detail levels: `summary` (~5-20 KB), `standard` (~50-200 KB), or `full` (includes Merkle proofs, receipt bundles, context manifests). |

### Approval Tools

> **OS analogy:** Permission resolution (like PAM / polkit).

| Tool | Description |
|------|-------------|
| `hermit_pending_approvals` | List all pending approval requests across tasks. Optionally filter by task ID. |
| `hermit_approve` | Approve one or more pending requests. Optional `await_after` parameter waits for affected tasks to complete after approval. |
| `hermit_deny` | Deny one or more pending requests with a reason. Optional `await_after` parameter. |

### Self-Iteration Tools

> **OS analogy:** Kernel self-upgrade interface.

| Tool | Description |
|------|-------------|
| `hermit_submit_iteration` | Submit self-improvement goals. Each iteration flows through: research -> spec -> decompose -> implement -> review -> benchmark -> learn. |
| `hermit_spec_queue` | Manage the spec backlog queue: `list`, `add`, `remove`, or `reprioritize` entries. |
| `hermit_iteration_status` | Get status of iterations including research findings, generated specs, and DAG task references. |

### Observability Tools

> **OS analogy:** Kernel metrics (like `/proc`, `sysfs`, `perf`).

| Tool | Description |
|------|-------------|
| `hermit_metrics` | Unified metrics endpoint. `kind="health"` for system health scores and stale task detection. `kind="governance"` for approval rates, rollback rates, risk distribution. `kind="task"` for per-task step timings and duration breakdowns. |
| `hermit_benchmark_results` | Retrieve benchmark results for iterations or specs, including optional clade score breakdowns. |
| `hermit_lessons_learned` | Query lessons learned from past self-improvement iterations. Filter by domain, category, or iteration. |

---

## 3. Connecting from Claude Code

This is analogous to connecting a terminal emulator to an OS -- Claude Code becomes the
shell, Hermit is the kernel.

### Step 1: Enable the MCP server in Hermit

Add the following to your `~/.hermit/.env` file:

```bash
# In ~/.hermit/.env
HERMIT_MCP_SERVER_ENABLED=true
HERMIT_MCP_SERVER_HOST=127.0.0.1
HERMIT_MCP_SERVER_PORT=8322
```

### Step 2: Start the Hermit service

```bash
hermit serve
```

The MCP server starts automatically alongside the main service. You should see
`mcp_server_started host=127.0.0.1 port=8322` in the logs.

### Step 3: Configure Claude Code's MCP client

Add to your project's `.mcp.json` (or `~/.claude/mcp.json` for global access):

```json
{
  "mcpServers": {
    "hermit": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8322/mcp"
    }
  }
}
```

### Step 4: Verify the connection

Restart Claude Code. You should see the Hermit tools listed in your available tools.
Test with a simple call:

```
hermit_list_tasks(limit=5)
```

If successful, you will receive a JSON response with recent tasks (or an empty list).

---

## 4. Connecting from Other Supervisors

Any MCP-compatible client can connect to Hermit's Streamable HTTP endpoint.

### Generic client configuration

- **Transport:** Streamable HTTP
- **Endpoint:** `http://127.0.0.1:8322/mcp`
- **Authentication:** None required (localhost only by default)

### Python example (using the `mcp` SDK)

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def connect_to_hermit():
    async with streamablehttp_client("http://127.0.0.1:8322/mcp") as (
        read_stream, write_stream, _
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print(f"Available tools: {[t.name for t in tools.tools]}")

            # Submit a task
            result = await session.call_tool(
                "hermit_submit",
                arguments={
                    "description": "Fix the typo in README.md line 42",
                    "policy_profile": "autonomous",
                    "await_completion": 120,
                },
            )
            print(result)
```

### Cursor / Windsurf / other editors

If your editor supports MCP servers via Streamable HTTP, add the Hermit endpoint
using the same URL pattern: `http://127.0.0.1:8322/mcp`.

---

## 5. Usage Patterns

Using Hermit via MCP feels like writing a shell script that uses system calls -- you
submit work, the kernel governs execution, and you get structured results back.

### 5.1 Single Task Submission

The simplest pattern: submit a task and wait for the result in a single call.

```python
# One call: submit + wait (up to 120 seconds)
hermit_submit(
    description="Add input validation to the /api/users endpoint",
    policy_profile="autonomous",
    await_completion=120
)
```

The response includes the task status, recent events, and (if completed) a summary
of what happened. If the task is still running after the timeout, use
`hermit_await_completion` to continue waiting.

**Fire-and-forget variant** (when you do not need to wait):

```python
# Returns immediately with task_id
result = hermit_submit(
    description="Run the full test suite and report failures",
    policy_profile="autonomous"
)
task_id = result["task_id"]
# ... do other work ...
# Check later:
hermit_task_status(task_ids=[task_id])
```

### 5.2 Parallel Independent Tasks

When work items are independent, submit them in batch for maximum parallelism.

```python
# Batch mode: submit 3 independent tasks in one call
hermit_submit(
    tasks=[
        {"description": "Refactor src/hermit/kernel/context/memory/ for clarity"},
        {"description": "Add unit tests for memory retrieval service"},
        {"description": "Add integration tests for memory governance flow"},
    ],
    policy_profile="autonomous"
)
# Returns: { "task_ids": ["id-1", "id-2", "id-3"], "submitted": 3, ... }

# Wait for all to finish:
hermit_await_completion(
    task_ids=["id-1", "id-2", "id-3"],
    timeout=300,
    mode="all"
)
```

Key principles:
- **Prefer many small tasks over few large tasks.** Parallelism is free.
- Each task should have a single, clear objective.
- Do not combine unrelated work into a single task.

### 5.3 DAG Workflows

For work with dependencies, use `hermit_submit_dag_task` to define a directed acyclic
graph. Hermit schedules steps automatically: independent steps run in parallel, and
dependent steps wait for upstream completion.

```python
hermit_submit_dag_task(
    goal="Add a new caching layer to the API",
    nodes=[
        {
            "key": "research",
            "kind": "research",
            "title": "Research caching strategies"
        },
        {
            "key": "implement",
            "kind": "code",
            "title": "Implement cache middleware",
            "depends_on": ["research"]
        },
        {
            "key": "test",
            "kind": "code",
            "title": "Write tests for cache layer",
            "depends_on": ["research"]
        },
        {
            "key": "review",
            "kind": "review",
            "title": "Review implementation and tests",
            "depends_on": ["implement", "test"],
            "join_strategy": "all_required"
        }
    ],
    policy_profile="autonomous"
)
```

**Node fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `key` | Yes | Unique identifier for the step. |
| `kind` | Yes | Step type: `execute`, `research`, `code`, `review`, etc. |
| `title` | Yes | Human-readable name. |
| `depends_on` | No | List of upstream step keys. Default: `[]` (root step). |
| `join_strategy` | No | `all_required` (default), `any_sufficient`, `majority`, `best_effort`. |
| `input_bindings` | No | Maps local parameter names to `"upstream_key.output_ref"`. |
| `max_attempts` | No | Maximum retry count. Default: `1`. |
| `metadata` | No | Arbitrary metadata dict attached to the step. |

### 5.4 Await and Polling Patterns

Hermit provides server-side blocking via `hermit_await_completion`, which eliminates
the need for client-side polling loops.

**Wait for any task (first to finish):**

```python
hermit_await_completion(
    task_ids=["id-1", "id-2", "id-3"],
    timeout=120,
    mode="any"
)
# Returns as soon as any one task finishes.
# Call again with the remaining task_ids for the rest.
```

**Wait for all tasks:**

```python
hermit_await_completion(
    task_ids=["id-1", "id-2", "id-3"],
    timeout=300,
    mode="all"
)
# Returns when all tasks reach completed/failed/cancelled/blocked.
```

**What happens on timeout:**

If the timeout expires before tasks finish, the response includes `"timed_out": true`
with a snapshot of each still-running task's current status. Chain another
`hermit_await_completion` call to continue waiting -- do not poll in a loop.

**Blocked tasks:**

When a task hits a policy boundary, it enters `blocked` status with pending approvals.
`hermit_await_completion` returns blocked tasks with their approval details inline:

```json
{
  "completed": {
    "task-abc": {
      "status": "blocked",
      "pending_approvals": [
        {
          "approval_id": "apv-123",
          "approval_type": "tool_execution",
          "action_summary": "bash: rm -rf /tmp/old-cache"
        }
      ]
    }
  }
}
```

### 5.5 Approval Flow Handling

When Hermit's policy engine determines an action exceeds the trust threshold for the
active policy profile, it creates an approval request and blocks the task.

**Recommended flow:**

```python
# 1. Check for pending approvals
result = hermit_pending_approvals()

# 2. Review and approve/deny
for approval in result["approvals"]:
    print(f"{approval['approval_id']}: {approval.get('action_summary', 'N/A')}")

# 3. Approve with post-approval wait
hermit_approve(
    approval_ids=["apv-123"],
    reason="Reviewed: safe to delete temp cache",
    await_after=60  # Wait up to 60s for the task to complete after approval
)

# Or deny:
hermit_deny(
    approval_ids=["apv-456"],
    reason="Command too destructive for this environment"
)
```

Approvals are also surfaced inline in `hermit_task_status` and
`hermit_await_completion` responses, so you rarely need to call
`hermit_pending_approvals` separately.

---

## 6. Best Practices

Think of each task as a process. Keep processes small and focused. Let the OS handle
scheduling, isolation, and cleanup.

### Atomic tasks

Each task should accomplish one clear objective. Large requests should be decomposed
into multiple independent tasks submitted in batch.

```python
# Good: three atomic tasks
hermit_submit(tasks=[
    {"description": "Add error handling to the payment service"},
    {"description": "Write unit tests for payment error paths"},
    {"description": "Update API documentation for payment errors"},
])

# Bad: one monolithic task
hermit_submit(
    description="Add error handling, write tests, and update docs for payments"
)
```

### Policy profiles

Choose the right policy profile for the trust level you need:

| Profile | Autonomy | Use case |
|---------|----------|----------|
| `autonomous` | High | Routine development tasks, refactoring, test writing. Most actions auto-approved. |
| `default` | Medium | General work with approval gates on destructive actions. |
| `supervised` | Low | Sensitive operations. Most mutations require explicit approval. |
| `readonly` | None | Research, analysis, code reading. No write operations permitted. |

### Do not micro-manage

Hermit is autonomous. After submitting a task:
- Do not poll status in a loop. Use `hermit_await_completion` instead.
- Only intervene when a task is `blocked` (needs approval) or `failed`.
- Intermediate statuses like `reconciling` are normal and transient.

### Proof export

Export proof bundles only when you need auditability or compliance evidence.
Use the appropriate detail level:

```python
# Lightweight verification (default)
hermit_task_proof(task_ids=["task-abc"], detail="summary")

# Full governance records
hermit_task_proof(task_ids=["task-abc"], detail="standard")

# Complete bundle with Merkle proofs, receipts, and context manifests
hermit_task_proof(task_ids=["task-abc"], detail="full")
```

### Pipeline dependent work

When task B depends on task A's output, submit A first, then submit B after A completes.
Keep independent tasks flowing in the meantime:

```python
# Submit independent tasks
result = hermit_submit(tasks=[
    {"description": "Generate the database schema migration"},
    {"description": "Write API endpoint stubs"},
])

# Wait for the migration to complete (it produces the schema file)
hermit_await_completion(task_ids=[result["task_ids"][0]], mode="any")

# Now submit the dependent task
hermit_submit(
    description="Write the ORM models based on the generated migration",
    await_completion=120
)
```

---

## 7. Example Workflows

### Example 1: Bug Fix with Test Verification

A supervisor agent receives a bug report and delegates the fix to Hermit.

```python
# Step 1: Submit the fix and a test task in parallel
result = hermit_submit(
    tasks=[
        {
            "description": (
                "Fix the off-by-one error in src/hermit/kernel/ledger/journal/store_tasks.py "
                "where list_tasks returns limit+1 results when status filter is applied. "
                "The bug is in the SQL LIMIT clause."
            ),
            "priority": "high",
        },
        {
            "description": (
                "Add a regression test for list_tasks with status filter "
                "to verify the LIMIT clause returns exactly the requested count."
            ),
        },
    ],
    policy_profile="autonomous",
)
task_ids = result["task_ids"]

# Step 2: Wait for both to complete
completion = hermit_await_completion(task_ids=task_ids, timeout=180, mode="all")

# Step 3: Check outputs
output = hermit_task_output(task_ids=task_ids)
for task_out in output["outputs"]:
    print(f"Task {task_out['task_id']}: {task_out['status']}")
    print(f"  Actions: {task_out.get('total_actions', 0)}")
```

### Example 2: DAG Workflow for Feature Development

A multi-step feature with dependencies between research, implementation, and review.

```python
# Submit the DAG
dag = hermit_submit_dag_task(
    goal="Implement rate limiting middleware for the REST API",
    nodes=[
        {
            "key": "research",
            "kind": "research",
            "title": "Research rate limiting algorithms and existing middleware",
        },
        {
            "key": "implement",
            "kind": "code",
            "title": "Implement token bucket rate limiter middleware",
            "depends_on": ["research"],
        },
        {
            "key": "tests",
            "kind": "code",
            "title": "Write unit and integration tests for rate limiter",
            "depends_on": ["research"],
        },
        {
            "key": "review",
            "kind": "review",
            "title": "Code review of implementation and tests",
            "depends_on": ["implement", "tests"],
            "join_strategy": "all_required",
        },
    ],
    policy_profile="autonomous",
)

task_id = dag["task_id"]
print(f"DAG task {task_id} created with {dag['dag_topology']['total_steps']} steps")
print(f"Root steps: {dag['dag_topology']['roots']}")

# Wait for the entire DAG to complete
hermit_await_completion(task_ids=[task_id], timeout=300)

# Export proof for compliance
proof = hermit_task_proof(task_ids=[task_id], detail="standard")
```

### Example 3: Self-Iteration for Codebase Improvement

Use the self-iteration pipeline for governed self-improvement that goes through
research, spec generation, implementation, review, and benchmarking.

```python
# Submit improvement goals
result = hermit_submit_iteration(
    iterations=[
        {
            "goal": "Reduce memory allocations in the context compiler hot path",
            "priority": "high",
            "research_hints": [
                "Profile src/hermit/kernel/context/compiler/",
                "Look for unnecessary dict copies in compile_context()",
            ],
        },
        {
            "goal": "Add structured logging to the policy evaluation pipeline",
            "priority": "normal",
        },
    ],
    policy_profile="autonomous",
)

# Monitor iteration progress
for r in result["results"]:
    if r["status"] == "ok":
        status = hermit_iteration_status(iteration_ids=[r["iteration_id"]])
        print(f"Iteration {r['iteration_id']}: phase={status['iterations'][0]['phase']}")

# Later: check benchmark results
benchmarks = hermit_benchmark_results(
    iteration_ids=[r["iteration_id"] for r in result["results"] if r.get("iteration_id")]
)

# Query lessons learned across all iterations
lessons = hermit_lessons_learned(categories=["performance"], limit=10)
```

### Example 4: Supervised Execution with Approval Handling

For sensitive operations, use `supervised` policy to require human approval.

```python
# Submit with supervised policy -- destructive actions will need approval
result = hermit_submit(
    description="Clean up unused database migration files in migrations/",
    policy_profile="supervised",
)
task_id = result["task_id"]

# Wait -- task will likely block on approval
completion = hermit_await_completion(task_ids=[task_id], timeout=30)

if completion["completed"].get(task_id, {}).get("status") == "blocked":
    approvals = completion["completed"][task_id]["pending_approvals"]

    for apv in approvals:
        print(f"Approval needed: {apv.get('action_summary')}")
        # Review the action and decide
        hermit_approve(
            approval_ids=[apv["approval_id"]],
            reason="Reviewed: these migration files are confirmed unused",
            await_after=120,
        )

# Verify completion
hermit_task_output(task_ids=[task_id])
```

---

## Configuration Reference

MCP server settings are configured via environment variables in `~/.hermit/.env`.

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `HERMIT_MCP_SERVER_ENABLED` | `false` | Enable the MCP server. |
| `HERMIT_MCP_SERVER_HOST` | `127.0.0.1` | Bind address. |
| `HERMIT_MCP_SERVER_PORT` | `8322` | Listening port. |

The MCP server starts and stops with `hermit serve`. It supports hot-reload: when
`hermit reload` runs, the server swaps its internal runner reference without restarting
the HTTP listener.

---

## Troubleshooting

**"Runner is not attached" error:**
The MCP server started but `hermit serve` has not finished initializing. Wait a few
seconds and retry.

**Task stuck in "reconciling" status:**
This is a normal transient state. Hermit is reconciling execution results with the
kernel ledger. Use `hermit_await_completion` instead of polling `hermit_task_status`.

**Approval not found:**
The approval may have expired or been resolved by another supervisor. Call
`hermit_pending_approvals` to get the current list.

**Connection refused on port 8322:**
Verify that `HERMIT_MCP_SERVER_ENABLED=true` is set in `~/.hermit/.env` and that
`hermit serve` is running. Check `~/.hermit/logs/` for startup errors.

**Tools not appearing in Claude Code:**
Restart Claude Code after adding the MCP server configuration. Verify the `.mcp.json`
file is in your project root or `~/.claude/mcp.json` for global access.

---

## See Also

- [Getting Started](./getting-started.md) -- initial setup and first run
- [Architecture](./architecture.md) -- system design and kernel layers
- [Plugin Development](./plugin-development.md) -- building custom plugins
- [Use Cases](./use-cases.md) -- real-world usage scenarios
