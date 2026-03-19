# Governance Systems Research Report

**Task:** `task_d95a9fee685f` (DAG, 9 steps, all completed)
**Date:** 2026-03-19
**Scope:** approval, policy, verification, rollback, workspace, ledger, context/memory, observation

---

## Executive Summary

Hermit's governance subsystems form a surprisingly complete **governed execution pipeline**: ActionRequest -> PolicyEngine -> AuthorizationPlan -> Approval -> CapabilityGrant -> Execution -> Receipt -> Proof -> Reconciliation -> Rollback. Each stage is backed by durable records in a hash-chained SQLite journal (schema v13, 30+ tables).

The system's strongest areas are:
- **Receipt + proof pipeline**: hash-chained events, receipt bundles, Merkle inclusion proofs, optional HMAC signing
- **Policy engine**: rule-based evaluation with action classification, risk banding, and obligation-driven branching
- **Rollback coverage**: file_restore, git_revert_or_reset, and memory_invalidate strategies with full governed rollback steps

The most significant gaps for Task OS evolution are:
1. **No cross-task resource coordination** -- workspace leases are per-step, no multi-task contention protocol
2. **Memory governance is disconnected from the receipt/proof chain** -- memories are governed by classification rules but not receipted
3. **No budget/quota enforcement in the kernel** -- execution budgets exist only at runtime layer
4. **Observation polling is runtime-coupled** -- ObservationService depends on runner/agent, not kernel-native
5. **Authorization plans lack revalidation execution** -- revalidation_rules are stored but never checked post-approval

---

## 1. Approval System

### Current Capabilities

**Core model**: `ApprovalRecord` created via `ApprovalService.request()` with fields: approval_id, task_id, step_id, step_attempt_id, approval_type, requested_action (dict), request_packet_ref, approval_packet_ref, policy_result_ref, requested_contract_ref, authorization_plan_ref, evidence_case_ref, drift_expiry, fallback_contract_refs, decision_ref, state_witness_ref, expires_at.

**Resolution flow**:
- `approve()` / `deny()` -> `_resolve()` -> `_issue_resolution_receipt()` which creates a full governed chain: DecisionRecord -> CapabilityGrantRecord -> ReceiptRecord, then updates the approval's resolution dict with all three refs
- `approve_mutable_workspace()` grants with mode="mutable_workspace"
- Idempotency: if approval already resolved with a receipt_ref, returns the existing receipt

**Batch approvals**: `request_batch()` creates correlated approvals sharing a batch_id; `approve_batch()` approves all pending approvals for a batch_id. Used for parallel DAG steps.

**Drift detection**: `ApprovalHandler.matching_approval()` checks:
- drift_expiry (time-based expiry)
- Fingerprint matching (current action vs approved action fingerprint)
- On mismatch, transitions to re-approval or escalation

**Async path**: Approval blocks execution by transitioning the step_attempt to "waiting" status. `ObservationService._tick()` polls for "observing" attempts; the MCP supervisor surface resolves approvals externally.

### Gaps

- **No approval delegation/escalation chains** -- a denied approval has no automated escalation path
- **No approval templates** -- each approval is created from scratch; no reusable pre-approved patterns
- **Batch approval is scan-based** -- `approve_batch` does a full table scan with `list_approvals(status="pending", limit=1000)` filtered by batch_id
- **No approval timeout handling** -- `expires_at` is stored but no automatic expiry/cleanup daemon
- **No approval audit log separate from events** -- approval history is in the general event stream

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/policy/approvals/approvals.py` -- ApprovalService
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/approvals/decisions.py` -- DecisionService
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/approvals/approval_copy.py` -- ApprovalCopyService
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/executor/approval_handler.py` -- ApprovalHandler (drift, matching)

---

## 2. Policy & Permits

### Current Capabilities

**PolicyEngine** (`policy/evaluators/engine.py`):
- `evaluate(tool_or_request, payload)` -> `PolicyDecision`
- Pipeline: `build_action_request()` -> `derive_request()` (enrich with observables) -> `evaluate_rules()` -> `merge_outcomes()` -> return PolicyDecision
- Builds approval_packet with fingerprint for drift detection

