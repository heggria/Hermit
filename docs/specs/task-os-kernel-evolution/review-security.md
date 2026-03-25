# Security Review: Task OS Kernel Evolution

**Reviewer:** Security Review Agent
**Date:** 2026-03-19
**Scope:** Task OS Kernel Evolution changes — new and modified files in `src/hermit/kernel/`
**Branch:** `release/0.3`

---

## Executive Summary

Ten files were reviewed across the HMAC signing, approval/delegation, budget enforcement, DAG mutation, replay, workspace lease, memory receipt, SQL query, and input validation surfaces. The overall posture is strong by design — the kernel enforces deny-by-default delegation, parameterized SQL queries in production paths, and evidence-bound memory promotion. However, **three HIGH and two MEDIUM findings** require attention before this branch ships.

| Severity | Count | Findings |
|----------|-------|----------|
| CRITICAL | 0 | — |
| HIGH | 3 | Unsafe `eval()` in DAG predicate, HMAC secret optional/silent, `_ensure_column` SQL injection in migration |
| MEDIUM | 2 | Budget tracking TOCTOU race, workspace queue in-memory-only (no persistence) |
| LOW | 3 | Signing covers partial fields, prestate artifact trust tier, DelegationService in-memory state |
| INFO | 2 | No TTL cap on `extend()`, `approve_batch` full-scan |

---

## Finding 1 — HIGH: Arbitrary Code Execution via `eval()` in DAG Predicate

**File:** `src/hermit/kernel/task/services/dag_builder.py`, line 487
**OWASP:** A03 Injection

### Description

`StepDAGBuilder.evaluate_predicate()` calls Python's built-in `eval()` on a caller-supplied predicate string:

```python
result = eval(predicate, {"__builtins__": {}}, upstream_outputs)
```

The `{"__builtins__": {}}` sandbox is a well-known incomplete restriction. An attacker who can write a `StepNode` with a crafted `predicate` field (via the DAG mutation API — `add_step`, `rewire_dependency`, or the initial `build_and_materialize`) can escape the sandbox using standard Python introspection tricks. For example:

```python
[c for c in ().__class__.__bases__[0].__subclasses__() if c.__name__ == 'Popen'][0](['id'])
```

The predicate is stored durably in `dag_node_metadata` inside the step attempt context and re-evaluated at dispatch time (`dag_execution.py` line 200). A predicate injected into a task that has already passed policy evaluation will execute without further approval.

**Callsite in production:**
```python
result = StepDAGBuilder.evaluate_predicate(predicate, upstream_outputs)
# dag_execution.py:200
```

### Recommendation

Replace `eval()` with a restricted expression evaluator. The `simpleeval` library provides a safe single-expression evaluator that supports only arithmetic and comparison operators. If the predicate grammar must stay Python-like, define an explicit AST whitelist (only `ast.Compare`, `ast.BoolOp`, `ast.Name`, `ast.Constant`) and walk the AST before evaluation. Reject any predicate containing attribute access, subscript, or call nodes at write time, not at evaluation time.

At minimum, validate predicates against a whitelist regex before persisting them in `StepNode`. Reject anything containing `.`, `[`, `(`, `__`, or `import`.

---

## Finding 2 — HIGH: `_ensure_column` SQL Injection in Migration Helpers

**File:** `src/hermit/kernel/ledger/journal/store.py`, line 1033
**OWASP:** A03 Injection

### Description

The `_ensure_column` helper builds a DDL statement using Python string interpolation:

```python
self._get_conn().execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
```

All three arguments (`table`, `column`, `definition`) are interpolated directly. SQLite does not support parameter binding in DDL statements, which is a known limitation. In the current codebase these calls originate from hardcoded strings in the kernel itself and are not directly reachable from user input. However:

1. The method is `public` (no leading underscore convention aside from naming) and can be called by any mixin or subclass.
2. If a plugin or extension ever calls `_ensure_column` with values derived from plugin metadata, arbitrary SQL can be injected.
3. The same pattern appears in `PRAGMA table_info({table})` on line 1029.

### Recommendation

Validate `table` and `column` against a strict allowlist before interpolation:

```python
_ALLOWED_TABLES = frozenset({"tasks", "steps", "step_attempts", ...})  # copy _KNOWN_KERNEL_TABLES

def _ensure_column(self, table: str, column: str, definition: str) -> None:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"_ensure_column: unknown table '{table}'")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", column):
        raise ValueError(f"_ensure_column: invalid column name '{column}'")
    # definition is still interpolated but is always a hardcoded literal in practice
    ...
```

This converts the latent risk into a hard error if future code violates the invariant.

---

## Finding 3 — HIGH: HMAC Signing Secret Is Optional with Silent Degradation

**File:** `src/hermit/kernel/verification/receipts/receipts.py`, lines 30-40
**OWASP:** A02 Cryptographic Failures

### Description

`_compute_signature` returns `None` when `HERMIT_PROOF_SIGNING_SECRET` is not set, and the caller silently skips updating the signature:

```python
secret = os.environ.get("HERMIT_PROOF_SIGNING_SECRET")
if not secret:
    return None
```

```python
signature = self._compute_signature(...)
if signature is not None:
    self.store.update_receipt_signature(receipt.receipt_id, signature)
```

The design intent — that signing is optional — means production deployments that omit the environment variable produce receipts with no integrity guarantee. A receipt without a signature cannot be distinguished from a tampered receipt. There is no log warning, no startup check, and no way for an operator to determine after the fact whether a receipt was issued in an unsigned configuration.

Additionally, the HMAC message covers only five fields:

```python
message = f"{receipt_id}:{task_id}:{step_id}:{action_type}:{result_code}"
```

Fields that are security-relevant but excluded from the message include `approval_ref`, `capability_grant_ref`, `workspace_lease_ref`, and `authorization_plan_ref`. An attacker who can tamper with the database directly could swap these references without invalidating the signature.

### Recommendation

1. Emit a `structlog` WARNING at `ReceiptService.__init__` time when `HERMIT_PROOF_SIGNING_SECRET` is absent, so operators notice the degraded mode.
2. Add a startup check in `hermit serve` that refuses to start in production mode without the secret (configurable with `--allow-unsigned-receipts` for dev environments).
3. Extend the HMAC message to include `approval_ref`, `capability_grant_ref`, and `authorization_plan_ref` so swapping these in the DB invalidates the signature. Coordinate a key rotation and re-sign if this change is applied to an existing deployment.
4. Consider key rotation support: store the key ID alongside the signature so old receipts remain verifiable after a rotation.

---

## Finding 4 — MEDIUM: Budget Tracking TOCTOU Race (Post-Execution, Not Pre-Execution)

**File:** `src/hermit/kernel/execution/executor/executor.py`, lines 1605-1624
**OWASP:** A04 Insecure Design

### Description

Token budget tracking in `execute()` occurs **after** the tool has already run:

```python
task_record = self.store.get_task(attempt_ctx.task_id)
if task_record is not None and task_record.budget_tokens_limit is not None:
    token_cost = len(str(tool_input)) + len(str(raw_result))
    new_used = task_record.budget_tokens_used + token_cost
    self.store.update_task_budget(attempt_ctx.task_id, budget_tokens_used=new_used)
```

In a concurrent dispatch scenario (multiple workers processing steps of the same task simultaneously), two workers can read the same `budget_tokens_used`, both compute a non-exceeding `new_used`, and both call the tool before either writes back. The `budget.exceeded` event and the `budget_exceeded` status update are never reached, allowing the task to consume up to 2× the budget limit.

The `evaluate_communication_budget_guard` rule (`rules_budget.py`) is evaluated at policy time, but it reads the budget from `request.context`, not from the live database. If the context was assembled before a sibling step consumed tokens, the guard sees stale values.

### Recommendation

Use a SQLite atomic increment with a constraint check:

```sql
UPDATE tasks
SET budget_tokens_used = budget_tokens_used + ?
WHERE task_id = ?
  AND (budget_tokens_limit IS NULL OR budget_tokens_used + ? <= budget_tokens_limit)
```

Check the number of affected rows. If zero, the budget was exceeded; deny the attempt retroactively and emit the `budget.exceeded` event. This eliminates the read-modify-write race within a single SQLite write operation.

Alternatively, move the budget check to a pre-execution gate (before `invoke_tool_handler`) using a `SELECT ... FOR UPDATE`-equivalent advisory lock on the task row.

---

## Finding 5 — MEDIUM: Workspace Lease Queue Is In-Memory Only

