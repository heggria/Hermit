# Approval Workflow

Demonstrates the governed approval pipeline: how tasks hit policy gates, how
operators approve or deny actions, and how to inspect the resulting receipt
trail.

## How Approvals Work

Hermit's kernel enforces a policy layer between the LLM's proposed actions and
actual execution. The flow looks like this:

```
LLM proposes action
  → Policy evaluator checks the action against active rules
    → If the action requires approval:
        → Task is SUSPENDED, waiting for operator decision
        → Operator approves or denies
        → On approval: execution proceeds, receipt is issued
        → On denial: step is marked denied, task may continue or abort
    → If no approval needed:
        → Execution proceeds directly
```

### What Triggers an Approval

Approvals are triggered by the kernel's policy evaluator. Common triggers
include:

- **Write operations** — file writes, destructive bash commands
- **Sensitive tool calls** — actions that modify state outside the sandbox
- **Policy rules** — custom rules defined in `~/.hermit/rules/`

### The Receipt Chain

Every approved (or denied) action produces a **receipt** containing:

- The original action request
- The policy evaluation result
- The operator's decision (approve/deny)
- A timestamp and hash linking it to the previous receipt

These receipts form a chain. The `hermit task proof` command verifies that the
chain is complete and untampered.

## Walkthrough

### 1. Run a Task That Requires Approval

```bash
hermit run "Create a file called hello.txt with the content 'Hello from Hermit'"
```

If write operations require approval in your policy config, Hermit will pause
and prompt for approval.

### 2. Approve or Deny

When the kernel suspends for approval, you will see a prompt. Approve with:

```bash
hermit task approve <task-id>
```

Or deny with:

```bash
hermit task approve <task-id> --deny
```

### 3. Inspect the Receipt Trail

After the task completes (or is denied), inspect the full trail:

```bash
hermit task show <task-id>
```

Each step shows its policy evaluation, approval decision, and execution
receipt.

### 4. Verify the Proof Chain

```bash
hermit task proof <task-id>
```

This confirms that every receipt in the chain is present and correctly hashed.

## Rollback

If a completed task produced undesirable side effects, Hermit supports
rollback for certain receipt types:

```bash
hermit task rollback <task-id>
```

Rollback is only available for actions whose receipts include reversible
execution records (e.g., file writes where the original content was captured).

## Key Concepts

| Concept | Description |
|---------|-------------|
| Policy evaluator | Checks proposed actions against rules before execution |
| Approval gate | Suspends execution until an operator approves or denies |
| Receipt | Immutable record of an action's authorization and execution |
| Proof chain | Hash-linked sequence of receipts for a task |
| Rollback | Reversal of a completed action using its receipt data |
