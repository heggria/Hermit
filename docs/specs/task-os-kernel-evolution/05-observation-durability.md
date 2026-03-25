# Spec 05: Observation Durability

## Goal
Make ObservationService kernel-native with durable tickets persisted in the journal, surviving restarts.

## Current Problem
- ObservationService depends on runner/agent instances (runtime-coupled)
- Observation tickets are in memory, lost on restart
- No ledger events for observation lifecycle
- No timeout enforcement for observations

## Deliverables
1. **ObservationTicketRecord** in records.py:
   - `ticket_id`, `task_id`, `step_id`, `step_attempt_id`
   - `observer_kind`, `status` (active/completed/timed_out/cancelled)
   - `poll_after_seconds`, `hard_deadline_at`
   - `ready_patterns`, `failure_patterns`
   - `created_at`, `last_polled_at`, `resolved_at`
2. **Persist tickets in KernelStore**:
   - `create_observation_ticket()` → emit `observation.created` event
   - `update_observation_progress()` → emit `observation.polled` event
   - `resolve_observation()` → emit `observation.resolved` event
   - `timeout_observation()` → emit `observation.timed_out` event
3. **Timeout enforcement**:
   - Background check for `hard_deadline_at` exceeded → auto-timeout
   - On timeout, mark step attempt as failed with reason "observation_timeout"
4. **Recovery on restart**:
   - On service start, query all active observation tickets from store
   - Resume polling for active tickets
5. **Tests** — create/poll/resolve/timeout/restart-recovery

## Files to Modify
- `src/hermit/kernel/task/models/records.py` (ObservationTicketRecord)
- `src/hermit/kernel/execution/coordination/observation.py` (durability)
- `src/hermit/kernel/ledger/journal/store.py` (observation table + methods)
- `tests/` (new test files)

## Constraints
- Observation polling logic stays the same — only storage changes
- Must not break existing ObservationService callers
- SQLite schema bump required (v13 → v14)
