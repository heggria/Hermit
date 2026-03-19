# Unit Test Coverage Gap Analysis

Current coverage: **67.02%** | Target: **95%**

---

## Executive Summary

The 28-point gap is concentrated in four layers:

1. **CLI surfaces** (~3,600 LOC, 10-48% coverage) — Typer commands with heavy side effects
2. **Runtime runner/provider** (~4,900 LOC, 0-40% coverage) — orchestration and LLM integration
3. **Kernel execution** (~7,000 LOC, 0-33% on key files) — dispatch, coordination, supervision
4. **Apps companion** (~1,100 LOC, 15-20% coverage) — macOS-specific appbundle and control

Reaching 95% requires approximately **220-260 new test functions** across ~25 new and existing test files.

---

## Module-by-Module Analysis

### 1. Kernel Execution Coordination

#### `coordination/dispatch.py` — 0% coverage (311 LOC)

**Untested functions (all):**
- `KernelDispatchService.__init__`, `start`, `stop`, `wake`
- `_loop` — main dispatch loop (thread-based polling)
- `_capacity_available`, `_reap_futures`, `_force_fail_attempt`
- `_on_attempt_completed`
- `_recover_interrupted_attempts` — 3-phase recovery: inflight recovery, ready dedup, task status repair
- `_fail_orphaned_sync_attempt`, `_recover_single_attempt`

**Key branches to cover:**
- Recovery of inflight attempts with/without capability grants (block vs requeue)
- Sync-path orphan failure
- Duplicate ready attempt deduplication
- Task status repair for stale async tasks
- Future reaping: success path and exception path (`_force_fail_attempt`)
- Capacity gating

**Estimated tests:** 18-22
**Dependencies to mock:** `runner.task_controller.store` (KernelStore methods: `claim_next_ready_step_attempt`, `list_step_attempts`, `update_step_attempt`, `update_step`, `update_task_status`, `get_step_attempt`, `get_task`, `propagate_step_failure`, `has_non_terminal_steps`), `runner.process_claimed_attempt`
**Complexity:** Medium — threading can be tested by calling methods directly without starting the loop thread. Recovery logic is pure store interaction.
**Priority:** CRITICAL — core dispatch path, failure recovery is safety-critical

---

#### `coordination/data_flow.py` — 13% coverage (74 LOC)

**Untested functions:**
- `StepDataFlowService.resolve_inputs` — all branches: no bindings, malformed binding, key_to_step_id lookup, node_key fallback, raw step_id fallback, output_ref resolution
- `inject_resolved_inputs` — empty resolved, missing attempt, context merge

**Estimated tests:** 8-10
**Dependencies to mock:** `KernelStore` (get_step, get_step_by_node_key, get_step_attempt, update_step_attempt)
**Complexity:** Easy — pure data transformation with store lookups
**Priority:** HIGH — DAG data flow correctness

---

### 2. Kernel Execution Executor

#### `executor/formatting.py` — 20% coverage (69 LOC)

**Untested functions:**
- `format_model_content` — string/dict/list/JSON branches
- `progress_signature`, `progress_summary_signature`
- `compact_progress_text` — empty, within limit, truncation

**Note:** `truncate_middle` may have partial coverage.

**Estimated tests:** 8-10
**Dependencies to mock:** `serialize_tool_result`, `normalize_observation_progress`, `normalize_progress_summary`
**Complexity:** Easy — pure functions
**Priority:** MEDIUM — formatting helpers, low risk

---

#### `executor/snapshot.py` — 30% coverage (207 LOC)

**Untested functions/branches:**
- `RuntimeSnapshotManager.create_envelope` — unsupported keys error, size limit error, happy path
- `extract_payload` — schema version 1/2/3 branching, kind validation, expiry check, unknown keys, size check
- `store_resume_messages`, `load_resume_messages` — unknown artifact, non-list payload, valid path
- `store_snapshot_artifact`, `load_snapshot_envelope` — missing artifact, JSON decode error

