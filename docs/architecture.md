---
description: "Hermit architecture overview: a governed agent kernel with task-first execution, policy-gated side effects, receipt-aware verification, and event-sourced durability."
---

# Hermit Architecture

Hermit's architecture mirrors a traditional operating system kernel. Just as Linux mediates between user processes and hardware, Hermit mediates between AI agents and the resources they modify. It is a **governed agent kernel** -- not a framework for building agents, but a kernel that enforces governance at the lowest level of agent execution. Models propose actions. The kernel decides whether, how, and under what authority those actions run. Every consequential mutation is policy-evaluated, scoped, receipted, and verifiable after the fact.

This document is written for engineers evaluating Hermit for the first time. It covers the conceptual model, the layer structure, the governance pipeline, the plugin system, key design decisions, and extension points. It assumes familiarity with LLM-based agent systems but no prior knowledge of Hermit.

## How Hermit Differs from Typical Agent Frameworks

Most agent frameworks focus on **composition**: how to wire a model to tools, manage prompts, and orchestrate multi-step workflows. The fundamental unit of work is a chat turn or a graph node. Tool calls execute under the ambient authority of the host process. Accountability is reconstructed from logs.

Hermit focuses on **governance**: how to scope authority, gate side effects, record decisions, and make execution verifiable. The fundamental unit of work is a durable **Task**. Tool calls execute only after the kernel evaluates policy, issues scoped grants, and acquires workspace leases. Accountability is built into the execution path, not bolted on afterward.

| Concern | Typical Framework | Hermit |
|---|---|---|
| Unit of work | Chat turn / graph node | Durable Task with lifecycle |
| Tool execution | Direct, under process authority | Policy-gated, scoped, receipted |
| State durability | In-memory, lost on crash | Event-sourced SQLite ledger |
| Audit trail | Logs (unstructured, after the fact) | Receipts + proof bundles (structured, inline) |
| Authority model | Ambient (whatever the process can do) | Scoped (CapabilityGrants + WorkspaceLeases) |
| Rollback | Manual cleanup | Structured, receipt-linked reversal |
| Memory | Free-form text snippets | Evidence-bound, governed artifacts |

This is a deliberate trade-off. Hermit is not optimized for rapid prototyping or casual experimentation. It is built for work that is long-running, approval-sensitive, worth auditing, and operated by someone who needs trust without continuous supervision.

## OS Mapping

The analogy between Hermit and a traditional operating system is not superficial -- it is structural. Each kernel concept has a direct counterpart:

```
Traditional OS          →  Hermit (AI Task OS)
─────────────────────────────────────────────
Process                 →  Task
System call             →  Action Request
Permission check        →  Policy Engine evaluation
File descriptor / fd    →  Capability Grant
Process isolation       →  Workspace Lease
Kernel audit log        →  Receipt (HMAC-signed)
/proc + dmesg           →  Kernel Ledger (SQLite)
Kernel module           →  Plugin (adapter/hook/tool)
Shell                   →  CLI surface
IPC / pipes             →  Artifact blackboard
Signal (SIGTERM etc)    →  Steering signal
Filesystem journal      →  Event-sourced ledger
```

This mapping is intentional. OS kernels solved the problem of mediating untrusted user-space programs and shared hardware decades ago. Hermit applies the same structural discipline to a new class of untrusted principal: autonomous AI agents operating on shared resources (files, APIs, databases). The kernel boundary exists for the same reason -- to ensure that no agent can bypass governance and act with unscoped authority.

## Layer Architecture

Hermit is organized in four layers. Each layer has a clear responsibility and a strict dependency direction: upper layers depend on lower layers, never the reverse.

```
 ┌─────────────────────────────────────────────────────────┐
 │              SURFACES  (User Space)                     │
 │  CLI (chat, run, task)  Adapters (Feishu, Slack, ...)   │
 │  TUI    Scheduler    Webhook    MCP Server              │
 └────────────────────────┬────────────────────────────────┘
                          │
 ┌────────────────────────▼────────────────────────────────┐
 │              RUNTIME  (System Libraries)                │
 │  AgentRunner    PluginManager    LLM Providers          │
 │  SessionManager    ToolRegistry    MCP Client           │
 │  SystemPromptBuilder    ProfileCatalog                  │
 └────────────────────────┬────────────────────────────────┘
                          │
 ┌────────────────────────▼────────────────────────────────┐
 │              KERNEL  (Ring 0)                           │
 │  TaskController    PolicyEngine    ToolExecutor          │
 │  ContextCompiler   Memory (24 modules)   Artifacts      │
 │  Authority (Grants, Leases)   Verification (Receipts,   │
 │  Proofs, Rollback, Benchmark)   Signals   Analytics     │
 └────────────────────────┬────────────────────────────────┘
                          │
 ┌────────────────────────▼────────────────────────────────┐
 │              LEDGER  (Block Device)                     │
 │  SQLite event journal    Hash-chained events            │
 │  Projection rebuild     Mixin-based KernelStore         │
 └─────────────────────────────────────────────────────────┘
```

