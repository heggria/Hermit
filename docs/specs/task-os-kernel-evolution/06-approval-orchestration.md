# Spec 06: Approval Orchestration

## Goal
Add approval timeout/escalation and nested approval delegation for multi-level task hierarchies.

## Current Problem
- No background process checks for expired approvals
- No escalation mechanism when approvals time out
- Delegated child tasks cannot have approvals auto-resolved by parent operator
- No approval batching across related steps

## Deliverables
1. **Approval timeout service**:
   - Background check for `drift_expiry` exceeded on pending approvals
   - On timeout: auto-deny with reason "approval_timeout", emit `approval.timed_out` event
   - Configurable escalation: before auto-deny, emit `approval.escalation_needed` event
2. **Nested approval delegation**:
   - `ApprovalDelegationPolicy` on DelegationRecord: rules for which child approvals parent auto-resolves
   - Example: `{"auto_approve": ["read_local"], "require_parent_approval": ["write_local"], "deny": ["network_write"]}`
   - When child approval is requested, check parent's delegation policy first
   - If policy says auto_approve → resolve immediately with `resolved_by: "delegation_policy"`
3. **Approval batching improvements**:
   - `request_batch()` already exists — add UI grouping metadata
   - `approve_batch()` — approve all pending in a batch with single decision
4. **Tests** — timeout, escalation, delegation policy, batch

## Files to Modify
- `src/hermit/kernel/policy/approvals/approvals.py` (timeout, delegation)
- `src/hermit/kernel/task/services/delegation.py` (delegation policy)
- `src/hermit/kernel/task/models/records.py` (DelegationRecord policy field)
- `tests/` (new test files)

## Constraints
- Auto-deny on timeout is the default; escalation is opt-in
- Delegation policy is deny-by-default for action classes not listed
- Must not change existing approval resolution flow