**File:** `src/hermit/kernel/authority/workspaces/service.py`, lines 51-52, 281-338
**OWASP:** A04 Insecure Design

### Description

The FIFO queue for mutable workspace leases is stored in a plain Python dict:

```python
self._queue: dict[str, list[WorkspaceLeaseQueueEntry]] = {}
self._queue_lock = threading.Lock()
```

Queue entries are never persisted to the SQLite store. If the process restarts while tasks are queued:

1. All queued entries are silently lost.
2. The tasks that raised `WorkspaceLeaseQueued` will remain suspended indefinitely with no mechanism to retry.
3. An operator has no visibility into the queue depth or queue contents from the audit log.

The `workspace.lease_queued` event is emitted to the store, but the queue itself is not reconstructed from those events on restart.

Additionally, `_process_queue` acquires `_queue_lock`, reads pending entries, then releases the lock before calling `self.acquire()`. A second release on the same workspace between the lock release and the `acquire()` call could result in two workers both entering `_process_queue`, both seeing the same pending entry (since the first sets `entry.status = "served"` inside the lock but the second may have read the entry list before the first acquired the lock), and issuing duplicate leases.

### Recommendation

1. Persist `WorkspaceLeaseQueueEntry` to a `workspace_lease_queue` store table so the queue survives restarts and can be reconstructed.
2. In `_process_queue`, hold `_queue_lock` across the entire sequence of marking the entry `"served"` and calling `acquire()`, or use an atomic store-level operation that creates the lease only if no active mutable lease exists for the workspace.

---

## Finding 6 — LOW: Delegation Auto-Approve State Held In-Memory

**File:** `src/hermit/kernel/task/services/delegation.py`, lines 223-246
**OWASP:** A04 Insecure Design

### Description

`DelegationService._delegations` is a plain dict (`{delegation_id: DelegationRecord}`). The `check_delegation_approval_policy` lookup iterates all records in memory. If the process restarts after a delegation is created, the delegation record is gone and all child approvals will return `"no_policy"` instead of `"auto_approve"` or `"deny"`. This could cause child tasks to hang awaiting approval that was previously auto-resolved, or bypass expected denials.

The `ApprovalDelegationPolicy.resolve()` method correctly implements deny-by-default for unlisted action classes (line 28), which is a positive finding.

### Recommendation

Persist `DelegationRecord` (including `approval_delegation_policy`) to the `delegations` SQLite table and reconstruct on startup. The `DelegationStoreMixin` already exists — verify it stores and loads the `approval_delegation_policy` field.

---

## Finding 7 — LOW: HMAC Covers Only Five of ~20 Receipt Fields

**File:** `src/hermit/kernel/verification/receipts/receipts.py`, lines 35-40
**Cross-reference:** Finding 3

### Description

As noted in Finding 3, the HMAC message string excludes most receipt fields. Beyond the authorization references already mentioned, the following fields are also excluded:

- `rollback_supported`, `rollback_strategy` — an attacker who flips `rollback_supported` from `True` to `False` in the database could prevent authorized rollback of a harmful action.
- `result_code` is included, but `result_summary` and `observed_effect_summary` are not — so the narrative in a receipt can be altered without detection.
- `idempotency_key` is excluded — replay of the exact approval for a different idempotency key would not be detected.

### Recommendation

Extend the canonical message to a sorted JSON serialization of all security-relevant fields. Consider using the same canonical JSON helper (`_canonical_json`) already present in `store_support.py`.

---

## Finding 8 — LOW: Memory Prestate Artifact Uses `trust_tier="observed"` Not `"system"`

**File:** `src/hermit/kernel/context/memory/knowledge.py`, lines 283-293

### Description

The prestate artifact created before a memory write operation (used for rollback) is stored with `trust_tier="observed"`:

```python
prestate_artifact = self.store.create_artifact(
    ...
    trust_tier="observed",
    ...
)
```

If rollback integrity is validated against trust tier (e.g., only `"system"` or `"kernel"`-tier artifacts are trusted for rollback inputs), this artifact could be considered low-confidence evidence. It is unclear from the current codebase whether rollback enforcement consults `trust_tier`, but using `"system"` or `"kernel"` for kernel-internal prestate artifacts would align with the least-surprise principle.

### Recommendation