**Surfaces** are entry points. They accept user intent (a CLI command, a chat message, an adapter webhook) and translate it into kernel operations. Surfaces are thin -- they do not contain business logic.

**Runtime** handles orchestration. It manages the LLM provider loop, discovers and loads plugins, assembles system prompts, resolves capabilities, and coordinates sessions. It is the bridge between user-facing surfaces and the kernel.

**Kernel** is where governance lives. It owns the task lifecycle, evaluates policy, issues authority grants, compiles context, manages memory, executes tools under scoped authority, and produces receipts. The kernel is the part that gives Hermit its identity.

**Ledger** is the durable truth. An append-only SQLite event journal with hash-chained entries. All kernel state is derived from this journal. Projections are materialized views that can be rebuilt from the event stream at any time.

## The Governance Pipeline

Every consequential action in Hermit passes through a structured pipeline. This is not a suggestion or a best practice -- it is the only path the kernel provides for effectful execution. This pipeline is the AI equivalent of a system call path. Just as `write()` goes through VFS -> filesystem -> block device, a Hermit action goes through Policy -> Approval -> Lease -> Grant -> Execute -> Receipt.

```
  ┌──────────┐
  │  Model   │  "I want to write to /src/app.py"
  │ proposes │
  └────┬─────┘
       │
       ▼
  ┌──────────────────┐
  │  Action Request   │  Derive action class, risk level, target
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  Policy Engine    │  Evaluate rules, trust score, guard chain
  │                   │  Verdict: auto-approve / require-approval / deny
  └────────┬─────────┘
           │
      ┌────┴────┐
      │ Needs   │──── yes ──▶ ┌──────────────┐
      │approval?│             │  Approval     │  Task parks. Operator
      └────┬────┘             │  Workflow     │  reviews and decides.
           │ no               └──────┬───────┘
           │◀─────────────────────────┘
           ▼
  ┌──────────────────┐
  │ Workspace Lease   │  Acquire scoped access to target paths
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ Capability Grant  │  Issue time-bound, scope-limited authority
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  Tool Executor    │  Execute under granted authority
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  Receipt          │  Record inputs, outputs, authority chain,
  │                   │  result, rollback metadata
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  Proof / Rollback │  Hash-chain into proof bundle;
  │                   │  enable structured reversal if needed
  └──────────────────┘
```

Key properties of this pipeline:

- **Fail-closed.** If governance metadata is ambiguous, execution is refused. The kernel prefers refusing work over permitting unscoped side effects.
- **Durable.** Every stage produces a kernel record. The full authorization chain is preserved in the ledger, not just the final outcome.
- **Inspectable.** An operator can examine any task after the fact and trace the complete path from proposal to execution to receipt.
- **Reversible.** For supported actions, receipts carry the metadata needed for structured rollback without manual cleanup.

## Task Lifecycle

The task is Hermit's fundamental unit of work. Unlike a chat turn that vanishes when the session ends, a task is a durable commitment that survives pauses, crashes, restarts, and approvals.

```
  Task
   ├── Step 1
   │    ├── StepAttempt 1  →  (failed, recoverable)
   │    └── StepAttempt 2  →  Receipt + Proof
   ├── Step 2
   │    └── StepAttempt 1  →  Receipt + Proof
   └── Step 3 (pending approval)
        └── ... (parked until approved)
```

A **Task** tracks a named objective. It has ingress (where the request came from), steps (what needs to happen), and a lifecycle (created, running, suspended, completed, failed, cancelled).

A **Step** is a unit of execution within a task. Steps can run sequentially or in parallel (fork-join).

A **StepAttempt** is a single try at completing a step. Failed attempts are preserved -- the kernel does not discard history. Recovery logic can retry with a new attempt.

Tasks are first-class kernel objects with their own projections (materialized views). Operator surfaces like `hermit task show`, `hermit task receipts`, and `hermit task proof-export` query these projections directly.

## Plugin Architecture

Hermit uses a plugin system for extensibility. Everything outside the kernel core -- adapters, tools, hooks, MCP integrations, subagents -- is a plugin loaded through a uniform discovery and registration mechanism.

