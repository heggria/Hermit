# CLAUDE.md

@AGENTS.md

## Claude Code Specific

- Use `uv` as the package manager (not pip or poetry)
- Run tests with `uv run pytest`
- Prefer `make check` for quick validation (lint + typecheck + test)
- When modifying existing files, read them first before suggesting changes
- Follow Ruff formatting — do not manually adjust style beyond what Ruff enforces

## Architecture at a Glance

Hermit is a **kernel-first governed agent runtime**. The core layers are:

```
Surfaces (CLI)  +  Adapters (Feishu, Slack, Telegram)  +  Hooks (Scheduler, Webhook)
    → AgentRunner (runtime/control/)
        → PluginManager + Task Controller
            → Policy Engine → Approval → WorkspaceLease → CapabilityGrant → Executor
                → Artifacts, Receipts, Proofs, Rollback
                    → Kernel Ledger (SQLite event journal + projections)
```

**Key packages:**
- `src/hermit/kernel/` — governed execution kernel (task, policy, execution, ledger, verification, signals, analytics, context, authority, artifacts, errors)
- `src/hermit/runtime/` — assembly, capability (registry, resolver, contracts, loader), control (lifecycle, runner), observation (logging), provider_host (LLM providers: claude, codex; execution services: approval, progress, sandbox, vision)
- `src/hermit/plugins/builtin/` — adapters (feishu, slack, telegram), hooks (scheduler, webhook, memory, image_memory, patrol, research, benchmark, decompose, metaloop, overnight, quality, subtask, trigger), tools (computer_use, file_tools, grok, web_tools), MCP servers (github, hermit_server, mcp_loader), subagents (orchestrator), bundles (compact, planner, usage)
- `src/hermit/infra/` — storage, locking, paths, system (i18n, executables, locales)
- `src/hermit/surfaces/cli/` — Typer CLI dispatcher (commands for core, task, memory, schedule, plugin, autostart, overnight, serve), TUI (Textual-based interactive chat with widgets)
- `src/hermit/apps/` — companion app

### Key Design Decisions

- **Task-first:** All meaningful work flows through durable Task → Step → StepAttempt objects
- **Governed execution:** Every mutation follows Approval → WorkspaceLease → CapabilityGrant → Execution → Receipt. No direct model-to-tool execution.
- **Event sourcing:** Durable state derived from append-only event logs in SQLite ledger
- **Artifact-native context:** Context compiled from kernel artifacts, not just message history
- **Evidence-bound memory:** Memory promotion requires evidence references
- **Scoped authority:** CapabilityGrants + WorkspaceLeases for least-privilege execution
- **Receipt-aware:** Every action produces receipts and hash-chained proof bundles
- **Kernel is synchronous:** Kernel methods are sync; async only at surface boundaries
- **Plugin architecture:** Adapters, hooks, tools, MCP servers, subagents, and bundles are all plugins loaded via PluginManager

## Using Hermit via MCP

### Task Submission

Submit atomic tasks — each should have a single clear objective.

- `hermit_submit(description, policy_profile)` — submit a single task
- `hermit_submit_dag_task(description, steps, policy_profile)` — submit a DAG of dependent steps
- `hermit_await_completion(task_id)` — block until task completes — no polling loops needed.
  For remaining tasks, chain another `hermit_await_completion` call.
- **Pipeline dependent work**: When task B depends on task A's output, submit A first,
  then submit B once A completes. Keep independent tasks flowing in the meantime.

Example — user says "refactor the memory module and add tests":

```
# Submit in parallel — these are independent
hermit_submit(description="Refactor src/hermit/kernel/context/memory/ ...", policy_profile="autonomous")
hermit_submit(description="Add unit tests for memory retrieval ...", policy_profile="autonomous")
hermit_submit(description="Add integration tests for memory governance ...", policy_profile="autonomous")
```

NOT:
```
# Wrong — one giant task that runs sequentially inside Hermit
hermit_submit(description="Refactor memory module AND add all tests ...", policy_profile="autonomous")
```

### Self-Iteration

Hermit supports governed self-improvement via `hermit_submit_iteration`. This pipeline:
spec → parse → branch → execute → proof-export → PR. Every mutation is authorized by
the policy engine, granted scoped capabilities, receipted, and verifiable via hash-chained
proof bundles. Use `hermit_iteration_status` and `hermit_spec_queue` to monitor.

### Available MCP Tools

Core task tools: `hermit_submit`, `hermit_submit_dag_task`, `hermit_task_status`,
`hermit_list_tasks`, `hermit_await_completion`, `hermit_cancel_task`, `hermit_task_output`,
`hermit_task_proof`

Approval flow: `hermit_pending_approvals`, `hermit_approve`, `hermit_deny`

Self-iteration: `hermit_submit_iteration`, `hermit_spec_queue`, `hermit_iteration_status`

Observability: `hermit_metrics`, `hermit_benchmark_results`, `hermit_lessons_learned`

### Principles

- Hermit is autonomous — do not micro-manage running tasks
- Only intervene when Hermit is `blocked` (critical approval) or `failed`
- **Prefer many small tasks over few large tasks** — parallelism is free
- Report results concisely; export proof only when user asks
