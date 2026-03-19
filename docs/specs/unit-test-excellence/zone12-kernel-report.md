# Zone 12: Kernel Verification, Policy Guards, and Misc Gaps — Test Report

## Summary

Added 206 unit tests across 10 new test files covering previously untested or under-tested kernel modules. All tests pass.

## Files Covered

### 1. `kernel/verification/proofs/merkle.py` (0% -> covered)
**Test file:** `tests/unit/kernel/test_merkle_proofs.py` (26 tests)
- Constants verification (proof mode strings, missing features tuple)
- Empty input returns None root
- Single bundle: root equals leaf hash, empty proof path
- Two bundles: correct root hash, correct sibling proofs (left/right positions)
- Three bundles: odd-count padding (last node duplicated), tree depth verification
- Four bundles: balanced tree depth
- Inclusion proof verification (reconstruct root from proof)
- Missing/None receipt_id edge cases

### 2. `kernel/verification/proofs/dag_proof.py` (0% -> covered)
**Test file:** `tests/unit/kernel/test_dag_proof_service.py` (12 tests)
- DAGProofBundle frozen dataclass validation, default fields
- Empty steps returns empty bundle
- Linear chain (A->B->C): root/leaf identification, receipt grouping by step
- Diamond DAG (A->B,C; B,C->D): single root/leaf
- Multiple receipts per step
- Multiple roots and leaves (parallel independent steps)
- Store interaction: correct parameters passed to list_steps, list_receipts, list_events

### 3. `kernel/policy/guards/rules_attachment.py` (47% -> covered)
**Test file:** `tests/unit/kernel/policy/test_rules_attachment.py` (14 tests)
- Non-attachment action classes return None
- Feishu adapter: allow_with_receipt verdict, reason code, receipt obligation, risk level from hint/default
- Non-adapter actors: deny verdict, error severity, no receipt, risk defaults
- Wrong adapter ID denied
- Edge cases: empty/missing actor kind and agent_id keys

### 4. `kernel/policy/guards/rules_planning.py` (45% -> covered)
**Test file:** `tests/unit/kernel/policy/test_rules_planning.py` (16 tests)
- Planning not required returns None
- Plan already selected returns None
- Non-gated action classes return None (parametrized over 5 classes)
- Gated action classes trigger approval_required (parametrized over 8 classes)
- Reason code, severity, obligations (receipt, preview, approval)
- Approval risk level from hint and default
- Approval packet structure
- Whitespace-only plan ref treated as empty

### 5. `kernel/policy/guards/rules.py` (~80% -> improved)
**Test file:** `tests/unit/kernel/policy/test_rules_main.py` (11 tests)
- Policy constants: rules version, strictness ordering
- Readonly profile: allows read_local, denies write_local/execute_command
- Delegation scope enforcement: allowed classes pass, disallowed denied, empty list = no restriction, no scope key = no enforcement
- Unclassified mutable action defaults to approval_required
- Autonomous profile: read_local allowed
- RuleOutcome dataclass defaults

### 6. `kernel/policy/guards/tool_spec_adapter.py` (60% -> covered)
**Test file:** `tests/unit/kernel/policy/test_tool_spec_adapter.py` (28 tests)
- `infer_action_class`: explicit class, readonly->read_local, non-readonly/no-class->unknown, empty string
- `normalize_scope_hints`: None/empty/well-known scopes, list of scopes, path-to-scope resolution (workspace, home, system, repo), deduplication, empty items
- `build_action_request`: basic fields from tool+context, no-context defaults, idempotent/supports_preview/requires_receipt propagation, workspace root, policy profile, plan ref from ingress, risk hint, conversation ID

### 7. `kernel/task/state/outcomes.py` (~65% -> covered)
**Test file:** `tests/unit/kernel/test_task_outcomes.py` (22 tests)
- `clean_runtime_text`: empty/None, plain text, session_time tag, feishu meta tags, both tags, blank lines, whitespace
- `trim_text`: short/exact/over limit, limit=1, None input, cleans before trimming
- `outcome_source_artifact_refs`: collection from receipts, deduplication, limit respect, empty/None refs
- `build_task_outcome`: non-terminal returns None, all terminal statuses accepted, no terminal event returns None, result_preview/result_text/completed_at extraction, outcome_summary priority (text > preview > default), source artifact refs, uses last terminal event

### 8. `kernel/signals/consumer.py` (~80% -> improved)
**Test file:** `tests/unit/kernel/test_signal_consumer_competition.py` (8 tests)
- Signal without goal skipped
- Competition path: high/critical risk uses competition service, critical risk uses competition
- High risk without competition service falls back to normal path
- Competition returns None record (consume not called)
- Low risk skips competition even when service available
- Conversation ID: from signal vs generated from signal_id

### 9. `kernel/execution/coordination/dispatch.py` (gaps -> improved)
**Test file:** `tests/unit/kernel/execution/test_dispatch_heartbeat.py` (12 tests)
- `report_heartbeat`: updates step attempt, exception logged not raised
- `check_heartbeat_timeouts`: no interval skipped, within interval not timed out, expired marks failed with correct status/reason, retries if allowed (max_attempts), propagates when max reached, task not failed if other steps remain, uses claimed_at fallback, no timestamps skipped, checks all 3 statuses

### 10. `kernel/ledger/journal/store_support.py` (~85% -> covered)
**Test file:** `tests/unit/kernel/test_store_support_coverage.py` (31 tests)
- `json_loads`: valid JSON, empty string, None, invalid JSON
- `canonical_json`: sorts keys, no spaces, unicode not escaped
- `canonical_json_from_raw`: valid JSON, None, empty string, invalid JSON fallback
- `sha256_hex`: string input (verified against known hash), bytes input, empty string
- `sqlite_optional_text`: None, string, int, float, bool, other type with default
- `sqlite_optional_float`: None, int, float, bool conversion, other type with default
- `sqlite_int`: int, string int, invalid, None, minimum constraint
- `sqlite_dict`: dict, OrderedDict, non-mapping, None
- `sqlite_list`: list, tuple, string/bytes rejected, None, non-sequence with default

## Test Execution

```
206 passed in 2.08s
```

All tests run in parallel via pytest-xdist with no failures.
