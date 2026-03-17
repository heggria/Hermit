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

That gives the system better semantics for pause, resume, supersession, and retry.

## 5. Governance Boundary

When a step attempt reaches consequential execution, the kernel can route it through:

- policy evaluation
- decision recording
- approval handling
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

The exact state machine is defined more formally in the kernel spec, but the practical current-state progression includes statuses such as:

- queued
- running
- blocked
- failed
- completed
- cancelled

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
