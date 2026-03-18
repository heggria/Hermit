---
id: governed-task-delegation
title: "Governed parent-child task delegation with authority transfer"
priority: high
trust_zone: low
---

## Goal

Enable governed task delegation: a parent task can spawn child tasks with delegated authority, scoped capability grants, and receipted ownership transfer. This builds on the existing `parent_task_id` field in TaskRecord and the subagent identity work.

## Steps

1. Create `src/hermit/kernel/task/services/delegation.py`:
   - `TaskDelegationService` class with methods:
     - `delegate(parent_task_id, child_goal, delegated_principal_id, scope_constraints)` → child_task_id
       - Creates child task with parent_task_id set
       - Issues a `DelegationGrant` (extends CapabilityGrant with delegation-specific constraints)
       - Records delegation_event in ledger
     - `recall(parent_task_id, child_task_id, reason)` → revokes delegation
       - Marks child as "recalled", revokes outstanding grants
       - Records recall_event
     - `child_completed(child_task_id)` → notifies parent
       - Rolls up child receipts into parent's evidence case
       - Updates parent step status
     - `list_children(parent_task_id)` → list of child task summaries

2. Create `src/hermit/kernel/task/models/delegation.py`:
   - `DelegationRecord`: parent_task_id, child_task_id, delegated_principal_id, scope_constraints, status (active/completed/recalled), delegation_grant_ref
   - `DelegationScope`: allowed_action_classes, allowed_resource_scopes, max_steps, budget_tokens

3. Add delegation store methods to `KernelStore`:
   - `save_delegation(record)`, `get_delegation(parent_task_id, child_task_id)`, `list_delegations(parent_task_id)`
   - Use existing `memory_records` or add a `delegations` table (prefer existing tables if feasible)

4. Wire into TaskController:
   - `decide_ingress()` should check if incoming task matches an active delegation scope
   - Child task creation should inherit parent's policy_profile unless overridden

5. Write tests in `tests/unit/kernel/test_task_delegation.py`:
   - Test delegation creates child task with correct parent_task_id
   - Test scope constraints are enforced (child cannot exceed parent's scope)
   - Test recall revokes child's grants
   - Test child completion rolls up to parent
   - Test delegation grant is receipted
   - Test list_children returns correct hierarchy

## Constraints

- Do NOT break existing task creation paths — delegation is opt-in
- Child tasks must not be able to escalate beyond parent's authority scope
- Use `write_file` for ALL file writes
- All delegation operations must produce ledger events

## Acceptance Criteria

- [ ] `src/hermit/kernel/task/services/delegation.py` exists
- [ ] `src/hermit/kernel/task/models/delegation.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_task_delegation.py -q` passes with >= 6 tests
- [ ] Delegation creates receipted child tasks with parent_task_id linkage

## Context

- TaskRecord already has `parent_task_id` field: `src/hermit/kernel/task/models/records.py`
- Subagent identity: `src/hermit/kernel/authority/identity/service.py`
- CapabilityGrantService: `src/hermit/kernel/authority/grants/service.py`
- TaskController: `src/hermit/kernel/task/services/controller.py`
