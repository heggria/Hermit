# Zone 4: kernel/execution/executor — Unit Test Report

## Summary

Brought `src/hermit/kernel/execution/executor/` from mixed low coverage (20-82%) to **95.56% overall** across all files, exceeding the 95% target.

## Coverage Before vs After

| File | Before | After | Delta |
|------|--------|-------|-------|
| `formatting.py` | 20% | 100% | +80 |
| `subtask_handler.py` | 22% | 100% | +78 |
| `snapshot.py` | 30% | 100% | +70 |
| `phase_tracker.py` | 33% | 100% | +67 |
| `dispatch_handler.py` | 53% | 100% | +47 |
| `authorization_handler.py` | 65% | 98% | +33 |
| `witness_handler.py` | 74% | 100% | +26 |
| `executor.py` | 75% | 82% | +7 |
| `observation_handler.py` | 77% | 98% | +21 |
| `witness.py` | 82% | 94% | +12 |
| **TOTAL** | — | **95.56%** | — |

## New Test Files (10)

| Test File | Tests | Target Module |
|-----------|-------|---------------|
| `test_formatting.py` | 24 | `formatting.py` — truncation, block formatting, progress signatures |
| `test_subtask_handler.py` | 15 | `subtask_handler.py` — descriptor normalization, spawn lifecycle |
| `test_snapshot.py` | 18 | `snapshot.py` — envelope creation/extraction, resume messages, artifacts |
| `test_phase_tracker.py` | 16 | `phase_tracker.py` — witness requirements, status mapping, phase transitions |
| `test_dispatch_handler.py` | 7 | `dispatch_handler.py` — dispatch denied handling with/without receipts |
| `test_authorization_handler.py` | 22 | `authorization_handler.py` — rollback plans, leases, constraints |
| `test_witness_handler_ext.py` | 11 | `witness_handler.py` — delegation, payload loading edge cases |
| `test_executor_coverage.py` | 31 | `executor.py` — deny/approve/governed paths, delegation, budget tracking |
| `test_observation_handler_ext.py` | 37 | `observation_handler.py` — submission, finalization, polling, progress summaries |
| `test_witness_coverage.py` | 14 | `witness.py` — capture, payload, path/git witness, validation |

**Total new tests: 195**

## Testing Approach

- Used `MagicMock` and `SimpleNamespace` for store, artifact_store, and service mocks
- Followed existing test patterns from `test_observation_handler.py` and `test_approval_handler.py`
- `ToolSpec` construction requires explicit `risk_hint` for mutating tools and `requires_receipt=False` for readonly tools
- `executor.py` delegation methods tested by mocking delegate handlers directly
- Full `execute()` flow tested for deny, approval-required, and governed-success paths
- `tmp_path` fixture used for filesystem-touching tests (rollback plans, path witnesses)

## Remaining Gaps

- `executor.py` at 82%: the remaining 51 uncovered lines are deep in the `execute()` method's governed execution flow (uncertain outcome handler, observation+spawn detection within governed path, CapabilityGrantError enforcement branch, revalidation gate). These require complex multi-mock integration setups that risk brittleness.
- `authorization_handler.py` at 98%: 2 uncovered lines in `lease_root_path` OSError fallback.
- `witness.py` at 94%: 4 uncovered lines in `path_witness` OSError handling during stat.
