# Report 08: Typed Blackboard Primitive

## Status: COMPLETE

## Summary

Implemented a typed blackboard as a kernel primitive for structured inter-step communication within a task. The blackboard supports posting, querying, superseding, and resolving typed entries with full event sourcing and SQLite persistence.

## Deliverables

### 1. BlackboardRecord + Enums (records.py)

- `BlackboardEntryType` (StrEnum): `claim`, `evidence`, `patch`, `risk`, `conflict`, `todo`, `decision`
- `BlackboardEntryStatus` (StrEnum): `active`, `superseded`, `resolved`
- `BlackboardRecord` dataclass with all spec fields: `entry_id`, `task_id`, `step_id`, `step_attempt_id`, `entry_type`, `content`, `confidence`, `supersedes_entry_id`, `status`, `resolution`, `created_at`

### 2. BlackboardService (new file)

- `post()` - Create new typed entry with validation (entry_type, confidence bounds)
- `query()` - Filter by task_id, optional entry_type and status; ordered by created_at
- `supersede()` - Marks old entry as superseded, creates new linked entry
- `resolve()` - Marks entry as resolved with resolution text
- All mutations emit kernel events (`blackboard.entry_posted`, `blackboard.entry_superseded`, `blackboard.entry_resolved`)

### 3. KernelStore Persistence

- New `blackboard_entries` SQLite table with index on `(task_id, entry_type, status)`
- CRUD methods: `insert_blackboard_entry`, `get_blackboard_entry`, `query_blackboard_entries`, `update_blackboard_entry_status`
- Migration `_migrate_blackboard_v15()` as safety net for existing databases
- Schema version bumped (blackboard introduced at v15)

### 4. Context Compiler Integration

- `ContextPack` gains `blackboard_entries: list[dict[str, Any]]` field
- `to_payload()` includes blackboard entries
- `compile()` accepts optional `blackboard_entries` parameter
- Blackboard entries affect pack hash (verified by test)

### 5. Tests

46 tests across 10 test classes:
- `TestBlackboardEntryType` - enum completeness and StrEnum behavior
- `TestBlackboardEntryStatus` - enum completeness
- `TestBlackboardRecord` - dataclass defaults and construction
- `TestBlackboardServicePost` - 10 tests: basic post, all types, validation, events, immutability
- `TestBlackboardServiceQuery` - 6 tests: filters, task scoping, ordering, combined filters
- `TestBlackboardServiceSupersede` - 5 tests: basic, errors, events, status transitions
- `TestBlackboardServiceResolve` - 4 tests: basic, errors, events, content preservation
- `TestBlackboardStorePersistence` - 7 tests: CRUD, JSON roundtrip, status updates
- `TestSchemaVersion` - schema version validation
- `TestCrossStepVisibility` - cross-step entry visibility
- `TestContextCompilerBlackboard` - 3 tests: inclusion, empty, hash impact

**Coverage: 100%** on `hermit.kernel.artifacts.blackboard`

## Files Modified

| File | Change |
|------|--------|
| `src/hermit/kernel/task/models/records.py` | Added `BlackboardEntryType`, `BlackboardEntryStatus`, `BlackboardRecord` |
| `src/hermit/kernel/artifacts/blackboard.py` | **New** - `BlackboardService` |
| `src/hermit/kernel/ledger/journal/store.py` | Added `blackboard_entries` table, CRUD methods, migration v15 |
| `src/hermit/kernel/context/compiler/compiler.py` | Added `blackboard_entries` to `ContextPack` and `compile()` |
| `tests/unit/kernel/test_blackboard.py` | **New** - 46 tests |

## Design Decisions

- **Task-scoped**: Blackboard entries are scoped to a single task; no inter-task sharing
- **Extensible types**: `BlackboardEntryType` uses StrEnum for type safety with string compatibility
- **Content immutability**: `post()` copies the content dict to prevent mutation after posting
- **Event sourcing**: All state changes emit events for auditability
- **Lazy imports**: Store CRUD methods use deferred imports to avoid circular dependencies