**Estimated tests:** 12-15
**Dependencies to mock:** `KernelStore` (get_artifact), `ArtifactStore` (read_text), `store_artifact` callable
**Complexity:** Easy-Medium — SnapshotError raises are straightforward
**Priority:** HIGH — snapshot reliability affects task suspension/resume

---

#### `executor/subtask_handler.py` — 22% coverage (170 LOC)

**Untested functions:**
- `normalize_spawn_descriptors` — non-dict input, missing envelope key, empty list, invalid items, strategy normalization
- `SubtaskSpawner.handle_spawn` — child creation, parent suspension, event recording, result construction
- `_spawn_children` — multi-descriptor creation

**Estimated tests:** 10-12
**Dependencies to mock:** `KernelStore` (create_step, create_step_attempt, update_step_attempt, update_step, update_task_status, append_event), `executor._set_attempt_phase`
**Complexity:** Medium — `normalize_spawn_descriptors` is pure; `handle_spawn` needs store + executor mock
**Priority:** HIGH — subtask spawning is a new core feature

---

#### `executor/phase_tracker.py` — 33% coverage (66 LOC)

**Untested functions:**
- `_execution_status_from_result_code` — all 9+ branches
- `PhaseTracker.set_attempt_phase`
- `_needs_witness` — in/not-in set

**Estimated tests:** 6-8
**Dependencies to mock:** `KernelStore`, `_set_attempt_phase` (from execution_helpers)
**Complexity:** Easy — mostly lookup functions
**Priority:** MEDIUM — small module, but status mapping is critical

---

### 3. Kernel Execution Controller

#### `controller/supervision.py` — 16% coverage (230 LOC)

**Untested functions:**
- `SupervisionService.build_task_case` — full case assembly with projections, claims, approvals, reentry, rollback
- `rollback_receipt`
- `_build_ingress_observability` — null task, conversation projection, focus task
- `_recent_related_ingresses` — relation filtering
- `_serialize_ingress`, `_serialize_ingress_list`
- `_reentry_observability` — required/resolved counting, recent filtering
- `_trim` — within/exceeding limit

**Estimated tests:** 14-16
**Dependencies to mock:** `KernelStore` (get_task, get_rollback_for_receipt, list_step_attempts, list_ingresses), `ProjectionService`, `ConversationProjectionService`, `RollbackService`
**Complexity:** Medium — data assembly with many nested projections
**Priority:** HIGH — supervision is the kernel observability surface

---

### 4. Kernel Policy

#### `approvals/approval_copy.py` — 42% coverage (659 LOC)

**Untested functions/branches:**
- `_format_with_optional_formatter` — formatter timeout, string return, None return
- `_copy_from_mapping` — missing title/summary
- `_sections_from_mapping` — non-list, invalid entries
- `_ensure_sections` — with/without existing sections
- `_template_copy` — all branches: git push, rm/trash/del, generic command, single sensitive file, outside workspace, single/multi file, single/multi host, packet title, fallback
- `_scheduler_copy` — schedule_create, schedule_update, schedule_delete
- `_scheduler_sections` — all three tool_name branches with sub-items
- `_describe_scheduler_timing` — once/interval/cron/unknown
- `_next_cron_run_text`, `_format_datetime_text`, `_format_interval`
- `blocked_message`, `model_prompt`

**Estimated tests:** 25-30
**Dependencies to mock:** i18n `tr`/`resolve_locale` (already in conftest), optional formatter callable
**Complexity:** Easy — pure template logic, no store dependencies. Most branches just produce strings.
**Priority:** MEDIUM — user-facing copy quality, but not correctness-critical

---

### 5. Kernel Ledger

#### `journal/store_scheduler.py` — 26% coverage (124 LOC)

**Untested functions:**
- `create_schedule` — INSERT OR REPLACE
- `update_schedule` — get + mutate + re-save, missing job
- `delete_schedule` — success and not-found
- `get_schedule` — found and not-found
- `list_schedules`
- `append_schedule_history`
- `list_schedule_history` — with and without job_id filter