**ActionRequest model** (`policy/models/models.py`):
- Fields: request_id, tool_name, tool_input, action_class, resource_scopes, risk_hint, idempotent, requires_receipt, supports_preview, actor, context, derived (enriched observables)

**PolicyDecision model**:
- Fields: verdict, action_class, reasons (list[PolicyReason]), obligations (PolicyObligations), normalized_constraints, approval_packet, risk_level
- Obligations: require_receipt, require_preview, require_approval, require_evidence, approval_risk_level

**Verdict enum** (`policy/models/enums.py`): `allow`, `allow_with_receipt`, `approval_required`, `preview_required`, `deny`, `require_approval` (legacy), `selected` (task-plan)

**ActionClass enum**: 19 classes covering read_local, write_local, patch_file, execute_command, network_read/write, vcs_mutation, delegate_execution/reasoning, scheduler_mutation, approval_resolution, rollback, etc.

**Policy rules** (`guards/rules.py`):
- Profile-based evaluation: autonomous, default, supervised, readonly
- Strictness ordering with POLICY_STRICTNESS dict (readonly=3, autonomous=0)
- Delegation scope enforcement: denies actions not in allowed_action_classes
- Multiple rule files: rules_filesystem.py, rules_shell.py, rules_network.py, rules_governance.py, rules_planning.py, rules_readonly.py, rules_adjustment.py, rules_attachment.py

**Request derivation** (`evaluators/derivation.py`):
- Enriches ActionRequest with: target_paths, sensitive_paths, outside_workspace detection, command_flags (writes_disk, deletes_files, sudo, curl_pipe_sh, git_push, network_access), network_hosts, vcs_operation, kernel_paths

**AuthorizationPlanService** (`permits/authorization_plans.py`):
- `preflight()` creates an AuthorizationPlanRecord with: contract_ref, policy_profile_ref, requested_action_classes, approval_route, witness_requirements, proposed_grant_shape, downgrade_options, current_gaps, estimated_authority_cost, expiry_constraints, revalidation_rules, required_workspace_mode, required_secret_policy, proposed_lease_shape
- `invalidate()` marks plan as invalidated with gaps
- Stored as artifact with kind="authorization.plan" in artifact store

**Execution contracts** (`controller/contracts.py`):
- `ActionContract` per action_class: default_risk_band, decision_required, witness_required, receipt_required, reconcile_strategy, rollback_strategy
- Defines the governed pipeline requirements for each action type

**Trust scoring** (`policy/trust/`):
- Trust models and scoring for evaluating evidence trustworthiness

### Gaps

