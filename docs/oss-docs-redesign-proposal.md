# Hermit Documentation System Redesign Proposal

This document is a PR-ready narrative and information architecture proposal for repositioning Hermit as a local-first governed agent kernel.

It is grounded in the current repository state:

- the repo already contains a task kernel, ledger, receipts, approvals, permits, proofs, and rollback primitives
- the repo still carries runtime-era framing in places such as the root README and package metadata
- the spec is ahead of the shipping story, but not detached from the codebase

The goal is to fix that gap without turning the project into a marketing page or a research paper.

## Part A. Documentation Strategy

### What the docs must solve now

1. Hermit still introduces itself too much like a local personal agent runtime and not enough like a governed kernel.
2. The repository already contains kernel-grade primitives, but the first three screens of the current README do not make that legible.
3. The narrative boundary between current implementation and target architecture is not yet sharp enough.
4. Newcomers can see many features, but not the thesis that makes the repo structurally different.
5. Advanced readers can find the spec, but they do not get a fast, credible bridge from README to real implementation.
6. The docs currently over-index on surfaces and operations, and under-index on object model, execution law, and trust model.
7. The term "local-first" is present, but the stronger differentiators are governed execution, artifact-native context, receipts, and rollback.
8. There is no clear documentation ladder for three audiences: newcomer, builder, and operator/trust-oriented reader.
9. Current wording across README, `AGENT.md`, and `pyproject.toml` is not fully aligned, which weakens external confidence.
10. Hermit needs a doc system that can support the march from alpha kernel to spec `0.1` and later `1.0` without rewriting the whole site again.

### Recommended external positioning

Hermit should be positioned as:

**A local-first governed agent kernel for durable, auditable, artifact-native work.**

Expanded version:

**Hermit is not just a chat shell with tools. It is a local-first governed agent kernel where work is task-first, execution is approval-aware, context is artifact-native, memory is evidence-bound, and important actions close with receipts.**

This is the center of gravity to use across the repo.

The secondary framing can remain:

- local-first runtime
- operator-trust-oriented agent system
- alpha kernel for governed execution

But those should support the kernel thesis, not replace it.

### Five tagline candidates

1. **Hermit: a local-first governed agent kernel**
2. **Hermit: task-first agents with approvals, receipts, and rollback**
3. **Hermit: a local-first kernel for durable and auditable agent work**
4. **Hermit: not just tool-calling agents, but governed execution**
5. **Hermit: artifact-native, receipt-aware, local-first agent infrastructure**

Recommendation:

- use `Hermit: a local-first governed agent kernel` as the main tagline
- use the others as section headers, social copy, and quote lines

### How Hermit should be differentiated

Against reference projects, Hermit should emphasize structure, not vague superiority:

- **vs OpenHands**: OpenHands is framed around agents that act like software developers. Hermit should be framed around a kernel that governs durable work, execution authority, and post-hoc audit. The emphasis is not the developer persona. It is the execution law.
- **vs LangGraph**: LangGraph is strong at stateful agent orchestration graphs. Hermit should emphasize that its primary abstraction is not graph composition alone, but a governed task ledger with approvals, receipts, capability grants, artifacts, and rollback semantics.
- **vs Letta**: Letta is memory-centric. Hermit should stress that memory is one governed object among several, and that memory promotion, retrieval, invalidation, and evidence are tied to task execution and artifact provenance.
- **vs OpenClaw-class local agents**: Hermit should not present itself as "another local assistant." It should stress that task, step attempt, approval, receipt, proof, rollback, and artifact-native context are first-class kernel semantics, not add-on UI behaviors.

### Who the repo should attract

1. **GitHub passerby**
   Goal: understand what Hermit is in 20 seconds and why it is not a commodity agent wrapper.
   Hook: local-first, governed, auditable, task-first.

2. **Advanced builder / agent developer**
   Goal: decide in 2 minutes whether Hermit has a real systems thesis and real primitives.
   Hook: task ledger, event-backed truth, approvals, capability grants, receipts, proofs, rollback, artifact-native context.