```
  ┌─────────────────────────────────────────────────┐
  │                 PluginManager                    │
  │                                                  │
  │  Discovery:  builtin/ dir  +  ~/.hermit/plugins/ │
  │  Manifest:   plugin.toml per plugin              │
  └───┬──────┬──────┬──────┬──────┬──────┬──────────┘
      │      │      │      │      │      │
      ▼      ▼      ▼      ▼      ▼      ▼
   Tools  Hooks  Adapters  MCP  Subagents  Bundles

   Examples:
   ├── tools/file_tools     — governed file read/write
   ├── hooks/memory         — evidence-bound memory promotion
   ├── hooks/scheduler      — cron-like task scheduling
   ├── hooks/patrol         — proactive code health checks
   ├── adapters/feishu      — Feishu messaging integration
   ├── adapters/slack       — Slack adapter (Socket Mode)
   ├── mcp/hermit_server    — expose kernel via MCP protocol
   └── subagents/orchestrator — delegated sub-task execution
```

Each plugin declares its entry points in a `plugin.toml` manifest:

```toml
[plugin]
name = "my-plugin"
version = "0.1.0"

[entry]
tools = "tools:register"
hooks = "hooks:register"
```

**Plugin categories:**

| Category | Purpose | Governance |
|---|---|---|
| **Tools** | Actions the agent can take | Subject to full governance pipeline |
| **Hooks** | React to lifecycle events (session start, dispatch result, etc.) | Dispatched by priority with signature-adaptive calling |
| **Adapters** | Messaging platform integrations (Feishu, Slack, Telegram) | Surface-level; route into kernel tasks |
| **MCP** | Model Context Protocol servers and clients | MCP tools governed identically to builtin tools |
| **Subagents** | Delegated execution with governed tool access | Run under kernel authority with receipts |
| **Bundles** | Slash-command groupings (`/compact`, `/plan`, `/usage`) | Command-level; no direct side effects |

Plugins are discovered from two paths:
1. `src/hermit/plugins/builtin/` -- ships with Hermit
2. `~/.hermit/plugins/` -- user-installed extensions

## Design Decision Rationale

### Why event sourcing?

The kernel ledger is an append-only event journal, not a mutable database. All state -- tasks, approvals, grants, receipts -- is derived from this journal.

This gives Hermit three properties that mutable state cannot:

1. **Auditability.** The journal is a complete history. You can answer "what was the state at time T?" by replaying events up to T. Mutable state only tells you the current state.
2. **Verifiability.** Events are hash-chained. Each event includes a hash of the previous event, forming a tamper-evident chain. If any event is modified or deleted, the chain breaks.
3. **Rebuildability.** Projections (materialized views) can be rebuilt from the journal at any time. If a projection is corrupted, `hermit task projections-rebuild` regenerates it from the event stream. The journal is the source of truth; projections are caches.

The cost is storage growth and query indirection. Hermit accepts this because, for governed execution, knowing *how you got here* is as important as knowing *where you are*.

### Why a synchronous kernel?

Kernel methods are synchronous. Async only exists at surface boundaries (CLI, adapters, MCP server).

This is a deliberate simplification, and the same reason OS kernels are synchronous in the system call path -- deterministic evaluation of security-critical decisions:

- **Predictable execution order.** Policy evaluation, grant issuance, and receipt recording happen in a deterministic sequence. No race conditions between governance stages.
- **Simpler reasoning.** The kernel's state transitions are sequential. You can read the code and know exactly what happens in what order.
- **Async where it matters.** The LLM provider loop, adapter ingress, and MCP server are async because they involve I/O. The kernel itself does not need to be.

The kernel is fast enough synchronously because its operations are local (SQLite, file I/O). Network I/O happens outside the kernel boundary.

### Why receipt-aware execution?

Most agent runtimes stop their accountability story at the tool loop: the tool ran, here is the return value, move on. Hermit takes a stronger position: **an important action is not complete until a receipt is issued.**

A receipt ties together:
- What was requested (action class, target, parameters)
- What authority allowed it (policy decision, approval, capability grant, workspace lease)
- What happened (result code, output references)
- How to reverse it (rollback metadata, when supported)

This is not about distrust. It is about operating in a regime where agents act with real authority -- writing files, running commands, calling APIs -- and the operator needs structured oversight without watching every action in real time.

### Why local-first?

All kernel state lives in `~/.hermit/`. The ledger is a local SQLite database. No cloud dependency for core operation. The LLM provider is a separate concern; the kernel itself is fully local.

