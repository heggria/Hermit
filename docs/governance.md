# Governance

Hermit treats governance as an execution primitive, not as a UI decoration.

The model may propose an action. It does not automatically get authority to perform it.

For consequential work, the kernel evaluates policy, records decisions, requests approval when required, issues a scoped capability grant, and only then dispatches the executor.

This is the core law:

**models reason; the kernel authorizes and executes**

That law is what separates Hermit from agent runtimes that rely on ambient process authority and post-hoc logs.

## Why Governance Lives In The Kernel

In many systems, approval is treated as a front-end feature. Hermit is moving in the opposite direction.

Hermit wants governance to survive:

- chat turns
- retries
- pauses
- resumptions
- service restarts
- operator inspection

That requires governance objects to be durable, local, and task-scoped.

## Current Governance Objects

The current repository already contains records for:

- `Decision`
- `Approval`
- `CapabilityGrant`
- `WorkspaceLease`

These work alongside task and step-attempt records so authority is attached to concrete execution context.

## Execution Law

The governed execution path is:

`action request -> policy evaluation -> decision -> approval if required -> workspace lease -> scoped capability grant -> execution -> receipt`

Important implications:

- the model is not the final authority
- effectful execution is not just "tool call allowed"
- approvals are durable records, not transient prompts
- authority should be scoped to the concrete action and resource envelope

## Policy Evaluation

Hermit's policy layer classifies actions and determines whether they are:

- allowed
- denied
- allowed only with obligations
- blocked pending approval

Examples of consequential action classes include:

- local writes
- command execution
- network writes
- credentialed API calls
- VCS mutation
- memory promotion

The exact policy surface is still evolving, but the current executor already uses policy evaluation as a real kernel path.

## Decisions

A decision records a consequential judgment.

In practice this is where Hermit can retain:

- verdict
- reason
- evidence references
- related policy result
- related approval and action class

This matters because "why did it happen?" should not require reverse-engineering raw logs.

## Approvals

Approvals in Hermit are first-class execution records.

They are attached to task and step-attempt context and can be inspected later. Approval handling already exists in the current codebase and CLI.

Operator actions include:

```bash
hermit task approve <approval_id>
hermit task deny <approval_id> --reason "not safe"
hermit task resume <approval_id>
```

Approval is not just permission. It is part of the durable execution story.

## Scoped Authority

Hermit aims for scoped authority rather than ambient authority.

The current implementation already uses:

- **capability grants** for scoped action authorization
- **workspace leases** for mutable or scoped workspace authority

This is the practical answer to "what authority allowed it?"

The point is not perfect least privilege everywhere today. The point is that authority is becoming explicit, durable, and inspectable.

## Witness And Drift

Some actions should not simply resume after a pause without checking that the execution preconditions still hold.

This is why the kernel direction includes:

- state witness references
- drift detection
- supersession instead of silent continuation

This is especially important for delayed approvals and write-like actions.

## What Hermit Ships Today

Safe claims:

- policy evaluation is already in the executor path
- approval objects and approval resolution are already implemented
- decision, principal, capability grant, and workspace lease records already exist
- the task CLI already exposes governed execution to operators

Careful claims:

- governance is materially real in the codebase
- governance is not yet a fully settled public contract across every runtime surface

## Why This Matters

Governance is not there to slow the agent down for style points.

It is there because high-trust agent work needs durable answers to:

- who allowed this
- under what scope
- based on what evidence
- can we inspect it later
- can we recover if it was wrong

That is the category Hermit is trying to be unusually strong in.