3. **Trust / governance / operator-minded reader**
   Goal: decide whether Hermit is serious about visibility, control, and recovery.
   Hook: scoped authority, evidence-bound memory, proof bundles, event hash chain, rollback support, task inspection and approval CLI.

### Three narrative routes

#### Route A. Governed kernel first

Lead with: "Hermit is a local-first governed agent kernel."

Then:

- explain why most agents are session-first and ambient-authority-heavy
- show Hermit's task-first and governed execution model
- then show current surfaces and quick start

Pros:

- strongest differentiation
- best for serious builders and technical amplification
- makes the repo memorable

Cons:

- requires careful control of jargon in the first screen

#### Route B. Local-first agent runtime first

Lead with: "Hermit is a local-first personal AI runtime that is evolving into a governed kernel."

Then:

- show runtime surfaces first
- transition into kernel thesis

Pros:

- easier for newcomers

Cons:

- weaker first-screen identity
- too easy to collapse back into a crowded category

#### Route C. Verifiable execution first

Lead with: "Hermit is building verifiable local agent execution with receipts, proofs, and rollback."

Then:

- explain task kernel and governance
- show current implementation

Pros:

- high intrigue
- good for social sharing and technical discussion

Cons:

- risks over-claiming if not carefully bounded

### Recommended route

Use **Route A: governed kernel first**.

Reason:

Hermit's strongest strategic asset is not that it is local, or that it has a CLI, or that it supports channels. Its strongest asset is that it is building a coherent kernel law for durable agent work. The docs should make that obvious immediately.

### What can be said boldly

- Hermit is a local-first governed agent kernel.
- Hermit already ships task, step, step-attempt, approval, decision, permit, path grant, receipt, proof, rollback, belief, memory, conversation, and ingress records in the kernel ledger.
- Hermit treats artifacts as first-class context and evidence.
- Hermit routes consequential actions through policy, approvals, and scoped authority.
- Hermit supports operator surfaces for task inspection, proof export, approval resolution, and rollback execution.
- Hermit is converging toward the `v0.1` kernel spec.

### What must be said carefully

- Do not say Hermit is fully event-sourced everywhere. Say the kernel uses event-backed truth and ledgered projections, while the broader runtime still includes pre-kernel surfaces.
- Do not say verifiable execution is complete. Say Hermit already ships proof primitives, proof summaries, receipt bundles, and event-chain verification, but the full verifiable story is still maturing.
- Do not say rollback is universal. Say rollback exists as a first-class object and executable path for supported receipts.
- Do not say ambient authority is fully eliminated repo-wide. Say Hermit is moving execution toward scoped authority and governed paths in the kernel.
- Do not present the `v0.1` spec as shipped. Present it as the target architecture that the current codebase is actively converging toward.

## Part B. Documentation Information Architecture

### Proposed docs tree

```text
.
├── README.md                                   P0
├── CONTRIBUTING.md                             P0
├── docs/
│   ├── getting-started.md                      P0
│   ├── why-hermit.md                           P0
│   ├── architecture.md                         P0
│   ├── kernel-spec-v0.1.md                     P0
│   ├── governance.md                           P0
│   ├── receipts-and-proofs.md                  P0
│   ├── context-model.md                        P0
│   ├── memory-model.md                         P0
│   ├── roadmap.md                              P0
│   ├── use-cases.md                            P1
│   ├── openclaw-comparison.md                  P1
│   ├── task-lifecycle.md                       P1
│   ├── operator-guide.md                       P1
│   ├── cli-and-operations.md                   P1
│   ├── configuration.md                        P1
│   ├── providers-and-profiles.md               P1
│   ├── repository-layout.md                    P2
│   ├── faq.md                                  P1
│   ├── status-and-compatibility.md             P1
│   ├── glossary.md                             P2
│   ├── desktop-companion.md                    P2
│   ├── serve-troubleshooting.md                P2
│   ├── feishu-ingress-spec.md                  P2
│   ├── i18n.md                                 P2
│   ├── i18n-roadmap.md                         P2
│   └── hermit-icon.svg
└── docs/oss-docs-redesign-proposal.md
```

### File responsibilities

#### `README.md` — P0

Audience:

- all readers

