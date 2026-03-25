# Merged Expert Verdict: Unit Test Excellence

**Chief Reviewer**: Claude Opus 4.6
**Date**: 2026-03-19
**Input**: 4 expert reviews (Quality, Architecture, Performance, Security)
**Test suite**: 5834 tests, ~57.7s serial execution

---

## 1. Consolidated Issue List

### CRITICAL

| # | Issue | Confidence | Category | File:Line | Recommended Fix | Disposition |
|---|-------|-----------|----------|-----------|----------------|-------------|
| C-1 | Real `time.sleep(2)` in approval copy formatter timeout test | HIGH (Expert 1 + Expert 3) | performance / quality | `tests/unit/kernel/test_approval_copy.py:477` | Reduce to `time.sleep(0.1)` -- the timeout is 1ms so any sleep >1ms triggers it | MUST-FIX |
| C-2 | Global mutable state (`_RUNTIME_BUDGET`) modified without fixture-based cleanup | MEDIUM (Expert 1 only, but failure mode is severe) | quality | `tests/unit/runtime/test_budgets_coverage.py:121-141` | Wrap in a fixture with `yield` or use `monkeypatch.setattr` to guarantee restoration even on test failure | MUST-FIX |

### HIGH

| # | Issue | Confidence | Category | File:Line | Recommended Fix | Disposition |
|---|-------|-----------|----------|-----------|----------------|-------------|
| H-1 | Real subprocess `time.sleep()` totaling 5.26s in sandbox tests | MEDIUM (Expert 3, corroborated by Expert 1 general sleep concern) | performance | `tests/unit/runtime/test_tools.py:117,183,212,248` | Reduce sleep durations by 50-75% (e.g., `sleep(2.0)` to `sleep(0.3)`) | SHOULD-FIX |
| H-2 | Thread-based `time.sleep()` polling in MCP await tests totaling 2.53s | LOW (Expert 3 only) | performance | `tests/unit/plugins/mcp/test_hermit_mcp_server.py:592-664` | Reduce timeout values: `timeout=1` to `timeout=0.1`, `sleep(0.3)` to `sleep(0.05)` | SHOULD-FIX |
| H-3 | Weak assertions: `assert result`, `assert result is not None`, `>= 1` on deterministic data (30+ occurrences) | MEDIUM (Expert 1, partially corroborated by Expert 4 positive assertion finding for other files) | quality | `test_approval_copy.py:204-229`, `test_store_ledger_coverage.py:151,182-186,239,296,384-393` and others | Tighten to exact value assertions: `== 1`, `== "1 hour"`, etc. | SHOULD-FIX |
| H-4 | Bare `assert_called()` without argument verification (25 occurrences in Feishu tests) | LOW (Expert 1 only) | quality | `tests/unit/plugins/feishu/test_feishu_adapter_extended.py:110,154,607` and 22 others | Use `assert_called_once_with(...)` or inspect `call_args` for critical arguments | SHOULD-FIX |
| H-5 | Kernel tests import runtime `ToolSpec` -- bidirectional layer coupling | LOW (Expert 2 only, but reflects production code debt) | architecture | `tests/unit/kernel/policy/test_tool_spec_adapter.py:16`, `test_executor_coverage.py:24`, and 5 others | Track as architectural debt; consider moving `ToolSpec` to kernel or shared contracts | DEFER |
| H-6 | Kernel tests import plugin types (`MemoryEntry`, `ScheduledJob`, etc.) | LOW (Expert 2 only) | architecture | `tests/unit/kernel/test_memory_governance.py:8`, `test_planner_kernel.py:10`, and 3 others | Elevate types to kernel-level contracts or use local stubs | DEFER |
| H-7 | Entire `test_sandbox_internals.py` (874 lines) tests only private methods | LOW (Expert 1 only) | quality | `tests/unit/runtime/test_sandbox_internals.py` (full file) | Accept for now; flag for review during any sandbox refactoring | DEFER |