- **Revalidation rules are stored but never executed** -- `check_witness`, `check_approval`, `check_policy_version` are recorded in the plan but no code reads them back for revalidation
- **No policy versioning** -- policy changes are not tracked; no mechanism to know which policy version was active for a given decision
- **No composable policy profiles** -- profiles are simple strings; cannot combine/layer policies
- **downgrade_options in AuthorizationPlan are advisory only** -- "gather_more_evidence", "reduce_scope", "request_authority" are stored but never auto-executed

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/policy/evaluators/engine.py` -- PolicyEngine
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/evaluators/derivation.py` -- Request enrichment
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/models/models.py` -- ActionRequest, PolicyDecision
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/models/enums.py` -- Verdict, ActionClass enums
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/permits/authorization_plans.py` -- AuthorizationPlanService
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/guards/rules.py` -- Rule evaluation dispatch
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/controller/contracts.py` -- ActionContract definitions

---

## 3. Receipts & Proofs

### Current Capabilities

**ReceiptService** (`verification/receipts/receipts.py`):
- `issue()` creates a ReceiptRecord and immediately calls `ProofService.ensure_receipt_bundle()`
- Rich receipt fields: task_id, step_id, step_attempt_id, action_type, receipt_class, input_refs, environment_ref, policy_result, approval_ref, output_refs, result_summary, result_code, decision_ref, capability_grant_ref, workspace_lease_ref, policy_ref, action_request_ref, contract_ref, authorization_plan_ref, witness_ref, idempotency_key, verifiability, signer_ref, rollback_supported/strategy/status/ref/artifact_refs, observed_effect_summary, reconciliation_required

**ProofService** (`verification/proofs/proofs.py`):
- **Hash chain verification**: `verify_task_chain()` walks all events for a task in sequence order, verifying each event's hash links to the previous
- **Receipt bundles**: `ensure_receipt_bundle()` creates sealed artifacts containing: input/output/environment hashes, policy_result hash, approval_packet hash, capability_grant hash, context_manifest_ref, task_event_head_hash
- **Context manifests**: Created per receipt with all reference chains (action_request_ref, policy_ref, approval_ref, decision_ref, capability_grant_ref, workspace_lease_ref, witness_ref, evidence_refs, memory_refs)
- **Merkle tree**: `_receipt_inclusion_proofs()` builds a Merkle tree over all receipt bundles, providing per-receipt inclusion proofs
- **HMAC signing**: Optional HMAC-SHA256 signing via `HERMIT_PROOF_SIGNING_SECRET`, applied to both receipt bundles and proof bundles
- **Proof modes**: hash_only -> hash_chained -> signed -> signed_with_inclusion_proof (progressive levels)
- **Export**: `export_task_proof()` with three detail levels: summary (~5-20KB), standard (~50-200KB), full (can be MBs)
- **Chain completeness check**: Validates that each receipt has linked contract, evidence_case, authorization_plan, and reconciliation records
- **Artifact hash validation**: `_validate_bundle_artifact_hashes()` checks that referenced artifacts' content_hash values match expectations, emits validation warning events on mismatch

**Proof coverage tracking**: Tracks signed_receipts, bundled_receipts, proved_receipts counts per task

### Gaps

- **No external anchoring implemented** -- `proofs/anchoring.py` exists but no actual external timestamping (e.g., blockchain, RFC 3161)
- **Signing is HMAC-only** -- no asymmetric key support for non-repudiation
- **No proof verification CLI** -- proofs can be exported but there's no standalone verifier tool
- **Receipt bundles are not content-addressed in the Merkle tree** -- they use canonical JSON hashing which could be fragile across serialization changes

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/verification/receipts/receipts.py` -- ReceiptService
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/proofs/proofs.py` -- ProofService (793 lines, largest single file)
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/proofs/merkle.py` -- Merkle utilities
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/proofs/anchoring.py` -- Anchoring (stub)
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/proofs/dag_proof.py` -- DAG-specific proofs
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/proofs/governance_report.py` -- Report formatter

---

## 4. Rollback System

### Current Capabilities

**RollbackService** (`verification/rollbacks/rollbacks.py`):
- `execute(receipt_id)` performs a fully governed rollback:
  1. Creates a new Step + StepAttempt for the rollback
  2. Creates a RollbackRecord (status="executing")
  3. Records a DecisionRecord for the rollback
  4. Acquires a workspace lease (mutable, 300s TTL)
  5. Issues a CapabilityGrant for the rollback
  6. Applies the rollback strategy
  7. Issues a rollback receipt
  8. Updates all records to succeeded/failed

**Supported strategies**:
- `file_restore` -- for write_local/patch_file: restores pre-state content or deletes newly created files
- `git_revert_or_reset` -- for vcs_mutation: hard resets to pre-state HEAD (refuses if repo is dirty)
- `supersede_or_invalidate` -- for memory_write: invalidates memory records and beliefs

**Rollback planning** (`rollbacks/rollback_models.py`):
- `RollbackPlan`: Ordered execution with dependency tracking (leaf-first, reverse order)
- `DependentReceipt`: Tracks depth, rollback support, and dependent receipt IDs
- `RollbackPlanExecution`: Tracks succeeded/failed/skipped IDs with per-receipt results
- Cycle detection support

**Dependency tracking** (`rollbacks/dependency_tracker.py`):
- Builds dependency graphs between receipts for recursive rollback

### Gaps

- **No rollback for network operations** -- network_write, credentialed_api_call, external_mutation have no automated rollback
- **No rollback for execute_command** -- shell commands have manual_only strategy
- **No partial rollback** -- either full rollback succeeds or fails; no checkpoint-based partial undo
- **Rollback plan execution is not yet wired** -- `RollbackPlanExecution` model exists but no service that executes plans recursively
- **No rollback dry-run** -- cannot preview what a rollback would do before executing

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/verification/rollbacks/rollbacks.py` -- RollbackService
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/rollbacks/rollback_models.py` -- RollbackPlan, DependentReceipt
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/rollbacks/dependency_tracker.py` -- Dependency graph builder

