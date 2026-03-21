---
description: "Hermit CLI reference: run, chat, serve, task, memory, config, plugin, schedule commands and their options."
---

# CLI And Operations

This document covers the CLI commands that currently exist, the long-running service path, and the operator-facing commands that matter most in day-to-day use.

This is a current-implementation document. It does not describe target-only kernel behavior as if it were already fully shipped.

## Top-Level Commands

Commands currently exposed by the CLI include:

- `setup`
- `init`
- `startup-prompt`
- `run`
- `chat`
- `serve`
- `reload`
- `sessions`
- `plugin`
- `autostart`
- `schedule`
- `config`
- `profiles`
- `auth`
- `task`
- `memory`
- `overnight`

## Basic Interactive Commands

### `hermit setup`

Interactive first-run setup:

- writes `~/.hermit/.env`
- can configure Feishu credentials
- initializes the local workspace

### `hermit init`

Initializes local state directories and baseline workspace files.

### `hermit startup-prompt`

Prints the assembled startup prompt for debugging. Useful when you want to inspect:

- base context
- rules
- skills
- hook-injected fragments

### `hermit run "..."`

Runs a one-shot task without entering an interactive session.

### `hermit chat`

Starts an interactive multi-turn session.

Slash commands available in `chat` include:

- `/new`
- `/history`
- `/help`
- `/quit`
- `/task`
- `/compact`
- `/plan`
- `/usage`

## Service Lifecycle

### `hermit serve feishu`

Starts the long-running service using the `feishu` adapter. The adapter name is a positional argument (default: `feishu`).

Current startup flow:

1. load configuration
2. run preflight checks
3. discover plugins
4. build runtime and kernel services
5. start the adapter
6. activate service hooks such as scheduler and webhook support

### `hermit reload feishu`

Triggers a graceful reload for the running adapter process. The adapter name is a positional argument (default: `feishu`).

Use this when you want to rebuild configuration, plugins, and tools without treating the change as a full source-code hot reload.

For local source edits, prefer `scripts/hermit-watch.sh dev` or `make env-watch ENV=dev` instead of repeatedly combining `serve` and `reload` by hand. Watch mode takes over the target environment, keeps only one watcher per adapter, and avoids duplicate menubar instances.

## Config, Profiles, And Auth

Useful inspection commands:

```bash
hermit config show
hermit profiles list
hermit profiles resolve --name codex-local
hermit auth status
```

These are the best first checks when the runtime is behaving differently than expected.

## Task Kernel Commands

Hermit's most distinctive operator-facing surface lives under `task`.

Useful commands:

```bash
hermit task list
hermit task show <task_id>
hermit task events <task_id>
hermit task receipts --task-id <task_id>
hermit task case <task_id>
hermit task explain <task_id>
hermit task proof <task_id>
hermit task proof-export <task_id>
hermit task approve <approval_id>
hermit task approve-mutable-workspace <approval_id>
hermit task deny <approval_id> --reason "not safe"
hermit task resume <approval_id>
hermit task rollback <receipt_id>
hermit task projections-rebuild --all
hermit task steer <task_id> "directive text" --type scope
hermit task steerings <task_id>
hermit task claim-status [<task_id>]
```

Capability-grant commands:

```bash
hermit task capability list
hermit task capability revoke <grant_id>
```

These commands are part of what makes Hermit feel like a governed kernel rather than only a conversational shell.

## Memory Commands

Memory-related operator commands:

```bash
hermit memory inspect <memory_id>
hermit memory inspect --claim-text "Use uv for local Python workflows"
hermit memory list --status active
hermit memory status
hermit memory rebuild
hermit memory export [--output <path>]
```

Use these when you want to inspect evidence-bound memory behavior rather than generic conversation memory.

## Plugin Commands

Plugin management:

```bash
hermit plugin list
hermit plugin install <git-url>
hermit plugin info <name>
hermit plugin remove <name>
```

Hermit's plugin model is still a major extension surface, even as the kernel becomes more central.

## Scheduler Commands

List schedules:

```bash
hermit schedule list
```

Create a schedule:

```bash
hermit schedule add \
  --name "daily-summary" \
  --prompt "Summarize the latest project changes" \
  --cron "0 18 * * 1-5"
```

Other schedule commands:

```bash
hermit schedule remove <id>
hermit schedule enable <id>
hermit schedule disable <id>
hermit schedule history --job-id <id>
```

Scheduled work is stored in the kernel-backed schedule state and later picked up by `hermit serve`.

## Autostart Commands

macOS launchd support:

```bash
hermit autostart enable --adapter feishu
hermit autostart disable --adapter feishu
hermit autostart status
```

These commands use the `--adapter` flag.

> **Note:** `serve` and `reload` take the adapter name as a positional argument (e.g. `hermit serve feishu`). Only `autostart` uses the `--adapter` flag.

## Overnight Report

Generate an activity report summarizing kernel events from the past hours:

```bash
hermit overnight
hermit overnight --lookback 24
hermit overnight --json
```

The command reads the kernel ledger database and produces a summary of task completions, failures, approvals, and other notable events within the lookback window (default: 12 hours). Use `--json` for machine-readable output suitable for dashboards or downstream tooling.

## Sessions

List known session files:

```bash
hermit sessions
```

Session persistence is still present in the broader runtime, even as task semantics become more central.

## Docker

The service entrypoint in containerized setups should match the actual CLI:

```bash
hermit serve feishu
```

## Practical Operator Flow

When something important happened and you want the shortest path to clarity:

1. `hermit task show <task_id>`
2. `hermit task proof <task_id>`
3. `hermit task receipts --task-id <task_id>`
4. `hermit task events <task_id>`
5. `hermit task rollback <receipt_id>` if recovery is supported and appropriate

## Related Docs

- [operator-guide.md](./operator-guide.md)
- [configuration.md](./configuration.md)
- [providers-and-profiles.md](./providers-and-profiles.md)
- [status-and-compatibility.md](./status-and-compatibility.md)
