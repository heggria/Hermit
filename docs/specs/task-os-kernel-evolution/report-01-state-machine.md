# Report: Spec 01 — Formal State Machine

## Status: COMPLETE

## Summary

Replaced implicit state transitions scattered across controller.py and store_tasks.py with explicit, validated state machines for Task and StepAttempt. All existing behavior is preserved; validation is soft (log-only) to avoid breaking changes.

## Deliverables

### 1. TaskState StrEnum
- File: `src/hermit/kernel/task/state/enums.py`
- 8 states: `queued`, `running`, `blocked`, `planning_ready`, `paused`, `completed`, `failed`, `cancelled`
- Backward-compatible: StrEnum values match existing string literals exactly

### 2. StepAttemptState StrEnum
- File: `src/hermit/kernel/task/state/enums.py`
- 16 states: all step attempt statuses used across the kernel
- Includes intermediate execution states: `dispatching`, `contracting`, `preflighting`, `observing`, `reconciling`, `policy_pending`

### 3. WaitingKind StrEnum
- File: `src/hermit/kernel/task/state/enums.py`
- 6 waiting reasons: `awaiting_approval`, `awaiting_plan_confirmation`, `dependency_failed`, `input_changed_reenter_policy`, `reentry_resumed`, `observing`

### 4. Transition Tables
- File: `src/hermit/kernel/task/state/transitions.py`
- `VALID_TASK_TRANSITIONS`: 8 entries (one per TaskState), terminal states have empty target sets
- `VALID_ATTEMPT_TRANSITIONS`: 16 entries (one per StepAttemptState), terminal states have empty target sets

### 5. Transition Validators
- `validate_task_transition(current, target) -> bool` — returns False for invalid, no side effects
- `validate_attempt_transition(current, target) -> bool` — same for step attempts
- `require_valid_task_transition(current, target)` — raises `InvalidTransitionError`
- `require_valid_attempt_transition(current, target)` — raises `InvalidTransitionError`
- `InvalidTransitionError(ValueError)` — carries `entity_type`, `current`, `target` attributes

### 6. Wired into controller.py
- `mark_suspended()` — validates transition to `blocked` before applying
- `pause_task()` — validates transition to `paused` before applying
- `cancel_task()` — validates transition to `cancelled` before applying
- All validations are soft (structlog warning only, no blocking)

### 7. Wired into store_tasks.py
- `update_task_status()` — validates every task status change
- Soft validation: logs `invalid_task_transition` warning via structlog
- Does NOT block the transition — observability-first approach

### 8. State package re-exports
- `src/hermit/kernel/task/state/__init__.py` re-exports all enums, constants, and validators

## Test Results

- **107 tests** in `tests/unit/kernel/test_formal_state_machine.py`
- **100% coverage** on both `enums.py` and `transitions.py`
- **2292 existing kernel tests pass** with zero regressions
- All Ruff lint checks pass

### Test Coverage Breakdown
- Enum completeness: 8 TaskState values, 16 StepAttemptState values, 6 WaitingKind values
- String compatibility (StrEnum interop with plain strings)
- Transition table completeness (every state has an entry, targets are valid)
- Terminal states have no outgoing transitions
- Valid transition parametrized tests (21 task + 26 attempt transitions)
- Invalid transition parametrized tests (10 task + 9 attempt transitions)
- Self-transition rejection for all TaskState values
- Unrecognized state handling
- `require_*` variants raise `InvalidTransitionError` with correct attributes
- `InvalidTransitionError` message format
- Backward compatibility with `TERMINAL_TASK_STATUSES` from `outcomes.py`
- Backward compatibility with `_ACTIVE_TASK_STATUSES` / `_TERMINAL_TASK_STATUSES` from `store_tasks.py`
- Dict key interoperability
- Re-export through `__init__.py`

## Design Decisions

1. **Soft validation (log-only)**: The spec says "do NOT change any existing behavior". Raising errors would risk breaking running systems. Logging warnings provides observability while maintaining stability. A future spec can escalate to hard validation once the logs confirm no unexpected transitions.

2. **StrEnum for backward compatibility**: Python 3.11+ `StrEnum` ensures that `TaskState.RUNNING == "running"` and can be used as dict keys, set members, and in SQL queries without any code changes.

3. **Separate files for enums and transitions**: Keeps `enums.py` dependency-free (can be imported anywhere) while `transitions.py` imports from `enums.py`. Avoids circular imports.

4. **Frozen sets for state groups**: `TERMINAL_TASK_STATES`, `ACTIVE_TASK_STATES`, `TERMINAL_ATTEMPT_STATES` are `frozenset` for immutability and fast membership checks.

## Files Modified
- `src/hermit/kernel/task/state/enums.py` (new)
- `src/hermit/kernel/task/state/transitions.py` (new)
- `src/hermit/kernel/task/state/__init__.py` (updated: re-exports)
- `src/hermit/kernel/task/services/controller.py` (updated: validation in mark_suspended, pause_task, cancel_task)
- `src/hermit/kernel/ledger/journal/store_tasks.py` (updated: validation in update_task_status)
- `tests/unit/kernel/test_formal_state_machine.py` (new)
