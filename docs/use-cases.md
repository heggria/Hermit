# Use Cases

Hermit is not the right answer for every agent workload.

It becomes interesting when the work is local-first, stateful, approval-sensitive, and worth inspecting later.

Good fits tend to have at least three traits:

- the work should persist as a task instead of dissolving into chat history
- side effects should be visible, governable, or recoverable
- artifacts and evidence matter more than conversational fluency alone

## Best-Fit Use Cases Today

### 1. Local Coding Agent With Boundaries

Scenario:

- you want a coding agent that can inspect a repo, propose changes, and operate locally
- you do not want broad ambient write authority to feel invisible

Why Hermit fits:

- task, approval, capability grant, workspace lease, and receipt semantics already exist
- the operator can inspect task history and proof summaries later
- workspace leases and governed execution align better with "ask before crossing the workspace boundary"

Why it matters:

The point is not only to get code written. The point is to know what changed, under what authority, and whether it can be rolled back.

### 2. Scheduled Agent Work With Receipts

Scenario:

- you want recurring jobs such as daily summaries, repo checks, or routine follow-ups
- you want more than "the scheduler ran"

Why Hermit fits:

- scheduled work lands in the kernel ledger
- task history, receipts, and proofs can be inspected after execution
- long-running `serve` mode and scheduler support already exist in the current repo

Why it matters:

Durable automation is easier to trust when it leaves receipts and a durable task trail.

### 3. Channel-Connected Operator Assistant

Scenario:

- work comes in through Feishu or another ingress surface
- requests may pause, resume, require approval, or continue over time

Why Hermit fits:

- conversation, ingress, and task objects already exist
- the repo is moving toward task-first continuation rather than chat-first continuation
- governed execution helps when a channel-triggered action becomes consequential

Why it matters:

Channel integrations become much more useful when the work survives as tasks instead of vanishing into message threads.

### 4. Evidence-Bound Personal Memory

Scenario:

- you want an assistant that carries forward preferences, conventions, and useful facts
- you do not want memory to become an opaque prompt pile

Why Hermit fits:

- beliefs and memory records are separate objects
- memory governance already includes scope, retention, expiry, and supersession logic
- context compilation already combines memory with artifacts and working state

Why it matters:

Memory becomes more useful when it stays inspectable and less dangerous when it stays governed.

## Where Hermit Is Not The Best Fit Yet

Hermit is not yet the strongest choice when you mainly need:

- polished multi-tenant SaaS platform behavior
- a finished hosted control plane
- a broad marketplace of pre-packaged integrations
- a stable public kernel API

Hermit is earlier than that. Its value today is structural clarity, not ecosystem completeness.

## How To Evaluate Fit

Ask these questions:

- Should this work survive as a durable task?
- Do approvals or scoped authority matter?
- Do I want artifacts and evidence to matter more than transcript alone?
- Do I expect to inspect what happened later?
- Would receipt and rollback semantics make this workload safer?

If the answer is mostly yes, Hermit is the kind of project worth evaluating now.

## Suggested Demo Flows

If you want homepage or demo material, the strongest examples are:

- a coding task that pauses for approval before writing outside the workspace
- a scheduled task that produces artifacts and a proof summary
- a channel-originated task that continues as task state rather than thread-only context
- a memory example that shows belief promotion and durable memory governance
