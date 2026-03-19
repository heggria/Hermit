# Kernel Internals Research Report

## Executive Summary

This report provides a deep analysis of Hermit's kernel task/step/execution/dispatch internals, evaluating the current implementation against four target dimensions for Task OS evolution: topology-adaptive, verification-driven, approval-parkable, and workspace-aware.

The kernel already has a substantial foundation: a DAG execution system with join strategies, a governed execution pipeline (approval -> capability grant -> execution -> receipt -> reconciliation), a competition/delegation system with worktree isolation, and an auto-park mechanism for approval-blocked tasks. However, significant gaps remain in runtime DAG topology mutation, end-to-end verification chain completeness, nested approval orchestration, and workspace lifecycle management.

**Key finding:** The kernel is ~60% ready for Task OS by component count, but the remaining 40% covers the hardest problems -- runtime topology adaptation, verification-driven scheduling, and workspace-aware resource management require new architectural primitives rather than incremental extensions.

---

## 1. Task Model & State Machine

### Current Capabilities

**TaskRecord** (`src/hermit/kernel/task/models/records.py:8-29`):
- Fields: `task_id`, `conversation_id`, `title`, `goal`, `status`, `priority`, `owner_principal_id`, `policy_profile`, `source_channel`, `parent_task_id`, `task_contract_ref`, `created_at`, `updated_at`, `requested_by_principal_id`, `child_result_refs`
- Parent-child hierarchy via `parent_task_id`
- Child result aggregation via `child_result_refs`
- Policy profile binding per task

**Task States** (derived from code usage across controller.py, outcomes.py, dispatch.py):
- `queued` -- task created, waiting for dispatch
- `running` -- actively executing
- `blocked` -- suspended (awaiting approval, plan confirmation, recovery)
- `paused` -- operator-initiated pause
- `completed` -- successful terminal
- `failed` -- failed terminal
- `cancelled` -- cancelled terminal
- `recalled` -- delegation recalled by parent
- `reconciling` -- post-execution reconciliation phase (observed in dispatch inflight statuses)

**Terminal statuses** (`outcomes.py:8`): `{"completed", "failed", "cancelled"}`

**State transitions** (from `TaskController`):
- `start_task()`: creates task in `running` status
- `enqueue_task()`: creates task in `queued` status
- `start_dag_task()`: creates task, then sets to `queued`
- `mark_blocked()`: sets task to `blocked`
- `mark_suspended()`: sets task to `blocked` with a `waiting_kind`
- `mark_planning_ready()`: sets task to `blocked` with `planning_mode=True`
- `pause_task()`: sets task to `paused`
- `cancel_task()`: sets task to `cancelled`
- `finalize_result()`: sets task to `completed`, `failed`, or `running` (if non-terminal steps remain)
- `enqueue_resume()`: sets task to `queued` (from blocked)

**TaskController** (`src/hermit/kernel/task/services/controller.py`):
- Manages full task lifecycle: creation, ingress routing, step management, finalization
- Ingress decision system with `IngressRouter` for NLP-based task binding
- Continuation guidance for determining whether new input continues an existing task or starts a new one
- CAS guard in `finalize_result()` via `try_finalize_step_attempt()` preventing double-finalization
- DAG activation on step completion: `activate_waiting_dependents()` + `StepDataFlowService` for input binding injection
- Focus management: tracks which task has user attention per conversation

**Projections** (`src/hermit/kernel/task/projections/projections.py`):
- `ProjectionService` with incremental rebuild support (schema version `tail-v8`)
- Assembles: task, proof, claims, topic, outcome, beliefs, knowledge, planning state, tool history, rollbacks, contract loop
- Cache validation against event head hash

### Gaps for Task OS

