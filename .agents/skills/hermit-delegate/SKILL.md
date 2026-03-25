---
name: hermit-delegate
description: Delegate a task to Hermit for autonomous governed execution. Claude acts as bridge — submits, monitors exceptions, reports results. Hermit runs autonomously under its policy engine.
---

# Delegate to Hermit

Submit a task to Hermit's governed execution kernel. Hermit runs autonomously; Claude only intervenes on exceptions.

## When to use

- Any task with side effects (file writes, shell commands, multi-step work)
- Tasks that benefit from durable records (receipts, proof, rollback)
- Multi-file refactors, spec iterations, feature implementations
- User explicitly asks to delegate or use Hermit
- Default choice for all execution work — prefer delegating over doing directly

## When NOT to use

- Pure read/analysis — Claude does this directly, no governance needed
- Questions and explanations — Claude answers directly

## Policy profiles

Choose the right autonomy level:

| Profile | Autonomy | Use when |
|---------|----------|----------|
| `autonomous` | High — auto-approves most operations, receipts preserved | **Default.** Trusted work in the project workspace |
| `default` | Medium — approval required for mutations | Unfamiliar code areas, external integrations |
| `supervised` | Low — approval required for everything | Untrusted agents, production-touching work |
| `readonly` | None — denies all side effects | Analysis-only tasks |

**Always use `autonomous` unless there's a specific reason not to.**

Even in `autonomous` mode, Hermit still:
- Denies dangerous patterns (sudo, curl|sh)
- Denies writes to sensitive paths (~/.ssh, /etc)
- Requires approval for kernel self-modification
- Issues receipts for every action
- Supports rollback

## Workflow

### 1. Compose task description

Write a self-contained description for Hermit. Include:
- **Goal**: What to accomplish
- **Scope**: Which files/modules are involved
- **Constraints**: What to avoid
- **Acceptance criteria**: How to verify success

Keep it under 500 words. Hermit reads the codebase itself.

### 2. Submit

```
hermit_submit(
  description="<task description>",
  priority="normal",
  policy_profile="autonomous"
)
```

### 3. Monitor (use await, not polling)

**Preferred: block until completion with `hermit_await_completion`**

```
hermit_await_completion(
  task_ids=["task_abc", "task_def", "task_ghi"],
  timeout=120
)
```

This blocks server-side and returns as soon as any task finishes, gets blocked, or times out.
No more polling loops. One call replaces N status checks.

**Response format:**
- `completed` — dict of finished/blocked/not-found tasks with full status
- `pending` — list of still-running tasks (only on timeout or partial completion)
- `timed_out` — true if timeout expired with tasks still running

**Fallback: `hermit_task_status(task_id)` for one-off checks.**

**Only act on:**
- `blocked` → rare in autonomous mode; means a critical-risk operation needs approval
- `failed` → report to user with error details

**Do NOT:**
- Micro-manage running tasks
- Use polling loops — use `hermit_await_completion` instead
- Intervene unless the task is blocked or failed

### 4. Handle critical approvals (rare)

In `autonomous` mode, approvals only trigger for:
- Kernel self-modification (src/hermit/kernel/)
- Writes to sensitive/protected paths
- Operations the policy engine deems critical-risk

When blocked:
1. Call `hermit_pending_approvals(task_id)`
2. Evaluate the critical operation — ask the user if unsure
3. Approve or deny with clear reason

### 5. Report result

When task completes:
- Brief summary of what was done
- Export proof if user needs audit trail: `hermit_task_proof(task_id)`
- If failed, report error and suggest next steps

## Parallel delegation (CRITICAL — always maximize)

Hermit's architecture is built for high-concurrency governed execution.
**Your primary job as orchestrator is to decompose work into the maximum number
of independent tasks and submit them all at once.**

### Decomposition rules

1. **Split by module/file scope** — if two changes touch different modules, they're separate tasks
2. **Split by concern** — implementation vs tests vs docs vs lint fixes are separate tasks
3. **Split by independence** — if A doesn't need B's output, submit A and B simultaneously
4. **Keep tasks focused** — a task that does one thing well finishes faster than a task that does five things

### Submit pattern

