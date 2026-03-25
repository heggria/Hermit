# 03 - Domain Partition Plan for Parallel Test Writing

> Generated: 2026-03-19
> Baseline: `release/0.3` branch, overall coverage 67%
> Target: 95% unit-test coverage per zone

---

## 1. Current Coverage Landscape

### Per-Area Summary

| Area | Files | Statements | Avg Coverage | Files <80% | Missing Lines to 95% |
|------|------:|----------:|-----------:|----------:|--------------------:|
| apps/ | 3 | 615 | 26% | 3 | ~440 |
| infra/ | 6 | 255 | 88% | 1 | ~32 |
| kernel/analytics | 5 | 272 | 82% | 1 | ~50 |
| kernel/artifacts | 4 | 428 | 96% | 0 | ~0 |
| kernel/authority | 6 | 198 | 83% | 2 | ~34 |
| kernel/context | 25 | 2,219 | 95% | 3 | ~70 |
| kernel/execution | 41 | 3,692 | 81% | 14 | ~551 |
| kernel/ledger | 9 | 1,634 | 85% | 3 | ~250 |
| kernel/policy | 23 | 1,217 | 78% | 6 | ~219 |
| kernel/signals | 5 | 264 | 97% | 0 | ~7 |
| kernel/task | 17 | 2,485 | 63% | 9 | ~801 |
| kernel/verification | 11 | 953 | 80% | 3 | ~164 |
| plugins/adapters | 15 | 2,798 | 55% | 3 | ~1,141 |
| plugins/bundles | 3 | 54 | 100% | 0 | ~0 |
| plugins/hooks | 34 | 3,032 | 71% | 11 | ~790 |
| plugins/mcp+subagents+tools | 16 | 1,023 | 74% | 6 | ~158 |
| runtime/assembly | 2 | 383 | 86% | 0 | ~54 |
| runtime/capability | 13 | 1,056 | 72% | 5 | ~301 |
| runtime/control | 10 | 1,200 | 31% | 8 | ~829 |
| runtime/observation | 1 | 16 | 44% | 1 | ~9 |
| runtime/provider_host | 12 | 2,029 | 72% | 6 | ~560 |
| surfaces/cli | 12 | 1,402 | 43% | 8 | ~802 |
| surfaces/cli/tui | 9 | ~400 est | 0% | 9 | ~380 |

### Completely Untested Modules (0% coverage or absent from coverage)

| Module | Statements | Notes |
|--------|----------:|-------|
| `kernel/authority/identity/service.py` | 14 | PrincipalService |
| `kernel/execution/coordination/dispatch.py` | 152 | KernelDispatchService (core async orchestration) |
| `kernel/task/services/dag_execution.py` | 35 | DAG execution service |
| `kernel/verification/proofs/dag_proof.py` | 41 | DAG proof generation |
| `kernel/verification/proofs/merkle.py` | 33 | Merkle tree |
| `plugins/builtin/hooks/memory/services.py` | 65 | Memory service assembly |
| `plugins/builtin/hooks/webhook/tools.py` | 121 | Webhook tool registration |
| `plugins/builtin/mcp/github/mcp.py` | 29 | GitHub MCP plugin |
| `plugins/builtin/mcp/mcp_loader/mcp.py` | 67 | MCP loader |
| `plugins/builtin/subagents/orchestrator/dag_orchestrator.py` | 27 | DAG orchestrator |
| `plugins/builtin/subagents/orchestrator/subagents.py` | 5 | Subagent registration |
| `plugins/builtin/tools/computer_use/tools.py` | 9 | Tool registration |
| `plugins/builtin/tools/grok/tools.py` | 6 | Tool registration |
| `plugins/builtin/tools/web_tools/tools.py` | 8 | Tool registration |
| `runtime/capability/contracts/kernel_services.py` | 34 | Kernel service contracts |
| `runtime/control/runner/async_dispatcher.py` | 111 | Async task dispatcher |
| `runtime/control/runner/control_actions.py` | 210 | Runner control actions |
| `runtime/control/runner/session_context_builder.py` | 38 | Session context assembly |
| `runtime/control/runner/task_executor.py` | 182 | Task execution bridge |
| All `surfaces/cli/tui/` files | ~400 | Textual TUI (9 files, never measured) |

---

## 2. Module Dependency Analysis

### Dependency Graph (simplified)

```
infra (leaf) ─────────────────────────────────────────────────┐
                                                               │
kernel/ledger ←── kernel/task ←── kernel/execution             │
       ↑              ↑              ↑                         │
       │              │              │                         ↓
kernel/policy ────────┤         kernel/signals          all modules
       ↑              │              ↑                    use infra
       │              │              │
kernel/authority ─────┤         kernel/analytics
       ↑              │
       │              │
kernel/artifacts ─────┤
       ↑              │
       │              │
kernel/context ───────┘
kernel/verification ──── kernel/ledger + kernel/task + kernel/authority

plugins ──── kernel (many imports) + runtime/capability
runtime ──── kernel (many imports) + infra
surfaces ─── kernel + runtime
```

### Coupling Assessment

**Highly coupled (central hubs):**
- `kernel/ledger/journal/store.py` -- imported by nearly everything in kernel
- `kernel/task/models/records.py` -- data models used across kernel
- `kernel/task/services/controller.py` -- task lifecycle used by execution, runtime, plugins
- `kernel/policy` models/engine -- used by execution and runtime
- `runtime/capability/registry/tools.py` -- ToolRegistry/ToolSpec used in all test fixtures

**Moderately coupled:**
- `kernel/execution/executor/executor.py` -- depends on policy, approvals, receipts, grants
- `kernel/context/memory/*` -- mostly internal to context, but used by plugins/hooks/memory
- `kernel/artifacts/` -- used by execution and verification