1. **No formal state machine definition**: Transitions are implicit in controller methods, not declared as a state machine. No validation that a transition is legal before applying it.
2. **No task taxonomy/type system**: Tasks are undifferentiated -- research, code, review, test all share the same TaskRecord. A Task OS needs typed tasks with type-specific lifecycle rules.
3. **No task SLA/deadline model**: No `deadline_at`, `estimated_duration`, or SLA fields. Cannot schedule based on urgency.
4. **No task dependency at task level**: Parent-child is hierarchical, not a DAG of tasks. Cross-task dependencies require the step-level DAG.
5. **No task versioning/snapshots**: Cannot checkpoint a task's state for rollback to a specific point.

### Key Files

- `src/hermit/kernel/task/models/records.py` -- TaskRecord, StepRecord, StepAttemptRecord, and all other record types
- `src/hermit/kernel/task/services/controller.py` -- TaskController (1663 lines, the largest file)
- `src/hermit/kernel/task/state/outcomes.py` -- Terminal status handling
- `src/hermit/kernel/task/state/continuation.py` -- Continuation guidance
- `src/hermit/kernel/task/state/control_intents.py` -- NLP control intent parsing
- `src/hermit/kernel/task/projections/projections.py` -- ProjectionService

---

## 2. Step & StepAttempt Model

### Current Capabilities

**StepRecord** (`records.py:37-55`):
- Fields: `step_id`, `task_id`, `kind`, `status`, `attempt`, `node_key`, `input_ref`, `output_ref`, `title`, `contract_ref`, `depends_on` (list of step_ids), `join_strategy`, `input_bindings`, `max_attempts`, timestamps
- DAG dependency via `depends_on` list of step_ids
- Join strategy per step: `all_required`, `any_sufficient`, `majority`, `best_effort`
- Input bindings for cross-step data flow (symbolic references like `"producer.output_ref"`)
- Retry support via `max_attempts`

**StepAttemptRecord** (`records.py:59-103`):
- 37 fields covering the full governed execution lifecycle
- Key fields: `step_attempt_id`, `task_id`, `step_id`, `attempt`, `status`, `context` (dict), `queue_priority`, `waiting_reason`, `approval_id`, `decision_id`, `capability_grant_id`, `workspace_lease_id`
- Artifact references: `state_witness_ref`, `context_pack_ref`, `working_state_ref`, `environment_ref`, `action_request_ref`, `policy_result_ref`, `approval_packet_ref`, `execution_contract_ref`, `evidence_case_ref`, `authorization_plan_ref`, `reconciliation_ref`, `pending_execution_ref`
- Reentry support: `reentry_boundary`, `reentry_reason`, `resume_from_ref`
- Supersession: `superseded_by_step_attempt_id`

**Step statuses** (derived from code):
- `ready` -- available for dispatch
- `running` -- actively executing
- `waiting` -- waiting for DAG dependencies
- `blocked` -- suspended (awaiting approval, recovery)
- `succeeded` / `completed` / `skipped` -- successful terminal
- `failed` -- failed terminal
- `superseded` -- replaced by a successor attempt

**StepAttempt statuses** (from code references):
- `ready` -- queued for dispatch
- `running` -- claimed by a worker
- `awaiting_approval` -- blocked on approval
- `awaiting_plan_confirmation` -- blocked on plan confirmation
- `observing` -- in observation phase
- `policy_pending` -- waiting for policy evaluation
- `dispatching` -- being dispatched
- `reconciling` -- post-execution reconciliation
- `contracting` -- execution contract phase
- `preflighting` -- pre-execution checks
- `succeeded` / `completed` / `skipped` / `failed` -- terminal
- `superseded` -- replaced by successor

**Successor/Superseded logic** (`controller.py:431-533`):
- When `input_dirty` flag is set during `awaiting_approval`, creates a successor attempt with `attempt + 1`
- Original attempt is marked `superseded` with `superseded_by_step_attempt_id` pointing to successor
- Successor gets `reentry_boundary: "policy_recompile"` and `reentry_reason: "input_dirty"`
- The `resume_from_ref` field allows an attempt to continue from a saved state snapshot