---

## 5. Workspace Lease

### Current Capabilities

**WorkspaceLeaseRecord** (`authority/workspaces/models.py`):
- Fields: lease_id, task_id, step_attempt_id, workspace_id, root_path, holder_principal_id, mode, resource_scope, environment_ref, status, acquired_at, expires_at, released_at, metadata

**WorkspaceLeaseService** (`authority/workspaces/service.py`):
- `acquire()`: Creates a lease with conflict detection for mutable mode (only one active mutable lease per workspace_id); auto-expires stale leases; captures environment snapshot (cwd, os, python, platform) as artifact
- `release()`: Updates status to "released"
- `validate_active()`: Checks status=="active" and not expired

**Mutable vs readonly semantics**:
- Mutable leases are exclusive per workspace_id
- Readonly leases can coexist with other readonly leases
- Mutable lease conflicts raise `WorkspaceLeaseConflict`

**Integration with CapabilityGrant**:
- `CapabilityGrantService.enforce()` validates the linked workspace lease is active and not expired
- Grants reference leases via workspace_lease_ref

**Integration with rollback**:
- RollbackService acquires mutable workspace leases for rollback operations
- Lease root_path derived from original receipt's workspace_lease or from prestate artifacts

### Gaps

- **No lease renewal** -- leases expire (default 300s TTL) with no renewal mechanism; long-running tasks must hope their grants don't expire
- **No multi-workspace coordination** -- each lease is independent; no protocol for tasks needing multiple workspaces atomically
- **No read-write upgrade** -- cannot upgrade a readonly lease to mutable
- **Environment snapshot is minimal** -- captures cwd/os/python/platform but not installed packages, env vars, or disk state
- **No lease queuing** -- if a mutable lease is held, other requesters get an immediate exception rather than waiting

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/authority/workspaces/models.py` -- WorkspaceLeaseRecord
- `/Users/beta/work/Hermit/src/hermit/kernel/authority/workspaces/service.py` -- WorkspaceLeaseService
- `/Users/beta/work/Hermit/src/hermit/kernel/authority/grants/models.py` -- CapabilityGrantRecord
- `/Users/beta/work/Hermit/src/hermit/kernel/authority/grants/service.py` -- CapabilityGrantService (with enforce + constraint validation)

---

## 6. Ledger / Journal

### Current Capabilities

**KernelStore** (`ledger/journal/store.py`):
- SQLite-backed with schema v13 (migrates from v5+)
- Thread-safe with per-thread connections (or shared connection for :memory:)
- Hash-chained event append (`_event_chain_lock` ensures serial hash linking)

**Tables** (30+ tables):
- Core: conversations, principals, ingresses, tasks, steps, step_attempts, events, artifacts
- Governance: approvals, receipts, decisions, capability_grants, workspace_leases, beliefs, memory_records, rollbacks
- Execution: execution_contracts, evidence_cases, authorization_plans, reconciliations
- Scheduling: schedule_specs, schedule_history
- Memory: memory_embeddings, memory_graph_edges, memory_entity_triples, procedural_memories
- Competition: competitions, competition_candidates
- Delegation: delegations
- Projections: projection_cache, conversation_projection_cache
- Integrity: hash_chain_checkpoints, evidence_signals

**Event sourcing**:
- Every significant state change emits an event via `append_event()`
- Events have: event_id, task_id, step_id, entity_type, entity_id, event_type, actor_principal_id, payload (JSON), occurred_at, event_hash, prev_event_hash, hash_chain_algo, causation_id, correlation_id
- Hash chain: each event's hash includes all fields + the previous event's hash (SHA-256)

**Store mixins**: KernelTaskStoreMixin, KernelLedgerStoreMixin, KernelProjectionStoreMixin, KernelSchedulerStoreMixin, KernelStoreRecordMixin, KernelV2StoreMixin, SignalStoreMixin, CompetitionStoreMixin, DelegationStoreMixin

**Projections** (`projections/store_projection.py`):
- `build_task_projection()` aggregates: steps, step_attempts, approvals, decisions, capability_grants, workspace_leases, receipts, execution_contracts, evidence_cases, authorization_plans, reconciliations

### Gaps

- **No event compaction/archival** -- events accumulate indefinitely; no mechanism to archive old tasks or compact the journal
- **Hash chain is per-task scoped** -- global chain integrity across tasks is not verified
- **No WAL checkpointing control** -- SQLite WAL mode is used but no explicit checkpoint management
- **No schema migration tooling** -- hard-cut migration from v5+ but no incremental migration framework
- **No event replay** -- event sourcing stores events but there's no replay/projection-rebuild mechanism

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store.py` -- KernelStore (main class)
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store_records.py` -- Record CRUD mixin
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store_tasks.py` -- Task-specific operations
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store_v2.py` -- V2 additions (contracts, evidence, auth plans, reconciliations)
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/events/store_ledger.py` -- Artifact/ledger record creation
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/projections/store_projection.py` -- Task projection builder

---

## 7. Context & Memory Governance

### Current Capabilities

**ContextPack** (`context/compiler/compiler.py`):
- Rich context assembly: static_memory, retrieval_memory, selected_beliefs, working_state, episodic_context, procedural_context, task_summary, step_summary, policy_summary, planning_state, carry_forward, continuation_guidance, recent_notes, relevant_artifact_refs, ingress_artifact_refs, focus_summary, bound_ingress_deltas
- Pack includes selection_reasons, excluded_memory_ids, excluded_reasons for transparency
- Content-addressed via pack_hash

**MemoryGovernanceService** (`context/memory/governance.py`):
- **Classification**: `classify_claim()` analyzes claim text via NLP signal detection (sensitive, stable_preference, task_state, project_convention, tooling_environment)
- **Category policies**: Predefined per-category rules for retention_class, scope_kind, static_injection, retrieval_allowed, ttl_seconds
- **Scope matching**: global, conversation, workspace, entity scopes with proper resolution
- **Supersession**: `find_superseded_records()` detects duplicate/topic-sharing memories for replacement
- **Retention classes**: user_preference, project_convention, tooling_environment, task_state, volatile_fact, sensitive_fact, pitfall_warning, procedural
- **TTL enforcement**: `is_expired()` checks, with task_state (7 days) and volatile_fact (24 hours) defaults

**Memory subsystem** (`context/memory/`):
- `retrieval.py` -- Memory retrieval with relevance scoring
- `consolidation.py` -- Memory consolidation
- `embeddings.py` -- Vector embeddings for semantic search
- `graph.py` / `graph_models.py` -- Knowledge graph (entity triples, graph edges)
- `episodic.py` / `episodic_models.py` -- Episodic memory (event-based recall)
- `procedural.py` -- Procedural memory (how-to knowledge)
- `lineage.py` / `lineage_models.py` -- Memory lineage tracking
- `decay.py` / `decay_models.py` -- Memory decay over time
- `confidence.py` -- Confidence scoring
- `memory_quality.py` -- Quality assessment
- `reranker.py` -- Result reranking
- `working_memory.py` -- Working memory management
- `anti_pattern.py` -- Anti-pattern detection
- `reflect.py` -- Self-reflection on memories
- `taxonomy.py` -- Category taxonomy
- `knowledge.py` -- Knowledge base operations
- `text.py` -- Text utilities (dedup, topic sharing)

### Gaps

- **Memory writes are not receipted** -- memory_write actions go through policy but the memory system itself doesn't issue receipts for memory creation/update
- **No memory provenance chain** -- memories track evidence_refs but don't form a verified chain back to the original task/step
- **Classification is NLP-heuristic only** -- no LLM-based or learned classification; relies on keyword matching
- **No cross-workspace memory sharing protocol** -- each workspace scope is independent
- **Episodic/procedural/graph memory have no governance integration** -- they exist as separate subsystems without the belief/classification pipeline

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/context/compiler/compiler.py` -- ContextPack, context compilation
- `/Users/beta/work/Hermit/src/hermit/kernel/context/memory/governance.py` -- MemoryGovernanceService
- `/Users/beta/work/Hermit/src/hermit/kernel/context/memory/retrieval.py` -- Memory retrieval
- `/Users/beta/work/Hermit/src/hermit/kernel/context/memory/consolidation.py` -- Memory consolidation
- `/Users/beta/work/Hermit/src/hermit/kernel/context/models/context.py` -- TaskExecutionContext, WorkingStateSnapshot

