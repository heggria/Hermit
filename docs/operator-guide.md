# Operator Guide

Hermit is local-first and operator-trust-oriented.

That means the operator should be able to inspect work, resolve approvals, export proof material, and recover supported actions without digging through opaque logs.

This guide focuses on the operator-facing task kernel commands that already exist.

## List And Inspect Tasks

List recent tasks:

```bash
hermit task list
```

Inspect one task:

```bash
hermit task show <task_id>
```

This is the fastest way to see:

- task metadata
- pending or recent approvals
- recent decisions
- recent capability grants
- recent workspace leases for the task

## Inspect Task History

Show task events:

```bash
hermit task events <task_id>
```

This is useful when you want the durable sequence rather than the summarized case view.

## Inspect Receipts And Proofs

Show receipts:

```bash
hermit task receipts --task-id <task_id>
```

Show proof summary:

```bash
hermit task proof <task_id>
```

Export a proof bundle:

```bash
hermit task proof-export <task_id>
```

Use these when the question is not "what did the model say?" but:

- what changed
- what evidence and authority were involved
- whether the chain still verifies

## Resolve Approvals

Approve once:

```bash
hermit task approve <approval_id>
```

Approve and persist directory allowance for the current conversation:

```bash
hermit task approve-always-directory <approval_id>
```

Deny:

```bash
hermit task deny <approval_id> --reason "not safe"
```

Resume a blocked task from approval:

```bash
hermit task resume <approval_id>
```

## Work With Path Grants

List recent capability grants:

```bash
hermit task capability list
```

Approve a mutable workspace lease for the blocked attempt:

```bash
hermit task approve-mutable-workspace <approval_id>
```

Revoke a capability grant:

```bash
hermit task capability revoke <grant_id>
```

## Roll Back Supported Receipts

If a receipt supports rollback:

```bash
hermit task rollback <receipt_id>
```

Treat this carefully:

- rollback is consequential
- rollback support is not universal
- rollback itself becomes part of the durable task story

## Rebuild Projections

Rebuild one task projection:

```bash
hermit task projections-rebuild <task_id>
```

Rebuild all cached task projections:

```bash
hermit task projections-rebuild --all
```

This is useful when you want the operator view to be rebuilt from durable records.

## Memory Inspection

Hermit's operator surface also includes memory-governance inspection commands:

```bash
hermit memory inspect <memory_id>
hermit memory list --status active
hermit memory status
hermit memory rebuild
```

These are useful when debugging evidence-bound memory behavior rather than generic chat memory.

## Recommended Inspection Order

When something consequential happened and you want to understand it, a practical order is:

1. `hermit task show <task_id>`
2. `hermit task proof <task_id>`
3. `hermit task receipts --task-id <task_id>`
4. `hermit task events <task_id>`
5. `hermit task rollback <receipt_id>` if recovery is supported and appropriate

## Why The Operator Surface Matters

Hermit is not trying to hide the kernel behind a polished black box.

The operator surface is part of the thesis:

- visibility
- control
- durable explanations
- recoverability
