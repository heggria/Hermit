---
description: "Hermit roadmap: kernel profile conformance, governance coverage, receipt and proof expansion, rollback semantics, and artifact-native context."
---

# Roadmap

Hermit is best understood today as a **beta local-first governed agent kernel**.

It is not starting from zero. The repository already contains real kernel objects, governance paths, proof primitives, and rollback support for selected actions. But the whole system is still converging on the `v0.1` kernel spec.

This roadmap separates five things clearly:

- what Hermit already ships
- what is claimable today
- what is partially implemented or compatibility-only
- what the `v0.1` spec defines as target behavior
- what remains experimental or open

For a chapter-by-chapter breakdown, see [kernel-spec-v0.1-section-checklist.md](./kernel-spec-v0.1-section-checklist.md).

## Current Status Snapshot

### Safe current-state claims

- Repository-level `Core`, `Governed`, and baseline `Verifiable` kernel profiles are claimable through the conformance matrix and `task claim-status`.
- Hermit already ships a local kernel ledger with first-class task-related records.
- Hermit already has governed execution primitives such as policy evaluation, approvals, decisions, capability grants, and workspace leases.
- Hermit already issues receipts and exposes proof summaries and proof export.
- Hermit already supports rollback for supported receipt classes.
- Hermit already has context compilation and memory governance primitives.

### Careful current-state claims

- Task-level strongest verifiable readiness still depends on proof bundle completeness, signing, and inclusion coverage for the specific task being exported.
- These claims apply to the kernel contract, not to every compatibility surface or legacy runtime affordance.
- Not every runtime surface should be described as fully aligned with the target kernel spec.
- Public APIs and module layout should still be treated as unstable while the runtime keeps converging on the kernel model.

## Status Matrix