Solves:

- what Hermit is
- why it matters
- why it is different
- what currently works
- where to go next

Relationship:

- landing page and routing hub

Length:

- medium
- strong first screen
- short architecture section
- diagram optional but useful

#### `docs/getting-started.md` — P0

Audience:

- first-time users and evaluators

Solves:

- install
- init
- first run
- first task
- first approval
- where state lives

Relationship:

- the operational expansion of README quick start

Length:

- short to medium
- screenshot and terminal flow preferred

#### `docs/why-hermit.md` — P0

Audience:

- readers deciding whether the thesis is interesting

Solves:

- why session-first agents are not enough
- why task-first, governed, artifact-native execution matters

Relationship:

- conceptual bridge from README to architecture

Length:

- medium
- prose first
- one comparison table recommended

#### `docs/architecture.md` — P0

Audience:

- advanced builders

Solves:

- the current implementation shape
- major modules
- execution path
- kernel and non-kernel boundary

Relationship:

- must explicitly link to `kernel-spec-v0.1.md` and `status-and-compatibility.md`

Length:

- medium to long
- diagrams strongly recommended

#### `docs/kernel-spec-v0.1.md` — P0

Audience:

- spec readers, maintainers, contributors

Solves:

- target architecture and invariants

Relationship:

- normative target document
- must not duplicate current implementation details

Length:

- long
- law/spec tone

#### `docs/governance.md` — P0

Audience:

- builders, operators, trust-minded readers

Solves:

- policy gates
- approvals
- decisions
- scoped authority
- capability/path grants

Relationship:

- deeper cut from `why-hermit.md`
- implementation bridge to `architecture.md`

Length:

- medium
- sequence diagram preferred

#### `docs/receipts-and-proofs.md` — P0

Audience:

- builders and operators

Solves:

- what counts as a receipt
- proof bundles
- event-chain verification
- observation and uncertain outcomes
- rollback relationship

Relationship:

- directly supports the project's seriousness

Length:

- medium
- object table plus workflow diagram preferred

#### `docs/context-model.md` — P0

Audience:

- advanced builders

Solves:

- why context is artifact-native, not transcript-native
- context pack composition
- working state vs beliefs vs artifacts

Relationship:

- conceptual pair with `memory-model.md`

Length:

- medium
- diagram and object examples preferred

#### `docs/memory-model.md` — P0

Audience:

- readers interested in memory correctness and governance

Solves:

- belief vs memory record
- evidence requirements
- promotion, invalidation, supersession, retrieval

Relationship:

- should explicitly say memory is one governed subsystem within the kernel

Length:

- medium
- lifecycle table preferred

#### `docs/roadmap.md` — P0

Audience:

- contributors, evaluators, maintainers

Solves:

- current status
- near-term milestones
- what is alpha vs target

Relationship:

- the authoritative status doc

Length:

- short to medium
- milestone table preferred

#### `docs/use-cases.md` — P1

Audience:

- newcomers and advocates

Solves:

- what Hermit is actually good for today
- where governed local-first agents matter

Relationship:

- conversion support for README

Length:

- short to medium
- screenshot and walkthrough friendly

#### `docs/openclaw-comparison.md` — P1

Audience:

- readers comparing categories

Solves:

- how Hermit differs from local-first assistant and agent-runtime archetypes

Relationship:

- comparison doc, not the main identity doc

Length:

- short
- table first

#### `docs/task-lifecycle.md` — P1

Audience:

- contributors and operators

Solves:

- task -> step -> step attempt -> approval/decision/permit -> receipt -> proof/rollback

Relationship:

- execution-centric companion to architecture

Length:

- medium
- state machine and sequence diagram first

#### `docs/operator-guide.md` — P1

Audience:

- operators and maintainers

Solves:

- inspect task
- review approvals
- export proofs
- run rollback
- rebuild projections

Relationship:

- maps directly to CLI operator commands

Length:

- medium
- terminal examples first

#### `docs/status-and-compatibility.md` — P1

Audience:

- all readers who want honesty

Solves:

- what is shipped
- what is partial
- what is experimental
- what is target-only

Relationship:

- the anti-overclaiming guardrail for the whole doc system

Length:

- short
- status matrix first

#### `CONTRIBUTING.md` — P0

Audience:

- contributors

Solves:

- what kinds of contributions matter most
- how to propose spec-aligned work
- doc writing rules for current vs target

Relationship:

- should link to roadmap, architecture, kernel spec, and status doc

Length:

- medium

#### `docs/faq.md` — P1

Audience:

- newcomers, skeptics, discussion readers

Solves:

- is this an app or a kernel
- is it production-ready
- how local-first it really is
- why approvals and receipts matter

Relationship:

- discussion and onboarding support

Length:

- short

### P0 / P1 / P2 summary

- **P0**: README, getting-started, why-hermit, architecture, kernel-spec-v0.1, governance, receipts-and-proofs, context-model, memory-model, roadmap, CONTRIBUTING
- **P1**: use-cases, openclaw-comparison, task-lifecycle, operator-guide, cli-and-operations, configuration, providers-and-profiles, faq, status-and-compatibility
- **P2**: repository-layout, glossary, desktop-companion, serve-troubleshooting, feishu-ingress-spec, i18n, i18n-roadmap

## Part C. README Complete Rewrite Draft

```markdown
# Hermit

[![CI](https://github.com/heggria/Hermit/actions/workflows/ci.yml/badge.svg)](https://github.com/heggria/Hermit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-black)](./LICENSE)

> **Hermit is a local-first governed agent kernel.**
>
> It is built for durable tasks, scoped execution, artifact-native context, evidence-bound memory, and important actions that end with receipts instead of hand-wavy tool logs.

Hermit is not just a chat shell with tools. It is an agent runtime moving toward a kernel model where:

- work is **task-first**
- truth is **event-backed**
- execution is **governed**
- context is **artifact-native**
- memory is **evidence-bound**
- side effects close with **receipts, proofs, and rollback hooks**

This matters when you want an agent you can actually inspect, interrupt, approve, audit, and recover.

## Why Hermit

Most agent systems are optimized to be helpful in the moment. Hermit is optimized to stay legible after the moment.

Most agents treat execution as "the model called a tool." Hermit treats execution as a governed path:

`task -> step -> step attempt -> policy -> approval -> scoped authority -> execution -> receipt -> proof / rollback`

The point is not merely to call tools. The point is to make durable work inspectable and controllable.

### Core ideas

- **Task-first kernel**
  Hermit is not session-first. CLI, chat, scheduler, webhook, and adapters are converging on the same task / step / step-attempt semantics.

- **Governed execution**
  The model proposes actions. The kernel decides whether they are allowed, whether they need approval, and what authority envelope they get.

- **Receipts, proofs, rollback**
  Tool execution is not the finish line. Important actions produce receipts. Proof summaries and proof bundles make the action chain inspectable. Supported receipts can be rolled back.

- **Artifact-native context**
  Context is more than transcript history. Hermit compiles context packs from artifacts, working state, beliefs, memory records, and task state.

- **Evidence-bound memory**
  Memory is not an ungoverned scratchpad. Durable memory promotion is tied to evidence, scope, retention, and invalidation rules.

- **Local-first operator trust**
  Hermit keeps the operator close to the runtime: local state, visible artifacts, inspectable ledgers, approval surfaces, and recovery paths.

## What Makes Hermit Different

| Instead of... | Hermit emphasizes... |
| --- | --- |
| chat-first sessions | task-first durable work |
| direct model-to-tool execution | policy, approval, and scoped authority |
| transcript as default context | artifacts, beliefs, working state, and memory records |
| tool logs as "audit" | receipts, proof summaries, and exportable proof bundles |
| memory as sticky notes | evidence-bound memory governance |
| keep-going-at-all-costs execution | observation, resolution, and rollback-aware recovery |

Hermit is not trying to be the most productized agent platform. It is trying to be unusually strong at local-first, trust-heavy, inspectable agent execution.

## Why It Is Worth Watching Now

Hermit is still early, but it is already past the "idea only" stage.

Today the repository already ships:

- a real kernel ledger with first-class records for `Task`, `Step`, `StepAttempt`, `Approval`, `Decision`, `ExecutionPermit`, `PathGrant`, `Artifact`, `Receipt`, `Belief`, `MemoryRecord`, `Rollback`, `Conversation`, and `Ingress`
- event-backed task history with hash-chained verification primitives
- governed tool execution with policy evaluation, approval handling, and scoped permits
- receipt issuance, proof summaries, proof export, and rollback execution for supported receipts
- local-first runtime surfaces across CLI, long-running `serve`, scheduler, webhook, and Feishu ingress

Current state, stated plainly:

- **Core** is close to a claimable alpha kernel
- **Governed execution** is already materially visible in the codebase
- **Verifiable execution** has strong primitives, but should still be treated as in-progress
- the **`v0.1` kernel spec** is the target architecture, not a claim that every surface is already fully migrated

## Quick Start

### Requirements

- Python `3.13+`
- [`uv`](https://docs.astral.sh/uv/) recommended
- macOS only: `rumps` for the optional menu bar companion

### Install

```bash
make install
```

This installs Hermit, initializes `~/.hermit`, and copies the basic local environment when available.

Or do it manually:

```bash
uv sync --group dev --group typecheck --group docs --group security --group release
uv run hermit init
```

### First run

Start an interactive session:

```bash
hermit chat
```

Run a one-shot task:

```bash
hermit run "Summarize the current repository"
```

Start the long-running service:

```bash
hermit serve --adapter feishu
```

Inspect the resolved config:

```bash
hermit config show
```

### Kernel inspection commands

Hermit already exposes operator surfaces for the task kernel:

```bash
hermit task list
hermit task show <task_id>
hermit task events <task_id>
hermit task receipts --task-id <task_id>
hermit task proof <task_id>
hermit task proof-export <task_id>
hermit task approve <approval_id>
hermit task rollback <receipt_id>
```

These commands matter because a task does not end at tool execution; it ends with an inspectable outcome.

## Architecture At A Glance

```text
CLI / Chat / Feishu / Scheduler / Webhook
                  |
                  v
             Task Controller
                  |
                  v
        Task -> Step -> StepAttempt
                  |
                  v
       Context Compiler + Policy Engine
                  |
      +-----------+------------+
      |                        |
      v                        v
 Approval / Decision       Capability Grant
      |                        |
      +-----------+------------+
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

