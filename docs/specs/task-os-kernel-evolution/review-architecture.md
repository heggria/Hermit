# Architecture Compliance Review: Task OS Kernel Evolution

**Review Date**: 2026-03-19
**Reviewer**: Architecture Review (Claude)
**Scope**: 10 sub-domain changes across the Hermit kernel, branch `release/0.3`
---

## Executive Summary

The task OS kernel evolution is a well-structured, architecturally coherent body of work that advances the Hermit kernel from a static DAG executor into a topology-adaptive, verification-driven, approval-parkable, workspace-aware task OS. The changes generally adhere to Hermit's core architecture principles (task-first, event-sourced, receipt-aware, scoped authority). However, this review identifies **3 critical issues**, **5 high-severity issues**, and **8 medium-severity observations** that should be addressed before this work is considered architecturally stable.

---

## Sub-Domain Review

### 1. Formal State Machine (enums.py, transitions.py)

**Files**: `src/hermit/kernel/task/state/enums.py`, `src/hermit/kernel/task/state/transitions.py`

**Compliance: GOOD**

- StrEnum usage for `TaskState`, `StepAttemptState`, and `WaitingKind` is clean and backward-compatible (values match existing string literals).
- Transition tables are exhaustive; terminal states correctly have empty target sets.
- `frozenset` usage for `TERMINAL_TASK_STATES`, `ACTIVE_TASK_STATES`, and `TERMINAL_ATTEMPT_STATES` enforces immutability.
- `validate_*` and `require_*` functions correctly separate side-effect-free validation from exception-raising enforcement.
- `InvalidTransitionError` provides good diagnostic information.

**CRITICAL Issue C-1: Phantom states used outside the enum**

The following states are written to the store by various services but are **not defined in any enum**:

| Status | Written By | Entity |
|---|---|---|
| `verification_blocked` | `dag_execution.py:231,241` | step, step_attempt |
| `budget_exceeded` | `executor.py:1624` | task |
| `receipt_pending` | `executor.py:1543,1556` | step_attempt, step |
| `needs_attention` | `executor.py:704` | task |
| `reconciling` | `executor.py:721,733` | step_attempt, step |
| `blocked` (for step) | `executor.py:1122,1281` | step |
| `executing` | `dispatch.py:94` | step_attempt (queried) |

`verification_blocked` and `budget_exceeded` are particularly concerning because they bypass the transition validation entirely. If `require_valid_task_transition` or `require_valid_attempt_transition` were ever enforced at the store level, these writes would fail silently or throw.

**Recommendation**: Add all runtime states to the enums, or establish a policy that the enum is the canonical set and all other status writes are bugs.

---

### 2. DAG Topology Mutation (dag_builder.py)

**File**: `src/hermit/kernel/task/services/dag_builder.py`

**Compliance: GOOD with caveats**

- `StepNode` and `DAGDefinition` are properly frozen dataclasses -- immutable by design.
- Constructor injection of `KernelStore` follows the kernel pattern.
- `add_step`, `skip_step`, `rewire_dependency` correctly emit `dag.topology_changed` events (event-sourced).
- Kahn's algorithm for cycle detection in both `validate()` and `_validate_no_cycles_for_rewire()` is correct.
- `compute_super_steps` is a pure static method (no side effects) -- good separation.

**HIGH Issue H-1: `eval()` in `evaluate_predicate` is a security risk**

At line 487:
```python
result = eval(predicate, {"__builtins__": {}}, upstream_outputs)
```

Even with `__builtins__` nullified, Python `eval()` can be exploited through attribute access on objects in `upstream_outputs`. Since upstream outputs come from step execution results (which may include user-controlled data), this is an attack surface. The kernel rules state "Input validation at system boundaries" and "Secure by default".

**Recommendation**: Replace `eval()` with a safe expression evaluator (e.g., `ast.literal_eval` for simple comparisons, or a purpose-built expression parser). At minimum, restrict the namespace further by only allowing primitive types in `upstream_outputs`.

