---
description: "How Hermit governs agent execution: policy evaluation, approval workflows, scoped capability grants, workspace leases, and decision recording."
---

# Governance

Hermit treats governance as an execution primitive, not as a UI decoration.

The model may propose an action. It does not automatically get authority to perform it.

For consequential work, the kernel evaluates policy, records decisions, requests approval when required, issues a scoped capability grant, and only then dispatches the executor.

This is the core law:

**models reason; the kernel authorizes and executes**

That law is what separates Hermit from agent runtimes that rely on ambient process authority and post-hoc logs.

## Why Governance Lives in the Kernel

In many systems, approval is treated as a front-end feature. Hermit is moving in the opposite direction.

Hermit wants governance to survive:

- chat turns
- retries
- pauses
- resumptions
- service restarts
- operator inspection

That requires governance objects to be durable, local, and task-scoped.

## Current Governance Objects

The current repository already contains records for:

- `Decision`
- `Approval`
- `CapabilityGrant`
- `WorkspaceLease`
- `PolicyDecision`
- `PolicyReason`
- `PolicyObligations`
- `ActionRequest`
- `AuthorizationPlan`
- `TrustScore`
- `RiskAdjustment`

These work alongside task and step-attempt records so authority is attached to concrete execution context.

## Execution Law

The governed execution path is:

`action request -> policy evaluation -> decision -> approval if required -> workspace lease -> scoped capability grant -> execution -> receipt`

Important implications:

- the model is not the final authority
- effectful execution is not just "tool call allowed"
- approvals are durable records, not transient prompts
- authority should be scoped to the concrete action and resource envelope

## Policy Evaluation

Hermit's policy layer classifies actions and determines whether they are:

- allowed (`allow`)
- allowed with a receipt obligation (`allow_with_receipt`)
- blocked pending approval (`approval_required`)
- blocked pending preview (`preview_required`)
- denied (`deny`)

These verdicts are defined in the `Verdict` enum (`src/hermit/kernel/policy/models/enums.py`).

### PolicyEngine

`PolicyEngine` (`src/hermit/kernel/policy/evaluators/engine.py`) is the single entry point for policy evaluation. It performs three steps:

1. **Infer action class** from the `ToolSpec` (or accept an `ActionRequest` directly).
2. **Derive observables** from tool input (target paths, network hosts, command flags, sensitive-path detection) via `derive_request()`.
3. **Evaluate the guard dispatch chain**, merge outcomes, and attach an approval packet with a fingerprint when approval is required.

The engine returns a `PolicyDecision` that the executor uses to determine whether to proceed, block, or deny.

### ActionRequest

`ActionRequest` (`src/hermit/kernel/policy/models/models.py`) is the structured request object that travels through the policy pipeline. It carries:

- `request_id`, `idempotency_key` -- deduplication identity
- `task_id`, `step_id`, `step_attempt_id` -- execution context
- `tool_name`, `tool_input` -- what tool and with what arguments
- `action_class` -- the classified action type (see Action Classes below)
- `resource_scopes` -- where the action targets (`task_workspace`, `repo`, `home`, `system`, `network`, `remote_service`, `memory_store`)
- `risk_hint` -- default risk level from the tool spec or execution contract
- `idempotent`, `requires_receipt`, `supports_preview` -- tool-declared properties
- `actor` -- the principal requesting the action
- `context` -- runtime context including `policy_profile`, `workspace_root`, `policy_suggestion`, `task_pattern`, `delegation_scope`
- `derived` -- observables computed by derivation (target paths, command flags, network hosts, sensitive paths, outside-workspace status, kernel paths)

### PolicyDecision

`PolicyDecision` (`src/hermit/kernel/policy/models/models.py`) is the output of the policy engine. It contains:

- `verdict` -- one of the `Verdict` values
- `action_class` -- the (possibly overridden) action class
- `reasons` -- list of `PolicyReason` records explaining the verdict
- `obligations` -- `PolicyObligations` struct with boolean flags: `require_receipt`, `require_preview`, `require_approval`, `require_evidence`, and `approval_risk_level`
- `normalized_constraints` -- additional constraint data (e.g. `kernel_paths`)
- `approval_packet` -- when approval is required, this dict carries title, summary, risk_level, resource_scopes, and a SHA-256 fingerprint of the action
- `risk_level` -- the final risk band (`low`, `medium`, `high`, `critical`)