**Independent (low coupling):**
- `infra/` -- leaf dependency, no kernel imports
- `apps/companion/` -- depends only on runtime/assembly/config and subprocess calls
- `plugins/builtin/adapters/slack/` -- self-contained adapter
- `plugins/builtin/adapters/telegram/` -- self-contained adapter
- `plugins/builtin/bundles/` -- thin wrappers, already 100%
- `kernel/context/memory/` internal modules (decay, embeddings, graph, taxonomy) -- mostly peer-independent
- `kernel/signals/` -- small, well-isolated
- `kernel/analytics/` -- reads from store, minimal write coupling

---

## 3. Ten-Zone Partition

### Design Principles

1. **Zones follow directory boundaries** -- minimizes conftest.py conflicts
2. **Cross-zone test dependencies flow in one direction** -- leaf zones first
3. **Effort roughly balanced** -- each zone targets 150-800 missing lines to 95%
4. **Heavy runtime/surfaces area split into two zones** to manage complexity
5. **Feishu adapter isolated** -- its deep coupling to globals warrants separate treatment

---

### Zone 1: Infrastructure + Apps Companion

**Scope:** `src/hermit/infra/` + `src/hermit/apps/companion/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `infra/paths.py` | 78% | 1 | 1 |
| `infra/locking/lock.py` | 82% | 9 | 4 |
| `infra/storage/atomic.py` | 90% | 3 | 0 |
| `infra/storage/store.py` | 88% | 11 | 6 |
| `infra/system/executables.py` | 81% | 5 | 4 |
| `infra/system/i18n.py` | 87% | 8 | 5 |
| `apps/companion/appbundle.py` | 15% | 143 | 135 |
| `apps/companion/control.py` | 20% | 294 | 257 |
| `apps/companion/menubar.py` | 58% | 18 | 16 |

**Estimated new tests:** ~50 test functions
**Effort to 95%:** ~428 lines to cover
**Test directory:** `tests/unit/infra/`, `tests/unit/apps/`

**Existing test files:**
- `tests/unit/infra/test_atomic_write.py`
- `tests/unit/infra/test_executables.py`
- `tests/unit/infra/test_i18n.py`
- `tests/unit/infra/test_flatten_dict.py`
- `tests/unit/infra/test_install_scripts.py`
- `tests/unit/apps/test_autostart.py`
- `tests/unit/apps/test_companion_menubar.py`

**New test files needed:**
- `tests/unit/infra/test_lock.py`
- `tests/unit/infra/test_store.py`
- `tests/unit/infra/test_paths.py`
- `tests/unit/apps/test_appbundle.py`
- `tests/unit/apps/test_companion_control.py`

**Key mocking dependencies:**
- `subprocess.run` / `subprocess.Popen` (apps/companion calls `open`, `osascript`, `pkill`)
- `shutil.which` (executables detection)
- `fcntl.flock` (lock tests)
- `pathlib.Path` filesystem ops (atomic writes)

**Testability assessment:**
- infra: Excellent testability. Pure functions, filesystem ops easily mocked with `tmp_path`.
- apps/companion: Moderate difficulty. Heavy subprocess usage for macOS operations (`osascript`, `open`, `launchctl`). All need to be mocked. `control.py` has many subprocess calls to check PIDs and manage services -- each branch needs a mock scenario.
- No singletons or globals in infra. `apps/companion` is clean.

**Reusable fixtures:** `tmp_path` (builtin), `_clean_hermit_env` (from `tests/unit/apps/conftest.py`)
**New fixtures needed:** `mock_subprocess` (monkeypatch `subprocess.run`), `mock_launchctl` for companion control

---

### Zone 2: Kernel Context (Memory, Compiler, Injection)

**Scope:** `src/hermit/kernel/context/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `context/compiler/compiler.py` | 80% | 26 | 20 |
| `context/injection/provider_input.py` | 79% | 35 | 27 |
| `context/memory/knowledge.py` | 73% | 23 | 19 |
| All other context/memory files | 96-100% | ~17 | ~4 |

**Estimated new tests:** ~20 test functions
**Effort to 95%:** ~70 lines to cover
**Test directory:** `tests/unit/kernel/context/`

**Existing test files (selected):**
- `tests/unit/kernel/context/test_memory_quality.py`
- `tests/unit/kernel/test_anti_pattern.py`
- `tests/unit/kernel/test_confidence_decay.py`
- `tests/unit/kernel/test_consolidation.py`
- `tests/unit/kernel/test_embeddings.py`
- `tests/unit/kernel/test_episodic_memory.py`
- `tests/unit/kernel/test_hybrid_retrieval.py`
- `tests/unit/kernel/test_memory_decay.py`
- `tests/unit/kernel/test_memory_governance.py`
- `tests/unit/kernel/test_memory_graph.py`
- `tests/unit/kernel/test_memory_lineage.py`
- `tests/unit/kernel/test_memory_taxonomy.py`
- `tests/unit/kernel/test_reflection.py`
- `tests/unit/kernel/test_working_memory.py`
- `tests/unit/kernel/test_cross_encoder_reranker.py`
- `tests/unit/kernel/test_context_compiler.py`
- `tests/unit/kernel/test_entity_triples.py`
- `tests/unit/kernel/test_procedural_memory.py`

**New test files needed:**
- `tests/unit/kernel/context/test_provider_input.py` (expand existing coverage)
- `tests/unit/kernel/context/test_knowledge.py`

**Key mocking dependencies:**
- `KernelStore` (via `kernel_store` fixture -- in-memory SQLite)
- `ArtifactStore` (filesystem-based, use `tmp_path`)
- `WorkspaceLeaseService` (for compiler)

**Testability assessment:**
- Excellent. Most context/memory modules are pure-computation or use simple store interfaces.
- Already near 95%. This is the lightest zone -- good for a quick win or combining with validation work.
- No globals or singletons.

**Reusable fixtures:** `kernel_store` (global conftest), `tmp_path`
**New fixtures needed:** Likely none -- existing patterns sufficient.

---

### Zone 3: Kernel Execution -- Executor Handlers