**MEDIUM Issue M-1: `add_step` does not pass `verification_required`, `verifies`, or `supersedes` to `create_step`**

The `materialize()` method passes these fields, but `add_step()` (used for runtime DAG mutation) omits them. This means dynamically added verification steps will not function correctly.

---

### 3. Verification-Driven Scheduling (dag_execution.py)

**File**: `src/hermit/kernel/task/services/dag_execution.py`

**Compliance: GOOD**

- `DAGExecutionService` follows constructor injection with `KernelStore`.
- Verification gates (`_check_verification_gate_blocked`) correctly block downstream steps when upstream receipts have `reconciliation_required=True`.
- `reopen_verified_step` creates new attempts without mutating existing completed attempts -- immutable history preserved.
- `_maybe_emit_super_step_checkpoint` provides the event-sourced foundation for replay-from recovery.
- The recursive `_compute_depth` method uses memoization via the `depth` dict to avoid redundant computation.

**HIGH Issue H-2: `verification_blocked` status not in the formal state machine**

As noted in C-1, `_check_verification_gate_blocked` writes `verification_blocked` to both step and step_attempt statuses, but this state is absent from `StepAttemptState` and there is no corresponding transition rule in the transition table. This means:
1. `validate_attempt_transition` will return `False` for any transition involving this state.
2. Recovery code cannot reason about these steps.

**MEDIUM Issue M-2: Duplicate DAG activation logic in `TaskController.finalize_result` and `DAGExecutionService.advance`**

`TaskController.finalize_result()` (lines 1004-1032 in controller.py) contains its own DAG activation and failure handling logic that parallels `DAGExecutionService.advance()`. The controller's version does NOT call through `DAGExecutionService`, meaning the verification gate, conditional evaluation, and super-step checkpoint logic are bypassed for controller-finalized steps.

**Recommendation**: Refactor `TaskController.finalize_result()` to delegate to `DAGExecutionService.advance()` instead of reimplementing the DAG progression inline.

---

### 4. Workspace Lifecycle (workspaces/service.py)

**File**: `src/hermit/kernel/authority/workspaces/service.py`

**Compliance: GOOD**

- `WorkspaceLeaseService` follows constructor injection with both `KernelStore` and `ArtifactStore`.
- FIFO queuing for mutable lease conflicts is a significant improvement over fail-fast.
- `extend()` and `expire_stale()` correctly emit events for audit trail.
- `release_all_for_task()` properly handles task-terminal cleanup.
- `WorkspaceLeaseQueued` exception carries the queue entry ID for downstream use.

**MEDIUM Issue M-3: In-memory queue not durable**

The `_queue` dict in `WorkspaceLeaseService` is an in-memory structure protected by `_queue_lock`. If the process restarts, all queued lease requests are lost. The `WorkspaceLeaseQueueEntry` model exists in `models.py` but is never persisted to the store.

For a kernel that emphasizes durable execution and recovery, this is an architectural gap. On restart, tasks waiting for workspace leases will remain blocked indefinitely with no mechanism to dequeue them.

**Recommendation**: Persist queue entries to a `workspace_lease_queue` table and recover them on service start, consistent with how `_recover_interrupted_attempts` works in `KernelDispatchService`.

**MEDIUM Issue M-4: `_process_queue` mutates `entry.status` in place**

At line 304: `entry.status = "served"` -- this is a direct mutation of the dataclass instance, violating the immutability principle. If the subsequent `acquire()` call fails and re-raises `WorkspaceLeaseQueued`, the entry is put back to `"pending"` (line 337), but a concurrent thread reading the queue between these two points would see inconsistent state.

---

### 5. Observation Durability (observation.py)

**File**: `src/hermit/kernel/execution/coordination/observation.py`

**Compliance: ACCEPTABLE with layer concern**

- `ObservationTicket` and `ObservationProgress` are well-structured data classes with proper serialization.
- `SubtaskJoinObservation` cleanly supports fork-join concurrency.
- `ObservationService.persist_ticket` and `resolve_ticket` provide durable observation tracking.

