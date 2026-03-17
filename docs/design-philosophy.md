# Design Philosophy

This document explores the reasoning behind Hermit's design decisions. It is not a feature list or an architecture diagram. It is an attempt to explain why Hermit is shaped the way it is, what problems it is reacting to, and what it deliberately gives up.

For a shorter summary of the thesis, see [why-hermit.md](./why-hermit.md). For the target architecture, see [kernel-spec-v0.1.md](./kernel-spec-v0.1.md).

---

## 1. Why Another Kernel

The agent ecosystem is not short on frameworks. There are orchestration libraries, tool-calling wrappers, prompt chaining systems, and cloud platforms that will happily manage the full lifecycle of an agent for you. The reasonable question is: why build another one?

The answer is that most of those systems are frameworks, not kernels. A framework gives you structure for composing agents. A kernel gives you semantics for governing execution. The difference matters when the work is consequential.

A framework says: here is how you wire a model to tools, manage context windows, and handle retries. A kernel says: here is how authority is scoped, how side effects are gated, how outcomes are recorded, and how the operator can later inspect or reverse what happened.

Hermit exists because the gap between "the agent did something" and "I can explain, verify, and if necessary undo what the agent did" is not a framework-level problem. It is a semantic problem. You cannot bolt receipts, scoped authority, and durable task state onto a system whose fundamental unit of work is a chat turn. Those properties need to be load-bearing from the start.

This is not an argument that frameworks are bad. They are appropriate for a wide range of agent work. Hermit is aimed at a narrower category: work that is long-running, approval-sensitive, worth auditing, and operated by someone who needs to trust the system without watching it continuously. For that category, the right primitive is not a better framework. It is a kernel that treats governance as a first-class concern.

## 2. Why Task-First

Most agent systems are organized around sessions or conversations. The user opens a chat, the model responds, tools execute, and the session ends. If something needs to continue later, you either keep the session alive or reconstruct enough context to start a new one.

This works when the unit of work fits inside a single interaction. It breaks when it does not.

Consider work that requires an approval before proceeding, or work that spans multiple days, or work that needs to be resumed after a failure. In a session-first system, these are edge cases that require workarounds: persisted session state, polling loops, external coordination. In a task-first system, they are the normal case. A task has a lifecycle. It can be created, advanced, suspended, resumed, and completed. It survives the session that created it.

The deeper issue is identity. In a session-first system, the unit of work is the conversation. But conversations are poor containers for accountability. They blend planning with execution, mix exploratory reasoning with consequential action, and offer no clean boundary between "the model was thinking" and "the model changed something." A task, by contrast, is a named commitment. It has ingress (where it came from), steps (what it tried to do), attempts (how each step was executed), and outcomes (what actually happened). That structure is not bureaucratic overhead. It is the minimum scaffolding needed to answer "what happened and why" after the fact.

Hermit does not prevent conversational interaction. It still supports `chat` and `run` surfaces. But underneath, meaningful work lands in durable task records. The conversation is how the operator communicates intent. The task is how the kernel tracks execution.

## 3. Why Receipts

In most agent systems, tool execution is the terminal event. The model calls a tool, the tool returns a result, and the system moves on. If you want to know what happened, you read the logs.

Logs are necessary but insufficient. They are unstructured, append-only, and oriented toward debugging rather than verification. A log entry tells you that something happened. A receipt tells you what happened, under what authority, with what inputs, producing what outputs, and whether the action can be reversed.

Hermit's receipt model is designed around a specific need: post-hoc inspection by someone who was not watching in real time. An operator should be able to pick up a task, examine its receipts, and understand the full chain of consequential actions without reconstructing the story from scattered log lines.

Receipts also enable two capabilities that logs structurally cannot: proof bundles and rollback. A proof bundle is a self-contained summary that can be verified independently. It collects the receipts, the policy decisions, the approval records, and the capability grants that authorized a sequence of actions. A rollback is a structured reversal of a receipted action. Not every action supports rollback, but for those that do, the receipt carries the information needed to undo the effect.

This is not about distrust. It is about operating in a regime where the agent acts with real authority and the operator needs to maintain real oversight without hovering over every action. Receipts are the mechanism that makes that possible.

## 4. Why Local-First

The default assumption in modern software is cloud-first. Store state in a remote database. Run computation on managed infrastructure. Centralize control so that updates, monitoring, and access management happen in one place.

Hermit takes the opposite position. The kernel ledger is a local SQLite database. State lives in `~/.hermit`. The agent runs on the operator's machine. This is not an accident or a temporary limitation. It is a deliberate design choice with specific trust implications.

Local-first means the operator has physical custody of their data. There is no question about who can access the kernel ledger, because it is a file on the operator's disk. There is no question about data residency, because the data does not leave the machine unless the operator sends it somewhere. There is no question about service continuity, because the kernel does not depend on a remote API being available (the LLM provider is a separate concern; the kernel itself is fully local).

This matters more than it might seem. Agent systems that handle consequential work accumulate sensitive state: approval records, execution history, memory, credentials. When that state lives in a cloud service, the operator's trust model includes every employee, contractor, and security practice of the service provider. When the state lives locally, the trust boundary is the operator's own machine.

Local-first also changes the failure model. A cloud service can be deprecated, repriced, or shut down. A local kernel is as durable as the filesystem it runs on. For work that matters, the operator should not have to wonder whether their execution history will still be accessible next year.