Use `trust_tier="system"` for prestate artifacts created by the kernel itself (not derived from external observations). Add a comment explaining the choice.

---

## Finding 9 — LOW: `replay_from` Creates Fresh Attempts Without Re-Approval

**File:** `src/hermit/kernel/execution/recovery/replay.py`, lines 44-150
**OWASP:** A01 Broken Access Control

### Description

`replay_from()` creates a new task that re-executes steps from a given point. The new attempts are created with:

```python
context={
    "ingress_metadata": {
        "dispatch_mode": "async",
        "source": "replay",
        ...
    },
}
```

No approval is transferred and no policy gate is applied at replay creation time. The new task will re-execute using whatever policy profile was on the original task. This is the correct behavior — the policy engine will re-evaluate each action at dispatch time.

However, there is a subtle risk: steps that were upstream of the replay point are marked `"skipped"` in the new task. The replay task's dependency graph is correctly rewired (`_collect_upstream`, `old_to_new`). But the replay inherits the original `policy_profile` without re-checking whether the operator still wants to allow replay under the current policy. If the original task was created under a permissive `"autonomous"` profile and the operator subsequently tightened the policy, the replay task inherits the original profile name and may still execute with fewer restrictions than intended.

The `parent_task_id=task_id` linkage preserves the audit trail.

### Recommendation

Document this behavior explicitly: `replay_from` inherits the original policy profile. If policy has changed since the original task was created, operators should create a new task rather than replaying.

Consider adding a `policy_profile` override parameter to `replay_from` so operators can explicitly supply a stricter profile for the replay.

---

## Finding 10 — INFO: No Maximum TTL Cap on `WorkspaceLeaseService.extend()`

**File:** `src/hermit/kernel/authority/workspaces/service.py`, lines 159-195

### Description

The `extend()` method accepts an arbitrary `additional_ttl` integer:

```python
new_expires_at = max(base, now) + additional_ttl
```

There is no upper bound on `additional_ttl`. A caller (or a compromised step) could issue `extend(lease_id, 10**18)` to create a lease that never expires in practice, effectively holding a mutable workspace lock indefinitely and starving all queued steps.

### Recommendation

Enforce a maximum single-extension TTL (e.g., `MAX_EXTENSION_TTL_SECONDS = 3600`) and a maximum absolute lease lifetime (e.g., 24 hours from `acquired_at`). Reject extensions that would exceed either limit.

---

## Finding 11 — INFO: `approve_batch` Performs Full Scan on `pending` Approvals

**File:** `src/hermit/kernel/policy/approvals/approvals.py`, lines 329-338

### Description

`approve_batch()` loads up to 1000 pending approvals and filters client-side by `batch_id`:

```python
approvals = self.store.list_approvals(status="pending", limit=1000)
for a in approvals:
    resolution = dict(a.resolution or {})
    if resolution.get("batch_id") == batch_id:
        ...
```

If the pending approval queue is large, this has O(n) cost and will silently miss approvals beyond position 1000. If the batch was large (e.g., 200 approvals) and there are already 900 unrelated pending approvals, some batch members will not be approved.

### Recommendation

Add a `batch_id` index to the `approvals` table and query by `batch_id` directly, or store batch membership in a separate `approval_batches` table. The current behavior is a functional correctness issue that can manifest as a security issue if partially-approved batches proceed.

---

## SQL Injection Assessment

The following dynamic SQL patterns were reviewed:

| Location | Pattern | Safe? |
|----------|---------|-------|
| `store_tasks.py:257` | `WHERE {where}` clauses assembled from `clauses.append("col = ?")` | Yes — clauses are hardcoded strings, values are parameterized |
| `store_ledger.py:640` | Same pattern | Yes |
| `store_v2.py:150,318,520,695` | Same pattern | Yes |
| `store.py:1033` | `ALTER TABLE {table} ADD COLUMN {column} {definition}` | No — see Finding 2 |
| `store.py:1029` | `PRAGMA table_info({table})` | No — same issue, but PRAGMA is read-only |
| `store_ledger.py:114,147` | `IN ({placeholders})` where placeholders = `"?, ?" * n` | Yes — placeholders are generated from count, not from input values |

All production `SELECT`/`INSERT`/`UPDATE`/`DELETE` paths use parameterized queries correctly. The only injection risk is in DDL helpers used exclusively at migration time (Finding 2).

