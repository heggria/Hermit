# Kernel Spec v0.1 Section Checklist

This document tracks the current repository against the `v0.1` spec chapter by chapter.

Use it alongside:

- [kernel-spec-v0.1.md](./kernel-spec-v0.1.md) for the normative target architecture
- [kernel-conformance-matrix-v0.1.md](./kernel-conformance-matrix-v0.1.md) for the claim-gated exit criteria
- [status-and-compatibility.md](./status-and-compatibility.md) for the short current-state honesty layer

Interpretation:

- `Completed` means the current repo has a concrete code path plus tests or operator surfaces for the chapter's main kernel semantics.
- `Claim status` tells you whether the chapter currently contributes to the repository-level `Core`, `Governed`, or `Verifiable` claims surfaced by `task claim-status`.
- `Compatibility debt` calls out the places where the kernel contract is real, but outer runtime surfaces, naming, or schema are still converging.

## Snapshot

Repository-level claim status as of 2026-03-15:

- `Core`: claimable
- `Governed`: claimable
- `Verifiable`: claimable

Important caution:

- strong task-level verifiable readiness still depends on proof bundle completeness plus signing and inclusion coverage for the specific task
- this checklist is broader than the claim matrix, so a chapter may be claimable at the kernel contract level while still carrying compatibility debt in outer runtime surfaces

## Framing Chapters

| Chapter | Status | Completed now | Claim status | Compatibility debt / caution |
| --- | --- | --- | --- | --- |
| 1. Design Position | `completed` | Public docs describe `v0.1` as the target architecture and current Hermit as an alpha kernel. | `n/a` | Keep summary docs aligned; if wording drifts, defer to the conformance matrix and claim CLI. |
| 2. Architectural Thesis | `completed` | The kernel is task-first, artifact-native, governed, and local-first in the current implementation direction. | `Core` / `Governed` / `Verifiable` | The broader runtime still includes compatibility-era surfaces around the kernel. |
| 3. Goals | `completed` | The main goals are materially visible through durable tasks, governed execution, receipts, proofs, and recovery-aware flows. | `Core` / `Governed` / `Verifiable` | Public API stability and full surface uniformity are still outside the current promise. |
| 4. Non-Goals | `completed` | The repo docs clearly reject the "chat shell with tools" framing and similar scope creep. | `n/a` | The discipline still depends on keeping public summaries honest as the codebase evolves. |
| 5. Normative Language | `completed` | The spec continues to act as the normative target document for the repo. | `n/a` | None beyond normal documentation drift control. |

## Kernel Contract Chapters

| Chapter | Status | Completed now | Claim status | Compatibility debt / caution |
| --- | --- | --- | --- | --- |
| 6. Kernel Invariants | `completed` | Durable state, scoped execution authority, evidence-bound context, and audit/recovery invariants are implemented in kernel paths. | `Core` / `Governed` / `Verifiable` | Runtime/operator views still expose compatibility-friendly summaries alongside strict ledger objects. |
| 7. Important Actions | `completed` | Consequential actions are classified, governed, and receipt-bearing in the current kernel path. | `Governed` / `Verifiable` | Coverage is strongest for the governed action classes Hermit already models; it is not a blanket promise over every historical surface. |
| 8. First-Class Objects | `completed` | `Task`, `Step`, `StepAttempt`, `Event`, `Artifact`, `Belief`, `MemoryRecord`, `Decision`, `Approval`, `Receipt`, `Ingress`, `Principal`, `CapabilityGrant`, and `WorkspaceLease` are real durable objects. | `Core` / `Governed` / `Verifiable` | None beyond keeping operator read models aligned with the canonical field names. |
| 9. Object Relationships | `completed` | Task-step-attempt ownership, evidence references, approval/decision/grant attachment, and projection rebuilding are all real in code. | `Core` / `Governed` / `Verifiable` | Some relationships are surfaced through projection caches and operator summaries rather than a final public API. |
| 10. Layered Architecture | `completed` | Control, orchestration, execution, policy, context, and supervision responsibilities are all materially present across `hermit/core/` and `hermit/kernel/`. | `Core` / `Governed` / `Verifiable` | Exact target layer names are not yet the literal module layout; several runtime-era boundaries still remain. |
| 11. Execution Lifecycle | `completed` | Task-first ingress, attempt creation, context compilation, policy gating, approval pause/resume, workspace lease acquisition, scoped capability grant issuance, and receipt issuance are implemented for the governed path. | `Core` / `Governed` | Remaining work is about broader outer-surface ergonomics, not missing durable lifecycle objects. |
| 12. State Machines | `completed` | Task, step, attempt, and approval-blocking state transitions are represented in the store, events, and controller/executor flow. | `Core` / `Governed` | State visibility is strongest through task CLI and projection services, not a separate dedicated supervision UI. |
| 13. Event Model | `completed` | Append-only task ordering, event-backed truth, hash-linking, idempotency keys, and projection rebuilds are all present. | `Core` / `Verifiable` | The kernel should be described as event-backed, but the whole repo should still not be described as uniformly event-sourced everywhere. |
| 14. Artifact and Evidence Model | `completed` | `context.pack`, `action.request`, `approval_packet`, `state.witness`, `receipt.bundle`, and related artifact/evidence flows are implemented. | `Core` / `Verifiable` | Artifact kinds and lineage ergonomics can still improve, and some outer surfaces continue to expose compatibility snapshots. |
| 15. Working State, Belief, Memory, and Context | `completed` | Working state, beliefs, durable memory, trust tiers, memory governance, and context compilation are all real kernel paths. | `Core` | The markdown memory mirror still exists as an export surface around kernel truth. |
| 16. Decisions, Policy, Approval, and Capability | `completed` | Policy profiles, action classes, approval packets, witness revalidation, workspace leases, and capability grants are live in the governed executor path. | `Governed` | Policy coverage can still broaden, and override surfaces remain intentionally narrow. |
| 17. Workspace, Environment, and Secrets | `completed` | Environment snapshots, mutable/scoped workspace leases, runtime constraints, and receipt redaction are present. | `Governed` | Environment and secret enforcement can still broaden across outer runtime surfaces, but the core durable lease model is now in place. |
| 18. Receipts, Replay, and Rollback | `completed` | Receipt issuance, proof bundles, replay metadata, signed proof export, and rollback execution for supported classes are implemented. | `Governed` / `Verifiable` | Rollback support remains scoped by receipt class, and stronger proof modes depend on local signing and proof completeness. |
| 19. Failure, Recovery, and Idempotency | `completed` | Observation, uncertain-outcome handling, durable re-entry, retry-safe idempotency keys, and interruption recovery are all real kernel behaviors. | `Governed` / `Verifiable` | Recovery semantics are strongest on the current governed task paths, not as a blanket statement about every adapter history. |
| 20. Concurrency and Consistency | `completed` | Hermit already enforces conservative single-writer per task behavior and treats projections as non-authoritative for side effects. | `Core` | Parallel-step semantics remain intentionally conservative rather than a broadly claimed surface. |
| 21. Supervision and Trust Surface | `completed` | `task show`, `task case`, `task claim-status`, proof export, projection rebuild, and conversation/task supervision surfaces already answer key operator questions. | `Core` / `Governed` / `Verifiable` | The supervision layer is still CLI and projection first, with some compatibility-friendly summaries rather than a final dedicated control plane UI. |

