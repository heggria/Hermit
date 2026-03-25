# Spec 04: Workspace Lifecycle Service

## Goal
Implement a full workspace lifecycle service with acquire/release/extend/expire semantics, wired to the existing `workspace_lease_id` scaffolding.

## Current Problem
- `workspace_lease_id` field exists on StepAttemptRecord but WorkspaceLeaseService in kernel/authority/ only has basic acquire/release
- No workspace queuing — second mutable request just fails
- No automatic cleanup on task terminal
- No workspace extension mechanism
- Competition worktrees work but are isolated from the lease system

## Deliverables
1. **Enhanced WorkspaceLeaseService**:
   - `acquire(task_id, step_attempt_id, workspace_id, mode, resource_scope, ttl)` → WorkspaceLeaseRecord
   - `release(lease_id)` — explicit release
   - `extend(lease_id, additional_ttl)` — extend TTL
   - `expire_stale()` — background check for expired leases, auto-release
   - Mutable exclusive lock with queuing: if mutable lease exists, queue the request and emit `workspace.lease_queued` event
2. **Workspace cleanup on task terminal**:
   - When task reaches terminal state, release all active leases for that task
   - Emit `workspace.auto_released` event
3. **Wire to step execution**:
   - Before step execution, acquire workspace lease
   - After step completion, release lease
   - On step failure, release lease
4. **Competition integration**: Wire competition worktree creation through lease service
5. **Tests** — acquire/release/extend/expire/queue/cleanup

## Files to Modify
- `src/hermit/kernel/authority/workspaces/service.py` (enhance)
- `src/hermit/kernel/authority/workspaces/models.py` (queue model)
- `src/hermit/kernel/execution/executor/executor.py` (wire lease)
- `src/hermit/kernel/task/services/controller.py` (cleanup on terminal)
- `src/hermit/kernel/task/services/competition/service.py` (wire to lease)
- `tests/` (new test files)

## Constraints
- TTL default remains 300s
- Queuing is FIFO, no priority-based preemption
- Must not break existing competition worktree flow
