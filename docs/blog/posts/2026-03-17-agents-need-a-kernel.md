---
date: 2026-03-17
authors:
  - beta
categories:
  - Philosophy
tags:
  - governance
  - kernel
  - local-first
  - design
slug: agents-need-a-kernel
description: "Capability and trustworthiness are not the same axis. Why AI agents need a kernel for governed execution, not just better prompts or frameworks."
schema_type: article
---

# Agents Don't Need Better Prompts. They Need a Kernel.

There is a quiet assumption running through most of the AI agent ecosystem: that the hard problem is making agents more capable. Better tool use, longer context, smarter planning, more autonomous execution. The implicit promise is that if we just make agents good enough, they will become trustworthy enough.

This assumption is wrong. Capability and trustworthiness are not the same axis.

<!-- more -->

A more capable agent that deletes the wrong file is not better than a less capable one that asks first. An agent that executes flawlessly but leaves no inspectable record of what it did is not trustworthy — it is merely lucky. The gap between "the agent did something" and "I can explain, verify, and if necessary undo what the agent did" is not closed by better prompts. It is closed by better semantics.

This is why we built Hermit. Not as another framework for composing agents, but as a kernel for governing their execution.

## The Session Illusion

Most agent systems are organized around sessions. A user opens a conversation, the model responds, tools execute, and the session ends. This is natural. It mirrors how we interact with chatbots, and it works well for lightweight assistance.

But sessions are an illusion of completeness. They give you the feeling that work happened, without giving you the structure to answer basic questions about it afterward:

- What exactly did the agent do?
- Under whose authority?
- Based on what evidence?
- Can it be verified?
- Can it be undone?

These are not edge-case questions. They are the questions that any operator asks the moment agent work becomes consequential — the moment it touches production systems, modifies real files, or makes decisions that cost money.

Session-first systems treat these questions as afterthoughts. They offer logs, maybe a transcript, maybe a dashboard with metrics. But logs are oriented toward debugging, not verification. Transcripts blend thinking with acting. Dashboards show that things happened, not why they were allowed to happen.

The problem is not that session-first systems are badly built. The problem is that sessions are the wrong primitive for accountable work.

## The Kernel Thesis

A framework gives you structure for composing agents. A kernel gives you semantics for governing execution. The difference is subtle but load-bearing.

A framework says: here is how you wire a model to tools, manage context, and handle retries. A kernel says: here is how authority is scoped, how side effects are gated, how outcomes are recorded, and how the operator can later inspect or reverse what happened.

You cannot bolt these properties onto a system whose fundamental unit of work is a chat turn. Receipts require a notion of "completed action." Scoped authority requires a notion of "granted capability." Rollback requires a notion of "reversible effect." Durable inspection requires a notion of "task with lifecycle." None of these emerge naturally from a conversation loop.

Hermit's thesis rests on five commitments:

**Tasks, not sessions.** The durable unit of work is a task — something with identity, lifecycle, ingress, steps, and outcomes. Conversations are how the operator communicates intent. Tasks are how the kernel tracks execution. Tasks survive sessions, approvals, pauses, and failures.

**Governed execution, not ambient authority.** Between the model's proposal and actual execution, the kernel evaluates policy, records decisions, and issues scoped capability grants. This is not "human-in-the-loop" — a binary approve/reject prompt that treats all actions as equally risky. It is structured governance: different actions face different policies, approvals are recorded with their reasoning, and authority is scoped in time and capability.

**Receipts, not logs.** When the agent does something consequential, the kernel produces a receipt — a structured record of inputs, outputs, policy evaluation, capability grants, and rollback relationship. Receipts enable proof bundles (self-contained, independently verifiable summaries) and rollback (structured reversal of receipted actions). Logs tell you something happened. Receipts tell you what happened, why it was authorized, and whether it can be undone.