**Scope:** `src/hermit/kernel/execution/executor/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `executor/executor.py` | 75% | 69 | 55 |
| `executor/authorization_handler.py` | 65% | 24 | 20 |
| `executor/dispatch_handler.py` | 53% | 14 | 11 |
| `executor/formatting.py` | 20% | 31 | 28 |
| `executor/observation_handler.py` | 77% | 51 | 40 |
| `executor/phase_tracker.py` | 33% | 15 | 13 |
| `executor/snapshot.py` | 30% | 42 | 37 |
| `executor/subtask_handler.py` | 22% | 36 | 33 |
| `executor/witness_handler.py` | 74% | 8 | 5 |
| All other executor files | 92-100% | ~15 | ~1 |

**Estimated new tests:** ~60 test functions
**Effort to 95%:** ~243 lines to cover
**Test directory:** `tests/unit/kernel/execution/`

**Existing test files:**
- `tests/unit/kernel/execution/test_approval_handler.py`
- `tests/unit/kernel/execution/test_contract_executor.py`
- `tests/unit/kernel/execution/test_drift_handler.py`
- `tests/unit/kernel/execution/test_observation_handler.py`
- `tests/unit/kernel/execution/test_receipt_handler.py`
- `tests/unit/kernel/execution/test_reconciliation_executor.py`
- `tests/unit/kernel/execution/test_recovery_handler.py`
- `tests/unit/kernel/execution/test_request_builder.py`
- `tests/unit/kernel/execution/test_state_persistence.py`

**New test files needed:**
- `tests/unit/kernel/execution/test_executor_core.py`
- `tests/unit/kernel/execution/test_authorization_handler.py`
- `tests/unit/kernel/execution/test_dispatch_handler.py`
- `tests/unit/kernel/execution/test_formatting.py`
- `tests/unit/kernel/execution/test_phase_tracker.py`
- `tests/unit/kernel/execution/test_snapshot.py`
- `tests/unit/kernel/execution/test_subtask_handler.py`
- `tests/unit/kernel/execution/test_witness_handler.py`

**Key mocking dependencies:**
- `ToolRegistry` + `ToolSpec` (from `tests/fixtures/task_kernel_support.py`)
- `KernelStore` (in-memory)
- `ArtifactStore`
- `PolicyEngine`
- `ApprovalService`
- `ReceiptService`
- `CapabilityGrantService`

**Testability assessment:**
- Moderate difficulty. `executor.py` is the most coupled file (~275 lines, 75% covered). It orchestrates policy evaluation, approval, receipt issuance, and rollback.
- Each handler file is relatively well-factored (single responsibility) which helps.
- `snapshot.py` depends on `KernelStore` queries and git worktree state.
- `subtask_handler.py` depends on `TaskController.start_task()` for delegation.
- No problematic globals.

**Reusable fixtures:** `kernel_store`, `_kernel_runtime()` from `task_kernel_support.py`, `e2e_runtime` from e2e conftest
**New fixtures needed:** `mock_grant_service`, `mock_dispatch_service` for authorization and dispatch handler tests.

---

### Zone 4: Kernel Execution -- Coordination, Controller, Recovery, Competition

**Scope:** `src/hermit/kernel/execution/coordination/` + `controller/` + `recovery/` + `competition/` + `suspension/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `coordination/dispatch.py` | 0% | 152 | 144 |
| `coordination/data_flow.py` | 13% | 29 | 27 |
| `controller/supervision.py` | 16% | 69 | 65 |
| `suspension/git_worktree.py` | 39% | 32 | 29 |
| `recovery/reconciliations.py` | 69% | 17 | 14 |
| `coordination/observation.py` | 84% | 15 | 10 |
| `controller/execution_contracts.py` | 87% | 10 | 5 |
| All other files | 90-100% | ~59 | ~14 |

**Estimated new tests:** ~60 test functions
**Effort to 95%:** ~308 lines to cover
**Test directory:** `tests/unit/kernel/execution/`, `tests/unit/kernel/`

**Existing test files:**
- `tests/unit/kernel/test_auto_park.py`
- `tests/unit/kernel/test_competition_criteria.py`
- `tests/unit/kernel/test_competition_evaluator.py`
- `tests/unit/kernel/test_competition_service.py`
- `tests/unit/kernel/test_competition_store.py`
- `tests/unit/kernel/test_competition_workspace.py`
- `tests/unit/kernel/test_join_barrier.py`
- `tests/unit/kernel/test_prioritizer.py`
- `tests/unit/kernel/test_reconcile_service.py`
- `tests/unit/kernel/test_contract_expiry_and_policy_revalidation.py`
- `tests/unit/kernel/test_contract_template_learner.py`
- `tests/unit/kernel/test_task_pattern_learner.py`

**New test files needed:**
- `tests/unit/kernel/execution/test_dispatch_service.py` (for `coordination/dispatch.py`)
- `tests/unit/kernel/execution/test_data_flow.py`
- `tests/unit/kernel/execution/test_supervision.py`
- `tests/unit/kernel/execution/test_git_worktree.py`
- `tests/unit/kernel/execution/test_reconciliations.py`
- `tests/unit/kernel/execution/test_observation_service.py`

**Key mocking dependencies:**
- `KernelStore`
- `ToolExecutor` (for KernelDispatchService)
- `TaskController`
- `asyncio` event loop (dispatch.py is async)
- `subprocess.run` (git_worktree.py shells out to `git`)
- `concurrent.futures.ThreadPoolExecutor` (dispatch.py)

**Testability assessment:**
- `coordination/dispatch.py` is the hardest file in this zone. It is a fully async service managing thread pools, background tasks, and timeout handling. Requires careful async test setup with `pytest-asyncio`.
- `controller/supervision.py` depends on `ToolExecutor` and `TaskController`, but can be tested with the standard kernel fixture from `task_kernel_support.py`.
- `suspension/git_worktree.py` shells out to `git` commands -- needs `subprocess` mocking.
- No problematic singletons.