```
# All independent — submit in ONE response
hermit_submit(description="Refactor error handling in memory/retrieval.py", policy_profile="autonomous")
hermit_submit(description="Add unit tests for memory quality scoring", policy_profile="autonomous")
hermit_submit(description="Fix lint errors in kernel/context/", policy_profile="autonomous")
hermit_submit(description="Update docstrings in memory/governance.py", policy_profile="autonomous")
```

### Monitor pattern

Use `hermit_await_completion` to block until tasks finish:
```
# Submit N tasks, collect all task_ids, then await
hermit_await_completion(task_ids=[task_id_1, task_id_2, task_id_3], timeout=120)
```

Returns as soon as any task finishes — report that result, then await remaining tasks.
For long-running batches, chain await calls for the still-pending task_ids.

### Pipeline pattern (dependent tasks)

When task B depends on A's output:
1. Submit A and all other independent tasks immediately
2. When A completes, submit B
3. Keep monitoring remaining tasks in parallel

### DAG task (steps with dependencies within a single task)

For tasks where steps have internal dependencies (e.g., research → implement → test),
use `hermit_submit_dag_task` instead of multiple independent tasks:

```
hermit_submit_dag_task(
  goal="Build user auth feature",
  nodes=[
    {"key": "research", "kind": "research", "title": "Research auth patterns"},
    {"key": "backend", "kind": "code", "title": "Implement backend", "depends_on": ["research"]},
    {"key": "frontend", "kind": "code", "title": "Implement frontend", "depends_on": ["research"]},
    {"key": "tests", "kind": "code", "title": "Write tests", "depends_on": ["backend", "frontend"]},
    {"key": "review", "kind": "review", "title": "Code review", "depends_on": ["tests"]}
  ],
  policy_profile="autonomous"
)
```

Hermit runs independent steps concurrently (backend + frontend in parallel),
waits at join points, handles failure cascade, and produces a unified proof bundle.

**Join strategies** (per step, via `join_strategy` field):
- `all_required` (default) — all deps must succeed
- `any_sufficient` — proceed when any dep succeeds
- `majority` — proceed when >50% of deps succeed
- `best_effort` — proceed when all deps are terminal (success or failure)

**When to use DAG vs multiple independent tasks:**
- **DAG**: Steps have real data dependencies (step B needs step A's output)
- **Independent tasks**: Steps are fully independent (different modules, different concerns)
- **Both**: Submit independent DAG tasks in parallel for maximum throughput

### Anti-patterns (DO NOT)

- Submit one giant task that bundles everything — kills parallelism
- Wait for task 1 to finish before submitting independent task 2
- Submit tasks one at a time across multiple responses
- Under-decompose — 3 tasks is almost always better than 1
- Use DAG when tasks are truly independent — overhead without benefit

## Self-iteration (autonomous improvement)

For continuous, goal-driven improvement tasks, use `hermit_self_iterate` instead of manual delegation:

```
hermit_self_iterate(
  iterations=[
    {"goal": "Improve error handling in store module", "priority": "high"},
    {"goal": "Add missing type annotations in utils/", "priority": "normal"}
  ]
)
```

**When to use self-iteration vs manual delegation:**

| Use case | Tool | Why |
|----------|------|-----|
| One-off task with specific instructions | `hermit_submit_task` | Direct control over what gets done |
| Multi-step task with dependencies | `hermit_submit_dag_task` | Step ordering and join strategies |
| Goal-driven improvement (research → plan → implement → review → learn) | `hermit_self_iterate` | Full autonomous pipeline with feedback loops |
| Continuous code quality improvement | `hermit_self_iterate` | Auto-spawns follow-up specs from lessons learned |

**Monitor self-iteration:**
```
hermit_spec_queue(action="list", filters={"status": "pending", "limit": 20})
```

Self-iteration runs a 10-phase pipeline (PENDING → RESEARCHING → GENERATING_SPEC → SPEC_APPROVAL → DECOMPOSING → IMPLEMENTING → REVIEWING → BENCHMARKING → LEARNING → COMPLETED) with automatic retry on failure and lesson-driven follow-up iterations.

See the `hermit-iterate` skill for full self-iteration details.

## Error handling

- **Task fails** → Report to user, do NOT auto-retry
- **MCP connection error** → Hermit may not be running
- **Task stuck in accepted >30s** → Check `hermit_list_tasks` for queue state