The cost is real: no automatic sync, no built-in multi-device access, no managed backups. Hermit accepts those costs because, for its target use case, operator custody of state is more important than operational convenience.

## 5. Governed Execution

The simplest form of agent safety is "human-in-the-loop": before the agent does something important, ask the human. This is better than nothing. It is also a blunt instrument.

Human-in-the-loop confirmation treats every action as equally risky and offers only a binary choice: approve or reject. It does not distinguish between an action that reads a file and an action that deletes a database. It does not record the basis for approval. It does not scope the authority granted. It does not compose: if you approve ten actions, you get ten independent yes/no prompts with no shared policy.

Hermit's governance model is more structured. Between the model's proposal and actual execution, the kernel evaluates policy, records decisions, and issues scoped capability grants. Policy evaluation can produce different outcomes: auto-approve for low-risk actions, require explicit approval for high-risk ones, deny actions that violate constraints. Capability grants are scoped: they specify what the agent is authorized to do, for how long, and within what boundaries. Decisions are recorded: the kernel retains why an action was approved, not just that it was.

This is the difference between "the human said yes" and "the action was authorized under policy P, approved by principal X, with capability grant G scoped to workspace W, and the decision was recorded as event E in the kernel ledger." The first is a UX pattern. The second is an audit trail.

The execution path in Hermit reflects this: task, step, step attempt, policy evaluation, decision, approval (when required), capability grant, workspace lease, execution, receipt, proof or rollback. Each stage produces a kernel record. The result is not just that the action happened, but that the full authorization chain is preserved and inspectable.

## 6. Evidence-Bound Memory

Memory in most agent systems is a convenience feature. The agent remembers things from previous conversations and uses them to provide better responses. The memory store is typically an unstructured collection of text snippets, retrieved by semantic similarity and injected into context.

This is useful for personalization. It is dangerous for consequential work.

The problem is provenance. When memory lacks provenance, the agent treats recalled information with the same confidence as fresh observation. A memory that was accurate last week might be stale today. A memory that was inferred from ambiguous evidence might be treated as established fact. A memory that was appropriate in one context might be misleading in another.

Hermit addresses this by separating memory into three tiers: bounded working state (short-lived, scoped to the current task), revisable beliefs (medium-term, subject to confidence and invalidation), and durable memory records (long-lived, requiring evidence references for promotion).

The key constraint is that durable memory promotion must cite evidence. You cannot write a permanent memory record without pointing to the artifact, receipt, or observation that supports it. This is not a UX feature. It is a governance mechanism. It means that when the agent later retrieves a memory and uses it to inform a decision, there is a traceable path back to the evidence that justified storing that memory in the first place.

The risks of ungoverned memory are subtle but compounding. Over time, an agent accumulates beliefs that influence its behavior. If those beliefs were never validated, or if they were valid once but are now stale, the agent's reasoning degrades in ways that are difficult to diagnose. Evidence-bound memory does not eliminate this risk, but it makes it visible. When a memory's evidence is invalidated or expired, the memory can be flagged, re-evaluated, or removed. Without evidence binding, stale memories are invisible until they cause a failure.

## 7. The Honest Cost

Hermit's design imposes real costs. It would be dishonest to describe the philosophy without acknowledging them.

**More ceremony.** A system that records tasks, evaluates policy, issues capability grants, and produces receipts requires more machinery than one that just calls tools. For simple, low-stakes work, this overhead is not justified. Hermit is not the right tool for quick questions or casual automation. It is designed for work where the ceremony pays for itself in auditability and trust.

**Less immediate convenience.** Local-first means no automatic sync, no web dashboard, no multi-device access out of the box. Governed execution means the agent cannot just act; it must be authorized to act. Evidence-bound memory means you cannot casually persist information without grounding it. Each of these constraints trades convenience for a property that Hermit considers more important, but the trade is real.

**Alpha-stage ecosystem.** Hermit is early. The kernel spec is a draft. Not every runtime surface fully implements every kernel semantic. The plugin ecosystem is small. Documentation is incomplete. The project is usable and the thesis is implemented in working code, but it is not mature. Users should expect rough edges, breaking changes, and gaps between the described architecture and the current state.

**Narrower audience.** Most agent work does not need governed execution or receipt-based auditing. Hermit deliberately targets a subset of use cases: long-running, trust-heavy, approval-sensitive, developer-grade work. Users outside that subset will find Hermit's overhead unjustified. That is by design, not by oversight.

**Cognitive load.** The kernel introduces concepts (tasks, steps, attempts, capability grants, workspace leases, receipts, beliefs, proofs) that a simpler system would not require. Understanding what the kernel is doing requires learning its vocabulary. This is a real barrier to adoption, and the project bears the responsibility of making these concepts as clear and as well-documented as possible.

These costs are not bugs. They are the price of the properties Hermit is designed to provide. The bet is that for the right category of work, those properties are worth the price. Whether that bet pays off is an open question. Hermit is honest about that.

---

## Read Next

- [why-hermit.md](./why-hermit.md) for a shorter thesis summary
- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md) for the target kernel architecture
- [architecture.md](./architecture.md) for the current implementation
- [governance.md](./governance.md) for policy, approvals, and scoped authority
- [receipts-and-proofs.md](./receipts-and-proofs.md) for completion, verification, and rollback semantics