**Reusable fixtures:** `kernel_store`, `_kernel_runtime()`, `FakeGitWorktree` (from task_kernel_support)
**New fixtures needed:** `mock_dispatch_executor` (async executor with controlled responses), `mock_git_subprocess`

---

### Zone 5: Kernel Policy (Guards, Evaluators, Approvals, Permits, Trust)

**Scope:** `src/hermit/kernel/policy/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `approvals/approval_copy.py` | 42% | 164 | 146 |
| `approvals/approvals.py` | 63% | 31 | 22 |
| `guards/rules_planning.py` | 45% | 6 | 5 |
| `guards/rules_attachment.py` | 47% | 5 | 4 |
| `guards/tool_spec_adapter.py` | 60% | 14 | 11 |
| `evaluators/derivation.py` | 79% | 34 | 27 |
| All other files | 87-100% | ~16 | ~4 |

**Estimated new tests:** ~50 test functions
**Effort to 95%:** ~219 lines to cover
**Test directory:** `tests/unit/kernel/policy/`

**Existing test files:**
- `tests/unit/kernel/policy/test_rules_adjustment.py`
- `tests/unit/kernel/policy/test_rules_filesystem.py`
- `tests/unit/kernel/policy/test_rules_governance.py`
- `tests/unit/kernel/policy/test_rules_readonly.py`
- `tests/unit/kernel/policy/test_rules_shell.py`
- `tests/unit/kernel/test_kernel_permits.py`
- `tests/unit/kernel/test_kernel_self_mod_guard.py`
- `tests/unit/kernel/test_policy_derivation.py`
- `tests/unit/kernel/test_policy_evidence_enricher.py`
- `tests/unit/kernel/test_policy_properties.py`
- `tests/unit/kernel/test_policy_suggestion.py`
- `tests/unit/kernel/test_delegation_policy.py`

**New test files needed:**
- `tests/unit/kernel/policy/test_approval_copy.py`
- `tests/unit/kernel/policy/test_approvals_service.py`
- `tests/unit/kernel/policy/test_rules_planning.py`
- `tests/unit/kernel/policy/test_rules_attachment.py`
- `tests/unit/kernel/policy/test_tool_spec_adapter.py`
- `tests/unit/kernel/policy/test_derivation_extended.py`

**Key mocking dependencies:**
- `KernelStore` (approval persistence)
- `ActionRequest` model construction
- `TaskExecutionContext`
- `CapabilityGrantService` (for approval_copy)
- `DecisionService`

**Testability assessment:**
- Good testability. Policy rules are mostly pure functions taking `ActionRequest` and returning `RuleOutcome`.
- `approval_copy.py` is the largest gap (164 missing lines). It copies approval decisions across sessions. Depends on `KernelStore` queries but is testable with in-memory store.
- `approvals.py` manages approval lifecycle with store persistence -- straightforward to test.
- No globals or singletons.

**Reusable fixtures:** `kernel_store`
**New fixtures needed:** `sample_action_request` factory fixture, `approval_context` fixture with pre-populated decisions.

---

### Zone 6: Kernel Task + Signals

**Scope:** `src/hermit/kernel/task/` + `src/hermit/kernel/signals/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `task/services/controller.py` | 47% | 296 | 263 |
| `task/state/control_intents.py` | 11% | 161 | 152 |
| `task/services/ingress_router.py` | 26% | 161 | 150 |
| `task/services/topics.py` | 45% | 94 | 85 |
| `task/services/planning.py` | 66% | 41 | 32 |
| `task/services/delegation_store.py` | 49% | 21 | 18 |
| `task/projections/projections.py` | 60% | 48 | 42 |
| `task/state/outcomes.py` | 62% | 11 | 8 |
| `task/services/dag_execution.py` | 0% | 35 | 33 |
| `task/projections/conversation.py` | 83% | 15 | 10 |
| signals/* | 82-100% | ~7 | ~5 |

**Estimated new tests:** ~100 test functions
**Effort to 95%:** ~801 lines to cover (HIGHEST effort zone)
**Test directory:** `tests/unit/kernel/`

**Existing test files:**
- `tests/unit/kernel/test_dag_builder.py`
- `tests/unit/kernel/test_planner_kernel.py`
- `tests/unit/kernel/test_step_dag_activation.py`
- `tests/unit/kernel/test_task_delegation.py`
- `tests/unit/kernel/test_task_delegation_coverage.py`
- `tests/unit/kernel/test_steering.py`
- `tests/unit/kernel/test_steering_coverage.py`
- `tests/unit/kernel/test_signal_consumer_coverage.py`
- `tests/unit/kernel/test_signal_protocol_coverage.py`
- `tests/unit/kernel/test_signal_store_coverage.py`
- `tests/unit/kernel/test_evidence_signals.py`

**New test files needed:**
- `tests/unit/kernel/task/test_controller.py`
- `tests/unit/kernel/task/test_control_intents.py`
- `tests/unit/kernel/task/test_ingress_router.py`
- `tests/unit/kernel/task/test_topics.py`
- `tests/unit/kernel/task/test_dag_execution.py`
- `tests/unit/kernel/task/test_delegation_store.py`
- `tests/unit/kernel/task/test_projections.py`
- `tests/unit/kernel/task/test_outcomes.py`
- `tests/unit/kernel/task/test_planning_extended.py`

**Key mocking dependencies:**
- `KernelStore` (heavy -- controller.py makes many store calls)
- `ArtifactStore`
- `StepDAGBuilder`, `StepDataFlowService`
- `PlanningService` depends on store and task models
- `SteeringProtocol` / `CompetitionService` (signals)

**Testability assessment:**
- `controller.py` (296 missing lines) is the biggest single-file gap in the project. It orchestrates task lifecycle (create, advance, complete, fail, cancel, pause, resume). Many methods, many branches.
- `control_intents.py` (161 missing) defines intent resolution -- appears to be pure logic, should be highly testable once you understand the state machine.
- `ingress_router.py` (161 missing) routes incoming messages to tasks -- depends on controller and store.
- **Hard-to-test pattern:** `controller.py` has deep coupling to `KernelStore` methods. However, since KernelStore works with in-memory SQLite, this is manageable.
- No globals or singletons.

**Reusable fixtures:** `kernel_store`, `_kernel_runtime()`
**New fixtures needed:** `task_controller_with_dag` (controller + store + pre-created DAG steps), `ingress_router_fixture`

---

### Zone 7: Kernel Ledger + Verification + Analytics + Artifacts + Authority

**Scope:** `src/hermit/kernel/ledger/` + `verification/` + `analytics/` + `artifacts/` + `authority/` + `errors.py`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `ledger/journal/store_tasks.py` | 74% | 103 | 83 |
| `ledger/events/store_ledger.py` | 79% | 83 | 63 |
| `ledger/journal/store_scheduler.py` | 26% | 26 | 22 |
| `verification/rollbacks/rollbacks.py` | 15% | 90 | 84 |
| `verification/proofs/dag_proof.py` | 0% | 41 | 39 |
| `verification/proofs/merkle.py` | 0% | 33 | 31 |
| `analytics/task_metrics.py` | 31% | 47 | 43 |
| `authority/identity/service.py` | 0% | 14 | 13 |
| `authority/workspaces/service.py` | 56% | 14 | 11 |
| All other files | 87-100% | ~9 | ~9 |

**Estimated new tests:** ~75 test functions
**Effort to 95%:** ~398 lines to cover
**Test directory:** `tests/unit/kernel/`

**Existing test files:**
- `tests/unit/kernel/test_kernel_store_tasks_support.py`
- `tests/unit/kernel/test_event_chain_concurrency.py`
- `tests/unit/kernel/test_execution_analytics.py`
- `tests/unit/kernel/test_health_monitor.py`
- `tests/unit/kernel/test_governance_report.py`
- `tests/unit/kernel/test_proof_anchoring.py`
- `tests/unit/kernel/test_proof_chain_completeness.py`
- `tests/unit/kernel/test_proof_formatter.py`
- `tests/unit/kernel/test_anchor_methods_coverage.py`
- `tests/unit/kernel/test_recursive_rollback.py`
- `tests/unit/kernel/test_dependency_tracker_coverage.py`
- `tests/unit/kernel/test_trust_scoring.py`
- `tests/unit/kernel/test_trust_scoring_coverage.py`

**New test files needed:**
- `tests/unit/kernel/ledger/test_store_tasks.py`
- `tests/unit/kernel/ledger/test_store_ledger.py`
- `tests/unit/kernel/ledger/test_store_scheduler.py`
- `tests/unit/kernel/verification/test_rollbacks_service.py`
- `tests/unit/kernel/verification/test_dag_proof.py`
- `tests/unit/kernel/verification/test_merkle.py`
- `tests/unit/kernel/analytics/test_task_metrics.py`
- `tests/unit/kernel/authority/test_identity_service.py`
- `tests/unit/kernel/authority/test_workspace_service.py`

**Key mocking dependencies:**
- `KernelStore` (in-memory -- most ledger tests just exercise SQLite directly)
- `ArtifactStore` (for verification)
- `ReceiptService` (for rollbacks)
- `CapabilityGrantService` (for rollbacks and proofs)
- `subprocess.run` (rollbacks may shell out for git reset)

**Testability assessment:**
- Ledger/store modules: Excellent testability. The in-memory KernelStore fixture makes SQL-backed tests fast.
- `rollbacks.py` (90 missing): Executes rollback operations (file restore, git reset). Needs filesystem and subprocess mocking.
- `merkle.py` and `dag_proof.py`: Pure computation, highly testable.
- `task_metrics.py`: Reads from store, computes timing data. Straightforward.
- No globals or singletons.

**Reusable fixtures:** `kernel_store`
**New fixtures needed:** `populated_store` (store with pre-inserted tasks, steps, receipts for projection/rollback testing)

---

### Zone 8: Plugins -- Hooks, Tools, MCP, Subagents, Bundles

**Scope:** `src/hermit/plugins/builtin/hooks/` + `tools/` + `mcp/` + `subagents/` + `bundles/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `hooks/scheduler/engine.py` | 15% | 191 | 180 |
| `hooks/webhook/tools.py` | 0% | 121 | 115 |
| `hooks/scheduler/tools.py` | 11% | 115 | 108 |
| `hooks/webhook/server.py` | 40% | 144 | 130 |
| `hooks/image_memory/hooks.py` | 56% | 76 | 68 |
| `hooks/memory/services.py` | 0% | 65 | 62 |
| `hooks/scheduler/models.py` | 57% | 23 | 20 |
| `hooks/image_memory/engine.py` | 78% | 26 | 20 |
| `hooks/webhook/models.py` | 48% | 19 | 16 |
| `mcp/mcp_loader/mcp.py` | 0% | 67 | 64 |
| `mcp/github/mcp.py` | 0% | 29 | 28 |
| `subagents/orchestrator/dag_orchestrator.py` | 0% | 27 | 26 |
| `tools/web_tools/tools.py` | 0% | 8 | 8 |
| `tools/grok/tools.py` | 0% | 6 | 6 |
| `tools/computer_use/tools.py` | 0% | 9 | 9 |
| All other files | 79-100% | ~87 | ~38 |

**Estimated new tests:** ~100 test functions
**Effort to 95%:** ~948 lines to cover
**Test directory:** `tests/unit/plugins/`

**Existing test files:**
- `tests/unit/plugins/hooks/test_scheduler_hooks.py`
- `tests/unit/plugins/hooks/test_webhook_hooks.py`
- `tests/unit/plugins/hooks/test_webhook_hooks_coverage.py`
- `tests/unit/plugins/hooks/test_webhook_policy_profile.py`
- `tests/unit/plugins/hooks/test_a2a_endpoint.py`
- `tests/unit/plugins/hooks/test_a2a_hooks_coverage.py`
- `tests/unit/plugins/hooks/test_patrol_*.py` (5 files)
- `tests/unit/plugins/hooks/test_overnight_*.py` (3 files)
- `tests/unit/plugins/hooks/test_trigger_*.py` (5 files)
- `tests/unit/plugins/memory/test_*.py` (4 files)
- `tests/unit/plugins/tools/test_*.py` (4 files)
- `tests/unit/plugins/mcp/test_hermit_mcp_server.py`
- `tests/unit/plugins/subagents/test_orchestrator.py`
- `tests/unit/plugins/bundles/test_*.py` (3 files)

**New test files needed:**
- `tests/unit/plugins/hooks/test_scheduler_engine.py`
- `tests/unit/plugins/hooks/test_scheduler_tools.py`
- `tests/unit/plugins/hooks/test_scheduler_models.py`
- `tests/unit/plugins/hooks/test_webhook_tools.py`
- `tests/unit/plugins/hooks/test_webhook_server.py`
- `tests/unit/plugins/hooks/test_webhook_models.py`
- `tests/unit/plugins/hooks/test_image_memory_hooks.py`
- `tests/unit/plugins/hooks/test_image_memory_engine.py`
- `tests/unit/plugins/memory/test_memory_services.py`
- `tests/unit/plugins/mcp/test_github_mcp.py`
- `tests/unit/plugins/mcp/test_mcp_loader.py`
- `tests/unit/plugins/subagents/test_dag_orchestrator.py`
- `tests/unit/plugins/subagents/test_subagents_register.py`
- `tests/unit/plugins/tools/test_tool_registrations.py`

**Key mocking dependencies:**
- `PluginContext` (from runtime/capability/contracts/base)
- `HooksEngine`
- `KernelStore`
- `aiohttp` / `httpx` (webhook server, scheduler engine)
- `croniter` (scheduler)
- `asyncio` event loops (scheduler engine is async)
- Feishu/Slack/Telegram SDKs (not needed here -- those are in Zone 9)

**Testability assessment:**
- **Hard-to-test:** `hooks/scheduler/engine.py` runs an async event loop with `croniter`-based scheduling. Needs careful async test setup and time mocking.
- **Hard-to-test:** `hooks/webhook/server.py` starts an `aiohttp` server. Tests need to mock the server startup or use `aiohttp.test_utils`.
- **Hard-to-test:** `hooks/memory/services.py` uses globals (`_cached_services`, `_schemas_initialized`) for lazy initialization. Must reset globals between tests.
- MCP plugins (`github/mcp.py`, `mcp_loader/mcp.py`) depend on external MCP protocol -- need protocol mocking.
- Tool registration files (`tools.py` in each tool plugin) are thin wrappers -- easy to test.
- Many hook files use `global` variables for lazy engine initialization -- need to reset these in test teardown.

**Reusable fixtures:** `kernel_store`, fixtures from `tests/unit/plugins/` conftest files
**New fixtures needed:** `mock_plugin_context`, `mock_scheduler_store`, `async_webhook_client`

---

### Zone 9: Plugins -- Adapters (Feishu, Slack, Telegram)

**Scope:** `src/hermit/plugins/builtin/adapters/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `feishu/adapter.py` | 9% | 1,050 | 991 |
| `feishu/normalize.py` | 13% | 91 | 82 |
| `feishu/tools.py` | 78% | 74 | 59 |
| `feishu/_client.py` | 100% | 0 | 0 |
| `feishu/hooks.py` | 81% | 12 | 6 |
| `feishu/reaction.py` | 92% | 1 | 0 |
| `feishu/reply.py` | 91% | 2 | 0 |
| `slack/adapter.py` | 96% | 3 | 0 |
| `slack/hooks.py` | 98% | 1 | 0 |
| `slack/normalize.py` | 100% | 0 | 0 |
| `slack/reply.py` | 98% | 1 | 0 |
| `telegram/adapter.py` | 95% | 3 | 0 |
| `telegram/hooks.py` | 98% | 1 | 0 |
| `telegram/normalize.py` | 100% | 0 | 0 |
| `telegram/reply.py` | 99% | 1 | 0 |

**Estimated new tests:** ~100 test functions
**Effort to 95%:** ~1,141 lines to cover (dominated by feishu/adapter.py)
**Test directory:** `tests/unit/plugins/feishu/`, `tests/unit/plugins/slack/`, `tests/unit/plugins/telegram/`

**Existing test files:**
- `tests/unit/plugins/feishu/test_feishu_card_builders.py`
- `tests/unit/plugins/feishu/test_feishu_message_tools.py`
- `tests/unit/plugins/feishu/test_feishu_reactions.py`
- `tests/unit/plugins/feishu/test_observation_and_feishu_client.py`
- `tests/unit/plugins/slack/test_slack_adapter.py`
- `tests/unit/plugins/slack/test_slack_hooks.py`
- `tests/unit/plugins/slack/test_slack_normalize.py`
- `tests/unit/plugins/slack/test_slack_reply.py`
- `tests/unit/plugins/telegram/test_telegram_adapter.py`
- `tests/unit/plugins/telegram/test_telegram_hooks.py`
- `tests/unit/plugins/telegram/test_telegram_normalize.py`
- `tests/unit/plugins/telegram/test_telegram_reply.py`
- (Plus 5 integration tests under `tests/integration/plugins/feishu/`)

**New test files needed:**
- `tests/unit/plugins/feishu/test_feishu_adapter.py` (the big one)
- `tests/unit/plugins/feishu/test_feishu_normalize.py`
- `tests/unit/plugins/feishu/test_feishu_tools_extended.py`

**Key mocking dependencies:**
- `lark_oapi` SDK (Feishu/Lark SDK, heavily used in adapter.py)
- `websocket` connections (Feishu WS adapter)
- `httpx.AsyncClient` (Feishu HTTP calls)
- `AgentRunner` / `AgentRuntime` (adapter dispatches messages to runner)
- Global variables: `_active_adapter`, `_feishu_ws_shutdown`, `_lark_receive_loop_patched`, `_lark_connect_patched`, `_lark_runtime_patched`

**Testability assessment:**
- **Hardest zone.** `feishu/adapter.py` (1,050 missing lines, 9% covered) is the single worst file. It:
  - Monkey-patches the Lark SDK at module load time (3 global patches)
  - Uses 5 global variables for state management
  - Manages WebSocket connections, reconnection logic, and graceful shutdown
  - Implements message deduplication with TTL caches
  - Has deeply nested async control flow
- Strategy: Mock the Lark SDK entirely, provide a `FakeLarkClient` that simulates message receipt. Test each logical section (connection, message dispatch, shutdown) independently.
- Slack and Telegram adapters are already at 95%+ -- minimal work needed.
- `feishu/normalize.py` (13%) parses Lark message formats -- more testable once you understand the SDK types.

**Reusable fixtures:** `feishu_dispatcher_support.py` (from `tests/fixtures/`)
**New fixtures needed:** `mock_lark_sdk`, `fake_ws_connection`, `feishu_adapter_env` (monkeypatch all globals + env vars)

---

### Zone 10: Runtime + Surfaces (CLI + TUI)

**Scope:** `src/hermit/runtime/` + `src/hermit/surfaces/`

| File | Coverage | Missing | To 95% |
|------|-------:|-------:|------:|
| `runtime/control/runner/runner.py` | 22% | 206 | 193 |
| `runtime/control/runner/control_actions.py` | 0% | 210 | 200 |
| `runtime/control/runner/task_executor.py` | 0% | 182 | 173 |
| `runtime/provider_host/execution/runtime.py` | 30% | 236 | 219 |
| `runtime/control/runner/async_dispatcher.py` | 0% | 111 | 106 |
| `runtime/provider_host/execution/sandbox.py` | 61% | 111 | 94 |
| `runtime/provider_host/execution/services.py` | 39% | 81 | 67 |
| `runtime/capability/resolver/mcp_client.py` | 16% | 131 | 123 |
| `surfaces/cli/_commands_task.py` | 25% | 141 | 131 |
| `surfaces/cli/_preflight.py` | 11% | 131 | 124 |
| `surfaces/cli/_commands_core.py` | 18% | 129 | 121 |
| `surfaces/cli/_serve.py` | 37% | 125 | 115 |
| `surfaces/cli/_commands_memory.py` | 21% | 107 | 100 |
| `surfaces/cli/_helpers.py` | 19% | 89 | 83 |
| `runtime/capability/loader/loader.py` | 44% | 53 | 44 |
| `runtime/assembly/config.py` | 82% | 54 | 36 |
| `runtime/capability/loader/config.py` | 30% | 37 | 33 |
| `runtime/control/runner/message_compiler.py` | 40% | 35 | 29 |
| `surfaces/cli/_commands_schedule.py` | 59% | 35 | 28 |
| `surfaces/cli/_commands_plugin.py` | 48% | 34 | 27 |
| `runtime/control/runner/session_context_builder.py` | 0% | 38 | 36 |
| `runtime/capability/contracts/kernel_services.py` | 0% | 34 | 32 |
| `runtime/provider_host/execution/vision_services.py` | 27% | 33 | 28 |
| `runtime/provider_host/execution/approval_services.py` | 30% | 26 | 22 |
| `runtime/provider_host/execution/progress_services.py` | 34% | 23 | 19 |
| `runtime/control/runner/approval_resolver.py` | 68% | 15 | 11 |
| `runtime/control/runner/utils.py` | 68% | 14 | 10 |
| `runtime/observation/logging/setup.py` | 35% | 9 | 8 |
| surfaces/cli/tui/* (9 files) | 0% | ~400 | ~380 |
| All other files | 84-100% | ~95 | ~39 |

**Estimated new tests:** ~200 test functions
**Effort to 95%:** ~2,632 lines to cover (LARGEST zone -- intentionally kept unified due to high coupling)
**Test directory:** `tests/unit/runtime/`, `tests/unit/surfaces/`

**Existing test files:**
- `tests/unit/runtime/test_approval_resolver.py`
- `tests/unit/runtime/test_claude_provider_caching.py`
- `tests/unit/runtime/test_codex_provider.py`
- `tests/unit/runtime/test_codex_provider_internals.py`
- `tests/unit/runtime/test_config.py`
- `tests/unit/runtime/test_context.py`
- `tests/unit/runtime/test_hooks_engine.py`
- `tests/unit/runtime/test_plugin_manager.py`
- `tests/unit/runtime/test_plugin_manager_governance.py`
- `tests/unit/runtime/test_provider_images.py`
- `tests/unit/runtime/test_provider_input_compiler.py`
- `tests/unit/runtime/test_provider_messages.py`
- `tests/unit/runtime/test_session.py`
- `tests/unit/runtime/test_subagent_identity.py`
- `tests/unit/runtime/test_tools.py`
- `tests/unit/runtime/test_zero_compact.py`
- `tests/unit/runtime/test_cli_error_branches.py`
- `tests/unit/surfaces/test_commands_overnight.py`
- `tests/unit/surfaces/test_serve_loop.py`
- `tests/unit/surfaces/tui/widgets/test_status_bar.py`

**New test files needed:**
- `tests/unit/runtime/test_runner.py`
- `tests/unit/runtime/test_control_actions.py`
- `tests/unit/runtime/test_task_executor.py`
- `tests/unit/runtime/test_async_dispatcher.py`
- `tests/unit/runtime/test_runtime_execution.py`
- `tests/unit/runtime/test_sandbox.py`
- `tests/unit/runtime/test_services.py`
- `tests/unit/runtime/test_mcp_client.py`
- `tests/unit/runtime/test_loader.py`
- `tests/unit/runtime/test_loader_config.py`
- `tests/unit/runtime/test_message_compiler.py`
- `tests/unit/runtime/test_session_context_builder.py`
- `tests/unit/runtime/test_kernel_services.py`
- `tests/unit/runtime/test_vision_services.py`
- `tests/unit/runtime/test_approval_services.py`
- `tests/unit/runtime/test_progress_services.py`
- `tests/unit/runtime/test_logging_setup.py`
- `tests/unit/surfaces/test_commands_core.py`
- `tests/unit/surfaces/test_commands_memory.py`
- `tests/unit/surfaces/test_commands_task.py`
- `tests/unit/surfaces/test_commands_plugin.py`
- `tests/unit/surfaces/test_commands_schedule.py`
- `tests/unit/surfaces/test_helpers.py`
- `tests/unit/surfaces/test_preflight.py`
- `tests/unit/surfaces/test_serve.py`
- `tests/unit/surfaces/tui/test_app.py`
- `tests/unit/surfaces/tui/test_bridge.py`
- `tests/unit/surfaces/tui/widgets/test_chat_message.py`
- `tests/unit/surfaces/tui/widgets/test_input_area.py`
- `tests/unit/surfaces/tui/widgets/test_approval_banner.py`
- `tests/unit/surfaces/tui/widgets/test_tool_display.py`

**Key mocking dependencies:**
- `anthropic` SDK (Claude provider)
- `AgentRuntime` (runtime.py -- the core execution loop)
- `PluginManager` (loader, capability system)
- `TaskController`, `ToolExecutor`, `KernelStore` (runner orchestration)
- `typer.testing.CliRunner` (CLI surface testing)
- `Textual` test harness (TUI widget testing)
- `asyncio` (async_dispatcher, mcp_client)
- `subprocess` (mcp_client spawns MCP servers)
- `get_settings` (lru_cache -- must `cache_clear()` in setup)

**Testability assessment:**
- **Hardest area overall.** `runner.py` (282 stmts, 22%) is the central orchestrator connecting sessions, plugins, providers, and the kernel. Deep coupling to everything.
- **Hard-to-test:** `control_actions.py` (210 stmts, 0%) implements slash commands and user actions within the runner context. Requires full runner mock setup.
- **Hard-to-test:** `task_executor.py` (182 stmts, 0%) bridges runtime to kernel task execution. Depends on runner, kernel store, executor.
- **Hard-to-test:** `runtime.py` (361 stmts, 30%) is `AgentRuntime` -- the LLM interaction loop. Needs provider mocking.
- **Hard-to-test:** `mcp_client.py` (167 stmts, 16%) spawns subprocess MCP servers and communicates over stdio. Needs process mocking.
- `get_settings` uses `@lru_cache` -- must clear cache between tests (already handled by conftest fixtures).
- CLI commands can be tested via `CliRunner` (Typer's test utility) -- well-established pattern.
- TUI widgets use Textual framework -- testable via `textual.pilot` but requires Textual test infrastructure.

**Reusable fixtures:** `_clean_hermit_env` (surfaces conftest), `kernel_store`, `FakeProvider` / `_AsyncAgent` / `_RunnerPluginManager` / `_RunnerSessionManager` (from `task_kernel_support.py`)
**New fixtures needed:** `cli_runner` (Typer CliRunner with env setup), `mock_agent_runtime`, `mock_runner` (AgentRunner with all dependencies mocked), `textual_pilot` (for TUI testing), `mock_mcp_process`

---

## 4. Zone Effort Summary and Priority

| Zone | Files | To 95% Lines | Priority | Parallelism Risk |
|------|------:|-----------:|----------|-----------------|
| Z02 Context | 25 | ~70 | Quick Win | None |
| Z05 Policy | 23 | ~219 | Medium | None |
| Z03 Exec/Executor | 20 | ~243 | Medium | Low (shares kernel fixtures with Z04) |
| Z04 Exec/Coord | 21 | ~308 | Medium | Low (shares kernel fixtures with Z03) |
| Z07 Ledger+Verify | 36 | ~398 | Medium | None |
| Z01 Infra+Apps | 9 | ~440 | Medium | None |
| Z06 Task+Signals | 22 | ~801 | High | None |
| Z08 Plugin Hooks | 53 | ~948 | High | None |
| Z09 Adapters | 15 | ~1,141 | High (Feishu-heavy) | None |
| Z10 Runtime+Surfaces | 50 | ~2,632 | Highest | None |

### Cross-Zone Fixture Dependencies

All zones depend on `kernel_store` (global conftest) and `tmp_path` (pytest builtin). These are already safe for parallel use.

| Fixture Source | Used By Zones |
|---------------|--------------|
| `tests/conftest.py::kernel_store` | Z02-Z08 |
| `tests/fixtures/task_kernel_support.py` | Z03, Z04, Z06, Z10 |
| `tests/fixtures/feishu_dispatcher_support.py` | Z09 only |
| `tests/e2e/conftest.py::e2e_runtime` | None (e2e only) |
| `tests/unit/apps/conftest.py` | Z01 only |
| `tests/integration/surfaces/conftest.py` | None (integration only) |

**Conclusion:** All 10 zones can be worked on in parallel without conftest conflicts. Each zone writes tests in its own subdirectory under `tests/unit/`. The shared global conftest provides `kernel_store` safely via per-test in-memory SQLite.

---

## 5. Recommended Execution Order

For maximum parallelism, all 10 zones can start simultaneously. If resources are limited, prioritize by ROI:

1. **Phase 1 (quick wins):** Z02 (Context, ~70 lines), Z01 (Infra+Apps, ~440 lines)
2. **Phase 2 (kernel core):** Z05 (Policy), Z03 (Executor), Z04 (Coordination), Z07 (Ledger+Verify) -- all in parallel
3. **Phase 3 (heavy lift):** Z06 (Task), Z08 (Plugin Hooks), Z09 (Adapters) -- in parallel
4. **Phase 4 (integration layer):** Z10 (Runtime+Surfaces) -- largest but benefits from kernel zones being done first

Each zone produces tests that are fully independent, so merging is conflict-free as long as each zone stays within its designated `tests/unit/<area>/` directory.