**HIGH Issue H-3: Layer violation -- kernel module imports from runtime**

At line 8:
```python
from hermit.runtime.control.lifecycle.budgets import ExecutionBudget, get_runtime_budget
```

`ObservationService` lives in `src/hermit/kernel/execution/coordination/` (kernel layer) but imports from `hermit.runtime` (runtime layer). The kernel layer should be self-contained; runtime concerns should not leak downward.

This is not an isolated case. A grep reveals 20+ files under `src/hermit/kernel/` importing from `hermit.runtime`. However, most of these are in `kernel/execution/` which is the boundary layer between kernel and runtime. The `observation.py` import is notable because `get_runtime_budget()` reads global configuration -- a runtime concern.

**Recommendation**: Inject `ExecutionBudget` as a constructor parameter rather than calling `get_runtime_budget()` directly. The existing `budget` parameter already supports this, but the default fallback to `get_runtime_budget()` creates the coupling.

---

### 6. Approval Orchestration (approvals.py)

**File**: `src/hermit/kernel/policy/approvals/approvals.py`

**Compliance: GOOD**

- `ApprovalService` follows the governed execution path: creates approval, issues decision, issues capability grant, issues receipt.
- `_issue_resolution_receipt` is a proper implementation of the Approval -> Grant -> Execution -> Receipt chain.
- `ApprovalTimeoutService` correctly handles drift_expiry with optional escalation.
- `request_with_delegation_check` integrates delegation policy without breaking the approval interface.
- Batch approval (`request_batch`, `approve_batch`) enables parallel step coordination.

**MEDIUM Issue M-5: `ApprovalService.__init__` probes store capabilities via `hasattr`**

The constructor checks `hasattr(store, attr)` for multiple methods to determine `_governed_resolution`. This is fragile -- if the store interface changes, the feature detection silently degrades. Constructor injection of an explicit `governed_resolution: bool` flag or a more specific interface type would be cleaner.

---

### 7. Delegation Model (delegation.py)

**File**: `src/hermit/kernel/task/models/delegation.py`

**Compliance: GOOD**

- `ApprovalDelegationPolicy` implements deny-by-default correctly: unlisted action classes are denied.
- `DelegationScope` captures authority boundaries including budget attenuation.
- `DelegationRecord` stores `attenuation_factor` for audit.
- All records use `@dataclass` with `from __future__ import annotations`.

No issues found. This is a well-designed, minimal model.

---

### 8. Ledger Migrations (store.py v14-v17)

**File**: `src/hermit/kernel/ledger/journal/store.py`

**Compliance: GOOD**

- Schema migrations v14 (verification columns), v15 (blackboard table), v16 (observation tickets), v17 (budget columns) are properly sequenced and use `_ensure_column` / `CREATE TABLE IF NOT EXISTS` for idempotency.
- `_MIGRATABLE_SCHEMA_VERSIONS` includes all versions 5-17, ensuring existing databases can upgrade.
- Blackboard CRUD methods are properly placed in the store with index support.
- Observation ticket CRUD includes proper event emission.

**MEDIUM Issue M-6: Migration ordering is fragile**

Migrations are called sequentially in `_init_schema` (lines 1007-1016), but their version suffixes (v4, v11, v6, v8, v12, v13, v14, v15, v16, v17) are out of numerical order. While each migration is individually idempotent, the conceptual model would be clearer if they were ordered by version number. More importantly, there is no individual version tracking -- all migrations run on every startup, relying on idempotency rather than checking what has already been applied.

---

### 9. Budget and Monotonicity Guards (rules_budget.py, executor.py)

**Files**: `src/hermit/kernel/policy/guards/rules_budget.py`, `src/hermit/kernel/execution/executor/executor.py`

**Compliance: ACCEPTABLE**