### MEDIUM

| # | Issue | Confidence | Category | File:Line | Recommended Fix | Disposition |
|---|-------|-----------|----------|-----------|----------------|-------------|
| M-1 | Temp file leak: `NamedTemporaryFile(delete=False)` without cleanup | MEDIUM (Expert 4, corroborated by Expert 3 xdist resource concern) | security / quality | `tests/unit/kernel/test_event_chain_concurrency.py:14-17` | Replace with `tmp_path` fixture | SHOULD-FIX |
| M-2 | KernelStore not closed in concurrency tests (open SQLite connections) | MEDIUM (Expert 4 + Expert 2 private access concern on same file) | security / quality | `tests/unit/kernel/test_event_chain_concurrency.py:31-111` | Add `try/finally` with `store.close()` or use fixtures | SHOULD-FIX |
| M-3 | 324 KernelStore instantiations use `tmp_path` disk I/O instead of `:memory:` | LOW (Expert 3 only) | performance | 75 test files across `tests/unit/kernel/` | Switch to `:memory:` where disk persistence is not tested; ~2-4s savings | DEFER |
| M-4 | Test helper duplication (`_make_task()`, `_make_adapter()`, etc.) across 16+ files | LOW (Expert 1 only) | quality | `test_supervision.py:20-33`, `test_drift_handler.py:27-39`, `test_feishu_adapter_extended.py:24-82` | Extract to shared `conftest.py` or utility module | DEFER |
| M-5 | `tests/fixtures/task_kernel_support.py` is a "god fixture" spanning all layers | LOW (Expert 2 only) | architecture | `tests/fixtures/task_kernel_support.py` | Split into layer-specific fixture modules | DEFER |
| M-6 | Global conftest monkeypatches `KernelStore.__init__` for resource tracking | LOW (Expert 2 only) | architecture | `tests/conftest.py:20-33` | Consider a factory fixture pattern instead | DEFER |
| M-7 | Mock fidelity concern: test patches internal method then tests the mock, not real code | LOW (Expert 4 only) | quality | `tests/unit/kernel/execution/test_state_persistence.py:305-317` | Test via store mock setup that triggers empty-envelope scenario naturally | SHOULD-FIX |
| M-8 | Direct access to private `KernelStore` internals (`_get_conn()`, `_existing_tables()`) | LOW (Expert 2 only, but Expert 1 also flagged private method testing) | architecture | `test_observation_durability.py:43,517`, `test_budget_monotonicity_guard.py:353,403`, and 6 others | Accept for migration/schema tests; prefer public API for data setup | DEFER |
| M-9 | Deeply nested mock setups (4-6 levels) in Feishu adapter tests | LOW (Expert 1 only) | quality | `tests/unit/plugins/feishu/test_feishu_adapter_extended.py:477-506` | Accept for now; refactor when adapter internals change | DEFER |
| M-10 | `_setup_cached()` helper called in 14/16 methods instead of using fixtures | LOW (Expert 1 only) | quality | `tests/unit/kernel/execution/test_supervision.py:522-540` | Convert to `@pytest.fixture(autouse=True)` | DEFER |

### LOW