### PolicyReason

Each reason in a `PolicyDecision` is a `PolicyReason` with:

- `code` -- machine-readable identifier (e.g. `readonly_profile`, `autonomous_auto_approve`, `kernel_self_modification`)
- `message` -- human-readable explanation
- `severity` -- `info`, `warning`, or `error`

### PolicyObligations

The obligations struct determines what the executor must do alongside or after the action:

- `require_receipt` -- the action must produce a durable receipt
- `require_preview` -- the action must be previewed before execution
- `require_approval` -- the action is blocked until an operator approves
- `require_evidence` -- the action requires evidence references
- `approval_risk_level` -- the risk level to display in the approval prompt

### Action Classes

The `ActionClass` enum (`src/hermit/kernel/policy/models/enums.py`) defines 21 canonical action classifications:

**Read-only:**
- `read_local` -- local file reads
- `network_read` -- outbound read-only network access
- `execute_command_readonly` -- read-only shell commands

**Local writes:**
- `write_local` -- local file writes
- `patch_file` -- file patches / edits
- `memory_write` -- memory store writes

**Process / shell execution:**
- `execute_command` -- shell commands with side effects

**Network / external mutations:**
- `network_write` -- outbound network writes
- `credentialed_api_call` -- API calls using stored credentials
- `external_mutation` -- mutations to external systems

**VCS / publishing:**
- `vcs_mutation` -- git push, branch deletion, force operations
- `publication` -- publishing to registries or public endpoints

**Orchestration / lifecycle:**
- `delegate_execution` -- spawning sub-tasks or delegating work
- `delegate_reasoning` -- delegating reasoning to sub-agents
- `scheduler_mutation` -- creating, updating, or deleting scheduled jobs
- `approval_resolution` -- resolving an approval (approve/deny)
- `rollback` -- rolling back a previous action

**UI / attachments / infrastructure:**
- `ephemeral_ui_mutation` -- transient UI state changes
- `attachment_ingest` -- ingesting file attachments
- `patrol_execution` -- background patrol operations

**Fallback:**
- `unknown` -- unclassified actions (defaults to approval required)

### Policy Profiles

Policy evaluation behavior changes based on the active profile. Four profiles are supported, ordered from most permissive to most restrictive:

| Profile | Ordinal | Behavior |
|---|---|---|
| `autonomous` | 0 | Receipts preserved, approvals skipped. Dangerous operations (sudo, curl-pipe-sh, sensitive-path writes outside workspace, kernel self-modification) are still denied or require approval. |
| `default` | 1 | Full guard dispatch chain runs. Unclassified mutable actions default to approval required. |
| `supervised` | 2 | Same as default with stricter evaluation. |
| `readonly` | 3 | All non-`read_local` actions are denied. |

> **Warning:** The `autonomous` profile is designed for trusted, well-understood tasks. It still blocks known-dangerous patterns, but most mutable operations proceed without operator review. Use `default` or `supervised` for tasks with uncertain scope or untrusted inputs.

Child tasks must not exceed their parent's strictness ordinal (a child of a `supervised` parent cannot run as `autonomous`).

### Guard Dispatch Chain

When the profile is not `autonomous` or `readonly`, the policy engine dispatches the action request through a chain of guard evaluators. The first evaluator that returns a result wins:

1. **`evaluate_readonly_rules`** -- handles read-only action classes
2. **`evaluate_filesystem_rules`** -- handles file writes, patches, sensitive paths, outside-workspace writes, kernel self-modification
3. **`evaluate_shell_rules`** -- handles shell command execution, dangerous patterns (sudo, curl-pipe-sh, git push)
4. **`evaluate_network_rules`** -- handles network writes, credentialed API calls
5. **`evaluate_attachment_rules`** -- handles attachment ingestion
6. **`evaluate_planning_rules`** -- handles plan-mode enforcement (blocks writes when planning is required but no plan is selected)
7. **`evaluate_governance_rules`** -- handles approval resolution, scheduler mutation, delegation, rollback

If no evaluator matches, the default is `approval_required` with `high` risk.

After the primary evaluator returns, two post-evaluation adjustments run:

- **`apply_policy_suggestion`** -- if the request carries a `policy_suggestion` context (template-confidence based), approval may be downgraded to `allow_with_receipt` or the risk level adjusted. Critical risk is never skipped.
- **`apply_task_pattern`** -- if the request carries a `task_pattern` context with high confidence (>= 85% success rate, >= 3 invocations), risk may be downgraded by one level for approval-required verdicts.

