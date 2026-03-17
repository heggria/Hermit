# Quick Start

A minimal walkthrough of the Hermit task lifecycle: create a task, list it,
inspect it, and verify its proof chain.

## Overview

Hermit is a governed agent kernel. Every piece of work flows through a durable
**Task** object. Tasks produce **receipts** (records of what happened) and
**proofs** (cryptographic evidence that the receipts are authentic and
complete). This example walks through that flow end to end.

## Steps

### 1. Run a Task

```bash
hermit run "List the files in the current directory and summarize them"
```

`hermit run` creates a new task, executes it through the governed pipeline
(policy evaluation, capability grants, tool execution), and prints the result.
The task and all associated events are persisted to the local ledger at
`~/.hermit/`.

### 2. List Tasks

```bash
hermit task list
```

Shows all tasks in the local ledger. Each task has an ID, status, and a brief
summary. Find the ID of the task you just ran.

### 3. Show Task Details

```bash
hermit task show <task-id>
```

Displays the full task record: every step, every tool call, every receipt.
This is the audit trail for the work that was performed.

### 4. Verify the Proof Chain

```bash
hermit task proof <task-id>
```

Verifies the proof bundle for the task. Hermit checks that all receipts are
present, that their hashes chain correctly, and that no records have been
tampered with. A passing proof check means the task's history is intact.

## What You Should See

- `hermit run` produces output from the LLM and any tool calls it made.
- `hermit task list` shows the task with status `completed`.
- `hermit task show` displays the full step-by-step execution trace.
- `hermit task proof` reports whether the proof chain is valid.

## Next Steps

- Try the [approval-workflow](../approval-workflow/) example to see policy
  gates in action.
- Run `hermit chat` for an interactive session where you can issue multiple
  tasks.
