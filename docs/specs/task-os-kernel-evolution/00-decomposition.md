# Task OS Kernel Evolution — 10 Sub-domain Decomposition

> Generated: 2026-03-19
> Based on: research-kernel-internals.md, research-governance-systems.md, research-frontier-patterns.md

## Priority Matrix

| # | Sub-domain | Priority | Impact | Effort | Code Area |
|---|-----------|----------|--------|--------|-----------|
| 1 | Formal State Machine | P0 | High | M | kernel/task/state/, kernel/task/models/ |
| 2 | DAG Topology Mutation | P0 | Critical | L | kernel/task/services/dag_*, ledger/journal/ |
| 3 | Verification-Driven Scheduling | P0 | High | M | kernel/verification/, kernel/task/services/dag_execution |
| 4 | Workspace Lifecycle Service | P1 | High | M | kernel/authority/workspaces/ |
| 5 | Observation Durability | P1 | High | M | kernel/execution/coordination/observation |
| 6 | Approval Orchestration | P1 | High | M | kernel/policy/approvals/ |
| 7 | Memory-Receipt Integration | P1 | Medium | S | kernel/context/memory/, kernel/verification/receipts/ |
| 8 | Typed Blackboard Primitive | P2 | High | M | kernel/artifacts/ (new) |
| 9 | Communication Budget & Monotonicity Guard | P2 | Medium | S | kernel/policy/ (new guards) |
| 10 | Durable Execution Enhancements | P2 | Medium | M | kernel/execution/, kernel/ledger/ |

## Dependency Graph

```
[1: State Machine] ──→ [2: DAG Mutation] ──→ [3: Verification Scheduling]
                   ──→ [4: Workspace Lifecycle]
                   ──→ [6: Approval Orchestration]

[5: Observation Durability] (independent)
[7: Memory-Receipt Integration] (independent)
[8: Typed Blackboard] (independent)
[9: Budget & Monotonicity] (independent)
[10: Durable Execution] ──depends on──→ [1: State Machine]
```

## Parallelism Plan

Can run in parallel (no code overlap):
- Group A: [1, 5, 7, 8, 9] — no shared files
- Group B (after 1 completes): [2, 4, 6, 10]
- Group C (after 2 completes): [3]

Since agents will use Hermit for execution and Hermit handles file-level locking,
we can launch all 10 in parallel with Hermit managing conflicts.
