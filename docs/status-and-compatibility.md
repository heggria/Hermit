# Status And Compatibility

This document is the short honesty layer for Hermit's docs.

Use it when you want to know what is claimable today, what remains compatibility-only, and what should still be treated as unstable.

## Current Positioning

Hermit is best described today as:

**an alpha local-first governed agent kernel**

That means:

- the repo already contains real kernel semantics and control paths
- the repo can now gate `Core` / `Governed` / `Verifiable` kernel claims in code, while keeping some compatibility surfaces around them

## Status Matrix

| Area | Status | Notes |
| --- | --- | --- |
| Task ledger and core records | shipped | task, step, step attempt, approval, decision, principal, capability grant, workspace lease, artifact, receipt, belief, memory, rollback, conversation, ingress |
| Governed execution | shipped | policy, approval, and scoped authority are already real in the codebase |
| Proof summaries and proof export | shipped | usable today through the task CLI |
| Rollback | shipped with scoped coverage | supported for governed receipt classes; not every historical action type has a rollback strategy |
| Artifact-native context | shipped | context compiler and context packs are the default runtime path |
| Evidence-bound memory | shipped | kernel-backed memory governance is the default truth path; markdown mirror is export-only |
| Full spec `0.1` convergence | claim-gated | current kernel profiles are surfaced through code and docs rather than README prose alone |
| Stable public kernel API | not a current promise | interfaces may still change as semantics settle |

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