### Outcome Merge

Multiple guard outcomes are merged via priority ordering: `deny` > `approval_required` > `preview_required` > `allow_with_receipt` > `allow`. The highest-priority verdict wins. Obligations are unioned across all outcomes (if any outcome requires a receipt, the final decision requires a receipt). The merged result becomes the `PolicyDecision`.

## Decisions

A `Decision` records a consequential judgment. The `DecisionService` (`src/hermit/kernel/policy/approvals/decisions.py`) creates decision records scoped to task, step, and step-attempt context.

A decision retains:

- `decision_type` -- what kind of judgment (e.g. `approval_resolution`, `policy_evaluation`)
- `verdict` -- the resolved outcome
- `reason` -- human-readable explanation
- `evidence_refs` -- references to evidence artifacts
- `policy_ref` -- the policy version or result that governed the decision
- `approval_ref` -- linked approval record
- `contract_ref` -- linked execution contract
- `authorization_plan_ref` -- linked authorization plan
- `evidence_case_ref` -- linked evidence case
- `reconciliation_ref` -- linked reconciliation record
- `action_type` -- the action class that triggered the decision
- `decided_by` -- the principal or system that made the decision

This matters because "why did it happen?" should not require reverse-engineering raw logs.

## Approvals

Approvals in Hermit are first-class execution records.

They are attached to task and step-attempt context and can be inspected later. The `ApprovalService` (`src/hermit/kernel/policy/approvals/approvals.py`) manages the full approval lifecycle: request, approve, deny, batch operations, and delegation-aware resolution.

Operator actions include:

```bash
hermit task approve <approval_id>
hermit task deny <approval_id> --reason "not safe"
hermit task resume <approval_id>
```

Approval resolution is itself a governed action. When an approval is resolved, the `ApprovalService` issues a full chain of governance records: a `Decision`, a `CapabilityGrant`, and a `Receipt` for the resolution action itself. The resolution receipt is attached back to the approval's resolution dict.

### Approval Timeout and Escalation

The `ApprovalTimeoutService` runs as a background check for expired approvals. Approvals carry an optional `drift_expiry` timestamp. When an approval exceeds its expiry:

1. If escalation is enabled, an `approval.escalation_needed` event is emitted
2. The approval is auto-denied with reason `approval_timeout`
3. An `approval.timed_out` event is emitted

### Delegation-Aware Approvals

When a child task needs approval, the `ApprovalService.request_with_delegation_check()` method consults the parent's delegation policy. If the parent allows auto-approval for the action class, the approval is immediately granted by `delegation_policy`. If the parent denies the action class, the approval is immediately denied.

### Batch Approvals

Multiple correlated approvals (e.g. parallel steps in the same task) can be created with `request_batch()` and resolved together with `approve_batch()`.

### Approval Copy

The `ApprovalCopyService` (`src/hermit/kernel/policy/approvals/approval_copy.py`) renders user-facing approval prompts from structured action facts. It produces `ApprovalCopy` records with title, summary, detail, and structured sections.

The service supports three copy sources in priority order:

1. **display_copy** from the requested action (explicit override)
2. **Optional formatter hook** (future LLM-based copy generation, with a 500ms timeout and deterministic fallback)
3. **Template-based copy** (deterministic, i18n-aware templates covering file writes, shell commands, git push, network access, sensitive paths, scheduler operations, and fallback)

Approval copy is not just a UI concern. The structured format enables consistent operator experience across CLI, MCP, and adapter surfaces.

Approval is not just permission. It is part of the durable execution story.

## Scoped Authority

Hermit aims for scoped authority rather than ambient authority.

The current implementation already uses:

- **capability grants** for scoped action authorization
- **workspace leases** for mutable or scoped workspace authority
- **authorization plans** for preflight verification of authority sufficiency

This is the practical answer to "what authority allowed it?"

### Workspace Leases

A workspace lease grants a task temporary authority over a workspace directory. Leases are issued as part of the governed execution path and scoped to a specific task and step attempt.

#### Security Properties

Workspace leases enforce four security properties:

