---
id: step-up-authorization
title: "Progressive step-up authorization with minimal initial permits"
priority: high
trust_zone: low
---

## Goal

Implement step-up authorization: tasks start with minimal capability permits (read-only by default), and the kernel dynamically requests additional CapabilityGrants only when the execution path requires elevated privileges. This follows the MCP OAuth step-up pattern, reducing the blast radius of early task steps.

## Steps

1. Create `src/hermit/kernel/policy/permits/step_up.py`:
   - `StepUpAuthorizer` class:
     - `initial_permit(task_id, goal_analysis)` → PermitScope
     - `request_escalation(task_id, step_attempt_id, needed_action_class, reason)` → EscalationResult
     - `current_scope(task_id)` → PermitScope
     - `revoke_escalation(task_id, action_class, reason)` → bool

2. Create `src/hermit/kernel/policy/permits/step_up_models.py`:
   - `PermitScope`: task_id, allowed_action_classes (set), escalation_history, created_at, last_escalated_at
   - `EscalationRequest`: task_id, step_attempt_id, needed_action_class, reason, current_scope
   - `EscalationResult`: approved (bool), new_scope, escalation_id, approver, rationale

3. Integrate into PolicyEngine.evaluate() — check current_scope before standard rules
4. Each escalation produces a receipt (action_type="scope_escalation")
5. Include escalation history in proof summary

6. Write tests in `tests/unit/kernel/test_step_up_authorization.py`:
   - Test initial permit is read-only by default
   - Test escalation request for write_local action
   - Test approved escalation expands scope
   - Test denied escalation blocks execution
   - Test current_scope includes all approved escalations
   - Test revoke_escalation narrows scope
   - Test escalation produces receipt
   - Test proof includes escalation history

## Constraints

- Initial permit MUST be minimal (read_local + delegate_reasoning only)
- Escalation MUST go through approval workflow — no auto-escalation
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/policy/permits/step_up.py` exists
- [ ] `src/hermit/kernel/policy/permits/step_up_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_step_up_authorization.py -q` passes with >= 8 tests

## Context

- PolicyEngine: `src/hermit/kernel/policy/evaluators/engine.py`
- CapabilityGrantService: `src/hermit/kernel/authority/grants/service.py`
- ApprovalService: `src/hermit/kernel/policy/approvals/approvals.py`
