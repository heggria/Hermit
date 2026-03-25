# Expert 4: Security & Correctness Review

Review of 10 new test files across unit, integration, and e2e layers for security hygiene, correctness, and resource safety.

## Files Reviewed

1. `tests/unit/kernel/execution/test_contract_executor.py`
2. `tests/unit/kernel/policy/test_rules_shell.py`
3. `tests/unit/kernel/policy/test_rules_filesystem.py`
4. `tests/unit/kernel/policy/test_rules_governance.py`
5. `tests/unit/kernel/execution/test_state_persistence.py`
6. `tests/unit/kernel/execution/test_recovery_handler.py`
7. `tests/unit/kernel/test_durable_execution.py`
8. `tests/unit/kernel/test_health_monitor.py`
9. `tests/unit/kernel/test_cross_encoder_reranker.py`
10. `tests/unit/kernel/test_event_chain_concurrency.py`
11. `tests/e2e/test_dispatch_recovery_e2e.py`
12. `tests/e2e/test_memory_production_path_e2e.py`

---

## Findings

### MEDIUM: Temp file not cleaned up in concurrency test

**File:** `tests/unit/kernel/test_event_chain_concurrency.py:14-17`

```python
def _make_file_store() -> KernelStore:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        pass
    return KernelStore(Path(tmp.name))
```

The `delete=False` named temp file is never cleaned up. The `store` object is created but never closed, and the file persists on disk after the test. This is called twice (once per test function), leaving two orphaned `.db` files per test run. Unlike `tmp_path` fixture files which pytest cleans up, `NamedTemporaryFile` with `delete=False` requires explicit cleanup.

**Recommendation:** Use `tmp_path` fixture instead (as other test files correctly do), or add a `try/finally` with `os.unlink` and `store.close()`.

---

### MEDIUM: KernelStore not always closed in concurrency tests

**File:** `tests/unit/kernel/test_event_chain_concurrency.py:31-78` and `:81-111`

Both `test_concurrent_appends_produce_valid_chain` and `test_concurrent_appends_to_different_tasks` create `KernelStore` instances via `_make_file_store()` but never call `store.close()`. This leaves SQLite connections open. While Python's GC will eventually clean these up, in a large test suite with `pytest-xdist` parallelism, this can exhaust file descriptor limits.

**Recommendation:** Wrap in `try/finally` with `store.close()`, or convert to fixtures using `tmp_path`.

---

### MEDIUM: Mock fidelity concern in test_state_persistence.py

**File:** `tests/unit/kernel/execution/test_state_persistence.py:305-317`

```python
def test_returns_empty_dict_when_no_envelope(self, ...):
    attempt = FakeAttempt(context={}, resume_from_ref=None)
    store.get_step_attempt.return_value = attempt
    result = persistence._load_runtime_snapshot_envelope(attempt)
    # Patch to return empty envelope
    persistence._load_runtime_snapshot_envelope = MagicMock(return_value={})
    result = persistence.load_suspended_state("attempt_1")
    assert result == {}
```

This test patches `_load_runtime_snapshot_envelope` on the instance mid-test, then calls `load_suspended_state`. The first call to `_load_runtime_snapshot_envelope` is made but its result is discarded. The test is actually verifying that when `_load_runtime_snapshot_envelope` returns `{}`, `load_suspended_state` also returns `{}`. This tests the mock, not the real code path. If `load_suspended_state` has any logic between loading the envelope and returning, that logic is bypassed.

**Recommendation:** Test the actual code path by setting up the store mock to trigger the empty-envelope scenario naturally, rather than patching the internal method.

---

### LOW: Hardcoded `/tmp/workspace` path in test helper

**File:** `tests/unit/kernel/execution/test_recovery_handler.py:28`

```python
"workspace_root": "/tmp/workspace",
```

This is a hardcoded path used only as a string value in a `TaskExecutionContext` dataclass (never accessed on the filesystem). No actual file I/O occurs at this path so there is no security risk, but using a non-existent hardcoded path could mask bugs if code ever validates that the workspace exists.

