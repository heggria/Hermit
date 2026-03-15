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

## Why Receipts Exist

Most agent runtimes stop their accountability story at the tool loop.

Hermit takes a stronger view:

**an important action is not complete just because the executor returned**

For important actions, the kernel should retain:

- input references
- output references
- policy result
- approval reference
- decision reference
- capability grant reference
- workspace lease reference
- execution environment reference
- result code and summary
- rollback relationship when supported

## What Counts As An Important Action

The target rule is broad:

- local write
- local delete
- command execution
- VCS mutation
- network write
- credentialed API call
- publication
- memory promotion
- rollback execution

The current implementation already treats consequential effectful actions as governed and receipt-worthy. Coverage depth will continue to expand.

## Receipt Objects

The current codebase already defines a receipt record with fields for:

- task, step, and step attempt identity
- action type
- input and output artifact references
- policy result
- approval, decision, capability grant, and workspace lease references
- witness references
- proof metadata
- rollback support and rollback status

This is why Hermit can already say it is receipt-aware in a concrete sense.

## Proofs

Hermit's proof layer currently includes:

- task proof summaries
- receipt bundles
- proof export
- event-chain verification primitives
- context manifests associated with receipts

These are available through the current CLI:

```bash
hermit task proof <task_id>
hermit task proof-export <task_id>
```

The kernel ledger also maintains event hash chaining, which supports verification-oriented reasoning over task history.

## Observation And Uncertain Outcomes

An important action should not be blindly replayed when the outcome is uncertain.

Hermit's kernel direction explicitly makes room for:

- observation
- resolution
- reconcile paths
- unknown outcome handling

This matters because a side effect that may already have run is not safely handled by "just try again."

## Rollback

Rollback in Hermit is a first-class object and action path.

Current reality:

- rollback support exists for supported receipt classes
- rollback is not universal
- rollback itself is consequential and should be tracked as part of the durable story

Operator command:

```bash
hermit task rollback <receipt_id>
```

That means the repo has more than an abstract promise of recovery. It already contains the recovery object model and executable path for selected cases.

## What Can Be Claimed Today

Safe claims:

- Hermit already ships receipts
- Hermit already ships proof summaries and proof export
- Hermit already has event-chain verification primitives
- Hermit already supports rollback for selected receipts

Claims that should stay careful:

- "verifiable execution" is directionally true, but still maturing
- proof coverage and rollback coverage are not complete across all action classes

## Why This Matters

Agents become more useful as they do more consequential work.

They also become less trustworthy if the only durable record is a chat transcript and a pile of logs.

Hermit's receipt and proof path is its answer to that tradeoff.