- `evaluate_monotonicity_guard` correctly skips coordination overhead for `readonly`/`additive` steps while still requiring receipts for `additive`.
- `evaluate_communication_budget_guard` provides cost awareness with configurable thresholds.
- Both guards return `RuleOutcome | None`, correctly fitting into the policy chain pattern.

**CRITICAL Issue C-2: Budget tracking in executor uses character count, not tokens**

In `executor.py` lines 1607-1608:
```python
token_cost = len(str(tool_input)) + len(str(tool_input))
```
Wait, re-reading:
```python
token_cost = len(str(tool_input)) + len(str(raw_result))
```

This uses `len(str(...))` (character count) as a proxy for token cost. The field is named `budget_tokens_used` and `budget_tokens_limit`, implying token-based budgeting, but the actual measurement is character-based. This is misleading and will produce wildly inaccurate budget tracking (character count vs. token count can differ by 3-4x depending on content).

**CRITICAL Issue C-3: `budget_exceeded` is not a valid TaskState**

When the budget is exceeded (executor.py line 1624), the task status is set to `budget_exceeded`, but this state does not exist in `TaskState` enum and has no transition rules. This means:
1. The task enters an undocumented terminal state.
2. No transition validation is applied.
3. Recovery/cleanup code cannot reason about this state.

---

### 10. Typed Blackboard (blackboard.py)

**File**: `src/hermit/kernel/artifacts/blackboard.py`

**Compliance: ACCEPTABLE**

- `BlackboardService` provides clean CRUD with validation via `BlackboardEntryType` enum.
- Supersession pattern preserves history (old entries marked `superseded`, new ones linked).
- Confidence validation at boundary (0.0-1.0 range check).
- Integration with `ContextPack` via the `blackboard_entries` field enables context compilation.

**HIGH Issue H-4: Direct call to private `_append_event_tx`**

`BlackboardService` calls `self._store._append_event_tx(...)` directly (lines 51, 106, 133) instead of the public `self._store.append_event(...)`. This:
1. Bypasses any public-API invariants (e.g., hash chain management, event sequence tracking).
2. Breaks encapsulation of the store's internal transaction handling.
3. Is inconsistent with every other service in the kernel, which uses `store.append_event()`.

**Recommendation**: Replace all `_append_event_tx` calls with `self._store.append_event()`.

**MEDIUM Issue M-7: Constructor accepts `Any` for store**

The constructor signature is `def __init__(self, store: Any)`. Every other kernel service types the store as `KernelStore`. Using `Any` defeats type checking and makes the dependency implicit.

---

### 11. Replay-From (replay.py)

**File**: `src/hermit/kernel/execution/recovery/replay.py`

**Compliance: GOOD**

- Creates a new task branch (immutable history -- original events are not modified).
- Upstream steps are marked `skipped`; downstream steps get fresh `ready` attempts.
- `replay.started` event links the new task to the original for traceability.
- `_collect_upstream` correctly traverses `depends_on` edges to find all ancestors.

**MEDIUM Issue M-8: No policy profile or budget propagation**

The replay task copies `policy_profile` from the original, but does not propagate `budget_tokens_limit`, `budget_tokens_used`, workspace leases, or delegation scope. A replayed task could exceed the original's authority boundaries.

---

### 12. Receipt HMAC Signing (receipts.py)

**File**: `src/hermit/kernel/verification/receipts/receipts.py`

**Compliance: GOOD**

- HMAC-SHA256 signing uses `HERMIT_PROOF_SIGNING_SECRET` from environment (not hardcoded).
- Signing is opt-in: returns `None` when no secret is configured.
- Signature is computed after receipt creation (using store-generated ID) and persisted separately.
- `ProofService.ensure_receipt_bundle` is called for every receipt.

No issues found. Clean, correct implementation.

---

### 13. Memory-Receipt Integration (knowledge.py)

**File**: `src/hermit/kernel/context/memory/knowledge.py`

**Compliance: GOOD**

