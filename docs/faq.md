---
description: "Hermit v0.3 FAQ and positioning: what Hermit is, how it differs from agent frameworks, and answers to common technical and practical questions."
---

# FAQ and Positioning

## Positioning

### What is Hermit?

Hermit is the first operating system for AI tasks. It provides process management, permission models, audit logging, and rollback for AI agent workloads -- the same primitives that traditional operating systems provide for user processes. Models propose actions; the kernel authorizes, executes, receipts, and -- when necessary -- rolls them back.

### How is Hermit different from LangChain, CrewAI, AutoGen, and similar projects?

Those are agent *frameworks* -- they help you build agents. Hermit is an agent *OS* -- it provides the infrastructure those agents run on. The relationship is like applications to an operating system: you can run LangChain agents inside Hermit's governed environment. In a framework, governance is something you bolt on after the fact. In Hermit, governance is architectural: every tool invocation passes through policy evaluation, approval, scoped capability grants, and receipt issuance before execution happens. The difference is not a feature gap; it is a category difference.

### Why call it a "kernel"?

Because it IS a kernel. Just as the Linux kernel mediates every interaction between user processes and hardware, Hermit mediates every interaction between AI agents and the resources they modify. Tasks are processes. Policy evaluation is permission checking. Receipts are audit log entries. The analogy isn't a metaphor -- it's the architecture. No agent can touch the filesystem, run a shell command, or call an API without the kernel authorizing it, scoping it, and recording what happened. The kernel is the trust boundary, not the model.

## Technical FAQ

### Is this just a metaphor, or is Hermit actually an OS?

It's the architecture. Hermit implements real OS primitives: process lifecycle management (Tasks), capability-based security (CapabilityGrants), process isolation (WorkspaceLeases), audit logging (Receipts), kernel attestation (Proofs), and journaling filesystem semantics (event-sourced SQLite ledger). The OS analogy isn't marketing -- it's the design document.

### Why event sourcing?

Auditability, replay, and debugging. Every kernel state change is derived from an append-only event log in SQLite. This means you can reconstruct the full history of any task, understand why a decision was made, and replay events for diagnostics -- without relying on fragile log scraping.

### Why is the kernel synchronous?

Deterministic governance requires deterministic execution order. A synchronous kernel eliminates race conditions in policy evaluation, approval resolution, and receipt issuance. Async boundaries exist only at the surface layer (CLI, adapters, MCP), never inside governance decisions.

### Why receipts and proofs?

Every consequential action must be accountable, like a financial transaction. A receipt ties together the task, policy decision, approval, capability grant, inputs, outputs, and rollback metadata into a single verifiable record. Proof bundles chain receipts with hash-linked events and optional HMAC signing, so you can answer "what happened, who authorized it, and can we verify it?" long after execution.

### Can I use Hermit with my own LLM provider?

Yes. Hermit's kernel is provider-agnostic. The provider layer currently ships with Claude and Codex (OpenAI) support. Profiles are configured in `~/.hermit/config.toml`, and the kernel itself has no dependency on any specific model or API.

### What is the performance overhead of governance?

Minimal. Kernel operations -- policy evaluation, approval checks, receipt issuance, proof chaining -- are in-memory computations backed by a local SQLite ledger. The governance path adds single-digit milliseconds per tool invocation. The bottleneck in any Hermit workflow is the LLM inference latency, not the kernel.

### Can Hermit run in production?

Hermit is currently at Beta status (v0.3). Its local-first design makes it suitable for single-operator production use: all state lives on your machine, no cloud dependency is required for core kernel operation. For multi-tenant or horizontally-scaled deployments, the architecture is not yet targeted.

### What is MCP and how does Hermit use it?

MCP (Model Context Protocol) is a standard for AI tool integration that lets models discover and invoke tools over a structured transport. Hermit exposes its kernel tools -- task submission, approval, metrics, proof export -- via an MCP server so supervisor agents like Claude Code can submit governed tasks without direct API coupling. See the [MCP integration guide](./mcp-integration.md) for setup and usage details.

### What happens if Hermit crashes mid-task?

Event sourcing means all kernel state is reconstructable from the append-only event log in SQLite. On restart, the kernel replays events to recover the exact task state at the point of interruption. No work is lost -- incomplete tasks resume from their last recorded state.

### What are the policy profiles (autonomous/default/supervised/readonly)?

Hermit ships four policy profiles that control how much human oversight is required. `autonomous` is the most permissive but still enforces guard rules against dangerous patterns. `default` provides balanced governance with approval required for high-risk mutations. `supervised` requires explicit approval for all side-effecting operations. `readonly` permits no side effects at all -- only read operations and analysis. See the [governance docs](./governance.md) for full details.

### How does Hermit handle secrets and API keys?

API keys and credentials are stored in `~/.hermit/.env` and loaded at startup -- they are never persisted in the ledger, artifacts, or receipt payloads. Guard rules in the policy engine detect and block accidental secret exposure in tool arguments and outputs.

### Can I use open-source LLMs (Llama, Mistral) with Hermit?

Not yet natively. The Provider protocol (`provider_host/shared/contracts.py`) is designed for extension, and Claude and Codex providers ship built-in. Community contributions for open-source model providers are welcome -- the kernel itself is fully provider-agnostic.

### How does Hermit compare to Claude Code or Codex CLI?

Claude Code and Codex CLI are agent interfaces -- they provide models with tool access and a conversation loop. Hermit is the governance layer that can sit underneath them, mediating what those agents are allowed to do. The intended architecture is: use Claude Code as the supervisor, and Hermit as the governed executor via MCP.

### How does self-iteration work?

Hermit supports governed self-improvement through a pipeline with defined phases: CREATED, MODIFYING, VERIFYING, MERGING, and COMPLETED. You submit an iteration goal, Hermit researches the problem, generates a spec, branches the code, executes the implementation (MODIFYING), runs verification and benchmarks (VERIFYING), merges the result (MERGING), exports proofs, and creates a PR. Every mutation in that pipeline is authorized by the policy engine, receipted, and verifiable.

## Practical FAQ

### What Python version does Hermit require?

Python 3.13 or higher. This is enforced in `pyproject.toml`. Do not attempt to run Hermit with earlier Python versions.

### Does Hermit support Windows?

Hermit targets macOS and Linux. Windows is not actively tested or supported, though running under WSL (Windows Subsystem for Linux) may work for core functionality. Some subsystems (launchd autostart, menubar companion) are macOS-specific.

### How do I write a plugin?

Create a directory with a `plugin.toml` manifest that declares entry points for tools, hooks, commands, subagents, adapters, or MCP servers. Place it in `~/.hermit/plugins/` for discovery. See the built-in plugins under `src/hermit/plugins/builtin/` for working examples and the `plugin.toml` schema in the [plugin development guide](./plugin-development.md).

### Can I disable governance?

You can adjust the permission model -- from `supervised` to `autonomous` -- like switching from a locked-down server to a permissive development environment. But the OS is always running: receipts are always generated, proofs are always chained, guard rules still block known-dangerous patterns (sudo, curl-pipe-sh, kernel self-modification). You can't uninstall the kernel.

## See Also

- [Getting Started](./getting-started.md) -- installation, setup, and first run
- [Use Cases](./use-cases.md) -- practical scenarios and workflows
- [MCP Integration](./mcp-integration.md) -- connecting supervisor agents via MCP
- [Architecture](./architecture.md) -- kernel design and execution flow
