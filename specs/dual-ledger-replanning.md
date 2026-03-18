---
id: dual-ledger-replanning
title: "Dual task/progress ledger with stall detection and replanning"
priority: high
trust_zone: low
---

## Goal

Implement Magentic-One inspired dual-ledger orchestration: a Task Ledger (what needs to be done) and a Progress Ledger (what has been accomplished). When progress ledger detects stall patterns, the kernel triggers replanning — revising the task and potentially spawning alternatives via competition.

## Steps

1. Create `src/hermit/kernel/task/services/progress_ledger.py`:
   - `ProgressLedger` class:
     - `update(task_id, step_attempt_id, outcome, store)` → ProgressEntry
     - `detect_stall(task_id, store)` → StallDetection (REPEATED_FAILURE, NO_PROGRESS, CIRCULAR_TOOLS, BUDGET_EXHAUSTION)
     - `suggest_replan(task_id, stall)` → ReplanSuggestion

2. Create `src/hermit/kernel/task/services/replanner.py`:
   - `TaskReplanner` class:
     - `replan(task_id, suggestion, store)` → ReplanResult
     - `should_abort(task_id, stall_history)` → bool

3. Create `src/hermit/kernel/task/models/progress.py`:
   - `ProgressEntry`, `StallDetection`, `StallPattern`, `ReplanSuggestion`, `ReplanResult`

4. Integrate into KernelDispatchService — after each step, update progress and check stalls
5. Write tests in `tests/unit/kernel/test_dual_ledger.py` (>= 8 tests)

## Constraints

- Progress ledger is append-only
- Stall detection uses sliding window, not full history
- Competition only for critical stalls
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/task/services/progress_ledger.py` exists
- [ ] `src/hermit/kernel/task/services/replanner.py` exists
- [ ] `src/hermit/kernel/task/models/progress.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_dual_ledger.py -q` passes with >= 8 tests

## Context

- KernelDispatchService: `src/hermit/kernel/execution/coordination/dispatch.py`
- CompetitionService: `src/hermit/kernel/execution/competition/`
- SteeringProtocol: `src/hermit/kernel/signals/steering.py`
