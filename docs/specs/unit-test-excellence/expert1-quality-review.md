# Expert Review: Test Quality Audit

**Reviewer**: Test Quality Expert (Opus 4.6)
**Date**: 2026-03-19
**Scope**: Sampled 10 test files across kernel, runtime, plugins, and apps zones
**Method**: Manual review of assertion quality, isolation, mocking, edge cases, naming, duplication, and flakiness risk

---

## Files Reviewed

1. `tests/unit/kernel/test_store_ledger_coverage.py` (984 lines)
2. `tests/unit/kernel/execution/test_supervision.py` (894 lines)
3. `tests/unit/runtime/test_sandbox_internals.py` (874 lines)
4. `tests/unit/runtime/test_budgets_coverage.py` (142 lines)
5. `tests/unit/plugins/feishu/test_feishu_adapter_extended.py` (1996 lines)
6. `tests/unit/kernel/execution/test_drift_handler.py` (830 lines)
7. `tests/unit/runtime/test_services_coverage.py` (258 lines)
8. `tests/unit/apps/test_companion_control.py` (1013 lines)
9. `tests/unit/runtime/test_hooks_engine_coverage.py` (163 lines)
10. `tests/unit/kernel/test_approval_copy.py` (1168 lines)

---

## CRITICAL Issues

### C1: Real `time.sleep(2)` in unit test causes unnecessary slowdown

**File**: `tests/unit/kernel/test_approval_copy.py:477`

```python
def fmt(facts):
    time.sleep(2)
    return "too late"
```

This test sleeps for 2 real seconds to verify a timeout path (with `formatter_timeout_ms=1`). In a suite of 5800+ tests running with xdist, a 2-second blocking call is wasteful. The test should mock time or use a threading.Event-based approach to simulate timeout without actual delay.

### C2: Global mutable state modified without cleanup isolation in `test_budgets_coverage.py`

**File**: `tests/unit/runtime/test_budgets_coverage.py:121-141`

```python
class TestRuntimeBudgetGlobals:
    def test_get_default_budget(self) -> None:
        configure_runtime_budget(None)
        budget = get_runtime_budget()
        ...

    def test_configure_custom_budget(self) -> None:
        custom = ExecutionBudget(tool_soft_deadline=99.0)
        configure_runtime_budget(custom)
        budget = get_runtime_budget()
        assert budget.tool_soft_deadline == 99.0
        # Restore default
        configure_runtime_budget(None)
```

These tests mutate a module-level global (`_RUNTIME_BUDGET`) and rely on manual cleanup via `configure_runtime_budget(None)`. If a test fails before the cleanup line, subsequent tests in the same process get corrupted state. This should use a pytest fixture with `yield` or `monkeypatch` to guarantee restoration.

---

## HIGH Issues

### H1: Widespread use of weak assertions (`assert result`, `assert result is not None`)

**Files**: `tests/unit/kernel/test_approval_copy.py:204-229` (17 occurrences in this file alone)

The `TestFormatInterval` class (lines 200-229) contains six tests that all assert only `assert result` -- i.e., "the return value is truthy." This verifies nothing about correctness. For a formatting function, the tests should assert the actual string content (e.g., `assert result == "1 hour"` or `assert "hour" in result`).

Similarly, `assert result is not None` appears 5+ times in `test_approval_copy.py` and across `test_budget_monotonicity_guard.py`, `test_reranker_coverage.py`, and `test_rules_readonly.py`. These assertions confirm only that the function returned something, not that it returned the right thing.

**Estimated count across the sampled files**: 30+ weak assertions project-wide.

### H2: Excessive use of `assert_called()` without verifying arguments

**File**: `tests/unit/plugins/feishu/test_feishu_adapter_extended.py` (25 occurrences)

Many tests in the Feishu adapter test file use bare `mock.assert_called()` without checking what arguments were passed. For example:

- Line 110: `mock_reply.assert_called()` -- does not verify the reply content
- Line 154: `mock_send.assert_called()` -- does not verify the error message text
- Line 607: `mock_patch.assert_called_once()` -- does not verify which card was patched

When a test's only meaningful assertion is "the function was called," it proves code paths are reached but not that they produce correct behavior. At minimum, use `assert_called_once_with(...)` or check `call_args` for the critical arguments.

