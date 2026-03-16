# FAQ

## Is Hermit an app, a runtime, or a kernel?

Today it is a local-first runtime with a real task kernel at its center. The direction of the project is best described as a local-first governed agent kernel.

## Is Hermit just another chat-plus-tools agent?

No. The strongest differentiators are:

- task-first execution
- governed side effects
- artifact-native context
- evidence-bound memory
- receipts, proofs, and rollback-aware recovery

## Is the `v0.1` kernel spec already fully shipped?

No. The spec defines the target architecture. The current repository is an alpha implementation converging toward it.

## Does Hermit already have real kernel objects, or is that mostly aspirational?

It already has real kernel records and services for tasks, approvals, decisions, principals, capability grants, workspace leases, artifacts, receipts, beliefs, memory records, rollbacks, conversations, and ingresses.

## Is Hermit fully event-sourced?

Say this carefully. The kernel uses event-backed truth and ledgered projections. The broader runtime should not yet be described as uniformly event-sourced in every layer.

## Does Hermit already support approvals?

Yes. Approval objects, approval resolution, and approval-linked task flow already exist.

## Does Hermit already support receipts and proofs?

Yes. It already ships receipt issuance, proof summaries, proof bundle export, and event-chain verification primitives.

## Does Hermit already support rollback?

Partially. Rollback exists as a first-class object and executable path for supported receipt classes. It is not universal.

## Is Hermit local-first?

Yes. State, kernel data, and operator surfaces are designed around local control. That does not mean every useful integration disappears; it means the trust model starts from local inspectability.

## Is Hermit production-ready?

It is better described as an alpha kernel with real semantics than as a finished production platform. Serious builders can evaluate it now, but should expect ongoing evolution.

## Who is Hermit for?

Three groups:

- people who want a local-first agent they can inspect
- builders who care about task semantics, governance, and evidence
- operators who want approvals, receipts, proof material, and recovery paths

## Where should I start?

Start here:

- [Getting started](./getting-started.md)
- [why-hermit.md](./why-hermit.md)
- [architecture.md](./architecture.md)
- [roadmap.md](./roadmap.md)