The current repo still contains runtime-era layers and operational surfaces. But the architectural center of gravity is shifting toward the task kernel and its governance law.

For the current implementation, see [docs/architecture.md](./docs/architecture.md).
For the target design, see [docs/kernel-spec-v0.1.md](./docs/kernel-spec-v0.1.md).

## Use Cases

Hermit is especially interesting when the work is:

- long-running
- local-first
- interruptible
- approval-sensitive
- stateful across turns
- worth auditing later

Examples:

- a local coding agent that should ask before writing outside the workspace
- a scheduled assistant that produces artifacts and keeps an inspectable task ledger
- a channel-connected operator assistant where approvals and task continuity matter
- a memory-bearing personal runtime where durable memory should cite evidence

Suggested homepage assets:

- a screenshot of `hermit task show` with approvals / receipts / permits visible
- a short terminal capture of `hermit task proof` and `hermit task rollback`
- an architecture diagram showing the governed execution path

## Documentation Map

- [Getting started](./docs/getting-started.md)
- [Why Hermit](./docs/why-hermit.md)
- [Architecture](./docs/architecture.md)
- [Kernel spec v0.1](./docs/kernel-spec-v0.1.md)
- [Governance](./docs/governance.md)
- [Receipts and proofs](./docs/receipts-and-proofs.md)
- [Context model](./docs/context-model.md)
- [Memory model](./docs/memory-model.md)
- [Use cases](./docs/use-cases.md)
- [Roadmap](./docs/roadmap.md)
- [OpenClaw comparison](./docs/openclaw-comparison.md)
- [CLI and operations](./docs/cli-and-operations.md)
- [Configuration](./docs/configuration.md)
- [FAQ](./docs/faq.md)

## Roadmap

Near-term direction:

- finish converging all ingress paths on task / step / step-attempt semantics
- tighten the governed execution path across more effectful surfaces
- deepen receipt coverage and proof export semantics
- mature rollback support beyond the current supported actions
- make artifact-native context and evidence-bound memory easier to inspect
- align package metadata, docs, and repo language around the kernel thesis

See [docs/roadmap.md](./docs/roadmap.md) for the current status and milestones.

## Contributing

Hermit is still early enough that architecture-sensitive contributions matter.

Good contribution areas:

- task kernel semantics
- governance and approval flow
- receipts, proof export, and rollback coverage
- artifact and context handling
- memory governance
- docs that clarify current state vs target state

Start with:

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [docs/architecture.md](./docs/architecture.md)
- [docs/kernel-spec-v0.1.md](./docs/kernel-spec-v0.1.md)
- [docs/roadmap.md](./docs/roadmap.md)

## License

MIT
```

## Part D. Key Docs Summary And Opening Samples

### `docs/why-hermit.md`

Summary:

- explain the problem class
- show why Hermit is not just "another agent runtime"
- keep the argument conceptual, not implementation-heavy

Suggested outline:

1. The problem with session-first agents
2. Why durable work needs a task kernel
3. Why governed execution matters
4. Why artifacts beat transcript-only context
5. Why memory needs evidence
6. Why local-first changes the trust model
7. What Hermit already ships vs where it is going

Opening sample:

```markdown
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
```

### `docs/architecture.md`

Summary:

- describe the current implementation only
- show where kernel modules sit today
- map runtime surfaces to kernel path
- make the old/new boundary explicit

Suggested outline:

1. Scope of this document
2. Current repo center of gravity
3. Runtime surfaces
4. Task kernel objects
5. Execution path
6. Kernel ledger and projections
7. Context, belief, and memory flow
8. Governance path
9. Operator surfaces
10. Current implementation vs target architecture

Opening sample:

```markdown
# Hermit Architecture

This document describes the implementation that exists in the current repository. It does not treat the `v0.1` kernel spec as fully shipped.

Hermit's current architecture has two visible layers:

1. a runtime layer that exposes CLI, chat, `serve`, scheduler, webhook, and adapters
2. a task kernel layer that introduces first-class records, governance, receipts, proofs, and rollback-aware execution

The important change is not just that Hermit has more features. The important change is that the runtime is being re-centered around kernel semantics.

In practice, that means Hermit now has durable objects such as `Task`, `Step`, `StepAttempt`, `Approval`, `Decision`, `ExecutionPermit`, `Artifact`, `Receipt`, `Belief`, `MemoryRecord`, `Rollback`, `Conversation`, and `Ingress`, backed by a local kernel ledger and projection cache.
```

### `docs/governance.md`

Summary:

- explain how Hermit governs effectful execution
- focus on policy, approvals, decisions, permits, path grants
- use both concept and current implementation language

Suggested outline:

1. Why governance is in the kernel path
2. Model authority vs execution authority
3. Policy evaluation
4. Decision records
5. Approval packets and approval resolution
6. Capability grants and path grants
7. State witness and drift
8. What is shipped today
9. What remains target-state

Opening sample:

```markdown
# Governance

Hermit treats governance as an execution primitive, not as a UI decoration.

The model may propose an action. It does not automatically get authority to perform it.

For consequential work, the kernel evaluates policy, records decisions, requests approval when required, issues a scoped capability grant, and only then dispatches the executor.

This is the core law:

**models reason; the kernel authorizes and executes**

That law is what separates Hermit from agent runtimes that rely on ambient process authority and post-hoc logs.
```

### `docs/receipts-and-proofs.md`

Summary:

- explain why receipt-aware execution matters
- define receipt, proof summary, proof bundle, observation, uncertain outcome, rollback

Suggested outline:

1. Why logs are not enough
2. What a receipt is
3. What counts as an important action
4. Receipt bundles and context manifests
5. Event-chain verification
6. Observation and unknown outcomes
7. Rollback as a first-class action
8. Current implementation notes

Opening sample:

```markdown
# Receipts And Proofs