**Estimated tests:** 8-10
**Dependencies to mock:** Real in-memory KernelStore (via `kernel_store` fixture)
**Complexity:** Easy — direct SQL CRUD, test with fixture
**Priority:** MEDIUM — scheduler store correctness

---

### 6. Kernel Authority

#### `identity/service.py` — 0-45% coverage (41 LOC)

**Untested functions:**
- `PrincipalService.resolve` — delegates to store.ensure_principal
- `resolve_name` — actor=None, actor="kernel", actor with source_channel mapping

#### `identity/models.py` — 0% coverage (17 LOC)

**Untested:** `PrincipalRecord` dataclass instantiation

**Estimated tests:** 5-6
**Dependencies to mock:** `KernelStore` (ensure_principal)
**Complexity:** Easy — thin wrapper
**Priority:** LOW — trivial delegation

---

### 7. Kernel Analytics

#### `analytics/task_metrics.py` — 31% coverage (183 LOC)

**Untested functions:**
- `TaskMetricsService.compute_task_metrics` — task not found, steps with/without timing, attempt fallback timing, step status counting
- `compute_multi_task_metrics` — empty list, mixed found/not-found, timing aggregation

**Estimated tests:** 10-12
**Dependencies to mock:** `KernelStore` (get_task, list_steps, list_step_attempts) — or use in-memory fixture
**Complexity:** Easy-Medium — timing fallback logic needs step-attempt fixtures
**Priority:** MEDIUM — metrics correctness for MCP exposure

---

### 8. Kernel Execution Suspension

#### `suspension/git_worktree.py` — 39% coverage (113 LOC)

**Untested functions/branches:**
- `GitWorktreeSnapshot.to_state` — present/not-present/error
- `to_witness` — present/not-present/with-error
- `to_prestate` — present/error
- `GitWorktreeInspector.snapshot` — no .git dir, rev-parse failure, status failure, OSError, clean/dirty
- `hard_reset`
- `create_worktree`, `remove_worktree`
- `_command_error` — returncode 0, nonzero with/without stderr

**Estimated tests:** 10-12
**Dependencies to mock:** `subprocess.run`, `Path.exists`
**Complexity:** Medium — subprocess mocking, filesystem state
**Priority:** MEDIUM — task suspension correctness

---

### 9. CLI Surfaces (`src/hermit/surfaces/cli/`)

#### `main.py` — ~48% coverage (110 LOC)

**Untested:** `_load_hermit_env` edge cases, `_current_locale` exception path

**Estimated tests:** 3-4
**Complexity:** Easy — env var mocking
**Priority:** LOW

---

#### `_commands_core.py` — ~20% coverage (365 LOC)

**Untested functions:**
- `setup()` — interactive wizard (confirmations, prompts)
- `run()` / `chat()` — full orchestration with AgentRunner
- `init()` — workspace initialization
- `startup_prompt()` — context assembly

**Estimated tests:** 10-12
**Dependencies to mock:** `typer.testing.CliRunner`, `AgentRunner`, `PluginManager`, `Settings`
**Complexity:** Hard — deeply integrated with runtime assembly
**Priority:** MEDIUM — CLI correctness, but integration-test territory

---

#### `_commands_task.py` — ~15% coverage (579 LOC)

**Untested:** `task_list`, `task_show`, `task_proof`, `task_approve`, `task_deny`, `task_cancel`, `task_await`, `task_metrics`, `task_health`

**Estimated tests:** 12-15
**Dependencies to mock:** `KernelStore`, `TaskController`, `typer.testing.CliRunner`
**Complexity:** Medium — each command is a thin store query + format
**Priority:** MEDIUM

---

#### `_commands_memory.py` — ~10% coverage (494 LOC)

**Untested:** `memory_list`, `memory_show`, `memory_search`, `memory_forget`, `memory_export`

**Estimated tests:** 10-12
**Complexity:** Medium
**Priority:** LOW

---

#### `_commands_schedule.py` — ~15% coverage (277 LOC)