**Attempt phases** (tracked in `context["phase"]`):
- `planning` -- initial phase
- `authorized_pre_exec` -- after approval, before execution
- `awaiting_approval` -- blocked on approval
- `observing` -- observation phase

### Gaps for Task OS

1. **No WaitingKind enum**: Waiting reasons are string literals scattered across the codebase, not a formal enum. Values include: `"awaiting_approval"`, `"awaiting_plan_confirmation"`, `"worker_interrupted_recovery_required"`, `"worker_interrupted_requeued"`, `"input_changed_reenter_policy"`, `"duplicate_recovered_superseded"`, `"reentry_resumed"`.
2. **Attempt context is a loose dict**: The `context` field is `dict[str, Any]` with no schema. Phase tracking, dirty flags, workspace roots, ingress metadata all live in this untyped bag.
3. **No step-level resource requirements**: Steps don't declare what resources they need (workspace, tools, model, tokens). Resource allocation is implicit.
4. **No step priority/ordering hints**: Beyond DAG dependencies, there's no way to express scheduling preferences.

### Key Files

- `src/hermit/kernel/task/models/records.py` -- StepRecord, StepAttemptRecord
- `src/hermit/kernel/task/services/controller.py` -- Step lifecycle in TaskController
- `src/hermit/kernel/execution/executor/executor.py` -- ToolExecutor (not fully analyzed but referenced)

---

## 3. DAG Execution

### Current Capabilities

