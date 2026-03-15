# Roadmap

Hermit is best understood today as an **alpha local-first governed agent kernel**.

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
| Public API stability | not a current goal | later concern after kernel semantics settle |

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

## Contribution Priorities

The highest-leverage contributions right now are:

- task lifecycle correctness
- governance correctness
- receipt and proof coverage
- rollback safety
- context and memory discipline
- docs that sharply separate current implementation from target architecture

## What The Roadmap Is Not

The roadmap is not a promise that Hermit is trying to become:

- a giant multi-tenant cloud platform
- a no-tradeoff autonomous agent system
- a surface-level tool catalog race

Hermit's goal is narrower and stronger:

to become a local-first governed kernel for durable agent work that operators can inspect, approve, and recover.
