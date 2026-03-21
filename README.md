<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/hermit-icon-white.svg">
    <source media="(prefers-color-scheme: light)" srcset="./docs/assets/hermit-macos-icon.svg">
    <img src="./docs/assets/hermit-macos-icon.svg" alt="Hermit" width="120" height="120">
  </picture>
</p>

<h1 align="center">Hermit</h1>

<p align="center">
  <strong>The first operating system for AI tasks.</strong><br>
  <sub>Process management. Permission model. Audit log. Transaction safety.<br>Not another agent framework — the first operating system for AI tasks.</sub>
</p>

<p align="center">
  <a href="https://github.com/heggria/Hermit/actions/workflows/ci.yml"><img src="https://github.com/heggria/Hermit/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="https://pypi.org/project/hermit-agent/"><img src="https://img.shields.io/pypi/v/hermit-agent?color=blue" alt="PyPI"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-black" alt="License: MIT"></a>
  <a href="https://pypi.org/project/hermit-agent/"><img src="https://img.shields.io/pypi/dm/hermit-agent" alt="Downloads"></a>
  <a href="https://discord.gg/XCYqF3SN"><img src="https://img.shields.io/discord/1483353136834936924?logo=discord&logoColor=white&label=Discord" alt="Discord"></a>
  <a href="https://heggria.github.io/Hermit/"><img src="https://img.shields.io/badge/docs-github%20pages-0F172A" alt="Docs"></a>
</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

---

AI agents are doing real work — writing code, managing infrastructure, running deployments. But they're running without an OS. No process management. No permission model. No audit log. No rollback. Hermit is the operating system that AI tasks have been missing.

**Hermit treats AI task execution the way an OS kernel treats process execution:** every action requires authorization, every mutation produces a signed receipt, every session generates a cryptographic proof bundle, and any governed action can be rolled back.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Hermit — AI Task OS                          │
│                                                                     │
│  Agent proposes action                                              │
│       │                                                             │
│       ▼                                                             │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌────────────────┐   │
│  │Permission │─▶│  Access   │─▶│ Process   │─▶│ Capability-    │   │
│  │ Model     │  │ Control   │  │ Isolation │  │ Based Security │   │
│  └───────────┘  └───────────┘  └───────────┘  └───────┬────────┘   │
│                                                         │           │
│                                                         ▼           │
│                                              ┌──────────────────┐   │
│                                              │  System Call     │   │
│                                              │  Handler         │   │
│                                              └────────┬─────────┘   │
│                                                       │             │
│       ┌───────────────────────────────────────────────┼──────┐      │
│       ▼                    ▼                          ▼      │      │
│  ┌───────────┐   ┌────────────────┐        ┌──────────────┐  │      │
│  │ Audit Log │   │    Kernel      │        │   Rollback   │  │      │
│  │  Entry    │   │  Attestation   │        │  (if needed) │  │      │
│  │  (HMAC)   │   │  (hash-chain)  │        └──────────────┘  │      │
│  └───────────┘   └────────────────┘                          │      │
│       └──────────────────────────────────────────────────────┘      │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │              SQLite Event-Sourced Ledger                      │  │
│  │   Tasks · Steps · Receipts · Proofs · Artifacts · Approvals   │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**No action bypasses the kernel.** Agents propose, the OS authorizes, the syscall handler runs, the ledger records.

## Key Features

**Process Management for AI** — Tasks are first-class kernel objects with full lifecycle state machines, event sourcing, and durable step-level tracking. Not ephemeral chat — inspectable process records.

**Permission Model** — Every agent action goes through the same authorization path: Approval -> Lease -> Grant -> Execute -> Receipt -> Proof. No direct model-to-tool execution. Ever.

**System Audit Log** — Every action produces HMAC-SHA256 signed receipts. Sessions generate hash-chained, Merkle-anchored proof bundles. Auditable AI execution, not "trust the transcript."

**Transaction Safety** — Recursive dependency tracking with leaf-first rollback planning. Any governed action can be rolled back, like filesystem journaling.

**Self-Upgrading OS** — Hermit can improve itself through governed `spec -> implement -> benchmark -> learn` pipelines. Every self-modification is policy-authorized, receipted, and provable.

**System API (MCP-Native)** — Exposes kernel tools via Model Context Protocol. Claude Code, Cursor, or any MCP client connects as a supervisor via the system API, like userspace programs connect via syscalls.

**Evidence-Bound Memory** — Memory promotion requires evidence references. No hallucinated memories — only facts backed by execution artifacts.

**Plugin Architecture** — Adapters (Feishu, Slack, Telegram), hooks (scheduler, webhook, patrol, research, quality), tools, MCP servers, subagents — all loaded via `plugin.toml` manifests.

## Quick Start

Install the OS. Run your first governed task.