Hermit draws a hard line between "a tool ran" and "an important action is durably complete."

A log line is not enough.

For important actions, Hermit records a `Receipt` that ties together the task, step attempt, inputs, outputs, policy result, authority references, and outcome summary. Proof services then turn those records into proof summaries and exportable proof bundles.

The point is not cryptographic theater. The point is operational truth:

- what happened
- why it happened
- what evidence and authority were involved
- what changed
- whether the result can be verified or rolled back
```

### `docs/roadmap.md`

Summary:

- become the official status page
- state what is current, partial, target, experimental

Suggested outline:

1. How to read this roadmap
2. Current status snapshot
3. Spec convergence goals
4. Milestones to `v0.1`
5. Longer-term `1.0` themes
6. Contribution priorities

Opening sample:

```markdown
# Roadmap

Hermit is best understood today as an **alpha governed agent kernel**.

It is not starting from zero. The repository already contains real kernel objects, governance paths, proof primitives, and rollback support for selected actions. But the whole system is still converging on the `v0.1` kernel spec.

This roadmap separates four things clearly:

- what Hermit already ships
- what is partially implemented
- what the `v0.1` spec defines as target behavior
- what remains experimental or open
```

### `docs/use-cases.md`

Summary:

- show concrete, high-signal scenarios
- optimize for social explanation and conversion

Suggested outline:

1. Who Hermit is for
2. Best-fit use cases today
3. Why governance matters in those cases
4. Example flows
5. Where Hermit is not the best fit yet

Opening sample:

```markdown
# Use Cases

Hermit is not the right answer for every agent workload.

It becomes interesting when the work is local-first, stateful, approval-sensitive, and worth inspecting later.

Good examples:

- a coding agent that should ask before mutating files outside the task workspace
- a scheduled operator assistant that should leave receipts and proof bundles
- a channel-connected assistant where work should continue as tasks, not disappear into message history
- a memory-bearing assistant where durable memory must cite evidence instead of growing by vibe
```

## Part E. Style And Wording Rules

### Recommended vocabulary

- local-first
- governed
- task-first
- durable
- event-backed
- artifact-native
- evidence-bound
- scoped authority
- approval-aware
- receipt-aware
- rollback-aware
- operator-visible
- inspectable
- recoverable
- converging toward
- first-class object
- target architecture
- current implementation

### Use carefully

- event-sourced
- verifiable
- auditable
- explainable
- trusted
- secure
- autonomous
- production-ready
- complete
- deterministic

These are not forbidden, but they must be anchored to concrete scope.

Examples:

- good: `Hermit uses an event-backed kernel ledger with hash-chained verification primitives.`
- bad: `Hermit is fully event-sourced.`

- good: `Hermit already ships receipt bundles and proof export primitives.`
- bad: `Hermit delivers full verifiable execution.`

### Avoid entirely

- revolutionary
- next-gen
- cutting-edge
- game-changing
- magical
- seamless
- fully autonomous
- trustless
- production-grade everywhere
- enterprise-ready by default
- complete kernel
- solved memory
- perfect rollback
- better than X
- X killer

### Sentence templates

#### Shipped / implemented

- `Hermit is a local-first governed agent kernel.`
- `Hermit currently ships a kernel ledger with first-class task, approval, receipt, and proof records.`
- `Hermit already exposes operator commands for task inspection, approval resolution, proof export, and rollback execution.`

#### Partial / converging

- `Hermit is converging on task-first semantics across its runtime surfaces.`
- `Governed execution is already visible in the codebase, though some runtime-era paths still coexist with the newer kernel model.`
- `Verifiable execution primitives are present today, but the broader proof story is still maturing.`

#### Target architecture

- `The v0.1 kernel spec defines the target architecture for the next major iteration of Hermit.`
- `This document describes the target architecture, not the full current repository state.`
- `Hermit is moving toward a kernel where important actions are only complete when they are receipted.`

#### Experimental / unstable

- `This interface is experimental and may change as the kernel model continues to settle.`
- `This capability exists today, but should be treated as alpha and subject to migration.`
- `This path is available for exploration, not yet a stable public contract.`

## Part F. 30-Day Execution Plan

### Week 1

- rewrite `README.md`
- create `docs/getting-started.md`
- create `docs/why-hermit.md`
- create `docs/status-and-compatibility.md`
- align `pyproject.toml` description with the new external positioning

Goal:

- fix the first impression
- fix narrative consistency

### Week 2

- rewrite `docs/architecture.md`
- refine `docs/kernel-spec-v0.1.md` intro and scope language
- create `docs/governance.md`
- create `docs/task-lifecycle.md`

Goal:

- make the kernel story legible and credible

### Week 3

- create `docs/receipts-and-proofs.md`
- create `docs/context-model.md`
- create `docs/memory-model.md`
- rewrite `docs/openclaw-comparison.md`

Goal:

- sharpen the unique thesis
- make Hermit's seriousness visible

### Week 4

- create `docs/use-cases.md`
- create `docs/faq.md`
- rewrite `CONTRIBUTING.md`
- add diagrams and terminal screenshots

Goal:

- improve conversion
- improve contributor onboarding

### Highest-leverage first moves

1. README rewrite
2. Why Hermit
3. Architecture rewrite
4. Governance doc
5. Receipts and proofs
6. Status and compatibility

### Most valuable diagrams to add first

1. **Governed execution path**
   `task -> step attempt -> policy -> approval -> permit -> executor -> receipt -> proof / rollback`

2. **Kernel object map**
   show relations among task, step, attempt, approval, decision, permit, artifact, receipt, belief, memory, rollback

3. **Context pack composition**
   artifact refs + working state + beliefs + memory records + task summary

4. **Current state vs target state**
   a two-column diagram or table to keep the repo honest

### Minimum viral version

If only a minimum set can ship soon, do this:

- `README.md`
- `docs/why-hermit.md`
- `docs/architecture.md`
- `docs/governance.md`
- `docs/receipts-and-proofs.md`
- `docs/roadmap.md`
- 1 architecture diagram
- 1 screenshot of task proof / task show

That is the smallest set most likely to improve GitHub conversion and technical credibility at the same time.

## Part G. Anti-Patterns

1. Do not open with a wall of architecture text before explaining why the project matters.
2. Do not describe Hermit as only a "personal AI runtime" in the first screen.
3. Do not list tools and integrations before establishing the kernel thesis.
4. Do not present the `v0.1` spec as the current shipped state.
5. Do not use "verifiable" as a blanket label without explaining the actual proof primitives.
6. Do not say rollback exists everywhere; say where it exists and how it is bounded.
7. Do not reduce governance to "human approval support"; include policy, decision, scoped authority, and witness drift.
8. Do not define context as message history plus memory; artifacts must appear as a first-class unit.
9. Do not talk about memory as if it were a generic sticky-note feature.
10. Do not hide complexity by pretending the runtime and kernel layers are already fully unified.
11. Do not turn the comparison docs into competitor dunking.
12. Do not make the README sound like a research abstract.
13. Do not make the README sound like a startup landing page.
14. Do not overuse big adjectives when object model and workflow can carry the argument.
15. Do not bury current status in vague language like "early but powerful"; say what is shipped, partial, target, and experimental.
16. Do not let package metadata, README, and architecture docs describe different projects.
17. Do not route every reader through the spec; give newcomers a shorter conceptual bridge first.
18. Do not make docs newcomer-friendly only by removing system detail; instead stage the detail across layers.
19. Do not define Hermit mainly by what integrations it supports.
20. Do not write "Hermit is like X but local-first"; Hermit needs its own object-model identity.

## Appendix: Suggested homepage proof-of-seriousness blocks

These are worth surfacing in README because they make the project feel real:

- a short "operator surfaces" command block with `task show`, `task proof`, `task rollback`
- a comparison table showing session-first vs task-first
- a quote block such as:

> A task does not end at tool execution. It ends with an inspectable outcome.

- a callout such as:

> Hermit is early, but it is not vague. The repo already contains the objects and control paths that define its kernel thesis.

- a state callout such as:

> The `v0.1` kernel spec is the target architecture. The current repository is an alpha implementation converging toward it.
