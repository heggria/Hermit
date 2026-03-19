# Zone 7 -- kernel/policy/ Test Coverage Report

## Summary

Zone 7 covers `src/hermit/kernel/policy/`, focusing on the three files with the largest coverage gaps. All three target files were brought to 95%+ coverage, with the overall policy directory reaching 95%.

## Results

| File | Before | After | Stmts | Missed |
|------|--------|-------|-------|--------|
| `approvals/approval_copy.py` | 42% | **99%** | 327 | 0 |
| `approvals/approvals.py` | 63% | **100%** | 104 | 0 |
| `evaluators/derivation.py` | 78% | **99%** | 171 | 0 |
| **policy/ directory total** | 60% | **95%** | 1233 | 42 |

The only uncovered items remaining are branch-partial misses (conditional jump targets), not statement misses.

## Test Files Created/Modified

### New: `tests/unit/kernel/test_approval_copy.py` (143 tests)

Comprehensive coverage of `ApprovalCopyService`:

- **Init/teardown**: with/without formatter, locale, executor lifecycle
- **Static helpers**: `_summarize_text`, `_safe_int`, `_format_datetime_value`
- **Datetime formatting**: ISO parsing, Z suffix, invalid dates
- **Interval formatting**: hours (1/multi), minutes (1/multi), seconds (1/multi)
- **Facts extraction**: all field paths, missing fields, None values, tool_input variants
- **Copy mapping**: valid/invalid mappings, section parsing edge cases
- **Section construction**: empty sections, non-list, non-dict entries, empty items after strip
- **Formatter integration**: dict return, string return, empty string, None, exceptions, timeout
- **Template copy branches**: all 14 template branches (git push, rm/trash/del, generic command, sensitive paths for .env/.ssh/.gnupg/Library, outside workspace, single/multi path, single/multi host, packet with/without summary, fallback with/without tool name)
- **Scheduler copy**: create/update/delete with all field combinations
- **Scheduler sections**: all three tool types with full field coverage, enabled/disabled states, cron timing
- **Contract sections**: objective, expected_effects, rollback_expectation, evidence_sufficiency (status/score int/float/gaps), authority (approval_route, resource_scope, current_gaps, drift_expiry)
- **Blocked message / model prompt**: rendering with various inputs

### New: `tests/unit/kernel/test_approval_service.py` (28 tests)

Comprehensive coverage of `ApprovalService`:

- **Init**: governed vs non-governed store detection
- **Request**: approval creation and ID return
- **Approve/deny methods**: approve, approve_once (alias), approve_mutable_workspace, deny with/without reason
- **Resolution flow**: no get_approval attr, get_approval returns None, idempotent receipt_ref return, governed receipt issuance, updated returns None
- **Receipt issuance**: with/without decision_ref, None decision, evidence_refs filtering
- **Static methods**: `_resolution_reason` (granted once/mutable, denied with/without reason, None mode), `_result_summary` (all four branches)
- **Batch operations**: request_batch (batch_id creation, consistency), approve_batch (matching/non-matching batch_id)

### Modified: `tests/unit/kernel/test_policy_derivation.py` (88 tests, +35 new)

Added coverage for previously missed branches:

- **derive_request**: file tools (read_file, write_file, write_hermit_file), sensitive path detection, outside workspace detection, kernel path detection, bash/execute_command/vcs_mutation action classes, empty command/path, non-dict tool_input
- **_is_kernel_path**: OSError on resolve, OSError on workspace resolve
- **_resolve_target**: OSError handling
- **_inside_workspace**: OSError handling
- **_outside_workspace_root**: OSError on resolve
- **_grant_candidate_prefix**: OSError on resolve
- **_extract_hosts**: shlex ValueError fallback
- **_extract_command_paths**: shlex ValueError fallback, empty command, shell separators
- **_extract_embedded_python_paths**: Path literal with/without segments, Path.home() segments, OSError on resolve
- **VCS operations**: git commit, checkout, push detection
- **Python write patterns**: 9 patterns (write_text, write_bytes, mkdir, touch, open, os.remove, os.unlink, shutil.rmtree, unlink)

## Pre-existing Failures

3 pre-existing test failures unrelated to Zone 7 work:
- `test_store_ledger_coverage.py::test_artifact_class_for_kind`
- `test_store_ledger_coverage.py::test_create_artifact_auto_derives_fields`
- `test_store_tasks_coverage.py::test_check_dag_cycles_detects_cycle`

## Remaining Coverage Gaps in policy/

Files below 90% that were not in scope for this zone:

| File | Coverage | Notes |
|------|----------|-------|
| `guards/rules_attachment.py` | 47% | 5 missed / 11 stmts |
| `guards/rules_planning.py` | 45% | 6 missed / 14 stmts |
| `guards/tool_spec_adapter.py` | 60% | 14 missed / 44 stmts |
| `permits/authorization_plans.py` | 86% | 4 missed / 43 stmts |

These are smaller files and could be addressed in a follow-up zone.
