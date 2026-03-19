# Expert 3: Performance Review

## Executive Summary

The 5834-test unit suite completes in ~57.7 seconds (serial, `-n0`). Overall performance is acceptable. The top 30 slowest tests account for roughly 20 seconds of wall time, dominated by real subprocess spawning with `time.sleep()` in `test_tools.py` and thread-based polling in `test_hermit_mcp_server.py`. The remaining ~5800 tests average under 7ms each -- well within acceptable bounds.

No catastrophic performance regressions were found. The issues below are optimization opportunities, not blockers.

---

## Findings

### CRITICAL

None.

### HIGH

**H-1: Real subprocess spawning with hard `time.sleep()` in `test_tools.py` -- 5.26s total**

- `tests/unit/runtime/test_tools.py:248` -- spawns `python -c "time.sleep(2.0)"`, then polls with `_wait_for_poll()` up to 5s. **2.05s measured.**
- `tests/unit/runtime/test_tools.py:212` -- spawns `python -c "time.sleep(0.5)"`, then does `time.sleep(1.5)` in the test body. **1.62s measured.**
- `tests/unit/runtime/test_tools.py:183` -- spawns `python -c "time.sleep(1.0)"`, polls up to 5s. **1.05s measured.**
- `tests/unit/runtime/test_tools.py:117` -- spawns `python -c "time.sleep(0.5); ... time.sleep(0.2)"`, polls up to 2s. **0.54s measured.**

These tests exercise `CommandSandbox` observation/polling and deliberately spawn real Python subprocesses with real sleeps. They are marked `@pytest.mark.slow` which is good, but the sleep durations are longer than necessary.

Recommendation: Reduce sleep durations by 50-75%. A `time.sleep(2.0)` subprocess can be replaced with `time.sleep(0.3)` -- the test only needs the process to live long enough to observe the "running" state before it completes. Similarly, the hard `time.sleep(1.5)` at line 229 could be `time.sleep(0.3)` if the grace window monkeypatch is also reduced proportionally.

**H-2: Thread-based `time.sleep()` polling in MCP server tests -- 2.53s total**

- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:616-618` -- `test_await_timeout_returns_pending` uses `timeout=1` on a running task, causing a real 1-second wall-clock wait. **1.01s measured.**
- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:592-614` -- `test_await_running_task_becomes_completed` spawns a thread with `time.sleep(0.3)`. **0.51s measured.**
- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:633-664` -- `test_await_multiple_tasks_returns_on_first_completion` spawns a thread with `time.sleep(0.3)`. **0.51s measured.**
- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:624-631` -- `test_await_blocked_task_reports_approvals` uses `timeout=2`. **0.50s measured.**

Recommendation: For the timeout tests, reduce `timeout=1` to `timeout=0.1` (the task is never going to change status, so shorter timeout is fine). For thread-delay tests, reduce `time.sleep(0.3)` to `time.sleep(0.05)`.

**H-3: 2-second `time.sleep()` in approval copy formatter timeout test**

- `tests/unit/kernel/test_approval_copy.py:477` -- `time.sleep(2)` in a formatter function to test timeout behavior with `formatter_timeout_ms=1`.

A 2-second sleep is excessive for testing a 1ms timeout. The formatter runs in a thread; the test blocks until the thread finishes or is abandoned. Using `time.sleep(0.1)` would be equally effective since the timeout is 1ms.

### MEDIUM

**M-1: 324 KernelStore instantiations across 75 files -- each creates a SQLite database on disk**

Most test files create a fresh `KernelStore(tmp_path / "state.db")` per test function. Each instantiation runs schema migrations against a new on-disk SQLite file. Files with the highest counts:

| File | Instantiations | Tests |
|------|---------------|-------|
| `test_consolidation.py` | 22 | 22 |
| `test_memory_graph.py` | 17 | 17 |
| `test_hybrid_retrieval.py` | 17 | 19 |
| `test_competition_store.py` | 16 | 16 |
| `test_kernel_store_tasks_support.py` | 15 | 14 |
| `test_memory_decay.py` | 15 | 15 |
| `test_store_scheduler_coverage.py` | 14 | 14 |
| `test_provider_input_coverage.py` | 15 | ~15 |

While isolation per test is correct practice, these files could benefit from:
1. Using `Path(":memory:")` instead of `tmp_path / "state.db"` where disk persistence is not being tested (only 16 of 324 instantiations currently use `:memory:`).
2. Using a module-scoped or class-scoped fixture that creates the store once and resets relevant tables between tests, where test isolation allows it.

Estimated savings: ~2-4 seconds across the full suite by eliminating disk I/O and repeated schema migrations.

**M-2: Event-driven await tests use `time.sleep()` for coordination**

- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:880` -- `time.sleep(DELAY)` where `DELAY=0.05`.
- `tests/unit/plugins/mcp/test_hermit_mcp_server.py:943` -- `time.sleep(0.02)`.

These are reasonable at 20-50ms but could be replaced with `threading.Event.wait()` for deterministic coordination instead of wall-clock delays.

**M-3: First-import cost inflates durations for isolated test runs**

- `tests/unit/surfaces/test_helpers.py::TestGetKernelStore::test_returns_store` shows as 1.44s in the full suite run but only 0.17s when its file runs alone.

This suggests the 1.44s duration is an artifact of xdist worker cold-start (first import of `hermit.kernel` in that worker). Not a test-level problem, but worth noting: xdist worker count tuning or import-heavy modules could amplify this.

**M-4: Projection tests create full KernelStore + task + step + attempt per test**

- `tests/unit/kernel/task/test_projections.py` -- 6 tests averaging 0.75s each. Total ~4.5s.

Each test creates a `KernelStore`, conversation, task, step, and step attempt, then runs `rebuild_task()`. The `_setup()` helper at line 19 could be a module-level fixture that shares a single store across read-only tests (e.g., `test_key_input_empty`, `test_key_input_returns_first_value` don't even need a store).

### LOW

**L-1: `conftest.py` only exists for `tests/unit/apps/` -- no shared kernel test fixtures**

There is no shared `conftest.py` under `tests/unit/kernel/` providing a common `KernelStore` fixture. This means every kernel test file independently creates its own store setup. A shared `kernel_store` fixture (session or module scoped with `:memory:`) could reduce boilerplate and marginally improve performance.

**L-2: No `pytest.mark.slow` on MCP server await tests**

The 4 `@pytest.mark.slow` markers are only on `test_tools.py` sandbox tests. The MCP server await tests (`TestHermitAwaitCompletion`) contribute 2.5s but are not marked slow, making it harder to skip them during fast iteration.

**L-3: Subprocess mocking is done correctly in most places**

Reviewed: `test_proof_anchoring.py`, `test_competition_criteria.py`, `test_patrol_engine.py`, `test_patrol_checks.py`, `test_companion_control.py`, `test_companion_appbundle.py`, `test_commands_plugin.py`. All properly `patch("subprocess.run", ...)` without spawning real processes. Good pattern.

**L-4: `asyncio.sleep()` in serve command tests is not a real wait**

- `tests/unit/surfaces/test_serve_commands.py:420,460` -- `await asyncio.sleep(10)` appears in mock async functions that are cancelled/interrupted before the sleep completes. The 0.01s sleeps at lines 438/478 are in polling loops that exit quickly. No actual performance impact.

---

## Parallelism Compatibility (xdist)

**Global state mutation risks identified:**

1. `tests/unit/plugins/mcp/test_hermit_mcp_server.py:772,826` -- Sets `mcp_hooks._server = None` directly. If two workers import the same module, this could cause interference. Currently mitigated by each test class managing its own state, but fragile.

2. `tests/unit/plugins/hooks/test_webhook_hooks.py:30` and `test_webhook_hooks_coverage.py:32` -- Same pattern with `webhook_hooks._server = None`.

3. `tests/unit/runtime/test_config_coverage.py:299,303` -- Calls `get_settings.cache_clear()`. Safe under xdist (each worker has its own process), but would break with thread-based parallelism.

**Verdict:** xdist compatible. Each worker runs in a separate process, so module-level state mutations are isolated. No shared filesystem conflicts detected (all use `tmp_path`).

---

## Performance Budget Summary

| Category | Wall time | % of total |
|----------|-----------|------------|
| Subprocess sandbox tests (H-1) | ~5.3s | 9.2% |
| MCP await polling tests (H-2) | ~2.5s | 4.3% |
| Projection rebuild tests (M-4) | ~4.5s | 7.8% |
| Import cold-start overhead (M-3) | ~1.4s | 2.4% |
| Approval copy timeout (H-3) | ~2.0s | 3.5% |
| All other tests (~5800) | ~42.0s | 72.8% |
| **Total** | **~57.7s** | **100%** |

Addressing H-1, H-2, and H-3 alone could save ~5-6 seconds (roughly 10% of suite time) with minimal code changes -- just reducing sleep durations.

---

## Recommendations Priority

1. **Quick win (H-1, H-2, H-3):** Reduce `time.sleep()` durations in sandbox and MCP tests. Estimated effort: 30 minutes. Estimated savings: 5-6s.
2. **Medium effort (M-1):** Switch KernelStore instantiations from `tmp_path` to `:memory:` where disk persistence is not tested. Estimated effort: 2 hours. Estimated savings: 2-4s.
3. **Structural (M-4, L-1):** Add shared fixtures in `tests/unit/kernel/conftest.py`. Estimated effort: 3 hours. Estimated savings: 2-3s + reduced boilerplate.