**Untested:** `schedule_list`, `schedule_show`, `schedule_history`

**Estimated tests:** 5-7
**Complexity:** Medium
**Priority:** LOW

---

#### `_serve.py` — ~20% coverage (462 LOC)

**Untested:** `serve()` command, reload loop, PID management, signal handling, adapter dispatch

**Estimated tests:** 8-10
**Complexity:** Hard — async event loop, signal handlers, process management
**Priority:** MEDIUM — partially covered by `test_serve_loop.py`

---

#### `_helpers.py` — ~30% coverage (269 LOC)

**Untested:** `ensure_workspace`, `caffeinate`, `require_auth`, `auth_status_summary`, `resolved_config_snapshot`, `stop_runner_background_services`

**Estimated tests:** 6-8
**Complexity:** Medium — subprocess calls, settings interaction
**Priority:** LOW

---

#### `_preflight.py` — ~25% coverage (632 LOC)

**Untested:** Preflight check functions (auth validation, workspace checks, version checks)

**Estimated tests:** 12-15
**Complexity:** Medium — i/o and settings mocking
**Priority:** MEDIUM

---

### 10. Runtime Runner (`src/hermit/runtime/control/runner/`)

#### `runner.py` — ~40% coverage (808 LOC)

**Untested functions:**
- `AgentRunner.__init__` — validation, attribute setup
- `start_background_services`, `stop_background_services`
- `add_command`, `wake_dispatcher`
- `_ensure_session_started`, `close_session`, `reset_session`
- `process_claimed_attempt`, `dispatch`, `handle_user_input`

**Estimated tests:** 15-18
**Dependencies to mock:** `AgentRuntime`, `SessionManager`, `PluginManager`, `TaskController`, `KernelDispatchService`, `ObservationService`
**Complexity:** Hard — central orchestration, many collaborators
**Priority:** HIGH — core execution path

---

#### `task_executor.py` — ~30% coverage (468 LOC)

**Untested:** `_compile_provider_input`, `execute_task`, `_resume_attempt`, full agent loop with tool calls

**Estimated tests:** 10-12
**Complexity:** Hard — LLM provider interaction, session management
**Priority:** HIGH

---

#### `control_actions.py` — ~20% coverage (537 LOC)

**Untested:** `ControlActionDispatcher.dispatch` — all control action branches (task commands, memory, system)

**Estimated tests:** 12-15
**Complexity:** Medium — each action is a dispatch branch
**Priority:** MEDIUM

---

#### `async_dispatcher.py` — ~25% coverage (277 LOC)

**Untested:** `enqueue_ingress`, `process_approval_resume`, `dispatch_result`, `_build_dispatch_result_text`

**Estimated tests:** 8-10
**Complexity:** Medium
**Priority:** HIGH — async ingress path

---

### 11. Runtime Provider Host

#### `execution/services.py` — ~27% coverage (301 LOC)

**Untested:** `build_runtime` — full assembly of provider, executor, store, tools

**Estimated tests:** 5-7
**Complexity:** Hard — imports and assembles the entire runtime stack
**Priority:** MEDIUM — integration assembly, better as integration test

---

#### `execution/runtime.py` — ~39% coverage (926 LOC)

**Untested:** `AgentRuntime.run` — multi-turn loop, tool dispatch, context-too-long recovery, token tracking

**Estimated tests:** 15-18
**Dependencies to mock:** `Provider`, `ToolExecutor`, `ToolRegistry`
**Complexity:** Hard — core LLM interaction loop
**Priority:** HIGH — but many paths are integration-level

---

### 12. Apps Companion

#### `companion/appbundle.py` — 15% coverage (417 LOC)

**Untested:** `app_name`, `bundle_id`, `app_path`, `_install_bundle_icon`, `build_app_bundle`, `install_app`, `uninstall_app`, CLI entrypoint

