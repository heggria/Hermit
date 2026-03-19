# Zone 13: Runtime Capability + Infra + Misc Gaps — Test Coverage Report

## Summary

Added 111 new tests across 9 test files targeting runtime capability contracts,
loader config, hooks engine, logging setup, budget lifecycle, memory reranker,
menubar companion, CLI helpers, and infra storage.

All 111 tests pass (2.70s, parallel via pytest-xdist).

## Files Created

| Test File | Target Source | Tests | Focus |
|-----------|--------------|-------|-------|
| `tests/unit/runtime/test_kernel_services.py` | `runtime/capability/contracts/kernel_services.py` | 16 | Protocol checks, registry CRUD, error messages |
| `tests/unit/runtime/test_plugin_config.py` | `runtime/capability/loader/config.py` | 17 | Template resolution, variable fallback chain, env vars |
| `tests/unit/runtime/test_hooks_engine_coverage.py` | `runtime/capability/contracts/hooks.py` | 10 | `_event_key`, `_safe_call` module-level, sig caching, priority |
| `tests/unit/runtime/test_logging_setup.py` | `runtime/observation/logging/setup.py` | 8 | Level parsing, noisy logger suppression, stream config |
| `tests/unit/runtime/test_budgets_coverage.py` | `runtime/control/lifecycle/budgets.py` | 18 | Deadline edge cases, global budget configure/get |
| `tests/unit/kernel/test_reranker_coverage.py` | `kernel/context/memory/reranker.py` | 7 | `_ensure_model` paths, availability caching, score ordering |
| `tests/unit/apps/test_menubar_coverage.py` | `apps/companion/menubar.py` | 9 | `_parse_args`, `main()` error paths, `_t` helper |
| `tests/unit/infra/test_json_store.py` | `infra/storage/store.py` | 14 | Read fallbacks, write atomicity, update exception rollback |
| `tests/unit/surfaces/test_helpers_coverage.py` | `surfaces/cli/_helpers.py` | 5 | `format_epoch` edges, caffeinate no-binary, require_auth codex |

**Total: 111 tests added, 0 failures, 0 source modifications.**

## Coverage Improvements by Target

### `runtime/capability/contracts/kernel_services.py` (0% -> ~100%)
- Full coverage of `KernelServiceRegistry`: register, replace, get, has, registered_names
- Protocol runtime check validation
- Error message content verification (empty name, duplicate, missing service with available list)

### `runtime/capability/loader/config.py` (~45% -> ~95%)
- `_resolve_templates`: full template, partial template, dict/list recursion, None dropping, non-string passthrough
- `_resolve_plugin_variables`: configured value, setting fallback, env var chain, default, required warning, no base_dir
- `resolve_plugin_context`: end-to-end integration

### `runtime/capability/contracts/hooks.py` (~75% -> ~95%)
- `_event_key` with string enum, int enum, plain string
- Module-level `_safe_call` with signature error fallthrough
- Instance `_safe_call` caching behavior
- `fire_first` all-None path
- Priority ordering verification

### `runtime/observation/logging/setup.py` (35% -> ~90%)
- Level parsing (valid, invalid, case-insensitive)
- Noisy logger suppression
- Custom stream vs default stream factory paths

### `runtime/control/lifecycle/budgets.py` (~80% -> ~100%)
- `configure_runtime_budget` / `get_runtime_budget` global state
- `Deadline.start` negative clamping, hard >= soft enforcement
- All remaining/exceeded methods with explicit `now` parameter

### `kernel/context/memory/reranker.py` (~90% -> ~98%)
- `_ensure_model` cached/unavailable/load paths
- `is_available` caching both True and False
- Score-based reordering with limit truncation

### `apps/companion/menubar.py` (~58% -> ~70%)
- `_parse_args` all flag combinations
- `main()` non-Darwin, missing rumps (with and without import error)
- `_t` helper returns valid string

### `surfaces/cli/_helpers.py` (~95% -> ~98%)
- `format_epoch` with zero and int inputs
- `caffeinate` when binary not found
- `require_auth` codex auth_mode edge cases

### `infra/storage/store.py` (~88% -> ~100%)
- `read()` nonexistent, invalid JSON, empty file, default copy isolation
- `write()` create and overwrite
- `update()` persist, exception rollback, sequential operations
