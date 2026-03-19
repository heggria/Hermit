# Test Quality Analysis Report

Date: 2026-03-19
Branch: release/0.3

---

## Part 1: Root Cause Analysis of Failing Tests

### Overview

All three failing tests are in `tests/unit/runtime/test_tools.py` and exercise the
`CommandSandbox` observation/polling lifecycle in
`src/hermit/runtime/provider_host/execution/sandbox.py`. They share the same root cause:
a **timing mismatch between the test's timeout assumptions and the sandbox's budget-driven
hard deadline**.

### Shared Root Cause

The tests create a `CommandSandbox` with `timeout_seconds=0.05` (50ms). This value flows
into the `ExecutionBudget` as `tool_soft_deadline=0.05`. The `tool_hard_deadline` is set to
`max(base_budget.tool_hard_deadline, soft)` -- since the default `tool_hard_deadline` is
`600.0` seconds, the hard deadline is always 600s. However, the `Deadline` object is created
from `time.monotonic()`, while the poll method's timeout check on line 264 compares `now`
(also `time.time()` at line 175) against `job.deadline.hard_at`.

**Critical bug**: `Deadline.start()` (budgets.py line 17) uses `time.monotonic()`, but
`poll()` (sandbox.py line 175) sets `now = time.time()`. These two clocks are **not
comparable** -- `time.monotonic()` returns a value relative to an arbitrary epoch (often
system boot), while `time.time()` returns Unix epoch seconds. This means the comparison
`now >= job.deadline.hard_at` on line 264 produces **unpredictable results**: it can
evaluate to `True` immediately if `time.time()` (typically ~1.7 billion) is greater than
`time.monotonic()` plus 600 seconds.

On most systems, `time.time()` returns a value vastly larger than `time.monotonic()`,
so `now >= job.deadline.hard_at` is **always true** on the very first poll, causing
every observed process to be immediately terminated as "timed out."

### Failure 1: `test_command_sandbox_followup_poll_quickly_reaches_completion`

**Symptom**: `assert observing["status"] == "observing"` fails because `observing["status"]
== "timeout"`.

**Mechanism**: The test launches a 2-second sleep command, waits 0.2s, then polls. The first
poll hits the `now >= job.deadline.hard_at` branch (line 264) because `time.time()` (Unix
epoch, ~1.7e9) is far greater than `deadline.hard_at` (monotonic clock + 600, ~a few
thousand). The sandbox force-kills the process and returns `status: "timeout"` instead of
`status: "observing"`.

**Suggested fix**: Make `poll()` use `time.monotonic()` instead of `time.time()` when
comparing against `Deadline` values. Replace line 175 (`now = time.time()`) with
`now = time.monotonic()`. Also audit all other `time.time()` usages in sandbox.py that
interact with `Deadline` fields -- specifically lines 107, 299, and 583 in
`_ObservedProcess.created_at` and `completed_at`.

### Failure 2: `test_command_sandbox_observation_emits_progress_and_ready`

**Symptom**: `assert starting is not None` fails -- the `_wait_for_poll` helper polls for
up to 2 seconds but never sees `progress.phase == "starting"`.

**Mechanism**: Same clock mismatch. The command prints "Booting server" immediately, but
the very first `poll()` call triggers the timeout branch, force-killing the process before
the progress pattern can be matched. The poll returns `status: "timeout"`, which never
satisfies the `phase == "starting"` predicate.

**Suggested fix**: Same as Failure 1 -- fix the clock domain mismatch.

### Failure 3: `test_command_sandbox_observation_uses_coarse_running_progress_without_metadata`

**Symptom**: `assert observing is not None` fails -- the helper polls for 2 seconds but
never sees `status == "observing"` with `phase == "running"`.

**Mechanism**: Identical to the above. The 1-second sleep command is immediately killed on
first poll due to the `time.time() >= deadline.hard_at` (monotonic) comparison.

**Suggested fix**: Same as Failure 1.

### Summary of Required Fix

A single fix addresses all three failures:

1. In `sandbox.py` `poll()`, change `now = time.time()` (line 175) to
   `now = time.monotonic()`.
