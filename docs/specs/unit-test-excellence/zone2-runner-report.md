# Zone 2: runtime/control/runner/ — Unit Test Report

## Summary

Brought unit test coverage for `src/hermit/runtime/control/runner/` from an average of ~19% to **95.16%** overall, meeting the 95%+ target.

## Coverage Before / After

| File                        | Before (stmts/missed) | After Coverage |
|-----------------------------|----------------------|----------------|
| `utils.py`                  | 68% (59 stmts, 14 missed) | **100%** |
| `session_context_builder.py`| 0% (38 stmts)        | **100%** |
| `message_compiler.py`       | 40% (66 stmts, 35 missed) | **98%** |
| `control_actions.py`        | 0% (210 stmts)       | **98%** |
| `runner.py`                 | 22% (282 stmts, 206 missed) | **97%** |
| `async_dispatcher.py`       | 0% (111 stmts)       | **96%** |
| `task_executor.py`          | 0% (182 stmts)       | **95%** |

`approval_resolver.py` (68%) was not in zone scope — it already has its own test file at `tests/unit/runtime/test_approval_resolver.py`.

## Test Files Created

| Test File | Tests | Covers |
|-----------|-------|--------|
| `tests/unit/runtime/test_runner_utils.py` | 29 | `utils.py` — markup stripping, result preview/status, DispatchResult, session trimming, locale, i18n |
| `tests/unit/runtime/test_session_context_builder.py` | 14 | `session_context_builder.py` — init, max_session_messages, ensure_session_started, maybe_capture_planning_result |
| `tests/unit/runtime/test_message_compiler.py` | 18 | `message_compiler.py` — prepare_prompt_context, provider_input_compiler, compile_provider_input, compile_lightweight_input, append_note_context |
| `tests/unit/runtime/test_async_dispatcher.py` | 22 | `async_dispatcher.py` — wake_dispatcher, enqueue_ingress, enqueue_approval_resume, emit_async_dispatch_result, record_scheduler_execution |
| `tests/unit/runtime/test_control_actions.py` | 55 | `control_actions.py` — all dispatch actions (new_session, focus_task, show_history, show_help, task_list, events, receipts, proof, rollback, projections, capabilities, schedules), planning helpers, approval resolution, _is_async_dispatch |
| `tests/unit/runtime/test_task_executor.py` | 41 | `task_executor.py` — run_existing_task, process_claimed_attempt (run/resume modes), planning capture, scheduler recording, async dispatch results |
| `tests/unit/runtime/test_runner.py` | 72 | `runner.py` — init, command registration, session helpers, dispatch, handle() (basic/planning/suspended/ingress/note/disambiguation), delegation methods, background services, resume_attempt, core slash commands |

**Total: 263 tests passing**

## Testing Strategy

- Heavy use of `MagicMock` and `patch()` since all modules have deep dependency chains (kernel store, sessions, plugins, LLM providers)
- Patched local imports at their source module path (e.g., `hermit.kernel.policy.approvals.approvals.ApprovalService`) rather than the importing module
- Used `SimpleNamespace` for lightweight settings objects
- Created helper factories (`_make_task_ctx`, `_make_agent_result`, `_make_session`) to reduce test boilerplate
- Organized tests into classes by feature area for clarity

## Remaining Uncovered Lines

The few remaining uncovered lines are primarily:
- Branch-only partial coverage (e.g., `104->106`, `107->exit` in runner.py — background service start edge cases)
- `task_executor.py` lines 76, 93 — `ProviderInputCompiler` instantiation happy path (requires real kernel store)
- `control_actions.py` line 313 — `_build_planning` returning None when store lacks `ensure_conversation`

These lines involve deep kernel integration that would require integration-level testing rather than unit mocking.