### H3: `>= 1` assertions are too loose for deterministic data

**File**: `tests/unit/kernel/test_store_ledger_coverage.py` (lines 151, 182-186, 239, 296, 384-393, etc.)

Multiple tests insert a known number of records into a fresh SQLite database (via `tmp_path`) and then assert `len(result) >= 1`. Since these are integration tests against a fresh database, the exact count is deterministic and knowable. For example:

```python
# Line 151 -- we just inserted 1 artifact, so the exact count is 1
all_artifacts = store.list_artifacts()
assert len(all_artifacts) >= 1  # Should be: assert len(all_artifacts) == 1
```

This pattern appears in `test_store_tasks_coverage.py`, `test_memory_graph.py`, `test_reflection.py`, and others (30+ occurrences across the codebase).

### H4: Tests test private method internals rather than public behavior

**Files**:
- `tests/unit/kernel/test_store_ledger_coverage.py:39-48` -- tests `_artifact_class_for_kind`, `_artifact_media_type`, `_artifact_byte_size`, `_artifact_sensitivity` (all prefixed with `_`)
- `tests/unit/runtime/test_sandbox_internals.py` -- the entire file (874 lines) tests private methods: `_normalize_payload`, `_default_display_name`, `_pattern_match`, `_render_text`, `_progress_from_rule`, `_match_output_rules`, `_coarse_running_progress`, `_observing_payload`, `_has_observation_output`, `_should_extend_coarse_observation`, `_should_briefly_wait_for_completion`, `_store_terminal_result`, `_terminate_job`, `_output_text`, `_drain_pending_events`

While testing private methods gives fine-grained coverage, it creates brittle tests tightly coupled to implementation details. Any refactoring that changes internal method signatures or splits will break these tests even if external behavior is unchanged. The public `run()` and `poll()` methods are also tested (good), but the private method tests represent significant maintenance burden.

---

## MEDIUM Issues

### M1: Test helper duplication across files

Multiple test files define nearly identical helper functions for creating test objects:

- `_make_task()` in `test_supervision.py:20-33` and similar patterns in `test_drift_handler.py:27-39`
- `_make_adapter()` + `_make_msg()` + `_mock_store()` + `_mock_runner()` in `test_feishu_adapter_extended.py:24-82`

These helpers are defined locally in each file rather than in a shared `conftest.py` or test utility module. While this avoids cross-file coupling, the repetition across 16+ zone files means any model change (e.g., adding a required field to `TaskRecord`) requires updating helpers in many places.

### M2: Some test classes use `_setup_cached` instead of fixtures

**File**: `tests/unit/kernel/execution/test_supervision.py:522-540`

The `TestBuildTaskCase` class defines `_setup_cached()` as a helper that must be called at the start of every test method:

```python
class TestBuildTaskCase:
    def _setup_cached(self, service, mock_store, **cached_overrides):
        cached = _make_cached_projection(**cached_overrides)
        service.projections = MagicMock()
        ...
```

This is called in 14 out of 16 test methods in the class. It should be a pytest fixture or `@pytest.fixture(autouse=True)` to reduce boilerplate and ensure consistent setup.

### M3: Feishu adapter tests have deeply nested mock setups

**File**: `tests/unit/plugins/feishu/test_feishu_adapter_extended.py`

Many tests require 4-6 levels of mock wiring:

```python
# Lines 477-506: TestDispatchMessageSyncCompat.test_blocked_result_binds_topic
store = _mock_store(
    conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {}})}
)
runner.task_controller.store = store
runner.dispatch.return_value = SimpleNamespace(
    text="waiting",
    agent_result=SimpleNamespace(
        blocked=True, suspended=False, task_id="t1", approval_id="ap_1",
        execution_status="blocked",
    ),
)
```

This level of mock nesting suggests the tests are tightly coupled to implementation internals. While functional, they will be fragile against any refactoring of the adapter's internal data flow.

### M4: Missing negative/error assertions on "missing" entity tests

**Files**: `tests/unit/kernel/test_store_ledger_coverage.py:323-326, 396-398, 479-481, 647-649, 722-724, 818-825`

Tests like `test_update_capability_grant_missing`, `test_update_workspace_lease_missing`, etc. call a mutation method on a nonexistent ID and have no assertion at all:

```python
def test_update_capability_grant_missing(tmp_path: Path) -> None:
    store = _setup(tmp_path)
    # Should not raise
    store.update_capability_grant("nonexistent", status="consumed")
```

The implicit assertion is "doesn't raise," which is valid but should be explicit: add a comment explaining the expected behavior, or assert the return value if one exists.

### M5: Potential flakiness from `time.monotonic()` comparisons

**File**: `tests/unit/runtime/test_budgets_coverage.py:39-43`

```python
def test_soft_remaining_with_now(self) -> None:
    now = time.monotonic()
    d = Deadline(started_at=now, soft_at=now + 10.0, hard_at=now + 20.0)
    remaining = d.soft_remaining(now=now)
    assert abs(remaining - 10.0) < 0.1
```

This is good practice (passing `now` explicitly). However, tests at lines 82-89 call `d.soft_remaining()` and `d.hard_remaining()` without passing `now`, relying on the wall clock. Under heavy CI load or xdist contention, these could theoretically fail, though the 1000-second margins make this extremely unlikely in practice.

---

## LOW Issues

### L1: Some test names could be more descriptive

**File**: `tests/unit/runtime/test_hooks_engine_coverage.py`

- `test_string_value_enum` -- what about string value enums?
- `test_non_string_value_uses_str` -- uses str for what?

Better: `test_event_key_extracts_value_from_string_enum`, `test_event_key_falls_back_to_str_for_non_string_enum`.

### L2: `test_none_treated_as_empty` tests the wrong thing

**File**: `tests/unit/kernel/execution/test_supervision.py:124-125`

```python
def test_none_treated_as_empty(self) -> None:
    assert SupervisionService._trim("", 5) == ""
```

The test name says "None treated as empty" but it passes an empty string `""`, not `None`. Either the test name is wrong, or the test should actually pass `None` as the first argument.

### L3: `__del__` explicitly called in tests

**File**: `tests/unit/kernel/test_approval_copy.py:99, 462, 471, 483`

Multiple tests call `svc.__del__()` directly. While this works to test cleanup, it's not how Python normally invokes destructors. Consider using a context manager or explicit `.close()` method instead.

### L4: Some parametrize opportunities missed

**File**: `tests/unit/kernel/test_store_ledger_coverage.py:60-63`

```python
def test_artifact_media_type_action_request_kinds() -> None:
    for kind in ("action_request", "policy_evaluation", "environment", "environment.snapshot"):
        result = KernelLedgerStoreMixin._artifact_media_type(kind=kind, uri="noext")
        assert result == "application/json", f"Failed for kind={kind}"
```

This manually iterates test cases in a loop. Using `@pytest.mark.parametrize` would give individual test IDs per kind, making failures easier to diagnose.

---

## Summary Statistics

| Severity | Count | Key Theme |
|----------|-------|-----------|
| CRITICAL | 2 | Real sleep in tests; global state without cleanup guarantee |
| HIGH | 4 | Weak assertions; bare `assert_called()`; loose count checks; testing privates |
| MEDIUM | 5 | Helper duplication; manual setup; deep mock nesting; missing assertions; timing |
| LOW | 4 | Naming; wrong test body; `__del__` usage; missed parametrize |

## Overall Assessment

The new tests are **structurally sound**: good use of `tmp_path` for isolation, proper fixture scoping, no cross-test dependencies, and comprehensive coverage of edge cases and error paths. The `test_supervision.py` and `test_drift_handler.py` files are exemplary in their use of well-organized test classes, clear helpers, and thorough behavioral verification.

The primary quality concern is **assertion weakness**: too many tests verify "something was returned" or "some function was called" without verifying the actual values or arguments. This pattern inflates coverage numbers without proportionally increasing defect detection. The `>= 1` and `assert result` patterns are the most prevalent issues and would benefit from a systematic tightening pass.

The secondary concern is **coupling to implementation internals**: `test_sandbox_internals.py` (874 lines testing exclusively private methods) and the deep mock hierarchies in `test_feishu_adapter_extended.py` will create maintenance friction during any refactoring.

**Recommended priority**: Fix C1 and C2 immediately, then schedule a pass to tighten H1/H2/H3 assertions across all 16 zones.