- **Mutual exclusion** -- only one task holds a mutable lease on a given workspace at a time, preventing race conditions between concurrent tasks writing to the same directory.
- **TTL expiry** -- every lease carries a time-to-live. This prevents indefinite resource locking; a task cannot hold a workspace hostage by stalling or running longer than expected.
- **Orphan reaping** -- a background sweep detects and reclaims stale leases left behind by crashed or abnormally terminated tasks. This defends against resource leaks from ungraceful exits.
- **Queue-based fairness** -- when a workspace is already leased, subsequent requests are queued rather than rejected. This prevents starvation and ensures waiting tasks eventually acquire the lease.

### Authorization Plans

The `AuthorizationPlanService` (`src/hermit/kernel/policy/permits/authorization_plans.py`) creates authorization plans during the preflight phase of governed execution. An authorization plan records:

- the requested action classes and their approval route (`operator` or `none`)
- the proposed grant shape (action class + resource scope)
- the proposed workspace lease shape (mutable or readonly)
- witness requirements (state witness checks)
- revalidation rules (policy version checks, approval checks, witness checks)
- downgrade options (`gather_more_evidence`, `reduce_scope`, `request_authority`)
- current gaps (e.g. `policy_denied`, awaiting approval)
- expiry constraints and estimated authority cost

Authorization plans transition through statuses: `preflighted` (ready to proceed), `awaiting_approval` (blocked on operator), `blocked` (policy denied), `invalidated` (revalidation failed).

The service supports plan invalidation (when preconditions change) and revalidation (checking whether policy versions have drifted since the plan was created). When policy drift is detected, the plan is invalidated and must be re-created.

Each authorization plan is stored as an artifact with `retention_class="audit"` and linked to the step attempt via `authorization_plan_ref`.

The point is not perfect least privilege everywhere today. The point is that authority is becoming explicit, durable, and inspectable.

## Trust Scoring

Hermit computes trust scores from historical execution data to produce advisory risk adjustments.

### TrustScore

`TrustScore` (`src/hermit/kernel/policy/trust/models.py`) is computed per action class (or per principal) from receipt history. The composite score formula is:

```
composite = 0.5 * success_rate + 0.3 * (1 - rollback_rate) + 0.2 * avg_reconciliation_confidence
```

A score requires at least 5 executions before it is produced. The `TrustScorer` (`src/hermit/kernel/policy/trust/scoring.py`) queries receipts and reconciliations from the kernel store to compute the score.

### RiskAdjustment

`RiskAdjustment` (`src/hermit/kernel/policy/trust/models.py`) is an advisory record suggesting a change in risk band based on a trust score. Risk bands map to composite score ranges:

| Composite Score | Suggested Band |
|---|---|
| >= 0.85 | `low` |
| >= 0.65 | `medium` |
| >= 0.40 | `high` |
| < 0.40 | `critical` |

Risk adjustments are logged as `trust.risk_adjustment_suggested` decision events but are never auto-applied to policy evaluation. The operator retains full control over whether to act on the suggestion.

## Witness And Drift

Some actions should not simply resume after a pause without checking that the execution preconditions still hold.

This is why the kernel direction includes:

- state witness references
- drift detection
- supersession instead of silent continuation

This is especially important for delayed approvals and write-like actions.

## What Hermit Ships Today

Safe claims:

- `PolicyEngine` with full guard dispatch chain is in the executor path
- `ActionRequest` derivation extracts target paths, network hosts, command flags, sensitive paths, and kernel paths from tool input
- 21 canonical action classes with `ActionClass` enum
- 4 policy profiles (`autonomous`, `default`, `supervised`, `readonly`) with strictness ordering
- approval objects, approval resolution, batch approvals, delegation-aware approvals, and approval timeout/escalation are implemented
- `PolicyDecision` with structured reasons, obligations, and approval packets is the standard policy output
- `AuthorizationPlanService` with preflight, invalidation, and revalidation is implemented
- `TrustScorer` computes trust scores and advisory risk adjustments from receipt history
- `ApprovalCopyService` renders user-facing approval prompts with i18n and structured sections
- decision, principal, capability grant, and workspace lease records exist
- the task CLI and MCP surface already expose governed execution to operators

Careful claims:

- governance is materially real in the codebase
- governance is not yet a fully settled public contract across every runtime surface

## Why This Matters

Governance is not there to slow the agent down for style points.

It is there because high-trust agent work needs durable answers to:

- who allowed this
- under what scope
- based on what evidence
- can we inspect it later
- can we recover if it was wrong

That is the category Hermit is trying to be unusually strong in.