| # | Issue | Confidence | Category | File:Line | Recommended Fix | Disposition |
|---|-------|-----------|----------|-----------|----------------|-------------|
| L-1 | Test name `test_none_treated_as_empty` passes `""` not `None` | LOW (Expert 1) | quality | `tests/unit/kernel/execution/test_supervision.py:124-125` | Fix test name or test body | DEFER |
| L-2 | MCP await tests not marked `@pytest.mark.slow` despite 2.5s runtime | LOW (Expert 3) | performance | `tests/unit/plugins/mcp/test_hermit_mcp_server.py` | Add `@pytest.mark.slow` markers | DEFER |
| L-3 | Missed `@pytest.mark.parametrize` opportunities | LOW (Expert 1) | quality | `tests/unit/kernel/test_store_ledger_coverage.py:60-63` | Convert loop-based test cases to parametrize | DEFER |
| L-4 | Direct `__del__()` calls in tests | LOW (Expert 1) | quality | `tests/unit/kernel/test_approval_copy.py:99,462,471,483` | Consider `.close()` or context manager instead | DEFER |
| L-5 | Hardcoded `/tmp/workspace` paths in mock objects | LOW (Expert 4) | security | `tests/unit/kernel/execution/test_recovery_handler.py:28` | No actual risk (string-only); leave as-is | DEFER |
| L-6 | `time.time()` evaluated at parameter collection, not execution | LOW (Expert 4) | quality | `tests/unit/kernel/execution/test_contract_executor.py:194-198` | Negligible risk with 3600s window; no action needed | DEFER |

---

## 2. Cross-Expert Agreements

These findings were flagged by multiple experts, giving highest confidence:

1. **`time.sleep(2)` in `test_approval_copy.py:477`** -- Expert 1 (CRITICAL: unnecessary slowdown) + Expert 3 (HIGH: 2s wall time). Both recommend reducing the sleep duration. This is the highest-confidence actionable finding.

2. **Private method testing creates brittle, implementation-coupled tests** -- Expert 1 (HIGH: `test_sandbox_internals.py` tests 15 private methods) + Expert 2 (MEDIUM: underscore-prefixed methods on services, `_get_conn()` on stores). Both agree this is a maintainability concern; both acknowledge it is sometimes acceptable for critical internal logic.

3. **Resource cleanup in `test_event_chain_concurrency.py`** -- Expert 4 (MEDIUM: temp file leak + unclosed stores) + Expert 3 (parallelism compatibility concern for resource exhaustion). Both point to the same file needing `tmp_path` fixture adoption.

4. **Real `time.sleep()` durations are too long** -- Expert 1 (C1) + Expert 3 (H-1, H-2, H-3) agree that multiple test files use unnecessarily long sleep durations that inflate suite time by ~10s total.

---

## 3. Cross-Expert Disagreements

### Private method testing: Tolerate or refactor?

- **Expert 1** leans toward "these create significant maintenance burden" (flagged as HIGH).
- **Expert 2** takes a nuanced position: "For high-criticality private methods like `_compute_signature`, `_recover_interrupted_attempts`, this is acceptable." For simpler ones, prefer public interface testing.
- **Expert 4** did not flag this, and positively noted that mock fidelity is generally good.

**Resolution**: Expert 2's nuanced approach is correct. Testing critical internal logic directly is pragmatic; the concern is valid for trivial private helpers. No blanket refactor needed. The `test_sandbox_internals.py` file (874 lines, 100% private methods) remains a borderline case -- acceptable but should be reviewed during any sandbox refactoring.

### Assertion quality: How strict?

- **Expert 1** flagged weak assertions as HIGH across many files.
- **Expert 4** reviewed 12 files and found "all assertions logically sound."

**Resolution**: No contradiction -- they reviewed different file sets. Expert 1 sampled files with the weakest assertions (Feishu adapter, approval copy, store ledger). Expert 4 sampled policy rule tests, recovery handlers, and contract executors which have strong assertions. The weak assertion problem is real but localized to specific files.

### KernelStore instantiation: Performance concern or proper isolation?

- **Expert 3** flagged 324 `tmp_path` instantiations as a performance issue (M-1, 2-4s overhead).
- **Expert 4** positively noted `tmp_path` usage as "proper" and "ensures automatic cleanup."

**Resolution**: Both are correct. `tmp_path` is the right isolation approach. The optimization to `:memory:` is valid where disk persistence is not being tested, but this is an optimization, not a correctness issue. Defer to post-merge.

---

## 4. Innovation Assessment

