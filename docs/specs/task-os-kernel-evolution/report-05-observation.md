# Report 05: Observation Durability

## Status: Complete

## Summary

Made ObservationService kernel-native with durable tickets persisted in the SQLite journal, surviving restarts. Added timeout enforcement and restart recovery.

## Changes

### 1. ObservationTicketRecord (`src/hermit/kernel/task/models/records.py`)

Added `ObservationTicketRecord` dataclass with fields:
- `ticket_id`, `task_id`, `step_id`, `step_attempt_id`
- `observer_kind`, `status` (active/completed/timed_out/cancelled)
- `poll_after_seconds`, `hard_deadline_at`
- `ready_patterns`, `failure_patterns`, `ticket_data`
- `created_at`, `last_polled_at`, `resolved_at`

### 2. KernelStore observation_tickets table (`src/hermit/kernel/ledger/journal/store.py`)

- Schema bump: v15 -> v16 (note: another concurrent agent bumped further to v17 for budget tracking)
- Added `observation_tickets` table to `_KNOWN_KERNEL_TABLES`
- Added `_migrate_observation_tickets_v16()` migration method
- Added CRUD methods:
  - `create_observation_ticket()` -> emits `observation.created` event
  - `update_observation_progress()` -> emits `observation.polled` event
  - `resolve_observation()` -> emits `observation.resolved` event
  - `timeout_observation()` -> emits `observation.timed_out` event
  - `list_active_observation_tickets()` -> returns all active tickets
  - `get_observation_ticket()` -> returns single ticket by ID

### 3. ObservationService durability (`src/hermit/kernel/execution/coordination/observation.py`)

- Added optional `store` parameter to `__init__`
- Added `_recover_active_tickets()` called on `start()` -- queries active tickets from store, logs recovery count
- Added `_enforce_timeouts()` called on each `_tick()` -- checks `hard_deadline_at` exceeded, auto-marks as timed_out, fails step attempt with reason `observation_timeout`
- Added `persist_ticket()` -- persists observation ticket to durable store
- Added `resolve_ticket()` -- marks durable ticket as resolved
- Backward compatible: existing callers without `store` parameter continue to work

### 4. Tests (`tests/unit/kernel/test_observation_durability.py`)

27 tests covering:
- Schema version and table existence (2)
- create_observation_ticket with full params, events, defaults (3)
- update_observation_progress with polled_at and events (1)
- resolve_observation completed and cancelled (2)
- timeout_observation with events (1)
- list_active_observation_tickets filtering (2)
- get_observation_ticket not found (1)
- Restart recovery: active tickets survive DB reopen (2)
- ObservationService.persist_ticket with and without store (2)
- ObservationService.resolve_ticket with and without store (2)
- Timeout enforcement: past deadline, future deadline, no deadline (3)
- Recovery on start (1)
- Enforce timeouts with no store (1)
- Recover with no store (1)
- Migration compatibility (1)
- Multiple tickets per attempt (1)
- ObservationTicketRecord dataclass instantiation (1)

## Test Results

```
27 passed in 0.88s
```

All 73 observation-related tests pass (27 new + 46 existing `test_observation_handler.py`).

## Constraints Met

- Observation polling logic unchanged -- only storage layer added
- Existing ObservationService callers not broken (store parameter is optional)
- SQLite schema bump applied (v16, with forward-compatible migration)
- Restart recovery tested: active tickets persist across store reopens
- Timeout enforcement tested: past-deadline tickets auto-timeout with step attempt failure
