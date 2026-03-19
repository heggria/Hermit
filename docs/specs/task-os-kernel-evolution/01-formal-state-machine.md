# Spec 01: Formal State Machine

## Goal
Replace implicit state transitions scattered across controller.py with explicit, validated state machines for Task and StepAttempt.

## Current Problem
- State transitions are implicit in controller methods, not declared as a state machine
- No validation that a transition is legal before applying it
- StepAttempt statuses are string literals scattered across code, not an enum
- WaitingKind reasons are also string literals

## Deliverables
1. **TaskState StrEnum** in `kernel/task/state/` — all task states with docstrings
2. **StepAttemptState StrEnum** in `kernel/task/state/` — all attempt states
3. **WaitingKind StrEnum** in `kernel/task/state/` — all waiting reasons
4. **Transition tables** — `VALID_TASK_TRANSITIONS: dict[TaskState, set[TaskState]]` and `VALID_ATTEMPT_TRANSITIONS`
5. **Transition validator** — `validate_transition(current, target) -> bool` called before every state change
6. **Wire into controller.py** — all `mark_*` methods call validator before updating
7. **Wire into store_tasks.py** — state updates go through validator
8. **Tests** — 95%+ coverage of transition logic, including invalid transition rejection

## Files to Modify
- `src/hermit/kernel/task/state/` (new files)
- `src/hermit/kernel/task/models/records.py` (type annotations)
- `src/hermit/kernel/task/services/controller.py` (wire validator)
- `src/hermit/kernel/ledger/journal/store_tasks.py` (wire validator)
- `tests/` (new test files)

## Constraints
- Do NOT change any existing behavior — only add validation
- Keep backward compatibility — existing string values must match enum values
- No new dependencies