2. In `_ObservedProcess`, change `created_at: float` initialization (line 107,
   `created_at=time.time()`) to `time.monotonic()`.
3. In `_terminate_job`, change `job.completed_at = time.time()` (line 583) to
   `time.monotonic()`.
4. In `_should_extend_coarse_observation`, the comparison on line 634
   (`completed_at - job.created_at < _COARSE_OBSERVATION_GRACE_SECONDS`) also requires
   consistent clocks -- this is already satisfied if steps 1-3 are done.
5. In the `run()` method, the `Deadline` object is already created via
   `self.budget.tool_deadline()` which uses `time.monotonic()`, so no change needed there.
6. Keep `_store_terminal_result` using `time.time()` since its TTL comparisons are
   self-contained (both `now` and `expires_at` use the same clock within
   `_prune_terminal_results`).

### Why the Passing Test Works

`test_command_sandbox_coarse_observation_only_extends_completion_once` passes because it
uses `monkeypatch` to set `_COARSE_OBSERVATION_GRACE_SECONDS = 5.0` and sleeps 1.5 seconds
before polling. Even with the clock mismatch, the test's assertions happen to align with
the timeout branch behavior since it explicitly expects a `"completed"` status on the
second poll.

---

## Part 2: Test Quality Assessment

### Files Reviewed

1. `tests/unit/runtime/test_tools.py` (sandbox and tool registry tests)
2. `tests/unit/kernel/test_join_barrier.py` (join barrier logic)
3. `tests/unit/runtime/test_session.py` (session management)
4. `tests/unit/infra/test_atomic_write.py` (atomic file operations)
5. `tests/unit/kernel/test_memory_governance.py` (memory promotion/supersession)
6. `tests/unit/runtime/test_config.py` (settings and configuration)
7. `tests/unit/kernel/test_dag_builder.py` (DAG validation and materialization)

### Strengths

**Naming conventions**: Test names follow a consistent `test_<feature>_<scenario>` pattern
and are generally descriptive. Examples: `test_builtin_tools_block_workspace_escape`,
`test_session_manager_expires_and_resets_projection`. Class-based grouping in
`test_join_barrier.py` and `test_dag_builder.py` (`TestEvaluateStrategy`,
`TestValidate`, `TestMaterialize`) provides good organization.

**Fixture usage**: `pytest.fixture` is used appropriately for shared setup (e.g., `store`
and `service` fixtures in `test_join_barrier.py`, `store` and `builder` in
`test_dag_builder.py`). The `tmp_path` fixture is used consistently for filesystem
isolation.

**Test isolation**: Tests do not share mutable state. Each test creates its own sandbox,
session manager, or store instance. The `monkeypatch` fixture is used correctly for
environment variable manipulation in `test_config.py`.

**Edge case coverage**: `test_dag_builder.py` covers cycle detection, duplicate keys,
unknown dependencies, empty input, disconnected graphs, fan-out, and complex DAGs.
`test_join_barrier.py` covers all four join strategies with boundary conditions.

**Assertion quality**: Most tests make specific, targeted assertions. The DAG tests verify
topological ordering constraints rather than exact ordering (good for non-deterministic
orderings).

### Issues and Anti-patterns

**1. Clock domain confusion (Critical)**

As documented in Part 1, `sandbox.py` mixes `time.time()` and `time.monotonic()` clocks.
The tests expose this bug but the tests themselves are correct -- the production code is
wrong.

**2. Real subprocess execution in unit tests (Moderate)**

The sandbox tests in `test_tools.py` spawn real Python subprocesses with `time.sleep()`.
This makes tests:
- Slow (the 3 failing tests take 2+ seconds each)
- Timing-sensitive and potentially flaky under CI load
- Dependent on system process scheduling

Recommendation: For unit tests, consider mocking `subprocess.Popen` to test the
observation state machine logic in isolation. Reserve real-process tests for integration
tests.

**3. Manual try/except instead of `pytest.raises` (Minor)**

In `test_tools.py` (lines 40-45, 80-85) and `test_config.py` (lines 96-101):

