---
description: "The Hermit task lifecycle: creation, steps, attempts, policy evaluation, approval, execution, receipts, and proof generation."
---

# Task Lifecycle

Hermit's kernel is easiest to understand through the lifecycle of one task.

The primary unit is not a chat session. It is a durable task that can be created, resumed, paused, approved, receipted, and inspected later.

## The Main Path

```text
Ingress / operator action
        |
        v
      Task
        |
        v
      Step
        |
        v
   StepAttempt
        |
        v
Policy / Decision / Approval / WorkspaceLease / CapabilityGrant
        |
        v
    Execution
        |
        v
 Artifact / Receipt
        |
        v
 Proof / Rollback / Projection
```

## 1. Ingress

Work can begin from:

- CLI input
- interactive chat
- Feishu ingress
- scheduler
- webhook

The kernel direction is that all of these should end up in task semantics rather than remaining unrelated entrypoints.

Relevant first-class records:

- `Conversation`
- `Ingress`
- `Task`

## 2. Task

A task is the durable unit of work.

It carries:

- title and goal
- conversation linkage
- source channel
- policy profile
- current status
- parent or continuation relationships when relevant

This is the object the operator should be able to inspect long after the original request.

## 3. Step

A task advances through steps.

Steps give Hermit a durable way to distinguish major execution phases without collapsing everything into one opaque run.

## 4. Step Attempt

A step attempt is the concrete execution instance of a step.

This is one of Hermit's most important differentiators. Recoverable work does not have to be modeled as "the task reran somehow." It can be modeled as a specific attempt with:

- status
- waiting reason
- approval linkage
- decision linkage
- capability grant linkage
- workspace lease linkage
- state witness references

That gives the system better semantics for pause, resume, supersession, and rollback than a flat "running / done" model.

### StepAttemptState Enum

The formal state machine for step attempts is defined by the `StepAttemptState` enum in `src/hermit/kernel/task/state/enums.py`. The states are grouped by lifecycle phase:

**Initial / active states:**

| State | Description |
|---|---|
| `ready` | Attempt created, ready to begin execution |
| `waiting` | Blocked on an external condition (see `WaitingKind` below) |
| `running` | Actively executing |

**Governance pipeline states** (the attempt passes through these during policy evaluation and authorization):

| State | Description |
|---|---|
| `dispatching` | Being routed to the appropriate executor |
| `contracting` | Building a governance contract for the action |
| `preflighting` | Running pre-execution checks |
| `policy_pending` | Awaiting policy engine evaluation |
| `awaiting_approval` | Policy requires operator approval before proceeding |
| `awaiting_plan_confirmation` | Plan mode: waiting for operator to confirm the proposed plan |
| `verification_blocked` | Blocked on a verification gate |
| `receipt_pending` | Execution completed, waiting for receipt issuance |

**Post-execution states:**

| State | Description |
|---|---|
| `observing` | Monitoring an ongoing or uncertain outcome |
| `reconciling` | Reconciling results against expectations |

**Terminal states** (defined in `TERMINAL_ATTEMPT_STATES`):

| State | Description |
|---|---|
| `succeeded` | Attempt completed successfully |
| `completed` | Attempt finished (general completion) |
| `skipped` | Attempt was skipped (e.g. superseded before starting) |
| `failed` | Attempt failed |
| `superseded` | Replaced by a newer attempt on the same step |

### WaitingKind Enum

When a step attempt enters the `waiting` state, the `WaitingKind` enum describes the specific reason:

| Kind | Description |
|---|---|
| `awaiting_approval` | Waiting for operator approval |
| `awaiting_plan_confirmation` | Waiting for operator to confirm a plan |
| `dependency_failed` | A dependency step failed; this attempt cannot proceed |
| `input_changed_reenter_policy` | Input changed after policy evaluation; must re-enter policy |
| `reentry_resumed` | Previously waiting, now resumed for re-entry |
| `observing` | Waiting while observing an ongoing outcome |

### Source reference

Both enums are defined in `src/hermit/kernel/task/state/enums.py` alongside the related frozen sets `TERMINAL_ATTEMPT_STATES` and `ACTIVE_TASK_STATES`.

## 5. Policy, Decision, And Approval

Before execution happens, the kernel evaluates:

- policy rules based on task profile, action type, and constraints
- decision records for the authorization result
- approval handling when the policy requires operator sign-off
- workspace lease issuance
- scoped capability grant issuance

This is where the task lifecycle becomes governed execution rather than direct model-to-tool dispatch.

## 6. Execution

If authorized, the executor performs the concrete action.

Important outputs from this stage include:

- artifacts
- result summaries
- observation state when outcomes are uncertain
- receipt issuance for important actions

## 7. Receipt

A task does not end at tool execution. It ends with an inspectable outcome.

For important actions, that means a receipt tying together:

- task identity
- step attempt identity
- input and output references
- policy result
- approval and decision references
- capability grant and workspace lease references
- result code
- rollback relationship when supported

## 8. Proof And Rollback

After receipt issuance, Hermit can support:

- task proof summaries
- proof bundle export
- rollback execution for supported receipts
- operator case views and projection rebuilds

This is part of the lifecycle, not just a debugging afterthought.

## Task Status Thinking

The formal task state machine is defined by the `TaskState` enum in `src/hermit/kernel/task/state/enums.py`:

| State | Description |
|---|---|
| `queued` | Task created, waiting to be picked up |
| `running` | Task is actively executing |
| `blocked` | Task is blocked on an external condition |
| `planning_ready` | Task is ready for plan-mode execution |
| `paused` | Task has been paused by the operator |
| `completed` | Task finished successfully |
| `failed` | Task failed |
| `cancelled` | Task was cancelled |
| `budget_exceeded` | Task exceeded its budget allocation |
| `needs_attention` | Task requires operator attention |

Terminal states (defined in `TERMINAL_TASK_STATES`): `completed`, `failed`, `cancelled`.

Active states (defined in `ACTIVE_TASK_STATES`): `queued`, `running`, `blocked`, `planning_ready`.

The important idea is not the label list. It is that status is durable and tied to real kernel objects.

## Operator Surface

Useful commands during this lifecycle:

```bash
hermit task list
hermit task show <task_id>
hermit task events <task_id>
hermit task proof <task_id>
hermit task approve <approval_id>
hermit task rollback <receipt_id>
```

## Current State Vs Target State

Current repository:

- real task, step, and step-attempt records exist
- governance objects exist
- receipt, proof, and rollback paths exist

Target architecture:

- deeper unification across all runtime surfaces
- stronger lifecycle invariants everywhere
- broader coverage for observation, replay, and rollback semantics