---

### LOW: Hardcoded `file:///tmp/*.json` URIs in mock objects

**Files:**
- `tests/unit/kernel/execution/test_state_persistence.py:388,645,675,753,829,843,869`
- `tests/unit/kernel/execution/test_recovery_handler.py:519`

Multiple tests use `SimpleNamespace(uri="file:///tmp/snapshot.json")` and similar patterns. These URIs are only used as string identifiers passed to mocked `artifact_store.read_text()` calls -- the actual filesystem is never accessed. No security risk, but the pattern could confuse future developers into thinking real file I/O occurs.

---

### LOW: `time.time()` race window in parametrized test

**File:** `tests/unit/kernel/execution/test_contract_executor.py:194-198`

```python
@pytest.mark.parametrize(
    "expiry_at,expected",
    [
        pytest.param(time.time() - 100, True, id="past-expiry"),
        pytest.param(time.time() + 3600, False, id="future-expiry"),
```

`time.time()` is evaluated at parameter collection time, not at test execution time. For a test with a 3600-second future window, this is practically safe. However, the `time.time() - 100` case has a theoretical (extremely unlikely) race if test collection is delayed by >100 seconds before execution. This is a negligible risk but worth noting for precision.

---

## Positive Findings (No Issues)

### No Hardcoded Secrets
All 12 files reviewed contain zero real API keys, tokens, passwords, or credentials. Test identifiers like `"req-test-001"`, `"task-1"`, `"conv-1"` are clearly synthetic.

### No Real Network Calls
No tests make actual HTTP/HTTPS requests. All external dependencies (LLM providers, web services) are properly mocked via `MagicMock` or use local SQLite databases.

### No Real Subprocess Execution
No tests execute actual shell commands. The policy rule tests (`test_rules_shell.py`) test the *policy evaluation logic* for shell commands, not the execution of those commands.

### Proper Use of `tmp_path` Fixture
The majority of tests (9 out of 12 files) correctly use pytest's `tmp_path` fixture for any file-system-backed state (SQLite databases, memory files, artifact directories). This ensures automatic cleanup. The exception is `test_event_chain_concurrency.py` noted above.

### No Path Traversal Risk
No tests construct file paths from user input or external data. All paths are either from `tmp_path` or hardcoded mock strings that never touch the filesystem.

### Assertion Correctness
All assertions reviewed are logically sound and test the intended behavior:
- Policy rule tests correctly verify verdict, risk_level, reason codes, and obligation flags
- Recovery handler tests verify both return values and side effects (mock call assertions)
- Contract executor tests verify state transitions and event emissions
- No inverted assertions or wrong comparison values found

### Mock Accuracy
Mocks generally represent real objects faithfully:
- `FakeAttempt` dataclass in `test_state_persistence.py` mirrors real `StepAttemptRecord` fields
- `SimpleNamespace` objects used for lightweight stubs match the attribute access patterns of real objects
- `MagicMock` specs are used where needed (e.g., `test_contract_executor.py:163` uses `spec=` to restrict available methods)

### Resource Management in E2E Tests
`test_memory_production_path_e2e.py` consistently uses `try/finally` blocks with `store.close()` for every `KernelStore` instance. `test_cross_encoder_reranker.py` similarly closes all stores.

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 0     | No critical security issues found |
| HIGH     | 0     | No high-severity issues found |
| MEDIUM   | 3     | Temp file leak (1), unclosed SQLite connections (1), mock fidelity (1) |
| LOW      | 3     | Hardcoded paths in mocks (2), time.time() race (1) |

**Overall Assessment:** The new test suite has strong security hygiene. No secrets, no real network calls, no real subprocess execution, no path traversal risks. The only actionable items are the resource cleanup issues in `test_event_chain_concurrency.py` (use `tmp_path` instead of `NamedTemporaryFile`) and the mock-patching pattern in `test_state_persistence.py` that reduces test fidelity.
