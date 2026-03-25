---
description: "Get started with Hermit in minutes. Install, configure a provider, and run your first governed agent task."
---

# Getting Started

Hermit is an operating system for AI tasks. This guide walks you through installing the OS, configuring your first provider, and running your first governed task — complete with receipts, proofs, and rollback capability.

Every code block below is copy-paste ready.

## Prerequisites

Requires Python `3.11+` and [`uv`](https://docs.astral.sh/uv/).

| Requirement | Version | Check |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| uv | latest | `uv --version` |
| API key | Anthropic or OpenAI | see [Connecting a Provider](#connecting-a-provider-like-installing-a-driver) |

Install `uv` if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Installing the OS

### One-line install (macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/heggria/Hermit/main/install-macos.sh | bash
```

### Install from source

```bash
git clone https://github.com/heggria/Hermit.git && cd Hermit
make install
```

Both paths run the same installer. It will:

1. Install Hermit via `uv tool install` with Python 3.11+
2. Run `hermit init` to create `~/.hermit/`
3. Auto-detect API keys from your shell, `~/.claude/settings.json`, and `~/.codex/auth.json`

Verify the installation:

```bash
hermit auth status
```

## Connecting a Provider (Like Installing a Driver)

Hermit needs an LLM provider the way an OS needs device drivers. Add your API key to `~/.hermit/.env`:

```bash
# Option A: Anthropic (Claude)
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.hermit/.env

# Option B: OpenAI (Codex)
echo 'OPENAI_API_KEY=sk-...' >> ~/.hermit/.env
echo 'HERMIT_PROVIDER=codex' >> ~/.hermit/.env
```

If the installer already detected your keys, this step is done. Check with:

```bash
hermit profiles list
```

For advanced profile configuration, create `~/.hermit/config.toml`:

```toml
default_profile = "claude-code"

[profiles.claude-code]
provider = "claude"
model = "claude-sonnet-4-6"  # example; the default model may differ per profile
```

See [providers-and-profiles.md](./providers-and-profiles.md) for full details.

## Running Your First Task

Run a one-shot governed task:

```bash
hermit run "List the files in the current directory and summarize what you see"
```

That single command triggered the full governed execution pipeline:

1. The kernel created a **Task** with a unique ID
2. The policy engine evaluated each proposed action
3. Authorized actions received scoped **CapabilityGrants**
4. Each executed action produced a signed **Receipt**
5. The kernel assembled a hash-chained **Proof** bundle

Now inspect what happened:

```bash
# List all tasks
hermit task list

# Show details for the most recent task (replace <task_id> with the ID from the list)
hermit task show <task_id>

# View the execution receipts
hermit task receipts --task-id <task_id>

# Export the proof summary
hermit task proof <task_id>
```

## Reading the System Log

### Tasks

A **Task** is the durable unit of work — think of it as a process in the OS. It contains Steps, and each Step contains StepAttempts. Every task has a lifecycle: `pending` -> `running` -> `completed` (or `failed` / `blocked`), just like OS processes move through scheduling states.

```bash
hermit task show <task_id>
```

This shows the task status, steps taken, and timing information.

### Receipts

A **Receipt** is a durable record of an important action — like an audit log entry in syslog, but cryptographically signed. It ties together:

- **What** happened (tool name, inputs, outputs)
- **Why** it was allowed (policy decision, approval reference)
- **With what authority** (capability grant, workspace lease)
- **What changed** (result summary, affected resources)

Receipts are not log lines. They are kernel objects that survive restarts and support rollback.

```bash
hermit task receipts --task-id <task_id>
```

### Proofs

A **Proof** is a hash-chained summary of all events and receipts for a task — a kernel attestation bundle that proves exactly what happened. It provides tamper-evident verification that the recorded execution history is complete and unmodified.

Hermit supports three export tiers:

| Tier | Size | Contents |
|---|---|---|
| `summary` | ~5-20 KB | Chain status, verification result, refs |
| `standard` | ~50-200 KB | Adds full governance records |
| `full` | MBs | Adds receipt bundles, context manifests, Merkle proofs |

```bash
# Summary
hermit task proof <task_id>

# Full export
hermit task proof-export <task_id>
```

### Approvals

When a task proposes a high-risk action (e.g., writing to files outside the workspace, running network commands), the kernel blocks execution and creates an **Approval** request — like `sudo` for AI, requiring explicit operator authorization before the action proceeds.

```bash
# List pending approvals
hermit task list --limit 10

# Approve
hermit task approve <approval_id>

# Deny with reason
hermit task deny <approval_id> --reason "not safe"
```

## Interactive Mode

For a conversational session with the full governed pipeline:

```bash
hermit chat
```

Inside the session, use `/help` to see available commands, `/task` to inspect tasks, and `/quit` to exit.

## MCP Integration

Hermit exposes its kernel as an MCP server, so supervisor agents (Claude Code, Cursor, etc.) can submit governed tasks programmatically.

Start the MCP server:

```bash
hermit serve
```

Then configure your MCP client to connect. The available tools include:

- `hermit_submit` -- submit a task
- `hermit_submit_dag_task` -- submit a DAG of dependent steps
- `hermit_await_completion` -- block until a task finishes
- `hermit_task_status` / `hermit_task_output` -- inspect results
- `hermit_pending_approvals` / `hermit_approve` / `hermit_deny` -- approval flow

See the [MCP server plugin](../src/hermit/plugins/builtin/mcp/hermit_server/) for connection details.

## Next Steps

You've installed the OS and run your first governed task. Now explore what the OS can do:

- [Architecture](./architecture.md) -- how the kernel, runtime, and plugin system fit together
- [Governance](./governance.md) -- policy profiles, approval flow, and scoped authority
- [Receipts and Proofs](./receipts-and-proofs.md) -- receipt classes, proof export, and rollback
- [Configuration](./configuration.md) -- environment variables, profiles, and multi-environment isolation
- [CLI Reference](./cli-and-operations.md) -- full command reference
- [Plugin Development](./plugin-development.md) -- how plugins are structured and loaded
- [MCP Integration](./mcp-integration.md) -- connecting supervisor agents to the Hermit kernel
- [Self-Iteration](./self-iteration.md) -- governed self-improvement pipelines
- [Use Cases](./use-cases.md) -- real-world governed agent workflows
- [FAQ](./faq.md) -- frequently asked questions

## Troubleshooting

### `hermit: command not found`

The `uv tool` bin directory is not in your PATH. Add it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### `No provider configured` or `ANTHROPIC_API_KEY not set`

Hermit cannot find valid credentials. Verify your `.env` file:

```bash
cat ~/.hermit/.env
```

Make sure at least one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `HERMIT_AUTH_TOKEN` is set. Then check:

```bash
hermit auth status
```

### `Python >= 3.11 required`

Hermit requires Python 3.11 or later. Check your version:

```bash
python3 --version
```

If you have an older version, install 3.11+ via `uv`:

```bash
uv python install 3.11
```

The installer uses `--python 3.11` automatically, so this is only relevant for source installs via `uv sync`.

### Task stuck in `blocked` status

The task is waiting for operator approval. List and resolve pending approvals:

```bash
hermit task list --limit 10
hermit task approve <approval_id>
```

To avoid approval prompts during exploration, use the `autonomous` policy profile, which auto-approves low and medium risk actions:

```bash
hermit run --policy autonomous "your task description"
```

### SQLite or kernel state errors

The kernel ledger is stored at `~/.hermit/kernel/state.db`. If it becomes corrupted:

```bash
# Back up the current state
cp ~/.hermit/kernel/state.db ~/.hermit/kernel/state.db.bak

# Re-initialize
hermit init
```

### Build or install failures

If `make install` fails, try the manual path:

```bash
uv sync --group dev --group typecheck --group docs --group security --group release
uv run hermit init
```

For persistent issues, clean build artifacts first:

```bash
python3 scripts/clean_build_artifacts.py .
make install
```