- `MemoryRecordService` requires reconciliation evidence before promoting beliefs to memories (evidence-bound).
- `_issue_memory_write_receipt` creates proper receipts with prestate artifacts for rollback.
- `invalidate_by_reconciliation` handles cascading invalidation when reconciliations are violated.
- `reconcile_active_records` implements deduplication and supersession for active memory records.

No critical issues. The dependency on `ReceiptService` and `ArtifactStore` is properly injected via constructor.

---

### 14. Context Compiler Blackboard Integration (compiler.py)

**File**: `src/hermit/kernel/context/compiler/compiler.py`

**Compliance: GOOD**

- `ContextPack` includes `blackboard_entries` field for structured inter-step data.
- The field is properly defaulted with `field(default_factory=list)`.
- `to_payload()` serializes it alongside all other context sections.

No issues found.

---

### 15. Heartbeat (dispatch.py)

**File**: `src/hermit/kernel/execution/coordination/dispatch.py`

**Compliance: GOOD**

- `report_heartbeat` stores timestamp in attempt context.
- `check_heartbeat_timeouts` scans running attempts with configured intervals.
- Timed-out attempts are failed and trigger retry via `retry_step` if `max_attempts` allows, or cascade failure via `propagate_step_failure`.
- Recovery deduplication in `_recover_interrupted_attempts` handles multiple in-flight attempts per step.

**HIGH Issue H-5: Heartbeat stores timestamp in context JSON, not in `last_heartbeat_at` column**

`StepAttemptRecord` has a `last_heartbeat_at: float | None` field, and the store schema has a `last_heartbeat_at` column on `step_attempts`. However, `report_heartbeat` (dispatch.py line 74) writes to `context["last_heartbeat_at"]` instead of using `store.update_step_attempt(step_attempt_id, last_heartbeat_at=time.time())`. And `check_heartbeat_timeouts` (line 101) reads from `ctx.get("last_heartbeat_at")` instead of `attempt.last_heartbeat_at`.

This means the dedicated column remains NULL while the heartbeat data is buried in the unstructured context JSON, defeating indexing and query efficiency.

---

## Cross-Cutting Findings

### Governed Execution Path Compliance

The governed execution path (Approval -> Grant -> Execution -> Receipt) is **well maintained** across the changes:

- `ToolExecutor.execute()` follows the full pipeline: policy evaluation -> approval check -> capability grant -> tool invocation -> receipt issuance -> reconciliation.
- `ApprovalService._issue_resolution_receipt` follows Decision -> Grant -> Receipt for approval resolutions.
- `MemoryRecordService._issue_memory_write_receipt` issues receipts for memory promotions.
- `ReceiptService.issue()` computes HMAC signatures and creates proof bundles.

No bypass of the governed path was found in the new code.

### Circular Import Risk Assessment

**Risk: LOW**

- `dag_execution.py` imports `dag_builder.py` via deferred import (`from hermit.kernel.task.services.dag_builder import StepDAGBuilder` inside a method).
- `controller.py` imports `dag_builder.py` via deferred import as well.
- `knowledge.py` uses `TYPE_CHECKING` guards for `ArtifactStore` and `ReceiptService`.
- The primary circular risk is between `kernel/execution/executor/executor.py` and its many delegate handlers, but these are forward references resolved at import time without cycles.

### Event Sourcing Compliance

All new features emit events for state changes:
- `dag.topology_changed`, `dag.step_skipped`, `dag.step_rewired`
- `verification.gate_blocked`, `verification.step_invalidated`
- `checkpoint.super_step`
- `workspace.lease_queued`, `workspace.lease_extended`, `workspace.auto_expired`, `workspace.lease_dequeued`
- `observation.created`, `observation.timeout`
- `approval.escalation_needed`, `approval.timed_out`
- `blackboard.entry_posted`, `blackboard.entry_superseded`, `blackboard.entry_resolved`
- `budget.exceeded`
- `replay.started`

**Exception**: `BlackboardService` uses `_append_event_tx` instead of `append_event` (see H-4).

### Record Type Placement