**Local-first, not cloud-first.** The kernel ledger lives on the operator's machine. There is no ambiguity about who controls the data, no dependency on a service provider's continuity, no trust boundary extending to unknown infrastructure. For work that accumulates sensitive state — approvals, execution history, credentials, memory — operator custody is not a limitation. It is a feature.

**Evidence-bound memory, not sticky notes.** Memory without provenance is hidden authority. An agent that "remembers" something from three weeks ago and acts on it without knowing why it was stored, whether it is still valid, or what evidence supported it, is not recalling — it is hallucinating with extra steps. Hermit requires that durable memory promotion cite evidence. When evidence is invalidated, the memory can be flagged, re-evaluated, or removed.

## What This Actually Looks Like

This is not abstract. In Hermit, when an agent proposes to execute a tool, the following happens:

```
Task → Step → StepAttempt → Policy Evaluation → Decision → Approval
→ Capability Grant → Workspace Lease → Execution → Receipt → Proof / Rollback
```

Each stage produces a kernel record. The result is not just that the action happened, but that the full authorization chain is preserved and inspectable. An operator who was not watching in real time can pick up the task later, examine its receipts, and understand the complete chain of consequential actions.

This is the difference between "the agent ran `rm -rf build/`" and "the agent requested file deletion under task T-42, which was evaluated against policy P-3, approved by the operator at 14:32, granted capability C-7 scoped to the `build/` directory with a 5-minute lease, executed at 14:32:15 producing receipt R-891, which supports rollback via the recorded file manifest."

The first is a log line. The second is an audit trail.

## The Price of Governance

Hermit's design imposes real costs, and it would be dishonest to pretend otherwise.

**More ceremony.** Recording tasks, evaluating policy, issuing capability grants, producing receipts — this requires machinery. For quick questions or casual automation, this overhead is unjustified. Hermit is not the right tool for asking an LLM to fix a typo.

**Less convenience.** Local-first means no automatic sync, no web dashboard, no multi-device access out of the box. Governed execution means the agent cannot just act; it must be authorized. Evidence-bound memory means you cannot casually persist information without grounding it.

**Cognitive load.** Tasks, steps, attempts, capability grants, workspace leases, receipts, beliefs, proofs — these are real concepts that require learning. A simpler system would not demand this vocabulary.

**Narrower audience.** Most agent work does not need this. Hermit deliberately targets a subset: long-running, trust-heavy, approval-sensitive, developer-grade work. Users outside that subset will find the overhead unjustified. That is by design.

These costs are not bugs. They are the price of the properties Hermit provides. The bet is that for the right category of work — work where "what happened and why" matters more than "how fast can we ship" — those properties are worth the price.

## Why Now

We are at an inflection point. Models are becoming capable enough to perform real, consequential work autonomously. They can write code, modify infrastructure, manage deployments, handle customer interactions. The capability curve is steep and accelerating.

But the governance curve is flat. The tools for controlling, inspecting, and auditing agent work have not kept pace with the tools for enabling it. We are building increasingly powerful agents and running them on the same trust model as a chat widget: hope it works, check the logs if it doesn't.

This gap will not close by itself. Making models smarter does not make their execution more inspectable. Adding more tools does not make authority more structured. Scaling autonomy without scaling governance is how you get systems that are powerful and opaque — the worst combination for consequential work.

Hermit is early. The kernel spec is a draft. Not every surface fully implements every semantic. The ecosystem is small. But the thesis is implemented in working code, and the direction is clear: agent work that is worth doing is worth governing.

The question is not whether agents will become more autonomous. They will. The question is whether, when they do, the operator will still be able to answer: what happened, why was it allowed, and can it be undone.

That is a kernel question. And it deserves a kernel answer.

---

*Hermit is an open-source, local-first governed agent kernel. It is alpha software with a strong thesis and working code. If you are building agent work that needs to be inspectable, auditable, and recoverable, [take a look](https://github.com/heggria/Hermit).*