**StepDAGBuilder** (`src/hermit/kernel/task/services/dag_builder.py`):
- `StepNode` dataclass: `key`, `kind`, `title`, `depends_on`, `join_strategy`, `input_bindings`, `max_attempts`, `metadata`
- `DAGDefinition` dataclass: `nodes` (dict), `roots`, `leaves`, `topological_order`
- Validation: duplicate key detection, dangling dependency detection, cycle detection (Kahn's algorithm)
- Disconnected subgraphs explicitly allowed (independent parallel steps)
- Materialization: root nodes get `status="ready"`, dependent nodes get `status="waiting"`
- Per-step context injection with `entry_prompt`, `dag_node_key`, `dag_node_kind`, `dag_node_metadata`

**DAGExecutionService** (`src/hermit/kernel/task/services/dag_execution.py`):
- Stateless service, all state in KernelStore
- `advance()`: handles success (activate dependents) and failure (retry or cascade)
- `compute_task_status()`: determines aggregate task status from step states
- Success path: `activate_waiting_dependents()` + `StepDataFlowService.resolve_inputs()` + `inject_resolved_inputs()`
- Failure path: `retry_step()` if under `max_attempts`, else `propagate_step_failure()`

**JoinBarrierService** (`src/hermit/kernel/execution/coordination/join_barrier.py`):
- Four join strategies as `JoinStrategy` StrEnum:
  - `ALL_REQUIRED` -- all deps must succeed
  - `ANY_SUFFICIENT` -- at least one dep succeeds
  - `MAJORITY` -- more than half succeed
  - `BEST_EFFORT` -- all deps must be terminal (success or failure)
- `evaluate()`: checks if a step's dependencies satisfy its join strategy
- `check_failure_cascade()`: delegates to `store.propagate_step_failure()`

**StepDataFlowService** (`src/hermit/kernel/execution/coordination/data_flow.py`):
- Resolves symbolic input bindings (`"step_key.output_ref"`) to actual artifact references
- Three-level resolution: explicit key->step_id mapping, then node_key lookup, then literal step_id
- Injects resolved inputs into step attempt context

### Gaps for Task OS

1. **Static DAG topology**: Once materialized, the DAG cannot be modified. No API to add steps, remove steps, or rewire dependencies during execution. This is the single largest gap for topology-adaptive execution.
2. **No conditional branching**: No if/else or switch nodes. All paths are always executed (unless failed). A Task OS needs conditional execution based on intermediate results.
3. **No loop/iteration**: No support for repeating steps until a condition is met.
4. **No sub-DAG composition**: Cannot nest a DAG inside a step. Delegation creates separate tasks, not nested DAGs.
5. **No DAG visualization/introspection API**: The DAG structure is implicit in step `depends_on` fields, not queryable as a graph.
6. **Join barrier evaluation is per-step**: No global DAG-level barrier (e.g., "pause entire DAG if any step needs approval").

### Key Files

- `src/hermit/kernel/task/services/dag_builder.py` -- StepNode, DAGDefinition, StepDAGBuilder
- `src/hermit/kernel/task/services/dag_execution.py` -- DAGExecutionService
- `src/hermit/kernel/execution/coordination/join_barrier.py` -- JoinBarrierService, JoinStrategy
- `src/hermit/kernel/execution/coordination/data_flow.py` -- StepDataFlowService

---

## 4. Dispatch System

### Current Capabilities

**KernelDispatchService** (`src/hermit/kernel/execution/coordination/dispatch.py`):
- In-process worker pool using `ThreadPoolExecutor` (default 4 workers)
- Polling loop with 0.5s interval, woken by `wake()` signal
- `claim_next_ready_step_attempt()`: atomic claim from store (prevents double-dispatch)
- Capacity check: `len(futures) < worker_count`
- Future reaping: tracks `{Future -> step_attempt_id}`, handles completion and exceptions
- Force-fail on worker exception: marks attempt as failed, propagates DAG failure, prevents hanging

**Recovery** (`_recover_interrupted_attempts()`):
- Three-phase recovery on startup:
  1. Recover all in-flight attempts (statuses: `running`, `dispatching`, `reconciling`, `observing`, `contracting`, `preflighting`)
  2. Deduplicate ready attempts per step (keep highest attempt number)
  3. Repair ready attempts whose parent task has stale status
- Smart recovery logic: if attempt has `capability_grant_id`, blocks for manual review (action may have executed); otherwise, re-queues
- Sync-path orphan handling: marks non-async interrupted attempts as failed

**AutoParkService** (`src/hermit/kernel/execution/coordination/auto_park.py`):
- Automatic focus switching when a task is parked (blocked for approval)
- `on_task_parked()`: finds best alternative task via `TaskPrioritizer`
- `on_task_unparked()`: re-evaluates focus when approval is granted

**TaskPrioritizer** (`src/hermit/kernel/execution/coordination/prioritizer.py`):
- Scoring: `raw_score` (queue_priority) - `risk_penalty` (policy_profile) + `age_bonus` (hours since creation, capped at 10) + `blocked_bonus` (previously blocked = +10)
- `best_candidate_after_park()`: finds highest-scoring active task excluding parked one
- `recalculate_priorities()`: re-scores all active tasks

### Gaps for Task OS

1. **No distributed dispatch**: Single-process `ThreadPoolExecutor` only. No remote workers, no distributed queue, no cross-machine scheduling.
2. **No resource-aware scheduling**: Dispatch doesn't consider resource requirements (memory, GPU, workspace availability). Steps are dispatched purely by queue order.
3. **No priority preemption**: A high-priority task cannot preempt a running lower-priority worker. Only queuing priority is respected.
4. **No backpressure**: If all workers are busy, new ready steps wait without any feedback mechanism.
5. **No dispatch rate limiting**: No per-task or per-conversation rate limiting. A burst of ready steps can saturate the pool.
6. **No observability**: No metrics/counters for dispatch throughput, queue depth, worker utilization. Only structlog events.

### Key Files

- `src/hermit/kernel/execution/coordination/dispatch.py` -- KernelDispatchService
- `src/hermit/kernel/execution/coordination/auto_park.py` -- AutoParkService
- `src/hermit/kernel/execution/coordination/prioritizer.py` -- TaskPrioritizer

---

## 5. Delegation & Competition

### Current Capabilities

**TaskDelegationService** (`src/hermit/kernel/task/services/delegation.py`):
- `delegate()`: creates a child task under a parent, with `DelegationScope` constraints
- `recall()`: revokes delegation, sets child task to `recalled`
- `child_completed()`: notifies parent when child finishes
- `list_children()`: lists delegated child tasks with their status
- In-memory delegation storage (`self._delegations` dict)

**DelegationRecord** (`src/hermit/kernel/task/models/delegation.py`):
- Fields: `delegation_id`, `parent_task_id`, `child_task_id`, `delegated_principal_id`, `scope`, `status`, `delegation_grant_ref`, `recall_reason`, `authority_budget_remaining`, `attenuation_factor`
- Authority budget tracking with attenuation factor

**DelegationScope**:
- `allowed_action_classes` -- what actions the child can take
- `allowed_resource_scopes` -- what resources the child can access
- `max_steps` -- step count limit
- `budget_tokens` -- token budget limit
- `budget_remaining` -- remaining budget

**DelegationStoreMixin** (`src/hermit/kernel/task/services/delegation_store.py`):
- SQLite-backed persistence for delegation records
- CRUD operations: `create_delegation()`, `get_delegation_record()`, `find_delegation_by_pair()`, `find_delegation_by_child()`, `list_delegations_for_parent()`, `update_delegation_status()`

**CompetitionService** (`src/hermit/kernel/execution/competition/service.py`):
- Full lifecycle: `create_competition()` -> `spawn_candidates()` -> `on_candidate_task_completed()` -> `trigger_evaluation()` -> `select_winner()` -> `promote_winner()`
- Competition states: `draft` -> `spawning` -> `running` -> `evaluating` -> `decided` (or `cancelled`)
- Candidate states: `pending` -> `running` -> `completed`/`failed`/`disqualified`
- `CompetitionEvaluator` for scoring candidates
- Timeout policies: `evaluate_completed` (evaluate when enough candidates finish) or cancel
- Winner promotion: merge winner worktree, clean up all competition worktrees

**CompetitionWorkspaceManager** (`src/hermit/kernel/execution/competition/workspace.py`):
- Git worktree-based workspace isolation per candidate
- `create_workspace()`: creates worktree at `.hermit/competition/<competition_id>/<label>`
- `merge_winner()`: merges winner branch back with `--no-ff`
- `cleanup_all()`: removes all competition worktrees
- `list_orphans()`: detects orphaned worktree directories

### Gaps for Task OS

1. **Dual storage for delegations**: `TaskDelegationService` uses in-memory dict while `DelegationStoreMixin` provides SQLite persistence. These are not integrated -- the service doesn't use the store mixin.
2. **No delegation depth control**: No limit on delegation chains (A delegates to B delegates to C...). Authority attenuation factor exists but isn't enforced.
3. **No delegation monitoring**: Parent has no real-time visibility into child progress beyond completion notification.
4. **Competition is git-worktree-only**: Workspace isolation assumes git. No support for container-based, VM-based, or abstract workspace isolation.
5. **No partial result promotion**: Competition evaluates only completed candidates. No support for extracting partial results from failed candidates.
6. **No fork-join at DAG level**: Delegation creates separate tasks, not step-level forks within a single DAG. Cannot express "fork 3 approaches, evaluate, continue with best" as a DAG pattern.

### Key Files

- `src/hermit/kernel/task/services/delegation.py` -- TaskDelegationService
- `src/hermit/kernel/task/models/delegation.py` -- DelegationRecord, DelegationScope
- `src/hermit/kernel/task/services/delegation_store.py` -- DelegationStoreMixin
- `src/hermit/kernel/execution/competition/service.py` -- CompetitionService
- `src/hermit/kernel/execution/competition/models.py` -- CompetitionRecord, CompetitionCandidateRecord, CandidateScore
- `src/hermit/kernel/execution/competition/workspace.py` -- CompetitionWorkspaceManager
- `src/hermit/kernel/execution/competition/evaluator.py` -- CompetitionEvaluator
- `src/hermit/kernel/execution/competition/store.py` -- Competition store

---

## 6. Gap Analysis Summary

### Topology-Adaptive Readiness

**Current capabilities:**
- Static DAG definition with topological validation and cycle detection
- Four join strategies for flexible dependency resolution
- Data flow between steps via input bindings
- DAG activation on step completion (activate_waiting_dependents)
- Failure cascading and retry support

**Gaps:**
- **CRITICAL: No runtime DAG mutation**. Cannot add, remove, or rewire steps after materialization. This blocks adaptive execution patterns (retry with different approach, add verification step based on results, skip unnecessary steps).
- **CRITICAL: No conditional execution**. All paths are unconditionally executed. Cannot express "if step A produces X, run B; otherwise run C."
- **HIGH: No loop/iteration**. Cannot repeat steps until convergence. The retry mechanism (`max_attempts`) only retries the same step, not a sub-DAG.
- **MEDIUM: No sub-DAG composition**. Steps are flat; cannot nest a DAG inside a step.
- **Priority: P0** -- This is the fundamental architectural gap. Without runtime topology mutation, the kernel cannot adapt to the unpredictable nature of agent task execution.

### Verification-Driven Readiness

**Current capabilities:**
- Full receipt chain: every tool execution produces a `ReceiptRecord` with input/output refs, policy result, approval ref
- Execution contracts: `ExecutionContractRecord` with expected effects, success criteria, drift budget, reversibility class
- Evidence cases: `EvidenceCaseRecord` with support/contradiction refs, sufficiency scoring, freshness window
- Reconciliation: `ReconciliationRecord` comparing intended/authorized/observed/receipted effects
- Authorization plans: `AuthorizationPlanRecord` with approval routes, witness requirements
- Proof bundles: hash-chain verified event log with Merkle root over receipts
- Rollback support: `RollbackRecord` with strategy and status tracking
- Projection verification against event head hash

**Gaps:**
- **HIGH: No signature verification**. Proof coverage shows `signature_coverage` and `inclusion_proof_coverage` as missing features. Receipts have `signature` and `signer_ref` fields but they're not populated.
- **HIGH: No cross-task verification**. Verification is per-task. Cannot verify that a delegation chain maintained authority constraints across the full tree.
- **MEDIUM: Reconciliation is LLM-driven**. The reconciliation service uses LLM to compare intended vs observed effects. No deterministic verification for structured outcomes.
- **MEDIUM: No verification-driven scheduling**. The dispatch system doesn't consider verification state. A step with insufficient evidence should trigger additional verification steps before proceeding.
- **Priority: P1** -- The verification infrastructure is strong but not yet driving execution decisions. The gap is in using verification results to influence the DAG.

### Approval-Parkable Readiness

**Current capabilities:**
- Step-level parking: `mark_blocked()` / `mark_suspended()` sets step and task to `blocked` with a `waiting_kind`
- Auto-park with focus switching: `AutoParkService.on_task_parked()` finds next best task via `TaskPrioritizer`
- Priority-based unparking: `on_task_unparked()` re-evaluates focus when approval granted
- Input-dirty reentry: when new input arrives during `awaiting_approval`, creates a successor attempt with policy recompilation
- Approval records: `ApprovalRecord` with `drift_expiry`, `fallback_contract_refs`, `state_witness_ref`
- Resume mechanism: `enqueue_resume()` and `resume_attempt()` for clean resume after approval

**Gaps:**
- **HIGH: No nested approval orchestration**. If a delegated child task needs approval, the parent has no mechanism to approve on behalf of its operator. Approval is always resolved at the conversation level.
- **HIGH: No approval timeout/escalation**. `expires_at` field exists on `ApprovalRecord` but no background process checks for expired approvals or escalates them.
- **MEDIUM: No approval batching**. Each approval is individual. Cannot batch-approve multiple related actions.
- **MEDIUM: No approval delegation**. An operator cannot delegate approval authority to another principal for a specific task.
- **LOW: Park/unpark is conversation-scoped**. `AutoParkService` only switches focus within a single conversation. Cross-conversation scheduling is not considered.
- **Priority: P2** -- The basic park/resume mechanism works well. The gaps are in multi-level approval orchestration and timeout handling.

### Workspace-Aware Readiness

**Current capabilities:**
- `workspace_root` passed through `TaskExecutionContext` to execution
- `workspace_lease_id` on `StepAttemptRecord` for lease-based workspace tracking
- Competition workspaces: git worktree creation, merging, cleanup via `CompetitionWorkspaceManager`
- Delegation scope includes `allowed_resource_scopes`
- `required_workspace_mode` on `AuthorizationPlanRecord`
- Recovery-aware: workspace state considered during interrupt recovery

**Gaps:**
- **CRITICAL: No workspace lifecycle management**. `workspace_lease_id` field exists but no `WorkspaceLease` model or service. No acquire/release/extend lifecycle.
- **HIGH: No workspace isolation enforcement**. `workspace_root` is passed as a string path. No sandbox enforcement, no capability-gated filesystem access.
- **HIGH: No workspace resource tracking**. No tracking of disk usage, file count, or workspace health.
- **MEDIUM: Git-worktree-only isolation**. Competition workspaces use git worktrees. No container, VM, or abstract workspace provider.
- **MEDIUM: No workspace sharing model**. No mechanism for multiple steps to share a workspace with proper locking.
- **MEDIUM: No workspace snapshots**. Cannot checkpoint and restore workspace state.
- **Priority: P1** -- The fields and references exist but the actual workspace management service is missing. This is scaffolding waiting for implementation.

---

## 7. Recommended Priority Actions

### P0: Topology-Adaptive DAG

1. **Design a DAG mutation API**: Add `add_step()`, `remove_step()`, `rewire_dependency()` to `KernelStore` with event-sourced mutations. Each mutation produces a `dag.topology_changed` event.
2. **Implement conditional step nodes**: Add a `ConditionNode` type that evaluates a predicate on upstream outputs and activates one of several downstream branches.
3. **Implement step skip/cancel within DAG**: Allow a running DAG to skip steps that are no longer needed (e.g., verification passed, no need for retry path).

### P1: Workspace Lifecycle Service

4. **Implement WorkspaceLeaseService**: Model `WorkspaceLease` with acquire/release/extend/expire semantics. Wire `workspace_lease_id` on StepAttemptRecord to actual lease objects.
5. **Abstract workspace provider**: Define `WorkspaceProvider` interface with git-worktree, directory, and (future) container implementations.
6. **Workspace cleanup on task terminal**: Ensure workspaces are released/cleaned when tasks reach terminal state.

### P1: Verification-Driven Scheduling

7. **Verification gate in DAG activation**: Before activating a waiting step, check that upstream verification (reconciliation result) meets a configurable threshold. If not, auto-insert a verification step.
8. **Receipt signing**: Populate `signature` and `signer_ref` on ReceiptRecord using a local signing key. Enable signature verification in proof bundles.

### P2: Approval Orchestration

9. **Approval timeout background service**: Periodically check for expired approvals, auto-deny or escalate based on policy.
10. **Nested approval delegation**: Allow a parent task's operator to pre-authorize approval patterns for delegated child tasks (e.g., "approve all read-only actions automatically").

### P2: Structural Improvements

11. **Formalize the task state machine**: Define `TaskState` and `StepState` enums with explicit transition tables. Validate transitions before applying them.
12. **Type the StepAttempt context**: Replace `context: dict[str, Any]` with a typed `StepAttemptContext` dataclass. This eliminates scattered string key access and makes the phase/dirty/workspace state explicit.
13. **Unify DelegationService storage**: Wire `TaskDelegationService` to use `DelegationStoreMixin` for persistence instead of the in-memory dict.
14. **Extract WaitingKind enum**: Consolidate all waiting reason strings into a `WaitingKind` StrEnum.
