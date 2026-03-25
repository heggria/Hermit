# Expert 2: Architecture Compliance Review

**Reviewer**: Architecture compliance auditor
**Scope**: 10 new test files across kernel, runtime, plugins, and shared fixtures
**Branch**: `release/0.3`
**Date**: 2026-03-19

---

## Files Reviewed

1. `tests/conftest.py` (global conftest)
2. `tests/unit/kernel/test_formal_state_machine.py`
3. `tests/unit/kernel/test_durable_execution.py`
4. `tests/unit/kernel/test_blackboard.py`
5. `tests/unit/kernel/test_observation_durability.py`
6. `tests/unit/kernel/test_approval_orchestration.py`
7. `tests/unit/kernel/test_budget_monotonicity_guard.py`
8. `tests/unit/kernel/test_verification_driven_scheduling.py`
9. `tests/unit/kernel/test_memory_receipt_integration.py`
10. `tests/unit/runtime/test_approval_resolver.py`
11. `tests/fixtures/task_kernel_support.py` (shared test support module)

---

## Findings

### CRITICAL

None found. No test file exhibits an outright violation of the declared architectural layers (kernel, runtime, plugins, infra, surfaces) that would indicate a fundamental misunderstanding of the codebase structure.

---

### HIGH

#### H-1: Kernel unit tests import `runtime.capability.registry.tools.ToolSpec` (bidirectional coupling)

Multiple kernel unit tests import `ToolSpec` from the runtime layer:

- `tests/unit/kernel/policy/test_tool_spec_adapter.py:16`
- `tests/unit/kernel/execution/test_executor_coverage.py:24`
- `tests/unit/kernel/execution/test_dispatch_handler.py:15`
- `tests/unit/kernel/execution/test_receipt_handler.py:14`
- `tests/unit/kernel/execution/test_observation_handler.py:23`
- `tests/unit/kernel/execution/test_observation_handler_ext.py:33`
- `tests/unit/kernel/test_contract_template_learner.py:632,695`

**Impact**: The architecture diagram in AGENTS.md shows `kernel/` as an inner layer and `runtime/` as an outer orchestration layer. `ToolSpec` lives in `runtime/capability/registry/tools.py` but is consumed by `kernel/execution/executor/executor.py` (which the tests exercise). This is a design smell in the production code reflected in the tests: the kernel executor depends on a runtime contract type. While the tests are correctly testing what the production code does, this bidirectional dependency between layers should be tracked as architectural debt.

**Recommendation**: Consider moving `ToolSpec` and `ToolRegistry` to `kernel/execution/` or a shared contracts package since the kernel executor already depends on them. Tests would then import from the correct layer.

#### H-2: Kernel unit tests import plugin types directly

Several kernel tests import from `hermit.plugins.builtin`:

- `tests/unit/kernel/test_memory_governance.py:8` imports `MemoryEntry` from `plugins.builtin.hooks.memory.types`
- `tests/unit/kernel/test_planner_kernel.py:10` imports `_cmd_plan` from `plugins.builtin.bundles.planner.commands`
- `tests/unit/kernel/context/test_governance.py:18` imports `MemoryEntry`
- `tests/unit/kernel/context/test_compiler.py:14` imports `MemoryEntry`
- `tests/unit/kernel/test_store_scheduler_coverage.py:9` imports `ScheduledJob`, `JobExecutionRecord` from scheduler plugin

**Impact**: Plugin types should not flow into kernel tests. The kernel should define its own types or interfaces that plugins implement. This creates a coupling where kernel tests break if plugin type definitions change.

**Recommendation**: Either the types should be elevated to kernel-level contracts, or kernel tests should use local stubs/dataclasses that match the same shape.

---

### MEDIUM

#### M-1: Direct access to private `KernelStore` internals in tests

Multiple kernel tests call private methods on `KernelStore`:

- `tests/unit/kernel/test_observation_durability.py:43,517` -- `store._existing_tables()`
- `tests/unit/kernel/test_budget_monotonicity_guard.py:353` -- `store._get_conn().execute("PRAGMA table_info(tasks)")`
- `tests/unit/kernel/test_budget_monotonicity_guard.py:403` -- `store._migrate_budget_v17()`
- `tests/unit/kernel/test_memory_decay.py:31,336` -- `store._get_conn()`
- `tests/unit/kernel/test_entity_triples.py:128` -- `store._get_conn()`
- `tests/unit/kernel/test_event_chain_concurrency.py:41,89` -- `store._get_conn()`
- `tests/unit/kernel/test_task_metrics_coverage.py:62-166` (many) -- `store._get_conn().execute(...)`
- `tests/unit/kernel/test_confidence_decay.py:36` -- `store._get_conn()`

**Impact**: These tests are coupled to the SQLite internals of `KernelStore`. If the storage backend changes, every one of these tests breaks even though the public API might remain identical. The underscore-prefix convention signals these are not part of the public contract.

**Recommendation**: For schema verification tests (checking columns exist), this is arguably acceptable as those are migration-specific tests. For data insertion tests in `test_task_metrics_coverage.py` that use raw SQL to set up test state, prefer using the public `KernelStore` API instead.

#### M-2: Tests call underscore-prefixed (private) methods on services

Several tests exercise private methods directly:

- `tests/unit/kernel/test_observation_durability.py:414,445,469,500` -- `svc._enforce_timeouts()`
- `tests/unit/kernel/test_observation_durability.py:507` -- `svc._recover_active_tickets()`
- `tests/unit/kernel/test_verification_driven_scheduling.py:467-504` -- `ReceiptService._compute_signature()`
- `tests/unit/kernel/test_memory_receipt_integration.py:287` -- `memory_service._issue_memory_invalidate_receipt()`
- `tests/unit/kernel/context/test_knowledge.py:439,451,461` -- `svc._issue_memory_write_receipt()`, `svc._issue_memory_invalidate_receipt()`
- `tests/unit/kernel/execution/test_dispatch_service.py:398,416,444,496,527` -- `svc._recover_single_attempt()`, `svc._recover_interrupted_attempts()`
- `tests/unit/runtime/test_approval_resolver.py:75,83` -- `resolver._is_async_dispatch()`

**Impact**: Testing private methods couples tests to implementation details. When internals are refactored (method renamed, logic moved), these tests break unnecessarily. This is a moderate concern because some private methods contain genuinely important logic worth testing directly.

**Recommendation**: For high-criticality private methods (like `_compute_signature`, `_recover_interrupted_attempts`), this is acceptable -- they implement critical behavior that deserves direct testing. For simpler private methods, prefer testing through the public interface. Consider promoting the most important ones to public methods if they're worth testing directly.

#### M-3: `tests/fixtures/task_kernel_support.py` spans all layers

This shared fixture module imports from every layer:

- `hermit.kernel.artifacts`, `kernel.authority`, `kernel.context`, `kernel.execution`, `kernel.ledger`, `kernel.policy`, `kernel.task`, `kernel.verification` (kernel layer)
- `hermit.runtime.capability`, `runtime.control`, `runtime.provider_host` (runtime layer)
- `hermit.plugins.builtin.hooks.scheduler.models` (plugin layer)

**Impact**: This creates a "god fixture" that couples test infrastructure to all layers simultaneously. Any change in any layer's API can break this file and cascade failures to all tests that use it. It also makes it unclear which layer boundary a given test is operating within.

**Recommendation**: Split into layer-specific fixture modules: `tests/fixtures/kernel_support.py`, `tests/fixtures/runtime_support.py`, `tests/fixtures/plugin_support.py`. Tests in `tests/unit/kernel/` should only use kernel fixtures.

#### M-4: Global conftest monkeypatches `KernelStore.__init__`

`tests/conftest.py:20-33` patches `KernelStore.__init__` globally for resource tracking. While well-intentioned (auto-closing SQLite connections), this approach:

- Mutates a class at import time via a global side effect
- Makes it harder to reason about test behavior (stores behave differently in tests vs production)
- Could mask bugs where stores are not properly closed

**Impact**: Moderate. The tracking mechanism itself is sound, but the global mutation pattern is fragile.

