# Report 04: Workspace Lifecycle Service

## Status: COMPLETED

## Summary

Implemented full workspace lifecycle service with acquire/release/extend/expire/queue semantics, wired to existing `workspace_lease_id` scaffolding. Added automatic workspace cleanup on task terminal states.

## Deliverables

### 1. Enhanced WorkspaceLeaseService

**File:** `src/hermit/kernel/authority/workspaces/service.py`

New methods added:
- `extend(lease_id, additional_ttl)` - Extends TTL of an active lease. Validates lease exists, is active, and not expired. Computes new expiry as `max(current_expires_at, now) + additional_ttl`. Emits `workspace.lease_extended` event.
- `expire_stale()` - Background-callable method that scans all active leases and expires any past TTL. Emits `workspace.auto_expired` events. Triggers queue processing for affected workspaces. Returns list of expired lease IDs.
- `release_all_for_task(task_id)` - Releases all active leases for a task. Emits `workspace.auto_released` events with `reason: task_terminal`. Triggers queue processing. Returns list of released lease IDs.
- `queue_position(workspace_id)` - Returns number of pending queue entries for a workspace.
- `_process_queue(workspace_id)` - Internal method that serves the next queued request when workspace becomes available (FIFO order).

**Queuing behavior change:** When a mutable lease request conflicts with an existing active mutable lease, instead of raising `WorkspaceLeaseConflict`, the service now:
1. Creates an in-memory `WorkspaceLeaseQueueEntry` (FIFO)
2. Emits `workspace.lease_queued` event with `blocked_by_lease_id`
3. Raises `WorkspaceLeaseQueued` (subclass of `WorkspaceLeaseConflict`) with `queue_entry_id` and `position`

This is backward-compatible since `WorkspaceLeaseQueued` inherits from `WorkspaceLeaseConflict`.

### 2. WorkspaceLeaseQueueEntry model

**File:** `src/hermit/kernel/authority/workspaces/models.py`

New `@dataclass` for queued lease requests with fields: `queue_entry_id`, `workspace_id`, `task_id`, `step_attempt_id`, `root_path`, `holder_principal_id`, `mode`, `resource_scope`, `ttl_seconds`, `queued_at`, `status`, `metadata`.

### 3. Workspace cleanup on task terminal

**File:** `src/hermit/kernel/task/services/controller.py`

- `TaskController.__init__` now accepts optional `workspace_lease_service` parameter
- `finalize_result()` calls `release_all_for_task()` when task reaches terminal status
- Cleanup is best-effort (catches Exception) to never break task finalization
- Emits `workspace.task_terminal_cleanup` event with released lease IDs

### 4. Updated exports

**File:** `src/hermit/kernel/authority/workspaces/__init__.py`

Added `WorkspaceLeaseQueued` and `WorkspaceLeaseQueueEntry` to `__all__` and lazy `__getattr__`.

### 5. Competition integration

The competition service (`src/hermit/kernel/execution/competition/service.py`) already works through workspace creation via `CompetitionWorkspaceManager`. The lease system now provides automatic cleanup when competition tasks reach terminal states, since `TaskController.finalize_result()` releases all leases for the task.

## Test Coverage

**File:** `tests/unit/kernel/authority/test_workspace_lifecycle.py`

42 new tests covering:
- `TestWorkspaceLeaseQueueEntry` (2 tests) - dataclass defaults and custom values
- `TestWorkspaceLeaseQueued` (2 tests) - exception subclassing and queue info
- `TestWorkspaceLeaseServiceExtend` (7 tests) - active/nonexistent/released/expired/no-expiry/future-expiry/post-update-not-found
- `TestWorkspaceLeaseServiceExpireStale` (6 tests) - marks expired/skips valid/skips no-expiry/returns IDs/empty/events
- `TestWorkspaceLeaseServiceReleaseAllForTask` (5 tests) - release all/returns IDs/events/empty/correct query
- `TestWorkspaceLeaseServiceQueuing` (7 tests) - conflict queues/position empty/multiple/after release/FIFO/readonly not queued/events
- `TestWorkspaceLeaseServiceReleaseWithQueue` (5 tests) - triggers processing/no lease/active mutable blocks/expired skipped/re-queue on conflict
- `TestWorkspaceCleanupOnTaskTerminal` (8 tests) - completion/failure/running/no service/error swallowed/cancelled/already finalized/events

**Coverage:** 99% statement coverage on `workspaces/service.py` (139 statements, 0 missed; 46 branches, 1 partial).

All 63 workspace tests pass (42 new + 21 existing).

## Constraints Met

- TTL default remains 300s
- Queuing is FIFO, no priority-based preemption
- Existing competition worktree flow is not broken (existing tests pass)
- `WorkspaceLeaseQueued` is a subclass of `WorkspaceLeaseConflict` for backward compatibility
- Queue is in-memory (not persisted) - suitable for current single-process architecture
- All events use `store.append_event()` for audit trail

## Events Added

| Event Type | Entity Type | When |
|---|---|---|
| `workspace.lease_queued` | `workspace_lease` | Mutable request queued behind existing |
| `workspace.lease_extended` | `workspace_lease` | Lease TTL extended |
| `workspace.auto_expired` | `workspace_lease` | Stale lease auto-expired |
| `workspace.auto_released` | `workspace_lease` | Lease released on task terminal |
| `workspace.lease_dequeued` | `workspace_lease` | Queued request served |
| `workspace.task_terminal_cleanup` | `task` | All leases released on task terminal |