| Dimension | Score | Notes |
|-----------|-------|-------|
| **Test patterns** | 7/10 | Good use of `SimpleNamespace` for lightweight fakes, proper fixture scoping, `@pytest.mark.slow` on known-slow tests. Lost points for missing `parametrize` opportunities and manual setup patterns. |
| **Coverage strategy** | 8/10 | Comprehensive edge case coverage: timeout paths, error paths, missing entities, concurrent access. Both happy and unhappy paths tested. Lost points for assertion weakness in some files. |
| **Fixture design** | 6/10 | Per-test isolation is excellent. Global conftest with `KernelStore.__init__` monkeypatch is creative but fragile. Helper duplication across files indicates missing shared fixture layer. No shared kernel conftest. |
| **Parallelism safety** | 8/10 | xdist compatible: all `tmp_path` based, no shared filesystem state, process isolation handles module-level mutations. Lost points for `_RUNTIME_BUDGET` global without fixture cleanup (C-2) and `_server = None` mutations in MCP/webhook tests. |

**Overall Innovation Score: 7.3 / 10**

The test suite demonstrates solid engineering in its isolation model and edge-case coverage. The primary innovation gap is in assertion quality (too many existence-only checks) and fixture reuse (duplication over extraction).

---

## 5. Final Verdict

### MUST-FIX (Block Merge) -- 2 items

| # | Issue | Effort | Risk if Unfixed |
|---|-------|--------|-----------------|
| C-1 | Reduce `time.sleep(2)` to `time.sleep(0.1)` in `test_approval_copy.py:477` | 5 min | 2s wasted per test run; signals poor test hygiene |
| C-2 | Use `monkeypatch` or fixture for `_RUNTIME_BUDGET` in `test_budgets_coverage.py:121-141` | 15 min | Test failure can corrupt global state for subsequent tests in same process |

### SHOULD-FIX (Fix before merge, will not block) -- 6 items

| # | Issue | Effort | Risk if Unfixed |
|---|-------|--------|-----------------|
| H-1 | Reduce subprocess sleep durations in `test_tools.py` | 15 min | 5.3s wasted per run |
| H-2 | Reduce MCP await sleep/timeout values | 10 min | 2.5s wasted per run |
| H-3 | Tighten weak assertions (`assert result` to exact values) in `test_approval_copy.py` and `test_store_ledger_coverage.py` | 1 hr | Assertions pass vacuously; bugs hide behind truthy checks |
| H-4 | Add argument verification to bare `assert_called()` in Feishu adapter tests | 1 hr | Tests prove code path reached but not correctness |
| M-1 | Replace `NamedTemporaryFile(delete=False)` with `tmp_path` in `test_event_chain_concurrency.py` | 10 min | Orphaned temp files accumulate |
| M-2 | Close KernelStore in `test_event_chain_concurrency.py` | 10 min | SQLite connection leak under xdist |
| M-7 | Fix mock fidelity in `test_state_persistence.py:305-317` | 20 min | Test verifies mock behavior, not real code |

### DEFER (Post-merge) -- 14 items

All remaining MEDIUM and LOW items. Key deferrals:
- **H-5, H-6**: Architectural layer coupling (reflects production code debt, not test-specific)
- **H-7**: Private method testing in `test_sandbox_internals.py` (review during sandbox refactoring)
- **M-3**: KernelStore `:memory:` optimization (2-4s savings, low urgency)
- **M-4, M-5, M-10**: Fixture deduplication and organization (maintenance improvement, no correctness risk)
- **M-8, M-9**: Private access patterns and deep mock nesting (acceptable pragmatic trade-offs)

### Overall Quality Score: **7.5 / 10**

The test suite is structurally sound with good isolation, comprehensive edge-case coverage, and xdist compatibility. The two MUST-FIX items are straightforward (real sleep and unguarded global state). The main quality gap is assertion weakness -- too many tests verify existence rather than correctness, which inflates coverage numbers without proportional defect detection. Fixing the SHOULD-FIX assertion items would bring this to an 8.5.