**Prerequisites:** Python `3.13+`, [uv](https://github.com/astral-sh/uv)

**1. Install**

```bash
# macOS one-liner
curl -fsSL https://raw.githubusercontent.com/heggria/Hermit/main/install-macos.sh | bash

# or from source
git clone https://github.com/heggria/Hermit.git && cd Hermit && make install

# development install (all dependency groups)
uv sync --group dev --group typecheck --group docs --group security --group release
```

**2. Configure**

```bash
hermit setup        # interactive first-run setup
hermit auth status  # verify provider credentials
```

**3. Run**

```bash
hermit run "Refactor the auth module and add tests"   # one-shot governed task
hermit chat                                            # interactive session
hermit serve feishu                                    # long-running adapter service
```

**4. Inspect**

```bash
hermit task list                    # see all governed tasks
hermit task show <task_id>          # full task state + steps
hermit task receipts --task-id <id> # HMAC-signed execution receipts
hermit task proof-export <id>       # export cryptographic proof bundle
hermit task rollback <receipt_id>   # undo a governed action
```

## MCP Integration

Hermit exposes its kernel as an MCP server — the system API for the AI Task OS. Any MCP-compatible supervisor (Claude Code, Cursor, custom agents) gets governed execution for free:

```jsonc
// claude_desktop_config.json or .mcp.json
{
  "mcpServers": {
    "hermit": {
      "url": "http://localhost:8322/mcp"  // Streamable HTTP
    }
  }
}
```

> **Note:** Enable the MCP server first: `export HERMIT_MCP_SERVER_ENABLED=true` then run `hermit serve`

Then from your supervisor agent:

```python
# MCP tool calls (as invoked by a supervisor like Claude Code, Cursor, etc.)
# Submit parallel governed tasks
hermit_submit(description="Refactor the database layer", policy_profile="autonomous")
hermit_submit(description="Add integration tests for auth", policy_profile="autonomous")

# Wait for completion — no polling needed
hermit_await_completion(task_ids=["task_01", "task_02"])

# Inspect results
hermit_task_output(task_ids=["task_01"])
hermit_task_proof(task_ids=["task_01"])   # cryptographic proof bundle
```

Available MCP tools: `hermit_submit`, `hermit_submit_dag_task`, `hermit_await_completion`, `hermit_task_status`, `hermit_task_output`, `hermit_task_proof`, `hermit_pending_approvals`, `hermit_approve`, `hermit_deny`, `hermit_submit_iteration`, `hermit_metrics`, `hermit_lessons_learned`, and more.

## Why Hermit?

| | Agent Frameworks (No OS) | Hermit (AI Task OS) |
|---|---|---|
| **Metaphor** | Script runner | Operating system |
| **Execution model** | Model calls tools directly | Kernel authorizes every action |
| **Audit trail** | Chat transcript (if saved) | HMAC-signed receipts + hash-chained proofs |
| **Rollback** | Manual cleanup | Governed rollback with dependency tracking |
| **Task state** | Ephemeral session | Durable kernel objects with event sourcing |
| **Memory** | Vector store (anything goes) | Evidence-bound, governance-gated promotion |
| **Authority model** | Ambient permissions | Scoped capability grants + workspace leases |
| **Self-improvement** | Uncontrolled | Governed spec -> implement -> benchmark -> learn |
| **Multi-agent** | Trust the orchestrator | Every agent action governed independently |

## Architecture

```
Surfaces (CLI, TUI)  +  Adapters (Feishu, Slack, Telegram)  +  Hooks (Scheduler, Webhook)
    -> AgentRunner (runtime/control/)
        -> PluginManager + Task Controller
            -> Permission Model -> Access Control -> Process Isolation -> Capability-Based Security -> System Call Handler
                -> Artifacts, Audit Log, Kernel Attestation, Rollback
                    -> Kernel Ledger (SQLite event journal + projections)
```

```
src/hermit/
├── kernel/       # Governed execution kernel: task, policy, execution, ledger,
│                 #   verification, signals, analytics, context, authority, artifacts
├── runtime/      # Runner, provider host, capability registry, assembly
├── plugins/      # Adapters, hooks, tools, MCP servers, subagents, bundles
├── infra/        # Storage, locking, paths, i18n
├── surfaces/     # CLI + TUI
└── apps/         # macOS menu bar companion
```

Full architecture: [docs/architecture.md](./docs/architecture.md) | Repository layout: [docs/repository-layout.md](./docs/repository-layout.md)

## Documentation

| Getting Started | Core Concepts | Operations |
|---|---|---|
| [Quick Start](./docs/getting-started.md) | [Architecture](./docs/architecture.md) | [CLI & Operations](./docs/cli-and-operations.md) |
| [Configuration](./docs/configuration.md) | [Governance](./docs/governance.md) | [Operator Guide](./docs/operator-guide.md) |
| [Why Hermit](./docs/why-hermit.md) | [Receipts & Proofs](./docs/receipts-and-proofs.md) | [Task Lifecycle](./docs/task-lifecycle.md) |
| [Design Philosophy](./docs/design-philosophy.md) | [Memory Model](./docs/memory-model.md) | [Desktop Companion](./docs/desktop-companion.md) |

Specs: [Kernel Spec v0.1](./docs/kernel-spec-v0.1.md) | [Kernel Spec v0.2](./docs/hermit-kernel-spec-v0.2.md) | [Conformance Matrix](./docs/kernel-conformance-matrix-v0.1.md) | [Roadmap](./docs/roadmap.md)

## Contributing

Hermit is converging toward the definitive AI Task OS. Contributions that strengthen kernel semantics are especially welcome:

- Task lifecycle and state machines
- Policy, approval, and trust flows
- Receipt coverage and proof export
- Rollback and recovery
- Artifact and context handling
- Memory governance

Start with [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md).

## Community

- [Discord](https://discord.gg/XCYqF3SN) — real-time chat and support
- [GitHub Discussions](https://github.com/heggria/Hermit/discussions) — questions, ideas, architecture talk
- [Issues](https://github.com/heggria/Hermit/issues) — bug reports and feature requests

## License

[MIT](./LICENSE)