---

## 8. Observation Service

### Current Capabilities

**ObservationService** (`execution/coordination/observation.py`):
- Background daemon thread (`hermit-observation`) that polls at `budget.observation_poll_interval`
- `_tick()`: Lists all step_attempts with status="observing", calls `tool_executor.poll_observation()` for each, enqueues resume on should_resume=True
- Thread-safe via `_resuming` set to prevent duplicate resume attempts
- Integrates with `runner.task_controller.enqueue_resume()` and `runner.wake_dispatcher()`

**ObservationTicket**:
- Rich model: observer_kind, job_id, status_ref, poll_after_seconds, cancel_supported, resume_token, topic_summary, tool_name/input, display_name
- Pattern matching: ready_patterns, failure_patterns, progress_patterns
- Status tracking: last_status, last_status_summary, terminal_status, final_result
- Timing: started_at, hard_deadline_at, next_poll_at, last_progress_summary_at
- Normalization via `normalize_observation_ticket()` with required field validation

**ObservationProgress**: Tracks phase, summary, detail, progress_percent, ready status

**SubtaskJoinObservation**: Special observation for parent steps waiting on child step completion (fork-join), carrying child_step_ids and join_strategy

**ObservationPollResult**: Simple result with ticket + should_resume boolean

### Gaps