| Area | Current state | Direction |
| --- | --- | --- |
| Task-first execution | claimable at the kernel contract level | close remaining adapter and runtime gaps |
| Event-backed truth | claimable in the kernel ledger | deepen projection and replay semantics without over-claiming repo-wide event sourcing |
| Governed execution | claimable at the kernel contract level | extend coverage and tighten invariants |
| Receipts and proofs | claimable at baseline, task-specific strength still varies with proof coverage | broaden coverage and operator ergonomics |
| Rollback | supported for selected receipts | expand safe rollback classes |
| Artifact-native context | claimable in the kernel path | make it more central across all paths |
| Evidence-bound memory | claimable in the kernel path | tighten promotion, invalidation, and inspection |
| Public API stability | boundary defined | public vs internal modules classified; stability guarantees apply from Beta (see [status-and-compatibility.md](./status-and-compatibility.md#public-api-stability)) |

## Near-Term Milestones

### Milestone 1: Spec `0.1` Surface Closure

Focus:

- preserve the now-claimable kernel profiles with machine-checkable docs and tests
- close ambiguous gaps between runtime surfaces and kernel paths
- reduce legacy naming and compatibility drift where it obscures kernel semantics

### Milestone 2: Governed Execution Hardening

Focus:

- broaden policy coverage for consequential actions
- strengthen approval and witness drift semantics
- improve scoped authority handling

### Milestone 3: Receipt And Proof Coverage

Focus:

- increase receipt coverage across important action classes
- improve proof bundle completeness
- make operator inspection and proof export more usable

### Milestone 4: Recovery And Context

Focus:

- improve rollback coverage where safe
- deepen observation and resolution semantics
- strengthen artifact-native context and carry-forward behavior

## v0.2 Core Exit Criteria Status

12 of 12 v0.2 Core exit criteria are now `implemented`:

1. Consequential execution synthesizes an `ExecutionContractRecord` before dispatch
2. Contract admission records an `EvidenceCaseRecord` and `AuthorizationPlanRecord`
3. Receipt issuance carries contract / authorization linkage and requires reconciliation
4. Reconciliation writes a durable `ReconciliationRecord` and closes the contract loop
5. Witness drift supersedes prior attempts instead of silently reusing stale approval state
6. Durable memory promotion is reconciliation-gated
7. Projection / proof surfaces expose contract-loop entities
8. Idempotent reconciliation prevents duplicate reconciliation records for the same receipt
9. Contract expiry and policy version drift trigger durable re-entry before execution
10. Violated reconciliation invalidates memories learned from the same reconciliation ref
11. TrustLoop-Bench meaningful performance demonstration — all 15 tests pass, all 7 metric thresholds met (see below)
12. Learned contract templates for recurring governed action patterns

## TrustLoop-Bench Performance Thresholds

TrustLoop-Bench (`tests/integration/kernel/test_trustloop_bench.py`) is the formal benchmark for v0.2 exit criterion #12. It covers **5 task families** across **15 tests** and asserts **7 kernel governance metrics**.

### Task Families

| Family | Description | Tests |
| --- | --- | --- |
| TF1: Approval Drift Patch | Write, approve, external mutation, re-execute, drift detection | 1 |
| TF2: Bounded-Authority Ops | Read-only passes, unclassified mutation blocked by policy | 1 |
| TF3: Crash + Unknown Outcome | Execute, reconcile with unknown_outcome, verify uncertain | 1 |
| TF4: Contradictory Memory | Execute, reconcile, promote belief, violate, invalidate | 1 |
| TF5: Rollback-Qualified Publish | Write with rollback, verify receipt and prestate snapshot | 2 (TF5 + TF5b) |

Additional tests cover reconciliation result class coverage (3 tests), evidence case lifecycle (2 tests), authorization plan invalidation (1 test), knowledge blocking reasons (2 tests), and aggregate metric computation (1 test).

### Metric Pass Thresholds

These are the formal thresholds that the kernel must meet. All are asserted in `test_trustloop_bench_aggregate_metrics`.

| # | Metric | Threshold | Rationale |
| --- | --- | --- | --- |
| M1 | Contract Satisfaction Rate | >= 50% | Contracts satisfied / contracts total. Baseline floor; production targets will be higher. |
| M2 | Unauthorized Effect Rate | == 0% | Unauthorized effects / total effects. Zero tolerance for ungoverned side effects. |
| M3 | Stale Authorization Execution Rate | == 0% | Stale auth executions / total auth executions. No execution on expired or drifted authority. |
| M4 | Belief Calibration Under Contradiction | >= 80% | Beliefs recalibrated / beliefs contradicted. Memory must respond to contradicting evidence. |
| M5 | Rollback Success Rate | >= 80% | Rollbacks succeeded / rollbacks attempted. Rollback must be reliable where supported. |
| M6 | Mean Recovery Depth | <= 3.0 | Average steps to recover from crash or unknown outcome. Bounded recovery cost. |
| M7 | Operator Burden Per Successful Task | <= 3.0 | Operator interactions / successful tasks. Governance must not overwhelm operators. |

### Current Results

All 15 TrustLoop-Bench tests pass. All 7 metric thresholds are met. Criterion #12 is satisfied.

## Contribution Priorities

The highest-leverage contributions right now are:

- task lifecycle correctness
- governance correctness
- receipt and proof coverage
- rollback safety
- context and memory discipline
- docs that sharply separate current implementation from target architecture

## Beta Gate Checklist

Hermit will transition from Alpha to Beta when **all** of the following gates are satisfied. Each gate must be verified before the classifier in `pyproject.toml` is updated.

| # | Gate | Status | Notes |
| --- | --- | --- | --- |
| G1 | v0.2 Core exit criteria 12/12 completed | 12/12 — DONE | All 12 criteria implemented |
| G2 | All memory governance tests passing | All passing — DONE | Verified through `test_memory_engine` and integration suites |
| G3 | CI has no known flaky tests | Fixed — DONE | Sandbox observation and approval copy timing issues resolved |
| G4 | Public API scope defined | Defined — DONE | See [status-and-compatibility.md — Public API Stability](./status-and-compatibility.md#public-api-stability) |
| G5 | `status-and-compatibility.md` updated with API boundary | Updated — DONE | Public vs internal module classification added; Beta status noted |
| G6 | `pyproject.toml` classifier updated to Beta | Ready to update | All other gates satisfied |

**Transition rule:** When all six gates reach `done` / `passing`, update the classifier from `Development Status :: 3 - Alpha` to `Development Status :: 4 - Beta` and tag the release.

## What The Roadmap Is Not

The roadmap is not a promise that Hermit is trying to become:

- a giant multi-tenant cloud platform
- a no-tradeoff autonomous agent system
- a surface-level tool catalog race

Hermit's goal is narrower and stronger:

to become a local-first governed kernel for durable agent work that operators can inspect, approve, and recover.
