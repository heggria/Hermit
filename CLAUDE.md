# CLAUDE.md

@AGENTS.md

## Claude Code Specific

- Use `uv` as the package manager (not pip or poetry)
- Run tests with `uv run pytest`
- Prefer `make check` for quick validation (lint + typecheck + test)
- When modifying existing files, read them first before suggesting changes
- Follow Ruff formatting — do not manually adjust style beyond what Ruff enforces

## Claude ↔ Hermit Division of Labor

Claude = bridge between user and Hermit. Reads, analyzes, decides, orchestrates.
Hermit = autonomous executor. Runs tasks under governed policy with receipts and rollback.

### Claude does directly

- Read and analyze code
- Answer questions, explain architecture
- Git operations (commit, branch, PR)
- Quick validation (`make check`, `uv run pytest`)

### Hermit does (delegate via hermit-delegate skill)

- **All file writes and code modifications** — Hermit handles these autonomously
- **All shell commands with side effects** — builds, installs, deployments
- **Multi-step implementations** — features, refactors, migrations
- **Any task the user describes** — default to delegating, not doing directly

### Default: delegate to Hermit

When in doubt, delegate. Use `policy_profile="autonomous"` so Hermit runs with minimal friction.
Claude only does things directly when it's pure read/analysis or the user explicitly asks Claude to do it.

### Maximize parallelism — Hermit's core strength

Hermit is built for high-concurrency governed execution. **Always decompose work into
the maximum number of independent tasks and submit them all at once.**

- **Decompose aggressively**: A feature request is not one task — it's research + implementation
  + tests + docs, often with further splits per module. If two pieces don't depend on each
  other, they are separate tasks.
- **Submit in bulk**: Call `hermit_submit_task` multiple times in a single response. Hermit
  runs them concurrently under independent governed pipelines.
- **Monitor with await**: Use `hermit_await_completion(task_ids=[...], timeout=120)` to block
  until tasks finish. Returns as soon as any task completes — no polling loops needed.
  For remaining tasks, chain another `hermit_await_completion` call.
- **Pipeline dependent work**: When task B depends on task A's output, submit A first,
  then submit B once A completes. Keep independent tasks flowing in the meantime.

Example — user says "refactor the memory module and add tests":

```
# Submit in parallel — these are independent
hermit_submit_task("Refactor src/hermit/kernel/context/memory/ ...", policy_profile="autonomous")
hermit_submit_task("Add unit tests for memory retrieval ...", policy_profile="autonomous")
hermit_submit_task("Add integration tests for memory governance ...", policy_profile="autonomous")
```

NOT:
```
# Wrong — one giant task that runs sequentially inside Hermit
hermit_submit_task("Refactor memory module AND add all tests ...", policy_profile="autonomous")
```

### Principles

- Hermit is autonomous — do not micro-manage running tasks
- Only intervene when Hermit is `blocked` (critical approval) or `failed`
- **Prefer many small tasks over few large tasks** — parallelism is free
- Report results concisely; export proof only when user asks