- **Runtime-coupled, not kernel-native** -- ObservationService depends on runner, agent, and tool_executor; it's not a kernel-level primitive
- **No observation persistence** -- tickets live in memory on step_attempt context; service restart loses polling state
- **No observation receipts** -- observation polls and status transitions are not receipted
- **No observation cancellation enforcement** -- cancel_supported is tracked but no automated timeout->cancel path
- **Hard deadline not enforced** -- hard_deadline_at exists in the ticket but ObservationService doesn't check it

### Key Files

- `/Users/beta/work/Hermit/src/hermit/kernel/execution/coordination/observation.py` -- ObservationService, ObservationTicket, SubtaskJoinObservation
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/executor/observation_handler.py` -- Poll execution handler

---

## 9. System Interaction Analysis

### Currently Connected

1. **Approval -> Decision -> CapabilityGrant -> Receipt** (full chain): Approval resolution creates all three records linked by refs
2. **PolicyEngine -> AuthorizationPlan -> Approval**: Policy evaluation determines if approval is needed; authorization plan records the proposed grant shape
3. **CapabilityGrant -> WorkspaceLease**: Grant enforcement validates the linked workspace lease
4. **Receipt -> ProofBundle -> MerkleTree**: Every receipt gets a sealed bundle; all bundles form a Merkle tree on export
5. **Receipt -> Rollback**: Rollback service reads receipt's rollback_strategy and prestate artifacts
6. **ActionContract -> PolicyEngine -> Receipt**: Contracts define requirements; policy enforces them; receipt confirms execution
7. **Events -> HashChain**: All mutations emit events that are hash-chained per task
8. **ContextPack -> MemoryGovernance**: Context compiler uses governance service to select/filter memories
9. **SubtaskJoinObservation -> JoinBarrier**: Fork-join concurrency uses observation to resume parent steps

### Missing Connections

1. **Memory -> Receipt chain**: Memory writes go through policy but don't produce governance receipts; memory provenance stops at the belief level
2. **ObservationService -> Ledger**: Observation polls, status transitions, and resumptions are not recorded as events
3. **AuthorizationPlan -> Revalidation**: Revalidation rules are stored but never checked; a grant issued under an invalidated plan continues to work
4. **Workspace lease -> Cross-task coordination**: No mechanism for multiple tasks to coordinate access to shared workspaces
5. **Policy versioning -> Decision audit**: Decisions record policy_ref but there's no actual policy version tracking
6. **Budget enforcement -> Kernel**: Execution budgets exist only in runtime layer; kernel has no awareness of resource limits
7. **Proof verification -> Independent verifier**: Proofs are generated but can only be verified by the same ProofService
8. **Evidence case -> Outcome tracking**: Evidence cases are created but their resolution isn't tracked back to the authorization plan

---

## 10. Gap Analysis for Task OS Goals

### Goal 1: Durable Execution

**Status**: Strong foundation. SQLite journal, hash-chained events, receipt bundles.

| Aspect | Status | Gap |
|--------|--------|-----|
| Task persistence | Complete | None |
| Step/attempt lifecycle | Complete | None |
| Event sourcing | Complete | No compaction/archival |
| Hash chain integrity | Complete | Per-task only, no global chain |
| Observation durability | Weak | Tickets in memory, lost on restart |
| Budget/quota enforcement | Missing | Runtime-only, not kernel-governed |

### Goal 2: Governed Authority

**Status**: Well-designed pipeline with some enforcement gaps.

| Aspect | Status | Gap |
|--------|--------|-----|
| Policy evaluation | Complete | No policy versioning |
| Approval workflow | Complete | No escalation, no timeout enforcement |
| CapabilityGrants | Complete | No renewal, 300s default TTL |
| Workspace leases | Functional | No queuing, no multi-workspace atomicity |
| Authorization plans | Partial | Revalidation stored but never executed |
| Delegation scope | Complete | None |

### Goal 3: Inspectable Provenance

**Status**: Strongest area. Multi-level proof system.

| Aspect | Status | Gap |
|--------|--------|-----|
| Receipt generation | Complete | None |
| Proof bundles | Complete | None |
| Merkle inclusion proofs | Complete | None |
| HMAC signing | Complete | No asymmetric key support |
| Chain completeness check | Complete | None |
| Context manifests | Complete | None |
| External anchoring | Stub only | No actual implementation |
| Standalone verification | Missing | No independent verifier |

### Goal 4: Operator Trust

**Status**: Good foundation, needs operational tooling.

| Aspect | Status | Gap |
|--------|--------|-----|
| Rollback (file/git/memory) | Complete | No network/command rollback |
| Recursive rollback planning | Model exists | Execution not wired |
| Approval drift detection | Complete | None |
| Risk classification | Complete | None |
| Governance reports | Complete | None |
| Proof export | Complete (3 levels) | No CLI verifier |
| Memory governance | Partial | Not receipted, heuristic classification |
| Observation transparency | Weak | Not recorded in ledger |

### Top 5 Critical Gaps (Priority Order)

1. **Observation durability**: ObservationService is runtime-coupled and loses state on restart. For Task OS, observations must be kernel-native with durable tickets in the journal.

2. **Memory-receipt integration**: Memory governance exists but is disconnected from the receipt/proof chain. Memory writes need governed receipts for full provenance.

3. **Authorization plan revalidation**: Stored revalidation rules are dead code. For operator trust, grants must be revalidated before execution when conditions change.

4. **Cross-task workspace coordination**: No protocol for multiple tasks accessing shared resources. Task OS needs a workspace broker with queuing and deadlock prevention.

5. **Budget/quota kernel enforcement**: Execution limits exist only at runtime. The kernel should enforce resource budgets as a governance primitive alongside policy and approvals.
