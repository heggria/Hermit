# Getting Started

This guide gets you from clone to first task, first approval, and first proof export.

Hermit is local-first. By default it keeps state under `~/.hermit`, including configuration, tasks, artifacts, receipts, and memory.

## Requirements

- Python `3.13+`
- [`uv`](https://docs.astral.sh/uv/) recommended
- an LLM provider configuration

Optional:

- Feishu credentials if you want long-running channel ingress
- macOS `rumps` if you want the menu bar companion

## Install

The simplest path is:

```bash
make install
```

This initializes the local workspace and installs Hermit for local use.

Manual path:

```bash
uv sync --group dev --group typecheck --group docs --group security --group release
uv run hermit init
```

## Configure A Provider

Hermit supports `claude`, `codex`, and `codex-oauth`.

Example using OpenAI:

```bash
export HERMIT_PROVIDER=codex
export OPENAI_API_KEY=sk-...
export HERMIT_MODEL=gpt-5.4
```

You can also store long-lived configuration in `~/.hermit/.env` or `~/.hermit/config.toml`.

Check the resolved config:

```bash
hermit config show
hermit auth status
```

For deeper configuration details, see [configuration.md](./configuration.md).

## Run Your First Task

Interactive:

```bash
hermit chat
```

One-shot:

```bash
hermit run "Summarize the current repository"
```

Long-running service:

```bash
hermit serve --adapter feishu
```

## Inspect The Task Kernel

Hermit is not just a session shell. It already records durable task objects in the local kernel ledger.

List tasks:

```bash
hermit task list
```

Show one task:

```bash
hermit task show <task_id>
```

Inspect task events:

```bash
hermit task events <task_id>
```

Show receipts:

```bash
hermit task receipts --task-id <task_id>
```

Show a proof summary:

```bash
hermit task proof <task_id>
```

Export a proof bundle:

```bash
hermit task proof-export <task_id>
```

## Approval And Rollback

When a consequential action is blocked for approval, Hermit records an approval object and exposes it through the task CLI.

Approve:

```bash
hermit task approve <approval_id>
```

Deny:

```bash
hermit task deny <approval_id> --reason "not safe"
```

If a receipt supports rollback:

```bash
hermit task rollback <receipt_id>
```

Rollback is not universal today. Treat it as supported for selected receipt classes, not as a blanket guarantee.

## Where State Lives

Common paths under `~/.hermit`:

- `.env`
- `config.toml`
- `kernel/state.db`
- `sessions/`
- `memory/`
- `schedules/`
- `plugins/`

The kernel database is where Hermit records task, step, approval, receipt, proof, and memory-related state.

## How To Read The Docs

Start here:

- [why-hermit.md](./why-hermit.md)
- [architecture.md](./architecture.md)
- [governance.md](./governance.md)
- [receipts-and-proofs.md](./receipts-and-proofs.md)
- [roadmap.md](./roadmap.md)

If you are evaluating Hermit, the most important distinction is this:

- `architecture.md` describes what the repo currently implements
- `kernel-spec-v0.1.md` describes the target architecture Hermit is converging toward
