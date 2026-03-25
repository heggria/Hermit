---
description: "Hermit roadmap: from governed agent kernel to production-grade agent OS."
---

# Roadmap

Hermit is a **kernel-first governed agent runtime** — a system where every agent action flows through durable task records, policy evaluation, scoped authority grants, receipted execution, and hash-chained proof bundles. No shortcuts. No ungoverned side effects.

Every operating system evolves through the same arc: single-user → multi-user → networked → distributed. Hermit follows the same trajectory for AI tasks.

This roadmap describes where Hermit has been, where it is going, and why each step is a natural consequence of the kernel philosophy.

## Current: v0.3 (Released)

v0.3 marks the transition from a governed execution kernel to an adaptive **Task OS** — a system that does not just run governed tasks, but coordinates, competes, deliberates, and improves itself under the same governance guarantees.

v0.3 is Hermit's "multi-user moment" — programs, teams, worker pools, and competitive scheduling bring multi-tenant task management to the AI Task OS.

### What shipped

**Self-iteration pipeline.** Hermit can now improve its own codebase through a fully governed pipeline: spec parsing, workspace isolation, branch creation, execution, proof export, and pull request generation. Every mutation is authorized by the policy engine, granted scoped capabilities, receipted, and verifiable. The kernel eats its own dogfood.

**MCP supervisor extensions.** The Hermit MCP server exposes kernel tools via Streamable HTTP, enabling supervisor agents (Claude Code, Codex, or any MCP client) to submit tasks, manage approvals, monitor execution, and export proofs. Hermit becomes a governed execution backend for any agent system.

**Task OS kernel evolution.** The kernel is now topology-adaptive and verification-driven. Execution contracts, evidence cases, and authorization plans form a complete governance loop. Approval parking, workspace-aware execution, and fork-join concurrency with governed delegation are all first-class primitives.

**Competition and deliberation framework.** Multiple candidate solutions can be evaluated competitively, with deliberation protocols that select winners based on evidence quality and verification results — not just first-to-finish.

**Benchmark verification.** A benchmark registry with profile-based routing enables verification-driven quality gates. Tasks can declare benchmark profiles; the kernel routes them to appropriate verification suites and gates progression on results.

**5,800+ tests, 93% coverage.** The kernel is not just designed to be correct — it is tested to be correct. Unit, integration, and end-to-end tests cover task lifecycle, governance paths, receipt issuance, proof chain completeness, reconciliation, rollback, memory governance, and self-iteration.

### Foundation carried forward from v0.2

- 12/12 v0.2 Core exit criteria satisfied
- TrustLoop-Bench: all 15 tests passing, all 7 governance metric thresholds met
- Beta status with defined public API stability guarantees
- Full contract loop: ExecutionContract → EvidenceCase → AuthorizationPlan → Receipt → Reconciliation
- Hash-chained proof bundles with tiered export (summary / standard / full)
- Evidence-bound memory with contradiction detection and invalidation

## Next: v0.4

v0.4 extends the kernel from a single-node runtime to a coordination-capable system — still local-first, still governed, but ready for real-world multi-agent workflows.

v0.4 extends the kernel across boundaries — distributed execution, cross-repo proof federation, and multi-node coordination bring Hermit from a single-machine OS to a networked one.

### Distributed kernel (multi-node)

The kernel ledger and proof chain are designed to be portable. v0.4 introduces node-to-node task delegation with full governance preservation: a task delegated to a remote kernel carries its authorization plan, receives receipts from the remote executor, and reconciles back to the originating ledger. No trust assumptions are added — the proof chain spans nodes.

### Enhanced proof anchoring — external transparency logs and cross-repo federation

Git-native proof anchoring (via `GitNoteAnchor`) already ships in v0.3. v0.4 extends anchoring beyond the local repository: proof hashes are submitted to external transparency logs, enabling independent third-party verification. Blockchain anchoring provides tamper-evident timestamps for high-stakes governance decisions. Cross-repo proof federation allows proof chains that span multiple repositories to be verified as a single coherent bundle, supporting monorepo-to-polyrepo migration without breaking audit trails.

### Advanced team coordination — cross-team dependencies and role-based arbitration

Basic team coordination primitives (TeamRecord, role-bound worker pools, shared workspace leases) already ship in v0.3. v0.4 adds cross-team dependency resolution: when teams depend on each other's outputs, the kernel tracks inter-team data flow and enforces governance at team boundaries. Role-based slot arbitration lets operators define capacity constraints per role, and the kernel schedules work accordingly. Team-level governance policies allow different approval thresholds, trust baselines, and capability scopes per team — a security-sensitive team can require stricter governance without affecting the rest of the organization.

### Program orchestration — conditional branching, proof aggregation, and workflow templates

Programs and workflows are already first-class kernel objects in v0.3 (ProgramRecord, MilestoneRecord, ProgramManager, ProgramToolService store program definitions in the ledger alongside task records). v0.4 adds conditional branching: program steps can branch based on runtime evidence, benchmark results, or approval decisions. Program-level proof aggregation rolls up individual task proofs into a single bundle that covers the entire workflow execution. Workflow templates let operators define reusable program skeletons with parameterized governance profiles, making it practical to standardize recurring multi-step workflows across teams.