```python
try:
    registry.call("read_file", {"path": "../secret.txt"})
except ValueError as exc:
    assert "escapes workspace" in str(exc)
else:
    raise AssertionError("Expected workspace escape error")
```

This pattern is more verbose and less idiomatic than:

```python
with pytest.raises(ValueError, match="escapes workspace"):
    registry.call("read_file", {"path": "../secret.txt"})
```

The `pytest.raises` context manager is already used correctly in `test_dag_builder.py`.

**4. Verbose test setup without helpers (Minor)**

`test_memory_governance.py` has very long test bodies (50+ lines) with repetitive
boilerplate for creating reconciliations and beliefs. Each test repeats essentially the
same 15-line block to create a reconciliation. This should be extracted into a helper
function.

**5. Missing docstrings on some tests (Minor)**

Most tests in `test_tools.py` and `test_join_barrier.py` lack docstrings. Some files
(`test_session.py`, `test_dag_builder.py`) include docstrings on complex tests but not
simple ones. Consistency would improve maintainability.

**6. `import time` inside test body (Trivial)**

In `test_join_barrier.py` line 84, `import time` appears inside the test method body
rather than at the module level. This works but is unconventional.

**7. Accessing private members in assertions (Minor)**

`test_session.py` line 68 accesses `manager._active["chat-c"]` and line 82 accesses
`manager._active`. `test_tools.py` line 325 accesses `registry._tools`. While sometimes
necessary, this couples tests to implementation details.

**8. Manual cleanup with try/finally (Moderate)**

`test_memory_governance.py` uses `try/finally` blocks with `store.close()`. This is
better handled with a fixture:

```python
@pytest.fixture
def store(tmp_path):
    s = KernelStore(tmp_path / "state.db")
    yield s
    s.close()
```

**9. Insufficient negative-path testing (Moderate)**

The sandbox tests focus heavily on the happy path. There are no tests for:
- What happens when the subprocess crashes immediately
- Behavior when `command` is an empty string
- Handling of very long stdout/stderr output
- Concurrent `poll()` calls from multiple threads

**10. Environment variable cleanup verbosity (Minor)**

`test_config.py` has 7-8 `monkeypatch.delenv()` calls at the start of each test. This
should be extracted into a fixture:

```python
@pytest.fixture(autouse=True)
def clean_hermit_env(monkeypatch):
    for var in ["HERMIT_AUTH_TOKEN", "HERMIT_BASE_URL", ...]:
        monkeypatch.delenv(var, raising=False)
```

---

## Part 3: Recommendations

### Immediate (Fix the 3 Failures)

1. **Fix clock domain mismatch in `sandbox.py`**: Replace all `time.time()` usages that
   are compared against `Deadline` fields with `time.monotonic()`. This is a single-root-
   cause fix for all three test failures.

### Short-term (Test Quality)

2. **Adopt `pytest.raises` consistently**: Replace manual try/except/else patterns with
   `pytest.raises(ExceptionType, match="...")` across the codebase.

3. **Extract test helpers for verbose setup**: Create helper functions for common patterns
   like creating reconciliations, beliefs, and memory records in kernel tests.

4. **Use fixtures for resource cleanup**: Replace try/finally `store.close()` patterns
   with yielding fixtures.

5. **Create a shared environment cleanup fixture** for `test_config.py` to reduce
   boilerplate.

### Medium-term (Test Architecture)

6. **Separate unit and integration tests for CommandSandbox**: Mock `subprocess.Popen`
   for unit tests that verify the state machine logic. Keep real-process tests as
   integration tests with appropriate timeout margins.

7. **Add negative-path tests for CommandSandbox**: Test immediate process crash, empty
   command handling, and concurrent poll access.

8. **Establish a test style guide** covering: docstring expectations, fixture vs helper
   patterns, when to use `pytest.raises` vs manual exception handling, and private-member
   access policies.

### Long-term (Test Infrastructure)

9. **Add flaky test detection in CI**: Use `pytest-rerunfailures` or similar to catch
   timing-sensitive tests early.

10. **Track test coverage by module**: Ensure kernel, runtime, and infrastructure modules
    each maintain 80%+ coverage independently, not just as an aggregate.