All new record types are correctly placed in the records module:
- `BlackboardRecord`, `BlackboardEntryType`, `BlackboardEntryStatus` in `task/models/records.py`
- `ObservationTicketRecord` in `task/models/records.py`
- `ApprovalDelegationPolicy`, `DelegationScope`, `DelegationRecord` in `task/models/delegation.py`
- `WorkspaceLeaseQueueEntry` in `authority/workspaces/models.py`

### Constructor Injection Pattern

All new services follow the established constructor injection pattern with `KernelStore` dependency:
- `StepDAGBuilder(store: KernelStore)`
- `DAGExecutionService(store: KernelStore)`
- `WorkspaceLeaseService(store: KernelStore, artifact_store: ArtifactStore)`
- `ApprovalService(store: KernelStore)`
- `ApprovalTimeoutService(store: KernelStore)`
- `ReceiptService(store: KernelStore, artifact_store: ArtifactStore | None)`
- `MemoryRecordService(store: KernelStore)`
- `BeliefService(store: KernelStore)`

**Exception**: `BlackboardService(store: Any)` -- see M-7.

---

## Summary of Findings

### Critical (3)

| ID | Sub-Domain | Issue |
|---|---|---|
| C-1 | State Machine | Phantom states (`verification_blocked`, `budget_exceeded`, `receipt_pending`, `needs_attention`, `reconciling`) used but not defined in enums or transition tables |
| C-2 | Budget Guard | Budget tracking uses character count (`len(str(...))`) instead of actual token counts |
| C-3 | Budget Guard | `budget_exceeded` task status not in `TaskState` enum, no transition rules |

### High (5)

| ID | Sub-Domain | Issue |
|---|---|---|
| H-1 | DAG Builder | `eval()` for predicate evaluation is a security risk |
| H-2 | Verification | `verification_blocked` step status not in `StepAttemptState` enum |
| H-3 | Observation | Layer violation: kernel imports `get_runtime_budget()` from runtime |
| H-4 | Blackboard | Calls private `_append_event_tx` instead of public `append_event` |
| H-5 | Heartbeat | Stores heartbeat in context JSON instead of dedicated column |

### Medium (8)

| ID | Sub-Domain | Issue |
|---|---|---|
| M-1 | DAG Builder | `add_step` omits `verification_required`/`verifies`/`supersedes` parameters |
| M-2 | Verification | Duplicate DAG activation logic in `TaskController` and `DAGExecutionService` |
| M-3 | Workspace | FIFO queue is in-memory only, not durable |
| M-4 | Workspace | `_process_queue` mutates entry status in place |
| M-5 | Approvals | Constructor probes store capabilities via `hasattr` |
| M-6 | Ledger | Migration ordering is out of numerical sequence |
| M-7 | Blackboard | Constructor accepts `Any` instead of `KernelStore` |
| M-8 | Replay | No budget or delegation scope propagation to replay tasks |

---

## Recommended Prioritization

1. **Immediate** (before merge): C-1, C-3, H-2 -- the phantom states create an inconsistency between the formal state machine and actual runtime behavior. Either add these states to the enums with proper transition rules, or remove the code that writes them.

2. **Before production use**: H-1 (replace `eval`), H-4 (fix `_append_event_tx` calls), C-2 (fix budget calculation).

3. **Next iteration**: M-2 (deduplicate DAG logic), M-3 (persist workspace queue), H-3 (inject budget), H-5 (use dedicated heartbeat column).

4. **Backlog**: M-1, M-4, M-5, M-6, M-7, M-8.

---

## Key Files Referenced

- `/Users/beta/work/Hermit/src/hermit/kernel/task/state/enums.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/state/transitions.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/dag_builder.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/dag_execution.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/controller.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/models/records.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/models/delegation.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/authority/workspaces/service.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/coordination/observation.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/coordination/dispatch.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/executor/executor.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/recovery/replay.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/approvals/approvals.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/guards/rules_budget.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/artifacts/blackboard.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/context/memory/knowledge.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/verification/receipts/receipts.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/context/compiler/compiler.py`

---
