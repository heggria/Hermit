---
description: "How Hermit compares to generic agent frameworks: task-first vs session-first, governed execution vs ambient authority, receipts vs logs."
---

# Framework Comparison

This document offers an honest comparison between Hermit and other agent frameworks. Hermit is currently in **alpha** and is architecturally different from most frameworks in this space. The goal here is not to claim superiority, but to help you understand where Hermit's design choices lead to genuine advantages and where other frameworks are the better pick.

## Comparison Matrix

| Dimension | Hermit | LangChain | AutoGen | CrewAI | OpenAI Agents SDK |
|---|---|---|---|---|---|
| **Execution Model** | Task-first kernel: Task -> Step -> Policy -> Approval -> Execution -> Receipt. Models propose, the kernel authorizes. No direct model-to-tool execution. | Chain/graph-based (LCEL, LangGraph). Flexible composition of prompts, tools, and retrieval steps. | Multi-agent conversation. Agents exchange messages to collaborate on tasks. | Role-based crew orchestration. Agents with defined roles, goals, and backstories coordinate sequentially or in parallel. | Lightweight agent loop with handoffs between agents. Tool calls executed inline. |
| **Governance (Approvals / Policy)** | First-class. Policy evaluation, approval workflows, capability grants, and permits are core kernel primitives. Every tool invocation passes through a governance gate. | Not built-in. Can be added via callbacks or custom chains, but governance is application-level. | Not built-in. Human-in-the-loop is supported via conversation flow, but there is no structured policy layer. | Not built-in. Task delegation follows role definitions, but no formal approval or policy enforcement. | Guardrails for input/output validation. No structured approval workflow or policy engine. |
| **Persistence (Task Durability)** | Durable by design. SQLite-backed ledger with event sourcing. Tasks, steps, and attempts survive crashes and restarts. | Ephemeral by default. LangGraph adds checkpointing for graph state persistence. | Ephemeral. Conversation history lives in memory unless manually persisted. | Ephemeral. Task results can be saved, but no built-in durable task state. | Ephemeral. Conversation state is in-memory; persistence is left to the application. |
| **Rollback Support** | Built-in. Receipts carry rollback metadata. The kernel can reverse supported operations using proof-linked rollback execution. | None built-in. | None built-in. | None built-in. | None built-in. |
| **Local-First Design** | Core principle. All state lives in `~/.hermit`. No cloud dependency for kernel operation. Runs entirely on the operator's machine. | Cloud-oriented. Relies on external APIs and services. Local execution is possible but not the primary design target. | Cloud-oriented. Designed around API-connected agents. Local operation requires manual setup. | Cloud-oriented. Agents typically call cloud LLM APIs. No local-first state model. | Cloud-first. Tied to the OpenAI API. Local-first operation is not a design goal. |
| **Receipts / Audit Trail** | First-class. Every governed execution produces receipts and proof bundles. Proof chains provide verifiable execution history. | Not built-in. Tracing available via LangSmith (cloud service). | Not built-in. Conversation logs serve as informal history. | Not built-in. Task output logging is available but not structured as an audit trail. | Not built-in. Tracing is available but not structured as receipts or proofs. |
| **Memory Model** | Evidence-bound. Memory promotion requires evidence references. Memories are governed artifacts, not free-form sticky notes. | Multiple memory types (buffer, summary, vector). Flexible but ungoverned -- any content can be stored. | Conversational memory via message history. No structured governance over what enters memory. | Short-term and long-term memory. Crew-level shared memory. No evidence requirements. | No built-in memory beyond conversation context. |
| **Context Model** | Artifact-native. Context is compiled from artifacts with lineage tracking, not reconstructed from raw message history. | Message-history-based with retrieval augmentation (RAG). Context is assembled from chains of messages and retrieved documents. | Message-history-based. Context flows through agent conversation turns. | Task and role descriptions form context. Message history augmented with tool results. | Message-history-based. Context is the conversation thread plus tool call results. |

## When to Choose Hermit

- **You need operator trust guarantees.** Hermit's governance layer ensures every tool invocation is policy-evaluated and approved before execution. If your use case requires that a human (or a policy) explicitly authorizes actions, Hermit was built for this.

- **Audit trails are non-negotiable.** Receipts and proof bundles provide a verifiable record of what happened, why it was authorized, and how to reverse it. This matters for compliance-sensitive or high-stakes workflows.

- **You want local-first operation.** All state lives on your machine. No cloud accounts, no telemetry, no external dependencies for core kernel operation. You own your data and execution history.

- **Your tasks are long-running or must survive restarts.** Hermit's durable task model with event-sourced state means work-in-progress is not lost to a process crash or system reboot.

- **Rollback capability matters.** When an agent action needs to be reversed, Hermit's receipt-linked rollback system provides structured undo, rather than requiring you to manually clean up.

- **Memory should be trustworthy.** Evidence-bound memory means the kernel tracks why something was remembered, not just what. This prevents memory pollution and supports downstream verification.

## When to Choose Something Else

- **You need a mature, production-hardened ecosystem.** LangChain, AutoGen, and CrewAI have large communities, extensive integrations, and battle-tested deployment patterns. Hermit is alpha software with a small surface area.

- **Cloud-first or multi-tenant is your deployment model.** Hermit is designed for single-operator, local-first use. If you need multi-tenant SaaS deployment, managed infrastructure, or horizontal scaling, frameworks with cloud-native architectures are a better fit.

- **Rapid prototyping and experimentation.** LangChain's composability and CrewAI's role-based abstractions let you sketch agent workflows quickly. Hermit's governance layer adds rigor that slows down casual experimentation.

- **You want broad model and tool integrations out of the box.** LangChain and CrewAI offer hundreds of pre-built integrations. Hermit's plugin ecosystem is young and focused on core primitives.

- **Multi-agent conversation is your primary pattern.** AutoGen and CrewAI are purpose-built for multi-agent collaboration. Hermit's kernel is task-first, not conversation-first, and its multi-agent story is still developing.

- **You want managed observability.** LangSmith, AgentOps, and similar platforms provide cloud-hosted tracing and analytics for LangChain and CrewAI. Hermit's audit trail is powerful but local.

## Closing

Hermit is alpha software. Its kernel thesis -- governed, local-first, receipt-aware, evidence-bound execution -- is distinct from the session-oriented, cloud-first approach taken by most agent frameworks. The comparison above reflects the current state of all projects mentioned and will evolve as each matures.

If the idea of a governed agent kernel resonates with your requirements, Hermit is worth evaluating. If you need production stability and ecosystem breadth today, the other frameworks listed here are more appropriate choices.
