# Report 06: Approval Orchestration

## Status: Complete

## Deliverables Implemented

### 1. Approval Timeout Service (`ApprovalTimeoutService`)

New class in `src/hermit/kernel/policy/approvals/approvals.py`:

- `check_expired()` scans pending approvals where `drift_expiry < now`
- On timeout: auto-deny with `reason: "approval_timeout"`, emit `approval.timed_out` event
- Escalation opt-in: when `escalation_enabled=True`, emits `approval.escalation_needed` event before auto-deny
- Returns structured results with `approval_id`, `task_id`, `timed_out_at`, `escalation_emitted`

### 2. Nested Approval Delegation

**Model** (`src/hermit/kernel/task/models/delegation.py`):
- New `ApprovalDelegationPolicy` dataclass with `auto_approve`, `require_parent_approval`, `deny` lists
- `resolve(action_class)` method returns resolution; deny-by-default for unlisted action classes
- `DelegationRecord` gains optional `approval_delegation_policy` field

**Service** (`src/hermit/kernel/task/services/delegation.py`):
- `delegate()` accepts optional `approval_delegation_policy` parameter
- New `check_delegation_approval_policy()` returns `(resolution, delegation_id)` tuple
- Policy included in `delegation.created` event payload when present

**ApprovalService integration** (`src/hermit/kernel/policy/approvals/approvals.py`):
- New `request_with_delegation_check()` method creates approval and auto-resolves based on delegation policy
- `auto_approve` -> immediately approves with `resolved_by: "delegation_policy"`
- `deny` -> immediately denies with `reason: "denied_by_delegation_policy"`
- `require_parent_approval` / `no_policy` -> stays pending

### 3. Batch Improvements

- `request_batch()` accepts optional `batch_metadata: dict` for UI grouping metadata
- New `approve_batch_ids()` method approves specific approval IDs directly

## Files Modified

| File | Change |
|------|--------|
| `src/hermit/kernel/task/models/delegation.py` | Added `ApprovalDelegationPolicy`, field on `DelegationRecord` |
| `src/hermit/kernel/task/services/delegation.py` | Added `approval_delegation_policy` param, `check_delegation_approval_policy()` |
| `src/hermit/kernel/policy/approvals/approvals.py` | Added `ApprovalTimeoutService`, `request_with_delegation_check()`, `approve_batch_ids()`, `batch_metadata` param |
| `tests/unit/kernel/test_approval_orchestration.py` | 32 new tests across 6 test classes |

## Constraints Verified

- Auto-deny on timeout is the default; escalation is opt-in via `escalation_enabled=True`
- Delegation policy is deny-by-default for action classes not listed
- No existing method signatures or behaviors were changed
- All new code is additive (new methods, new parameters with defaults, new class)

## Test Coverage

- **60 total tests** (32 new + 28 existing) all pass
- **100% coverage** on `approvals.py` and `delegation.py` (models)
- **32 new tests** covering:
  - `TestApprovalDelegationPolicy` (5 tests): resolution logic, deny-by-default
  - `TestDelegationRecordWithPolicy` (2 tests): field presence
  - `TestTaskDelegationServiceApprovalPolicy` (8 tests): delegation with/without policy, all resolution paths
  - `TestApprovalTimeoutService` (7 tests): expired, non-expired, escalation, mixed scenarios
  - `TestRequestWithDelegationCheck` (5 tests): all delegation resolution paths
  - `TestBatchImprovements` (5 tests): metadata, approve_batch_ids

## Verification

```
uv run pytest tests/unit/kernel/test_approval_orchestration.py tests/unit/kernel/test_approval_service.py -v
# 60 passed

uv run ruff check src/hermit/kernel/policy/approvals/approvals.py src/hermit/kernel/task/models/delegation.py src/hermit/kernel/task/services/delegation.py tests/unit/kernel/test_approval_orchestration.py
# All checks passed
```
