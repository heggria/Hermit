# Status And Compatibility

This document is the short honesty layer for Hermit's docs.

Use it when you want to know what is claimable today, what remains compatibility-only, and what should still be treated as unstable.

## Current Positioning

Hermit is best described today as:

**a beta local-first governed agent kernel**

As of v0.2.5, Hermit has been upgraded from Alpha to Beta. All six Beta Gate criteria are satisfied: 12/12 v0.2 Core exit criteria implemented, memory governance tests passing, CI flaky tests fixed, public API scope defined, this document updated, and the `pyproject.toml` classifier updated to `Development Status :: 4 - Beta`.

That means:

- the repo already contains real kernel semantics and control paths
- the repo can now gate `Core` / `Governed` / `Verifiable` kernel claims in code, while keeping some compatibility surfaces around them
- public API stability guarantees (see [Public API Stability](#public-api-stability)) are now in effect

## Status Matrix

| Area | Status | Notes |
| --- | --- | --- |
| Task ledger and core records | shipped | task, step, step attempt, approval, decision, principal, capability grant, workspace lease, artifact, receipt, belief, memory, rollback, conversation, ingress |
| Governed execution | shipped | policy, approval, and scoped authority are already real in the codebase |
| Proof summaries and proof export | shipped | usable today through the task CLI |
| Reconciliation | shipped | durable reconciliation records for all governed action types; idempotent protection against duplicate reconciliation |
| Proof chain completeness | shipped | proof export reconstructs full contract/evidence/authority/receipt/reconciliation chains |
| Contract-sensitive retries | shipped | contract expiry and policy version drift trigger durable re-entry; violated reconciliation invalidates related memories |
| Rollback | shipped with scoped coverage | supported for governed receipt classes; not every historical action type has a rollback strategy |
| Artifact-native context | shipped | context compiler and context packs are the default runtime path |
| Evidence-bound memory | shipped | kernel-backed memory governance is the default truth path; markdown mirror is export-only |
| Full spec `0.1` convergence | claim-gated | current kernel profiles are surfaced through code and docs rather than README prose alone |
| Stable public kernel API | boundary defined | public vs internal modules classified; stability guarantees apply from Beta onward (see [Public API Stability](#public-api-stability)) |

## Recommended Wording

### Safe wording

- `Hermit is a local-first governed agent kernel.`
- `Hermit currently ships a local kernel ledger with first-class task and execution records.`
- `Hermit already exposes approvals, receipts, proofs, and rollback support for selected actions.`
- `Hermit surfaces kernel claim status through the v0.1 conformance matrix and CLI.`

### Wording to scope carefully

- `event-sourced`
- `verifiable`
- `auditable`
- `rollback-capable`
- `stable`

Use these only when you also say what layer or surface you mean.

## TrustLoop-Bench Results

TrustLoop-Bench is the formal benchmark for v0.2 exit criterion #12. It validates governed execution across 5 task families (15 tests) and 7 kernel governance metrics. See [roadmap.md](./roadmap.md#trustloop-bench-performance-thresholds) for threshold definitions.

### Metric Results

| Metric | Threshold | Current | Status |
| --- | --- | --- | --- |
| Contract Satisfaction Rate | >= 50% | 80% (4/5) | met |
| Unauthorized Effect Rate | == 0% | 0% (0/8) | met |
| Stale Authorization Execution Rate | == 0% | 0% (0/5) | met |
| Belief Calibration Under Contradiction | >= 80% | 100% (1/1) | met |
| Rollback Success Rate | >= 80% | 100% (1/1) | met |
| Mean Recovery Depth | <= 3.0 | 1.5 | met |
| Operator Burden Per Successful Task | <= 3.0 | 0.5 (2/4) | met |

### Task Family Coverage

| Family | Status | Notes |
| --- | --- | --- |
| TF1: Approval Drift Patch | passing | Drift detection verified through separate contract issuance |
| TF2: Bounded-Authority Ops | passing | Unclassified mutations correctly blocked |
| TF3: Crash + Unknown Outcome | passing | Reconciliation correctly classifies as partial/ambiguous |
| TF4: Contradictory Memory | passing | Memory invalidated on reconciliation violation |
| TF5: Rollback-Qualified Publish | passing | Receipt with rollback artifact refs verified |

All 15 tests pass. All 7 metric thresholds are met. v0.2 exit criterion #12 is satisfied.

## Public API Stability

This section defines which import paths are **public** (stability-committed) and which are **internal** (may change without notice). This boundary is a prerequisite for the Alpha-to-Beta transition (see [roadmap.md — Beta Gate Checklist](./roadmap.md#beta-gate-checklist)).

### Public modules

These modules form the public API surface. Within a minor version series (e.g. `0.3.x`), public modules will not receive breaking changes. Deprecations will be announced at least one minor version before removal.

| Import path | Contents | Notes |
| --- | --- | --- |
| `hermit.kernel.task.models` | `TaskRecord`, task state types | Core task data model |
| `hermit.kernel.policy` | Policy, approval, decision, permit types | Governance contract types |
| `hermit.kernel.verification` | Receipt, proof, rollback types | Verifiability contract types |
| `hermit.plugins` | `PluginManifest`, `HookEvent`, `SubagentSpec`, `AdapterSpec`, tool contracts | Plugin development surface |
| `hermit.infra.storage` | `JsonStore`, `atomic_write` | Persistence primitives |
| `hermit.surfaces.cli` | CLI entry point (`main:app`) | Operator surface |

### Internal modules

Everything not listed above is **internal**. This includes but is not limited to:

- `hermit.kernel.ledger` — journal and projection internals
- `hermit.kernel.execution` — executor, recovery, suspension internals
- `hermit.kernel.context` — compiler and memory governance internals
- `hermit.kernel.artifacts` — lineage, claims, evidence internals
- `hermit.kernel.authority` — identity, workspace, grant internals
- `hermit.runtime` — runner, capability registry, provider host, config assembly
- `hermit.infra.locking` — file guard internals
- `hermit.infra.system` — sandbox, i18n, executables
- `hermit.apps` — macOS companion internals
- `hermit.plugins.builtin` — built-in plugin implementations

Internal modules may change, be renamed, or be removed in any release. Do not depend on their import paths or internal types in downstream code.

### Stability guarantees

- **Public modules:** No breaking changes within a minor version. Deprecation warnings for at least one minor version before removal.
- **Internal modules:** Changes reserved. May break between any two releases.
- **CLI surface:** Command names and flags listed in the CLI Fact Sheet (see `AGENTS.md`) are treated as public. Subcommand output formats are not guaranteed stable.
- **Plugin `plugin.toml` schema:** The manifest schema is public. Entry point categories (`tools`, `hooks`, `commands`, `subagents`, `adapter`, `mcp`) are stable.

## Compatibility Expectations

Current expectations:

- CLI and local-first workflows are the primary operator surface
- kernel semantics are stricter than the remaining compatibility layers, but still not a final public contract
- docs should distinguish current implementation from target architecture

If you are building on Hermit today, assume:

- the direction is strong
- the semantics matter
- some compatibility interfaces may still be removed as the kernel model settles

## Read This Alongside

- [architecture.md](./architecture.md)
- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md)
- [kernel-conformance-matrix-v0.1.md](./kernel-conformance-matrix-v0.1.md)
- [roadmap.md](./roadmap.md)