**Recommendation**: Consider using a proper pytest plugin or a factory fixture instead of monkeypatching `__init__`. For example, a `store_factory` fixture that wraps creation and registers cleanup.

---

### LOW

#### L-1: Test directory structure correctly mirrors source layout

The test directories properly mirror the source structure documented in AGENTS.md:

| Source | Test |
|--------|------|
| `src/hermit/kernel/task/` | `tests/unit/kernel/task/` |
| `src/hermit/kernel/execution/` | `tests/unit/kernel/execution/` |
| `src/hermit/kernel/policy/` | `tests/unit/kernel/policy/` |
| `src/hermit/kernel/authority/` | `tests/unit/kernel/authority/` |
| `src/hermit/kernel/context/` | `tests/unit/kernel/context/` |
| `src/hermit/runtime/` | `tests/unit/runtime/` |
| `src/hermit/plugins/` | `tests/unit/plugins/` |
| `src/hermit/surfaces/` | `tests/unit/surfaces/` |

This is well-organized and compliant.

#### L-2: Conftest fixtures are appropriately scoped

- `tests/conftest.py` -- global scope, session-level locale setting, function-level store cleanup. Appropriate.
- `tests/unit/apps/conftest.py` -- properly scoped to `apps/` tests, clears settings cache per test.
- No evidence of shared state leaking between test functions.

The `autouse=True` pattern is used sparingly and correctly.

#### L-3: Mock placement is generally at the right seam

Reviewed test files show good mock placement practices:

- `test_approval_orchestration.py` mocks `KernelStore` when testing service logic, not internal store methods
- `test_approval_resolver.py` mocks `store` and `controller` (the seam between runtime and kernel), which is correct
- `test_workspace_lifecycle.py` mocks `store` and `artifact_store` interfaces
- `SimpleNamespace` is used appropriately for lightweight fakes (per `.claude/rules/testing.md`)

#### L-4: `test_blackboard.py` crosses into context compiler (intentional integration)

`tests/unit/kernel/test_blackboard.py:619-719` (`TestContextCompilerBlackboard`) imports from `hermit.kernel.context.compiler.compiler` and `hermit.kernel.context.models.context`. This is a kernel-internal cross-module test, not a cross-layer violation. Both modules are within `kernel/`. This is acceptable for verifying that the blackboard integrates correctly with context compilation.

#### L-5: Runtime tests importing kernel models is expected

`tests/unit/runtime/test_approval_resolver.py` and other runtime tests import from `hermit.kernel.context.models.context` (e.g., `TaskExecutionContext`). Since the runtime layer is an outer layer that orchestrates kernel components, this dependency direction is correct (outer depends on inner). The imports observed are:
- `TaskExecutionContext` (kernel context model)
- `CompiledProviderInput` (kernel context model)
- `KernelStore` (kernel ledger)
- `ProgressSummary` (kernel projection)

All are inward dependencies (runtime -> kernel), which is architecturally correct.

---

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| CRITICAL | 0 | -- |
| HIGH | 2 | Bidirectional layer coupling: kernel tests import runtime `ToolSpec` and plugin types |
| MEDIUM | 4 | Private method testing, god fixture, global monkeypatching, raw SQL in tests |
| LOW | 5 | Good structure overall; directory layout, conftest scoping, mock placement all sound |

### Overall Assessment

The new tests are architecturally sound in their organization and test scoping. The test directory structure mirrors the source tree correctly, fixtures are properly scoped, and mocks are placed at appropriate architectural seams. The two HIGH findings (kernel importing runtime ToolSpec and plugin types) reflect pre-existing architectural debt in the production code rather than test-specific violations. The MEDIUM findings around private method testing and the cross-layer fixture module are the most actionable items for improving long-term maintainability.

The tests comply with AGENTS.md conventions: they use `pytest` with `pytest-asyncio`, `tmp_path` for temporary databases, `SimpleNamespace` for lightweight mocks, and `monkeypatch` for environment manipulation. No deprecated import paths (`hermit/` vs `src/hermit/`) were found.