### Advanced benchmark routing

Benchmark profiles become composable and context-aware. The routing service learns from execution history which verification suites catch real issues for specific task families, and adjusts routing accordingly. Benchmark results feed back into trust scoring, creating a virtuous cycle: better verification produces better trust signals, which produce better policy decisions.

## Future: v1.0

v1.0 is the production-grade governed agent OS. Every design decision from v0.1 through v0.4 converges here: a system that enterprises can deploy, operators can trust, and auditors can verify.

v1.0 is the "enterprise distribution" — like Red Hat Enterprise Linux for the AI Task OS. Production-grade, compliance-certified, with a thriving plugin ecosystem.

### Production-grade governed agent OS

The kernel graduates from beta to stable. Public APIs are frozen with semantic versioning guarantees. The ledger schema supports online migration. Performance characteristics are documented and benchmarked. The system is ready for workloads where failure has real consequences.

### Enterprise deployment patterns

Reference architectures for common enterprise scenarios: CI/CD pipeline agents with approval gates, document processing workflows with audit trails, code review agents with rollback capability, and infrastructure management agents with scoped authority. Each pattern ships with governance profiles, benchmark suites, and proof export configurations tuned for the use case.

### Multi-tenant workspace isolation

Multiple teams sharing a Hermit deployment get cryptographic workspace isolation. Each tenant's tasks, artifacts, receipts, and proofs are partitioned with enforced boundaries. Cross-tenant delegation is possible but requires explicit authorization plans — the same governed execution model, applied to organizational boundaries.

### Compliance certification support

Proof bundles and governance records are already structured for auditability. v1.0 adds export formats and reporting tools aligned with common compliance frameworks. An auditor can take a proof export, verify the hash chain, and confirm that every consequential action was authorized, receipted, and reconciled — without understanding the kernel internals.

### Ecosystem of community plugins

The plugin system (adapters, hooks, tools, MCP servers, subagents, and bundles) is already extensible. v1.0 publishes a stable plugin SDK, a community registry, and governance profiles for third-party plugins. Community-contributed plugins inherit the same receipt and proof guarantees as builtin plugins — no second-class citizens.

## Design Principles

These principles are not aspirational. They are enforced in code today and guide every design decision going forward.

**Governed by default.** Every consequential action flows through policy evaluation, approval, and scoped authority grants. The kernel refuses ambiguous execution rather than permitting ungoverned side effects. This is not a safety layer bolted onto a permissive system — it is the execution model.

**Evidence over assertion.** Memory promotion requires evidence references. Trust scores derive from execution history. Benchmark results gate task progression. The kernel does not take claims at face value — it requires receipts.

**Receipts are non-negotiable.** Every governed action produces a receipt with HMAC-SHA256 signing and contract linkage. Receipts are not logging. They are durable, hash-chained records that form the basis of proof bundles, rollback decisions, and trust evaluation.

**Local-first, distribute-ready.** The kernel runs on a single machine with a SQLite ledger. No cloud dependency, no network requirement for core governance. But the proof chain and task model are designed to span nodes — distribution adds topology, not trust assumptions.

**Operator trust, not blind autonomy.** Hermit is not trying to remove humans from the loop. It is trying to make the loop inspectable, recoverable, and provable. Operators approve consequential actions, inspect proof bundles, and recover from failures. The kernel's job is to make that possible without overwhelming them.

**Fail closed.** When governance metadata is ambiguous, the kernel stops. When a contract expires, execution halts and re-entry is required. When reconciliation detects a violation, affected memories are invalidated. The system prefers refusing work to producing ungoverned results.

**The OS is the platform** — Hermit is not a tool you use; it's a platform you build on. Every design decision optimizes for being infrastructure, not an application.

## See Also

- [Release notes for v0.3](./release-notes-v0.3.md) — detailed changelog for the current release
- [Use cases](./use-cases.md) — practical scenarios and deployment patterns
- [MCP integration](./mcp-integration.md) — how supervisor agents interact with the Hermit kernel
- [FAQ](./faq.md) — frequently asked questions

## Contributing

The highest-leverage contributions strengthen kernel semantics:

- **Task lifecycle correctness** — the path from task creation to proof export
- **Governance coverage** — policy evaluation, approval flow, scoped authority
- **Receipt and proof expansion** — broader receipt classes, deeper proof chains
- **Rollback safety** — more action types with safe, tested rollback strategies
- **Benchmark and verification** — new benchmark profiles, verification suites
- **Plugin ecosystem** — adapters, hooks, and tools that demonstrate the governed execution model

Start with the [architecture overview](./architecture.md), read the [getting started guide](./getting-started.md), and run `make check` to verify your environment. The test suite is the best documentation of kernel behavior — when in doubt, read the tests.
