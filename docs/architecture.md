# Hermit Architecture

This document describes the implementation that exists in the current repository. It does not treat the `v0.1` kernel spec as fully shipped.

Hermit's current architecture has two visible layers:

1. a runtime layer that exposes CLI, chat, `serve`, scheduler, webhook, and adapters
2. a task kernel layer that introduces first-class records, governance, receipts, proofs, and rollback-aware execution

The important change is not just that Hermit has more features. The important change is that the runtime is being re-centered around kernel semantics, and the most sensitive execution paths now fail closed when governance metadata is ambiguous.

## Executive Summary

Hermit is best understood today as:

- a **kernel-first local runtime**
- with a **real task kernel**
- converging toward a **governed agent kernel architecture**

That means two things are true at once:

- the repo already contains durable kernel objects and operator surfaces
- the broader runtime still includes pre-kernel paths and compatibility layers

## Kernel-First Hard Cut

Several boundaries are intentionally stricter than older runtime-era Hermit:

- mutable tools must declare `action_class`, `risk_hint`, and `requires_receipt` explicitly
- readonly tools must declare `action_class` explicitly and are fixed to `requires_receipt = false`
- delegation tools and MCP tools no longer rely on name-based governance inference
- approval grant and deny transitions now create decision + receipt records with proof bundles
- dispatcher restart recovery no longer forces interrupted async governed attempts into terminal failure by default
- operator surfaces now expose claim status and durable re-entry summaries directly from cached task projections
- proof export can upgrade to signed bundles plus receipt inclusion proofs when local signing is configured

This does not mean the full `v0.1` target is shipped. It means the repo now prefers refusing ambiguous execution over preserving permissive legacy behavior.

## Current System Shape

```text
CLI / Chat / Feishu / Scheduler / Webhook
                  |
                  v
             AgentRunner
                  |
      +-----------+-----------+
      |                       |
      v                       v
  PluginManager         Task Controller
      |                       |
      v                       v
  tools / hooks / MCP   Task -> Step -> StepAttempt
                              |
                              v
                   Context Compiler + Policy Engine
                              |
           Approval / Decision / WorkspaceLease / CapabilityGrant
                              |
                              v
                         Tool Executor
                              |
                              v
                Artifact / Receipt / Proof / Rollback
                              |
                              v
                    Kernel Ledger + Projections
```

The runtime surfaces are still important, but the kernel is now the part that gives Hermit its identity.

## Runtime Surfaces

Hermit currently exposes work through:

- CLI commands such as `chat`, `run`, `serve`, `schedule`, and `task`
- long-running `serve` mode
- Feishu ingress
- scheduler-triggered work
- webhook-triggered work

These surfaces are converging on shared task semantics instead of remaining independent session shells.

## Core Runtime Modules

### `src/hermit/surfaces/cli/main.py`

Responsibilities:

- CLI entrypoints
- workspace setup
- runtime assembly
- task inspection and approval commands
- service lifecycle

### `src/hermit/runtime/control/runner/runner.py`

Responsibilities:

- shared orchestration across CLI and service surfaces
- session handling
- hook dispatch
- integration between provider runtime and task kernel paths

### `src/hermit/runtime/capability/registry/manager.py`

Responsibilities:

- plugin discovery
- tool registration
- hook loading
- subagent and adapter assembly
- MCP startup and shutdown

### `src/hermit/runtime/provider_host/`

Responsibilities:

- provider-facing tool loop
- streaming and non-streaming behavior
- execution handoff into task-scoped tool execution

## Task Kernel Modules

The kernel is implemented under `src/hermit/kernel/` with the following layered sub-packages:

- `task/` — TaskRecord models, TaskController, ingress routing, projections, state
- `ledger/` — KernelStore (SQLite-backed journal), event store, projections
- `execution/` — ToolExecutor, execution contracts, coordination (dispatch, observation), recovery
- `policy/` — approvals, decisions, permits, evaluators, guards
- `verification/` — receipts, proofs, rollbacks
- `context/` — context compiler, provider input injection, memory governance
- `artifacts/` — artifact models, lineage, claims, evidence
- `authority/` — identity, workspaces, capability grants

## First-Class Kernel Objects

Hermit already defines first-class records for:

- `TaskRecord`
- `StepRecord`
- `StepAttemptRecord`
- `ApprovalRecord`
- `DecisionRecord`
- `PrincipalRecord`
- `CapabilityGrantRecord`
- `WorkspaceLeaseRecord`
- `ArtifactRecord`
- `ReceiptRecord`
- `BeliefRecord`
- `MemoryRecord`
- `RollbackRecord`
- `ConversationRecord`
- `IngressRecord`

These are not just names in the spec. They exist in the current codebase and are persisted through the kernel store.

## Ledger And Projections

Hermit's kernel store is local and durable.

The current implementation includes:

- a local SQLite-backed kernel database
- a dedicated `events` table
- task-related tables for principals, approvals, decisions, capability grants, workspace leases, receipts, beliefs, memory records, and rollbacks
- projection rebuild paths
- event hash chaining for verification-oriented proof work

This is why "event-backed truth" is a fair description of the kernel direction, even though the entire repo should not yet be described as uniformly event-sourced.

## Execution Path

For governed execution, the interesting path is:

1. an ingress or operator action lands as task-scoped work
2. the task controller creates or resumes a task, step, and step attempt
3. context is compiled from working state, beliefs, memories, and artifacts
4. the policy engine evaluates the proposed action
5. if needed, the kernel creates a decision and requests approval
6. if authorized, the kernel acquires a workspace lease and issues a scoped capability grant
7. the executor performs the action
8. the kernel stores artifacts and issues a receipt
9. proof and rollback services can later inspect or act on the result

This matters because important actions are no longer reducible to "tool call happened."

## Governance Layer

The governance path is primarily visible through:

- policy evaluation
- approval records and approval copy generation
- decision records
- capability grants
- workspace leases
- witness and drift handling

Hermit's executor already uses these concepts to distinguish read-like actions from consequential effectful actions.

See [governance.md](./governance.md) for the deeper model.

## Context And Memory Layer

Hermit treats context as more than transcript replay.

The current codebase already includes:

- context packs
- task-scoped working state snapshots
- belief selection
- memory retrieval and static injection rules
- memory classification, retention, and invalidation logic

This is the practical expression of Hermit's artifact-native and evidence-bound direction.

See:

- [context-model.md](./context-model.md)
- [memory-model.md](./memory-model.md)

## Receipts, Proofs, And Rollback

Hermit already contains:

- receipt issuance
- receipt bundles
- proof summaries
- proof export
- rollback execution for supported receipts

This is enough to say the repo has meaningful verifiable-execution primitives. It is not enough to say the full verifiable story is done. The current proof baseline is hash-linked events plus sealed receipt bundles; stronger signed receipts and inclusion-proof exports are available only when local signing is configured, and are surfaced as conditional capability plus task-level proof coverage or missing proof coverage rather than implied completeness.

See [receipts-and-proofs.md](./receipts-and-proofs.md).

## Operator Surface

Hermit's seriousness is visible not just in internal modules, but in the operator-facing CLI:

- `hermit task list`
- `hermit task show`
- `hermit task events`
- `hermit task receipts`
- `hermit task proof`
- `hermit task proof-export`
- `hermit task approve`
- `hermit task rollback`

These commands give the operator direct access to the kernel ledger and its control surfaces.

## Current Implementation Vs Target Architecture

The boundary is important:

- this document describes the current repository structure and behavior
- the `v0.1` spec defines the target architecture and invariants

Current implementation:

- real kernel objects
- real policy and approval flow
- real receipts and proof primitives
- real rollback support for selected actions
- kernel-first hard gates for builtin, delegation, and MCP tool governance
- mixed runtime and kernel-era surfaces
- an explicit [kernel conformance matrix](./kernel-conformance-matrix-v0.1.md) plus claim gate surfaces that are checked against machine-readable claim rows

Target architecture:

- deeper task-first unification across all ingress paths
- stricter governance coverage
- broader receipt and rollback semantics
- stronger artifact-native context and evidence discipline everywhere

Read next:

- [why-hermit.md](./why-hermit.md)
- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md)
- [kernel-conformance-matrix-v0.1.md](./kernel-conformance-matrix-v0.1.md)
- [governance.md](./governance.md)
- [roadmap.md](./roadmap.md)