---

## Input Validation Assessment

| Surface | Validated? | Notes |
|---------|-----------|-------|
| `BlackboardService.post()` — `entry_type` | Yes | Checked against `BlackboardEntryType.__members__` |
| `BlackboardService.post()` — `confidence` | Yes | `0.0 <= confidence <= 1.0` |
| `StepNode.monotonicity_class` | Partial | Validated at policy evaluation time against `_VALID_MONOTONICITY_CLASSES`, but not at `StepNode` construction |
| `StepNode.predicate` | No | Stored and evaluated without validation — see Finding 1 |
| `ApprovalService.request_batch()` — batch input | Minimal | `approval_type` defaults silently to `"tool_use"` if absent |
| `WorkspaceLeaseService.acquire()` — `mode` | Implicit | Only `"mutable"` triggers conflict logic; any other string is treated as `"readonly"` without validation |
| `ReceiptService.issue()` — `result_code` | No | Accepted as a free-form string; no enum enforcement |

### Recommendation

Enforce `result_code` as an enum at the `ReceiptService.issue()` boundary. Validate `mode` in `WorkspaceLeaseService.acquire()` against an allowlist (`{"mutable", "readonly"}`).

---

## Authorization / Privilege Escalation Assessment

The governed execution path (Approval → CapabilityGrant → Execution → Receipt) is consistently enforced in `executor.py`. The following positive findings are noted:

- `ApprovalDelegationPolicy.resolve()` correctly denies unlisted action classes by default (deny-by-default, not allow-by-default). This is the correct design.
- `_is_governed_action()` determines whether a tool requires the full approval chain; the flag is evaluated from the tool registry + policy result, not from caller-supplied input.
- `capability_service.enforce()` is called immediately before `invoke_tool_handler`, with the capability consumed only after enforcement passes. This prevents re-use of a single grant across multiple executions.
- The `revalidation gate` in `execute()` (lines 1330-1343) correctly invalidates the authorization plan and supersedes the attempt if the policy version has drifted since the plan was created.

One area to watch: `request_with_delegation_check()` auto-approves based on the return value of `delegation_service.check_delegation_approval_policy()`. If `DelegationService` state is lost on restart (see Finding 6), the fallback is `"no_policy"` → `"pending"` (not `"auto_approve"`), which is safe-by-default.

---

## Summary of Recommendations (Prioritized)

| Priority | Finding | Action |
|----------|---------|--------|
| 1 | Finding 1 — `eval()` in DAG predicate | Replace with AST whitelist or `simpleeval`; validate at write time |
| 2 | Finding 2 — `_ensure_column` SQL injection | Allowlist `table` and `column` against known identifiers |
| 3 | Finding 3 — HMAC secret optional/silent | Add startup warning; extend signature to cover authorization refs |
| 4 | Finding 4 — Budget TOCTOU race | Use atomic SQL increment + constraint check |
| 5 | Finding 5 — Workspace queue in-memory | Persist queue to store; fix `_process_queue` lock scope |
| 6 | Finding 6 — Delegation state in-memory | Persist `DelegationRecord` including `approval_delegation_policy` |
| 7 | Finding 7 — HMAC covers partial fields | Extend message to canonical JSON of all security-relevant fields |
| 8 | Finding 8 — Prestate artifact trust tier | Use `trust_tier="system"` for kernel-internal prestate artifacts |
| 9 | Finding 10 — No TTL cap on lease extend | Add `MAX_EXTENSION_TTL_SECONDS` and absolute lifetime cap |
| 10 | Finding 11 — `approve_batch` full scan | Add `batch_id` index or dedicated batch table |

---

## Files Reviewed

- `/Users/beta/work/Hermit/src/hermit/kernel/verification/receipts/receipts.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/approvals/approvals.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/guards/rules_budget.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/policy/permits/authorization_plans.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/executor/executor.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/authority/workspaces/service.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/dag_builder.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/dag_execution.py` (supporting)
- `/Users/beta/work/Hermit/src/hermit/kernel/execution/recovery/replay.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/context/memory/knowledge.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/artifacts/blackboard.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/events/store_ledger.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/ledger/journal/store_tasks.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/models/delegation.py`
- `/Users/beta/work/Hermit/src/hermit/kernel/task/services/delegation.py`
