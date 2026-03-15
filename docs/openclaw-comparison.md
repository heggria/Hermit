# Hermit And OpenClaw Comparison

This is a high-level positioning comparison, not a file-by-file implementation audit of OpenClaw.

The goal is to clarify category differences without freezing volatile external details into Hermit's docs.

## Short Version

Both projects sit near the local-first agent space. Hermit's center of gravity is more explicitly a governed task kernel.

That is the key distinction to keep in mind.

## Comparison Table

| Question | Hermit | OpenClaw-style local agents |
| --- | --- | --- |
| Primary unit of work | task-first durable work | often more session-first or interaction-first |
| Execution model | increasingly governed | often optimized first for flexible agent behavior |
| Authority model | approvals, capability grants, workspace leases, receipts | varies, but often less centered on first-class governance objects |
| Context model | artifact-native direction | often more transcript-centric or runtime-centric |
| Memory model | evidence-bound direction with beliefs and memory records | varies by implementation |
| Operator surface | task inspection, proof, approval, rollback | varies; usually less centered on a kernel ledger |
| Current product shape | alpha governed kernel in a local runtime | often positioned as local agent experience or runtime system |

## Where Hermit Is Sharper

Hermit is sharper when you care about:

- task-first semantics
- governed side effects
- durable receipts
- proof-oriented inspection
- rollback-aware recovery
- evidence-bound memory

In other words, Hermit is not mainly competing on "how many integrations exist" or "how polished the assistant feels." It is competing on execution law.

## Where OpenClaw-Class Systems May Feel More Familiar

A more runtime-oriented or assistant-oriented local agent may feel more familiar if you mainly want:

- a local agent experience first
- broad interaction surfaces first
- a lighter conceptual model

Hermit asks the reader to care more about durable work and governance semantics. That is a strength for the right audience and extra weight for the wrong one.

## How To Choose

Choose Hermit when:

- you care about local inspectability
- you want consequential work to pass through explicit authority boundaries
- you want a task to remain legible after it completes
- you care about receipts, proofs, and rollback semantics

Choose a more runtime-first local agent when:

- you mainly want a helpful local assistant
- governance semantics are not central
- task durability and post-hoc inspection are not the primary value

## Important Note

This comparison should stay stable. If an external project changes, Hermit's docs should not become stale because they overfit to implementation trivia.

That is why this document compares design priorities, not release-by-release details.