**Estimated tests:** 10-12
**Dependencies to mock:** `subprocess.run`, `shutil`, `plistlib`, filesystem operations
**Complexity:** Medium — macOS-specific subprocess calls
**Priority:** LOW — platform-specific build tooling

---

#### `companion/control.py` — 20% coverage (695 LOC)

**Untested functions:**
- `log_companion_event`, `format_exception_message`
- `ensure_config_file`, `load_runtime_settings`, `load_profile_runtime_settings`
- `set_default_profile`, `update_profile_setting`
- `matching_process_pids`, `_iter_process_table`, `_has_env_assignment`
- `service_status`, `start_service`, `stop_service`, `reload_service`
- `switch_profile`, `update_profile_bool_and_restart`
- `open_path`, `open_in_textedit`, `open_url`

**Estimated tests:** 18-22
**Dependencies to mock:** `subprocess`, `os.kill`, filesystem, `Settings`
**Complexity:** Medium — process management and config file manipulation
**Priority:** MEDIUM — companion reliability

---

## Priority-Ordered Implementation Plan

### Phase 1 — Critical Path (est. +12-15% coverage)

| Module | Est. Tests | Complexity |
|--------|-----------|------------|
| `coordination/dispatch.py` | 18-22 | Medium |
| `coordination/data_flow.py` | 8-10 | Easy |
| `executor/subtask_handler.py` | 10-12 | Medium |
| `executor/snapshot.py` | 12-15 | Easy-Medium |
| `controller/supervision.py` | 14-16 | Medium |
| `runner/runner.py` | 15-18 | Hard |
| `runner/task_executor.py` | 10-12 | Hard |
| `runner/async_dispatcher.py` | 8-10 | Medium |

**Subtotal: ~95-115 tests**

### Phase 2 — High Value (est. +8-10% coverage)

| Module | Est. Tests | Complexity |
|--------|-----------|------------|
| `approvals/approval_copy.py` | 25-30 | Easy |
| `executor/formatting.py` | 8-10 | Easy |
| `executor/phase_tracker.py` | 6-8 | Easy |
| `analytics/task_metrics.py` | 10-12 | Easy-Medium |
| `store_scheduler.py` | 8-10 | Easy |
| `runner/control_actions.py` | 12-15 | Medium |
| `companion/control.py` | 18-22 | Medium |

**Subtotal: ~87-107 tests**

### Phase 3 — CLI and Platform (est. +5-7% coverage)

| Module | Est. Tests | Complexity |
|--------|-----------|------------|
| `cli/_commands_task.py` | 12-15 | Medium |
| `cli/_commands_core.py` | 10-12 | Hard |
| `cli/_preflight.py` | 12-15 | Medium |
| `cli/_serve.py` | 8-10 | Hard |
| `cli/_commands_memory.py` | 10-12 | Medium |
| `cli/_commands_schedule.py` | 5-7 | Medium |
| `execution/runtime.py` | 15-18 | Hard |
| `suspension/git_worktree.py` | 10-12 | Medium |
| `companion/appbundle.py` | 10-12 | Medium |
| `identity/service.py` + `models.py` | 5-6 | Easy |

**Subtotal: ~97-119 tests**

---

## Testing Patterns to Follow

Based on analysis of existing tests (`test_approval_handler.py`, `test_request_builder.py`, `conftest.py`):

1. **In-memory KernelStore** — use `kernel_store` fixture for any store-dependent test
2. **SimpleNamespace** for lightweight fakes — `FakeRunner`, `FakePM` pattern
3. **MagicMock** for complex collaborators with method tracking
4. **monkeypatch** for env vars, module-level functions, `subprocess.run`
5. **CliRunner** for Typer CLI command testing
6. **Helper factories** — `_make_action_request()`, `_make_step_attempt()` for test data
7. **Event list tracking** — append to lists in mock callbacks for assertion

## Estimated Overall Impact

- Phase 1: 67% -> ~80% coverage
- Phase 2: 80% -> ~89% coverage
- Phase 3: 89% -> ~95% coverage

Total estimated new tests: **220-260 functions** across **~25 test files**.