## Transition And Claim Chapters

| Chapter | Status | Completed now | Claim status | Compatibility debt / caution |
| --- | --- | --- | --- | --- |
| 22. Compatibility with Current Hermit | `completed` | The repo now explicitly maps `runner`, `runtime`, `scheduler`, `session`, and plugin/tool registry roles to the kernel model while keeping compatibility surfaces visibly outside the core contract. | `n/a` | Compatibility layers still exist, but the principal/capability/lease model no longer depends on legacy names, tables, or operator commands. |
| 23. Suggested Module Layout | `completed` | The identity, capability, and workspace domains now live under dedicated packages instead of only `hermit/kernel/`, and the old permit/path-grant modules are removed. | `n/a` | The full spec-wide package graph is not rewritten verbatim yet, but the chapters that needed the hard cut now use domain-first layout. |
| 24. Suggested Persistent Records | `completed` | Events, tasks, steps, attempts, principals, artifacts, beliefs, memory records, decisions, approvals, capability grants, workspace leases, receipts, rollbacks, and projection caches are durable today. | `Core` / `Governed` / `Verifiable` | Canonical records are now in place; remaining caution is mostly about read-model ergonomics rather than schema gaps. |
| 25. Conformance Profiles | `completed` | `Core`, `Governed`, and baseline `Verifiable` claims are surfaced in code and through `task claim-status`. | `Core` / `Governed` / `Verifiable` | Task-level strongest verifiable readiness still depends on proof coverage and export mode for that task. |
| 26. Security and Trust Posture | `completed` | Zero-trust memory, policy-before-side-effects, approvals, scoped grants, witness drift handling, receipts, and explicit rollback metadata are live kernel principles. | `Governed` / `Verifiable` | This is a kernel trust posture, not a blanket promise about every adapter surface or long-term API stability. |
| 27. Exit Criteria for v0.1 | `completed` | The current conformance matrix marks repository-blocking rows `implemented`, while stronger signed proof rows remain conditional on local signing configuration. | `Core` / `Governed` / `Verifiable` | These claims apply to the kernel contract, not to every legacy runtime affordance, and strong proof readiness is still task- and configuration-specific. |
| 28. Summary | `completed` | The summary matches the current kernel contract and claim interpretation. | `n/a` | None beyond keeping the summary aligned with the matrix and claim gate. |

## Practical Readout

If you need a short answer:

- the kernel contract is far enough along that repository-level `Core`, `Governed`, and `Verifiable` claims are now real
- the repo is still honestly described as alpha because outer runtime surfaces and public API stability are still settling
- the biggest remaining convergence work is no longer "make the kernel real"; it is "polish the outer surfaces without weakening the kernel hard cut"
