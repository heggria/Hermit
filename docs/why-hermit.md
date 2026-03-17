---
description: "Why Hermit exists: task-first durable work, governed execution with approvals, receipts and proofs, evidence-bound memory, and local-first trust."
---

# Why Hermit

Hermit exists because many agents are optimized for responsiveness, but not for durable trust.

Most agents treat a request as a conversation turn. Hermit treats meaningful work as a task with state, authority, evidence, and outcome.

Most agents treat tool execution as the key event. Hermit treats tool execution as one stage in a larger governed path:

`request -> task -> step attempt -> policy -> approval -> scoped execution -> receipt -> proof / rollback`

This matters when the work is long-running, interruptible, approval-sensitive, or worth auditing later.

Hermit is not trying to be everything an agent platform can be. It is trying to make one category of agent work unusually legible:

- local-first
- stateful across time
- governed at the execution boundary
- inspectable after the fact
- recoverable when things go wrong

## The Problem With Session-First Agents

Session-first agents are often good at staying conversational. They are much less consistent at staying accountable.

In many systems:

- the unit of work is a chat turn
- side effects happen under broad process authority
- context is mostly message history
- memory is loosely attached
- auditability is reconstructed from logs after the fact

That works for lightweight assistance. It breaks down when the operator later asks:

- What exactly happened?
- Why did it happen?
- What evidence was used?
- What authority allowed it?
- What changed?
- Can the action be verified or rolled back?

## Hermit's Thesis

Hermit is built around a different thesis.

### 1. Tasks Are The Durable Unit Of Work

Hermit is not session-first. Work should land in durable task semantics that can survive pauses, approvals, follow-ups, and inspection.

This is why the kernel centers on records such as:

- `Task`
- `Step`
- `StepAttempt`
- `Ingress`
- `Conversation`

### 2. Execution Must Be Governed

The model may reason, plan, and propose. It should not silently inherit broad execution authority.

Hermit pushes effectful work through:

- policy evaluation
- decision recording
- approval when required
- scoped authority records such as capability grants and workspace leases

The point is not merely to say "human-in-the-loop." The point is to make authority explicit.

### 3. Artifacts Matter More Than Transcript Alone

Message history is useful. It is not enough.

Hermit treats artifacts as first-class units of context and evidence. A task should be explainable in terms of what it read, what it produced, what it observed, and what it cited.

This is why context in Hermit is moving toward:

- artifact references
- working state
- beliefs
- durable memory records
- task and step summaries

## 4. Memory Must Be Evidence-Bound

Hermit does not treat memory as a generic sticky-note system.

It separates:

- bounded working state
- revisable beliefs
- durable memory records

Durable memory promotion should cite evidence and obey scope, retention, and invalidation rules. This matters because memory without provenance becomes hidden authority.

## 5. Important Actions End With Receipts

Tool execution is not the finish line.

For important actions, Hermit wants the kernel to retain a structured account of:

- inputs
- outputs
- policy result
- approvals
- capability grants and workspace leases
- execution environment
- result summary
- rollback relationship when supported

That is the role of the receipt and proof path.

## What Hermit Already Ships

Hermit is early, but the repo is not empty rhetoric.

Today the codebase already contains:

- a local kernel ledger
- first-class records for tasks, approvals, principals, capability grants, workspace leases, receipts, beliefs, memory records, rollbacks, conversations, and ingresses
- governed executor paths with policy and approval handling
- proof summaries, proof export, and rollback support for selected receipts
- context compilation and memory governance primitives

What this means:

- Hermit can already be described as a local-first governed agent kernel
- Hermit should not yet be described as if every runtime surface fully matches the target spec

## What Hermit Is Not

Hermit is not best understood as:

- just another chat-plus-tools shell
- a cloud-first opaque agent service
- a no-tradeoff autonomous agent platform
- a finished `1.0` kernel

Hermit is better understood as an alpha system with a strong kernel thesis and a codebase that already makes that thesis visible.

## Read Next

- [architecture.md](./architecture.md) for the current implementation
- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md) for the target architecture
- [governance.md](./governance.md) for policy, approvals, and scoped authority
- [receipts-and-proofs.md](./receipts-and-proofs.md) for completion, verification, and rollback semantics
