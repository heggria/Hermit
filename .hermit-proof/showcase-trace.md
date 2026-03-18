# Hermit Governed Self-Evolution — Execution Trace

> Hermit reads a spec, autonomously modifies its own codebase under full kernel governance,
> and produces a verifiable proof chain with rollback-capable receipts.

## Pipeline

```
spec → parse → branch → hermit run (autonomous) → proof-export → PR
```

## Execution Log

```
==> Executing Hermit with spec: specs/demo-governed-hello.md

  ▸ write_file(path='src/hermit/GOVERNED_ITERATION.md', content="# Governed Iteration...")
    → ok
    [governed] tool=write_file action=write_local verdict=allow_with_receipt
               receipt=receipt_e191e79bc7bf decision=decision_586e473ca9a0
               grant=grant_a2584241154d risk=high

  ▸ bash(command='uv run pytest tests/unit/runtime/test_tools.py -q')
    → 14 passed in 3.91s
    [governed] tool=bash action=execute_command verdict=allow_with_receipt
               receipt=receipt_4b0f64360bdd decision=decision_eafe20da7982
               grant=grant_4ea5193ce2bc risk=critical

  ▸ iteration_summary(task_id='demo-governed-hello', status='success')
    → acceptance_results: all passed

==> Finding task ID...
    Task: task_f60e0232f028 (completed)

==> Exporting proof to .hermit-proof/demo-governed-hello.json
```

## Proof Summary

```
Task:       task_f60e0232f028
Status:     completed
Policy:     autonomous
Proof mode: hash_chained

Governance:
  Events:           113
  Steps:            1
  Decisions:        2
  Capability grants: 2
  Receipts:         2
  Approvals:        0

Chain Integrity:
  Valid:      yes
  Head hash:  46eba30c2dafa3b31fe25d0f35781e63...
  Event count: 113
```

## Receipts

| Receipt | Action | Result | Rollback | Status |
|---------|--------|--------|----------|--------|
| `receipt_e191e79bc7bf` | write_local | succeeded | yes | not_requested |
| `receipt_4b0f64360bdd` | execute_command | succeeded | no | not_requested |

## Governance Chain Per Tool Call

### write_file → GOVERNED_ITERATION.md

```
PolicyEngine  →  verdict: allow_with_receipt (autonomous profile)
                 action_class: write_local, risk: high
DecisionService → decision_586e473ca9a0 (execution_authorization)
CapabilityGrant → grant_a2584241154d (scoped to task workspace)
WorkspaceLease  → mutable lease on /Users/beta/work/Hermit/src/hermit
Executor        → prestate captured as artifact_* (rollback support)
ReceiptService  → receipt_e191e79bc7bf (hash_chained, baseline_verifiable)
                  rollback_supported=true, strategy=file_restore
```

### bash → pytest

```
PolicyEngine  →  verdict: allow_with_receipt (autonomous profile)
                 action_class: execute_command, risk: critical
DecisionService → decision_eafe20da7982 (execution_authorization)
CapabilityGrant → grant_4ea5193ce2bc (scoped to task workspace)
ReceiptService  → receipt_4b0f64360bdd (hash_chained, baseline_verifiable)
                  rollback_supported=false, strategy=manual_or_followup
```

## Rollback Verification

```
$ hermit task rollback receipt_e191e79bc7bf

{
  "rollback_id": "rollback_e4d75304c38c",
  "status": "succeeded",
  "result_summary": "Restored file state for src/hermit/GOVERNED_ITERATION.md."
}

$ ls src/hermit/GOVERNED_ITERATION.md
ls: No such file or directory    ← file correctly deleted by rollback
```

## Final Output

```
===========================================
  Iteration complete
===========================================
  Spec:   specs/demo-governed-hello.md
  Branch: iterate/demo-governed-hello
  Task:   task_f60e0232f028
  Status: completed
  Proof:  .hermit-proof/demo-governed-hello.json
  PR:     https://github.com/heggria/Hermit/pull/18
===========================================
```

## What Makes This Unique

1. **Every file write** goes through: Policy → Decision → Grant → Lease → Execute → Receipt
2. **Prestate capture** — the file's previous content is stored as an artifact before mutation
3. **Rollback** — `hermit task rollback <receipt-id>` restores the exact previous state
4. **Hash-chained proof** — 113 events linked by SHA-256 chain, tamper-evident
5. **The agent is modifying its own codebase** — governed self-evolution, not just chat+tools
