# Demo Flows

This guide is for homepage demos, terminal recordings, and first-pass evaluation.

The goal is not to explain every subsystem. The goal is to make Hermit's difference visible quickly:

- a task survives as a durable object
- important actions leave receipts and proof material
- approvals and rollback stay operator-visible instead of hiding inside tool logs

## Demo 1: Fastest Repository Evaluation

Use this when someone asks, "What does Hermit actually feel like?"

Prerequisites:

- Python `3.13+`
- a configured provider such as `codex` or `claude`

Recommended command flow:

```bash
make install

export HERMIT_PROVIDER=codex
export OPENAI_API_KEY=sk-...
export HERMIT_MODEL=gpt-5.4

hermit run "Summarize the current repository and leave a durable task record"
hermit task list
hermit task show <task_id>
hermit task proof <task_id>
hermit task receipts --task-id <task_id>
```

What to point out while recording:

- the work becomes a task you can inspect later
- `task show` is about operator visibility, not chat replay
- `task proof` and `task receipts` make the post-execution trail concrete

## Demo 2: Approval To Receipt

Use this when you want to show that Hermit does not flatten consequential actions into invisible tool calls.

Suggested flow:

1. Run a task that is likely to cross a policy boundary in your local setup.
2. Inspect the blocked task with `hermit task show <task_id>`.
3. Resolve the approval with `hermit task approve <approval_id>` or `hermit task deny <approval_id> --reason "not safe"`.
4. Re-open the task and inspect the resulting decision and receipt trail.

Commands:

```bash
hermit task show <task_id>
hermit task approve <approval_id>
hermit task show <task_id>
hermit task receipts --task-id <task_id>
hermit task proof <task_id>
```

Notes:

- the exact approval-triggering prompt depends on your current local policy and tool mix
- if your current environment is permissive, use a task that crosses a workspace boundary or another consequential effect surface
- do not claim universal approval coverage; show the policy path that exists today

## Demo 3: Rollback-Aware Recovery

Use this only with a receipt class that currently supports rollback.

Suggested flow:

1. Complete a task that emits a rollback-capable receipt.
2. Inspect the receipt with `hermit task receipts --task-id <task_id>`.
3. Execute rollback with `hermit task rollback <receipt_id>`.
4. Re-open the task proof or task events and show that recovery is part of the durable record.

Commands:

```bash
hermit task receipts --task-id <task_id>
hermit task rollback <receipt_id>
hermit task proof <task_id>
```

Notes:

- rollback is real, but not universal
- treat rollback support as bounded by receipt class, not as a blanket project claim

## Recording Checklist

If you are preparing homepage or launch assets, the highest-signal captures are:

- one `hermit task show` screenshot with approvals, capability grants, workspace leases, and receipts visible
- one short terminal clip covering `task proof` and `task receipts`
- one rollback clip only if you can truthfully show a supported receipt class

Good recording order:

1. run a task
2. open the task
3. show proof or receipts
4. show approval or rollback if applicable

## Anti-Demo Mistakes

Avoid these:

- spending the first minute explaining architecture before showing a command
- claiming the `v0.1` kernel spec is fully shipped
- using "verifiable" without showing the actual proof surface
- implying rollback exists for every side effect

Hermit demos land best when they start with a real task record and only then explain the kernel ideas behind it.