This means the operator has physical custody of their execution history, approval records, and memory. The trust boundary is the operator's own machine, not a cloud provider's security practices. For work that involves sensitive code, credentials, or compliance-sensitive decisions, this is the right default.

## Extension Points

Developers can extend Hermit at several levels:

### 1. Plugins (most common)

Create a directory under `~/.hermit/plugins/` with a `plugin.toml` manifest. Register tools, hooks, adapters, MCP servers, subagents, or commands. Tools registered through plugins are automatically subject to the full governance pipeline -- no extra work needed.

### 2. Hook events

Subscribe to lifecycle events to inject behavior at specific points:

- `SYSTEM_PROMPT` -- modify the system prompt before it reaches the model
- `REGISTER_TOOLS` -- dynamically add or modify available tools
- `SESSION_START` / `SESSION_END` -- run setup or teardown logic
- `PRE_RUN` / `POST_RUN` -- intercept before and after execution
- `DISPATCH_RESULT` -- react to completed governed execution
- `SUBTASK_SPAWN` / `SUBTASK_COMPLETE` -- coordinate sub-task workflows

### 3. MCP integration

Hermit can act as both an MCP client (connecting to external MCP servers) and an MCP server (exposing kernel tools to supervisor agents via `hermit_server`). External MCP tools are governed identically to builtin tools -- they pass through the same policy engine and produce the same receipts.

### 4. Policy profiles

Define named policy profiles that control governance strictness:

- `autonomous` -- high autonomy, most actions auto-approved
- `default` -- balanced, consequential actions require approval
- `supervised` -- low autonomy, most actions require approval
- `readonly` -- no mutations permitted

Profiles are selected per-task, allowing different governance levels for different workloads.

### 5. LLM providers

The provider host layer supports pluggable LLM backends. Current implementations include Claude and Codex. Adding a new provider means implementing the `Provider` protocol defined in `runtime/provider_host/shared/contracts.py`.

## Source Layout

```
src/hermit/
├── kernel/             # Governed execution kernel
│   ├── task/           #   Task models, controller, projections, state
│   ├── policy/         #   Approvals, evaluators, guards, trust scoring
│   ├── execution/      #   Executor, coordination, recovery, workers
│   ├── ledger/         #   SQLite journal, event store, projections
│   ├── verification/   #   Receipts, proofs, rollback, benchmarks
│   ├── context/        #   Context compiler, memory (24 modules)
│   ├── artifacts/      #   Artifact models, lineage, claims
│   ├── authority/      #   Identity, workspaces, capability grants
│   ├── analytics/      #   Governance metrics, health monitoring
│   └── signals/        #   Steering signals, evidence, signal store
├── runtime/            # Orchestration and provider layer
│   ├── control/        #   AgentRunner, session manager, budgets
│   ├── capability/     #   PluginManager, tool registry, MCP client
│   ├── provider_host/  #   LLM providers, sandbox, profiles
│   ├── assembly/       #   Config and context assembly
│   └── observation/    #   Logging
├── plugins/builtin/    # Shipped plugins
│   ├── adapters/       #   Feishu, Slack, Telegram
│   ├── hooks/          #   Memory, scheduler, patrol, research, ...
│   ├── tools/          #   File tools, web tools, computer use, ...
│   ├── mcp/            #   GitHub, Hermit MCP server, MCP loader
│   ├── subagents/      #   Orchestrator
│   └── bundles/        #   Compact, planner, usage
├── surfaces/cli/       # CLI entrypoints and TUI
├── infra/              # Storage, locking, i18n, paths
└── apps/               # macOS companion app
```

## Further Reading

- [Why Hermit](./why-hermit.md) -- the thesis in shorter form
- [Design Philosophy](./design-philosophy.md) -- deeper reasoning behind each design choice
- [Governance](./governance.md) -- the governance model in detail
- [Receipts and Proofs](./receipts-and-proofs.md) -- receipt semantics, proof bundles, rollback
- [Context Model](./context-model.md) -- how context is compiled from artifacts
- [Memory Model](./memory-model.md) -- evidence-bound memory subsystem
- [Getting Started](./getting-started.md) -- installation and first run
- [Configuration](./configuration.md) -- profiles, plugins, and environment setup
- [MCP Integration](./mcp-integration.md) -- MCP Integration Guide
- [Plugin Development](./plugin-development.md) -- Plugin Development Guide
- [Use Cases](./use-cases.md) -- Use Cases and Scenarios
- [FAQ](./faq.md) -- FAQ and Positioning
- [Roadmap](./roadmap.md) -- Roadmap
