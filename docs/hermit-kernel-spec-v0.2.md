
# Hermit Kernel Spec v0.2

**Status:** Draft
**Last updated:** 2026-03-15

This document defines the **target kernel architecture** for the next major iteration of Hermit. It is a forward-looking specification built on the repository’s current v0.1 kernel trajectory. It is **not** a claim that every runtime surface in the current repository already ships the full v0.2 behavior.

Hermit v0.2 is not a reset of v0.1. It is the next hard cut:

- v0.1 made **governed execution** real.
- v0.2 makes **governed cognition** a kernel primitive.

The governing idea is:

> **Contract-first evidence-governed agency**

中文可表述为：

> **契约优先、证据治理型 agent intelligence**

The operational thesis is:

> Hermit v0.2 changes the optimization target of agent intelligence from
> “find an action sequence that can get something done”
> to
> “find and maintain a contract that remains valid under evidence, authority, drift, and reversibility constraints.”

The shortest law of the system is:

> **`receipt` closes side effects; `reconciliation` closes cognition.**

Read this document alongside:

- `architecture.md`
- `roadmap.md`
- `kernel-conformance-matrix-v0.1.md`
- `governance.md`
- `context-model.md`
- `memory-model.md`
- `receipts-and-proofs.md`

---

## 1. Design Position

Hermit Kernel v0.2 is defined as:

> A **local-first, event-backed, contract-first governed agent kernel** where durable work advances through recoverable step attempts; context is compiled from artifacts, bounded working state, evidence-backed beliefs, and governed memory; models propose **contracts**, not raw execution authority; the kernel admits, authorizes, executes, receipts, reconciles, and only then learns.

Hermit v0.2 preserves the core strengths of v0.1:

- durable `Task` / `Step` / `StepAttempt` boundaries
- append-only event-backed truth
- artifact-native context
- policy / approval / decision / scoped capability execution
- receipts, proofs, observation, and rollback-aware recovery

But v0.2 changes what those primitives are **for**.

In v0.1, they primarily governed execution.
In v0.2, they also govern:

- what plans are admissible
- what evidence is strong enough to support a contract
- what authority gaps are visible before execution
- when drift invalidates an old plan
- what outcomes are allowed to become durable knowledge

Hermit remains intentionally narrow. It is not trying to be the best generic agent runtime. It is trying to be unusually strong for:

- local-first work
- long-running tasks
- approval-sensitive operations
- durable stateful workflows
- evidence-bearing context
- inspectable, recoverable, explainable execution

---

## 2. Core Law and Architectural Thesis

### 2.1 v0.1 core law

**models reason; the kernel authorizes and executes**

### 2.2 v0.2 core law

**models propose contracts; the kernel admits, authorizes, executes, receipts, reconciles, and only reconciled outcomes may become durable knowledge**

This changes the meaning of the main runtime objects:

- `ActionRequest` is no longer the highest execution object in the cognitive loop.
- `Receipt` is no longer sufficient as the terminal closure of a consequential attempt.
- `ReconciliationRecord` becomes the gate that closes the cognitive loop.
- `Belief` revision, `MemoryRecord` promotion, and `ContractTemplate` learning must now depend on reconciliation outcome.

### 2.3 Consequence for the loop

A conformant v0.2 kernel treats the main loop as:

```text
observe
-> compile evidence
-> synthesize contracts
-> preflight authorization
-> execute
-> receipt
-> reconcile
-> learn
```

A concise external rendering is:

```text
observe -> contract -> authorize -> execute -> receipt -> reconcile -> learn
```

### 2.4 The v0.2 hard cut

The v0.2 hard cut is not “the planner becomes more powerful.”
The hard cut is that the kernel now optimizes for **contract validity** under four constraints:

1. evidence sufficiency
2. authority satisfiability
3. drift tolerance
4. reversibility

That is the shift from governed execution to governed cognition.

---

## 3. Goals

Hermit Kernel v0.2 has the following primary goals:

1. Preserve `Task` as the durable unit of work.
2. Preserve append-only `Event` streams as the durable unit of truth.
3. Preserve `StepAttempt` as the primary recovery boundary.
4. Preserve `Artifact` as the default unit of context, lineage, and evidence.
5. Upgrade planning from action-search to **contract-search**.
6. Require an `ExecutionContract` before consequential execution.
7. Require `EvidenceCase` sufficiency before admitting a consequential contract.
8. Make authority reasoning planner-visible through `AuthorizationPlan`.
9. Make drift invalidate or supersede stale contracts instead of silently reusing them.
10. Keep `Receipt` as the closure object for side effects.
11. Introduce `ReconciliationRecord` as the closure object for contract validity.
12. Prevent durable learning from ambiguous, violated, or unreconciled outcomes.
13. Promote governed memory and contract templates instead of hidden prompt accumulation.
14. Make approval surfaces evaluate **contract packets**, not bare action requests.
15. Preserve compatibility with the repository’s current task, governance, receipt, proof, and rollback surfaces.

---

## 4. Non-Goals

This spec does **not** require:

- distributed consensus or multi-machine coordination
- a stable public API surface
- universal autonomous execution
- perfect rollback for every action class
- byte-identical replay for every attempt
- replacement of `Decision`, `Approval`, `CapabilityGrant`, `WorkspaceLease`, `Receipt`, or `RollbackRecord`
- removal of `ActionRequest` as a typed proposal object
- a claim that every current runtime surface already behaves like v0.2
- a giant multi-tenant control plane
- transcript elimination everywhere in the repo

v0.2 prioritizes kernel semantics, not platform breadth.

---

## 5. Normative Language

The keywords `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are to be interpreted in RFC 2119 style usage.

---

## 6. Kernel Invariants

The following are hard constraints for v0.2.

### 6.1 Durable state invariants

1. **No direct durable mutation.** Every durable state change `MUST` be recorded as one or more events before projections are updated.
2. **No orphan durable objects.** Every durable object `MUST` belong to a task and `MUST` be attributable to a principal.
3. **No silent overwrite of revisable knowledge.** `Belief`, `MemoryRecord`, and learned `ContractTemplate` changes `MUST` be versioned, invalidated, or superseding.
4. **No silent contract swap.** Replacing an active `ExecutionContract` `MUST` emit a durable supersession transition.

### 6.2 Contract and authority invariants

1. **No consequential action without contract.** Any consequential execution path `MUST` bind to an `ExecutionContract` before entering authorization or execution.
2. **No contract without evidence sufficiency.** A consequential `ExecutionContract` `MUST` reference an `EvidenceCase` whose status is sufficient for the contract’s claim.
3. **No authority reasoning hidden behind execution.** Planner-visible authority feasibility `MUST` exist as an `AuthorizationPlan` or equivalent durable object.
4. **No ambient authority.** Executors `MUST NOT` rely on broad process authority alone for effectful work.
5. **No irreversible action without decision.** Destructive, publish-like, external, credentialed, payment, policy-override, or similarly consequential actions `MUST` have a durable `Decision` before execution.
6. **No delayed high-risk action without revalidation.** If approval or execution is delayed for a contract-sensitive action, the kernel `MUST` revalidate witness and freshness constraints before execution.
7. **No stale contract reuse.** Input drift, approval drift, witness drift, evidence drift, or contract expiry `MUST` cause re-admission, re-authorization, or contract supersession.

### 6.3 Knowledge and learning invariants

1. **No durable learning without reconciliation.** Outcomes `MUST NOT` be promoted into durable memory or learned templates until reconciliation is complete.
2. **No terminal success before reconciliation for contract-sensitive attempts.** A consequential attempt `MUST NOT` transition to terminal success solely because a receipt exists.
3. **No bare memory in model context.** Retrieved memory re-entering model context `MUST` carry scope, freshness, evidence, and supersession metadata.
4. **No durable memory promotion without evidence and reconciliation.** Cross-task memory promotion is an important action and `MUST` be both receipted and reconciled.
5. **No contradiction without downgrade.** If new evidence contradicts an active belief or memory, dependent contracts `MUST` degrade, re-enter admission, or be superseded.

### 6.4 Audit, recovery, and drift invariants

1. **Every important action produces a receipt.** If no receipt exists, the action is not durably complete.
2. **No silent replay of uncertain side effects.** Unknown outcomes `MUST` re-enter observation and reconciliation before replay.
3. **Every task is replayable or explainable.** The kernel `MUST` retain enough information to replay inputs, observe outputs, or reconstruct evidence, authority, and contract chains.
4. **Receipt closes side effects; reconciliation closes cognition.** Both objects are required for full closure of consequential work.
5. **Blocked on ambiguity is a legal state.** The kernel `MUST NOT` pretend autonomy still exists when evidence, authority, or drift constraints are unresolved.

---

## 7. Consequential Contracts and Important Actions

### 7.1 Important actions

The following action classes are important by default and `MUST` produce receipts unless explicitly downgraded by policy into a non-effectful equivalent:

- local write
- local delete
- file patch
- command execution
- VCS mutation
- network write
- credentialed API call
- publication
- external mutation
- durable memory promotion
- policy override
- rollback execution
- approval resolution for consequential actions

An implementation `MAY` express these through canonical action classes such as:

- `write_local`
- `patch_file`
- `execute_command`
- `vcs_mutation`
- `network_write`
- `credentialed_api_call`
- `publication`
- `external_mutation`
- `memory_write`
- `rollback`
- `approval_resolution`

Readonly classes such as local read, network read, or delegate-only reasoning are not consequential by default, but policy profiles `MAY` tighten them.

### 7.2 Consequential contract

A **consequential contract** is any `ExecutionContract` that:

- includes one or more important action classes, or
- requires a decision, approval, capability grant, or mutable workspace lease, or
- can alter durable memory, policy state, external state, or rollback state

Consequential contracts are subject to:

- evidence sufficiency
- authorization preflight
- receipt requirements
- reconciliation before terminal success
- durable-learning gating

### 7.3 Contract-sensitive attempt

A **contract-sensitive attempt** is a `StepAttempt` whose selected contract:

- has `required_receipt_classes` not empty, or
- has a non-`none` reconcile strategy, or
- can produce durable memory promotion, policy override, rollback, or external mutation

A contract-sensitive attempt `MUST NOT` terminate as successful before reconciliation.

---

## 8. First-Class Objects

Hermit v0.2 retains the durable object model of v0.1 and adds new contract-first objects. Unless modified below, v0.1 semantics carry forward.

### 8.1 Task

A `Task` remains the durable entrypoint for meaningful work.

It still represents:

- explicit goal
- lifecycle state
- owner
- priority
- policy profile
- durable task boundary
- step graph boundary

`task_contract_ref` remains the task-level logical boundary. v0.2 does not replace task-first execution.

### 8.2 IngressRecord and continuation anchors

`IngressRecord` remains the durable record for inbound free-form input before it mutates task state.

Continuation anchors remain required for follow-up work that references terminal task outcomes. v0.2 adds one new principle:

- a continuation that materially changes evidence, authority assumptions, or requested effect `SHOULD` normally create a new attempt or a new child task instead of mutating an already admitted contract.

### 8.3 Step

A `Step` remains the smallest logical recoverable unit within a task.

`Step.contract_ref` remains the **logical** contract boundary for the step.
It defines the intent and success shape of the work unit.

In v0.2:

- a `Step` may have multiple **candidate** `ExecutionContract`s over time
- those contracts may be admitted, rejected, superseded, downgraded, or abandoned across attempts
- the logical step contract and the selected attempt-scoped execution contract are intentionally distinct

### 8.4 StepAttempt

A `StepAttempt` remains the concrete execution instance of a step and the primary recovery boundary.

v0.2 adds the following recommended fields:

- `execution_contract_ref`
- `evidence_case_ref`
- `authorization_plan_ref`
- `reconciliation_ref`
- `contract_version`
- `reentry_boundary`
- `reentry_reason`
- `selected_contract_template_ref`

Semantics:

- an attempt is the unit that receives policy results, decisions, approvals, grants, leases, receipts, and reconciliations
- approval pauses and resumptions remain attached to a specific attempt
- retries `MUST` create new attempt numbers
- newly bound task input remains task input delta first; it `MUST NOT` hot-patch a running executor
- drift across durable boundaries `MUST` cause re-admission, re-authorization, or supersession rather than in-memory mutation

### 8.5 Event

An `Event` remains the durable source of truth.

v0.2 adds new event-bearing entities:

- `execution_contract`
- `evidence_case`
- `authorization_plan`
- `reconciliation`

Every admission, supersession, invalidation, reconciliation, and learning gate transition `MUST` be event-backed.

### 8.6 Artifact

An `Artifact` remains the canonical container for work products, observations, and evidence.

v0.2 adds or recommends the following kinds:

- `execution.contract`
- `evidence.case`
- `authorization.plan`
- `contract.packet`
- `reconciliation.record`
- `reconciliation.summary`
- `contract.template`
- `admission.report`

Artifacts cited by contracts, approvals, receipts, or reconciliations `SHOULD` be sealed or hash-locked.

### 8.7 Belief

A `Belief` remains revisable working truth.

v0.2 extends `Belief` with epistemic metadata.

Recommended additional fields:

- `epistemic_origin`
  values: `observed | inferred | retrieved | operator_asserted | policy_asserted`
- `freshness_class`
- `evidence_case_ref`
- `last_validated_at`
- `validation_basis`
- `supersession_reason`

A belief used to support a consequential contract `SHOULD` reference an `EvidenceCase`, not only loose `evidence_refs`.

### 8.8 MemoryRecord

A `MemoryRecord` remains durable knowledge expected to survive task boundaries.

v0.2 extends `MemoryRecord` with:

- `memory_kind`
- `validation_basis`
- `last_validated_at`
- `supersession_reason`
- `learned_from_reconciliation_ref`

Durable memory remains governed knowledge. It is not hidden prompt authority.

### 8.8.1 ContractTemplate subtype

v0.2 does **not** require a fifth new first-class top-level learning object. Instead, contract learning is represented as:

- `MemoryRecord.memory_kind = contract_template`

A `ContractTemplate` stores learned knowledge about when a contract shape is valid.

Recommended fields:

- `goal_pattern`
- `required_evidence_shape`
- `required_authority_shape`
- `risk_band`
- `reversibility_requirements`
- `common_failure_modes`
- `downgrade_paths`
- `escalation_conditions`
- `success_rate`
- `rollback_rate`
- `last_used_at`
- `learned_from_reconciliation_refs`

A `ContractTemplate` is only eligible for durable promotion from reconciled outcomes.

### 8.9 ExecutionContract

An `ExecutionContract` is an **attempt-scoped dynamic contract** describing what the attempt is trying to achieve, under what evidence and authority assumptions, with what drift and reversibility requirements.

Minimum fields:

- `contract_id`
- `task_id`
- `step_id`
- `attempt_id`
- `objective`
- `proposed_action_refs[]`
- `expected_effects[]`
- `success_criteria`
- `evidence_case_ref`
- `authorization_plan_ref`
- `reversibility_class`
- `required_receipt_classes[]`
- `drift_budget`
- `expiry_at`
- `status`
- `created_at`

Recommended fields:

- `fallback_contract_refs[]`
- `operator_summary`
- `risk_budget`
- `expected_artifact_shape`
- `contract_version`
- `action_contract_refs[]`
- `state_witness_ref`
- `rollback_expectation`
- `selected_template_ref`

Suggested statuses:

- `draft`
- `admissibility_pending`
- `approval_pending`
- `authorized`
- `executing`
- `satisfied`
- `partially_satisfied`
- `violated`
- `expired`
- `superseded`
- `abandoned`

Semantics:

- `Step.contract_ref` remains the logical work boundary
- `ExecutionContract` is the concrete commitment for this attempt
- a consequential attempt `MUST` select one active contract before execution
- an `ExecutionContract` `MUST` compose with one or more action-class `ActionContract`s or equivalent action metadata
- if input drift, approval drift, evidence drift, or witness drift occurs, the old contract `MUST NOT` be silently reused

### 8.10 EvidenceCase

An `EvidenceCase` is a structured evidence dossier for belief use, contract admission, or memory promotion.

Minimum fields:

- `evidence_case_id`
- `task_id`
- `subject_kind`
  values: `belief | contract | memory | rollback`
- `subject_ref`
- `support_refs[]`
- `contradiction_refs[]`
- `freshness_window`
- `sufficiency_score`
- `drift_sensitivity`
- `unresolved_gaps[]`
- `status`
- `created_at`

Recommended fields:

- `witness_refs[]`
- `invalidates_refs[]`
- `last_checked_at`
- `confidence_interval`
- `freshness_basis`
- `operator_summary`

Suggested statuses:

- `sufficient`
- `insufficient`
- `stale`
- `contradicted`
- `expired`
- `superseded`

Semantics:

- an `EvidenceCase` is not just a bag of `evidence_refs`
- the kernel uses it to judge whether a belief, contract, or memory promotion is supported enough to proceed
- contradiction or staleness in an active evidence case `MUST` degrade dependent contracts and beliefs

### 8.11 AuthorizationPlan

An `AuthorizationPlan` is a **planning-time authority reasoning object**.

It is not a replacement for `CapabilityGrant`.
It exists before grant issuance and answers whether the candidate contract can become authorized.

Minimum fields:

- `authorization_plan_id`
- `task_id`
- `step_id`
- `attempt_id`
- `contract_ref`
- `policy_profile_ref`
- `requested_action_classes[]`
- `required_decision_refs[]`
- `approval_route`
- `witness_requirements[]`
- `proposed_grant_shape`
- `downgrade_options[]`
- `current_gaps[]`
- `status`
- `created_at`

Recommended fields:

- `estimated_authority_cost`
- `expiry_constraints`
- `revalidation_rules`
- `operator_packet_ref`
- `required_workspace_mode`
- `required_secret_policy`
- `proposed_lease_shape`

Suggested statuses:

- `draft`
- `preflighted`
- `awaiting_approval`
- `authorized`
- `blocked`
- `invalidated`
- `expired`
- `superseded`

Semantics:

- `CapabilityGrant` is execution-time authority
- `AuthorizationPlan` is planning-time authority feasibility
- authority gaps must be visible to the planner before the executor path
- downgrade, reduce-scope, and escalation are first-class outputs of authorization planning

### 8.12 Decision

A `Decision` remains a consequential judgment object.

v0.2 adds recommended linkage fields:

- `contract_ref`
- `authorization_plan_ref`
- `evidence_case_ref`
- `reconciliation_ref` when the decision resolves uncertain outcome or rollback

Suggested additional decision classes:

- `contracting`
- `admission`
- `authority_escalation`
- `downgrade`
- `reconciliation_resolution`

### 8.13 Approval

An `Approval` remains a first-class execution object.

v0.2 changes the approval surface:

- the operator approves a **contract packet**, not just a bare action
- the packet `MUST` make evidence sufficiency, authority scope, drift expiry, and rollback visibility explicit

Minimum fields:

- `approval_id`
- `task_id`
- `step_id`
- `attempt_id`
- `status`
- `approval_type`
- `requested_contract_ref`
- `authorization_plan_ref`
- `approval_packet_ref`
- `requested_at`
- `resolved_at`
- `resolved_by`

Recommended fields:

- `requested_action_ref`
- `expires_at`
- `constraints_ref`
- `state_witness_ref`
- `policy_result_ref`
- `evidence_case_ref`
- `drift_expiry`
- `fallback_contract_refs[]`

Statuses remain:

- `pending`
- `granted`
- `denied`
- `expired`
- `cancelled`
- `invalidated`

### 8.14 CapabilityGrant

A `CapabilityGrant` remains the scoped authority record authorizing execution.

Recommended v0.2 additions:

- `contract_ref`
- `authorization_plan_ref`
- `revalidation_rule_ref`
- `decision_ref`
- `lease_ref`

Semantics:

- grants remain least-privilege by default
- grants are attempt-scoped
- grants `MUST` refuse execution outside contract-aligned scope
- an earlier approval does not rescue an expired or invalidated grant

### 8.15 WorkspaceLease

A `WorkspaceLease` remains the explicit record for mutable or scoped workspace authority.

Recommended fields:

- `lease_id`
- `task_id`
- `step_id`
- `attempt_id`
- `workspace_id`
- `root_path`
- `holder_principal_id`
- `acquired_at`
- `expires_at`
- `mode`
- `resource_scope`
- `status`

Suggested lease modes:

- `readonly`
- `mutable`
- `isolated`
- `external_effects_disabled`

Lease and grant scopes `SHOULD` align with the selected `ExecutionContract`.

### 8.16 Receipt

A `Receipt` remains the durable proof object for an important action.

v0.2 extends receipt linkage.

Minimum fields:

- `receipt_id`
- `task_id`
- `step_id`
- `attempt_id`
- `receipt_class`
- `contract_ref`
- `action_request_ref`
- `input_refs`
- `environment_ref`
- `policy_result_ref`
- `approval_ref`
- `capability_grant_ref`
- `output_refs`
- `result_code`
- `result_summary`
- `created_at`

Recommended fields:

- `authorization_plan_ref`
- `decision_ref`
- `workspace_lease_ref`
- `witness_ref`
- `rollback_ref`
- `replay_class`
- `verifiability`
- `signer_ref`
- `signature`
- `receipt_bundle_ref`
- `observed_effect_summary`
- `reconciliation_required`

Semantics:

- a receipt proves what happened and under what authority
- it does **not** by itself conclude that the contract remained valid
- for consequential work, receipts feed reconciliation

### 8.17 ReconciliationRecord

A `ReconciliationRecord` is the durable post-execution accounting object.

It compares:

- what the contract intended
- what authority allowed
- what was observed
- what receipts prove
- what the kernel is now allowed to learn

Minimum fields:

- `reconciliation_id`
- `task_id`
- `step_id`
- `attempt_id`
- `contract_ref`
- `receipt_refs[]`
- `observed_output_refs[]`
- `intended_effect_summary`
- `authorized_effect_summary`
- `observed_effect_summary`
- `receipted_effect_summary`
- `result_class`
- `confidence_delta`
- `recommended_resolution`
- `created_at`

Recommended fields:

- `rollback_recommendation_ref`
- `invalidated_belief_refs[]`
- `superseded_memory_refs[]`
- `promoted_template_ref`
- `promoted_memory_refs[]`
- `operator_summary`
- `final_state_witness_ref`

Suggested result classes:

- `satisfied`
- `satisfied_with_downgrade`
- `partial`
- `ambiguous`
- `violated`
- `drifted`
- `unauthorized`
- `rolled_back`

Semantics:

- `Receipt` closes side effects
- `ReconciliationRecord` closes the contract-validity judgment
- no durable learning occurs before reconciliation
- `violated`, `drifted`, `unauthorized`, and `ambiguous` results block automatic durable learning

### 8.18 RollbackRecord

A `RollbackRecord` remains the durable record for rollback execution.

In v0.2:

- rollback is itself a consequential contract
- rollback requires its own authorization path when needed
- rollback produces its own receipt
- final learning about rollback safety or success should occur through reconciliation

### 8.19 Principal

A `Principal` remains the attributable actor record.

Principal kinds continue to include user, agent, service, scheduler, webhook, policy engine, executor, supervisor, and system identities.

---

## 9. Object Relationships

The v0.2 object graph follows these rules:

1. A `Task` owns many `Step`s.
2. A `Step` owns many `StepAttempt`s.
3. A `Step` defines the logical contract boundary; an attempt selects an `ExecutionContract`.
4. An `ExecutionContract` references one active `EvidenceCase` and one active `AuthorizationPlan`.
5. `Decision`, `Approval`, `CapabilityGrant`, and `WorkspaceLease` attach to the attempt and, when applicable, to the selected contract.
6. `Receipt` attaches to the action actually executed and references the contract and authority chain.
7. `ReconciliationRecord` references one contract and one or more receipts.
8. `Belief` and `MemoryRecord` may cite evidence directly, but consequential use `SHOULD` route through `EvidenceCase`.
9. `ContractTemplate` is durable memory derived from reconciled outcomes, not from bare planner preference.
10. `RollbackRecord` attaches to the receipt or reconciliation it compensates for.
11. Session or chat history remains a projection; it is not the source of truth.
12. Approval, evidence, and reconciliation packets `SHOULD` be reconstructible from artifacts without transcript replay.

---

## 10. Layered Architecture

Hermit Kernel v0.2 is best understood as eight cooperating layers.

### 10.1 Control plane

Responsibilities:

- accept ingress
- create, resume, cancel, pause, reprioritize tasks
- publish events
- expose supervision and inspection interfaces

The control plane `MUST NOT` contain hidden execution authority.

### 10.2 Task and step orchestrator

Responsibilities:

- create steps from task contract boundaries
- maintain dependency ordering
- allocate attempts
- enforce conservative single-writer semantics by default
- coordinate supersession and retry

This layer selects work. It does not authorize side effects by itself.

### 10.3 Context and evidence compiler

Responsibilities:

- compile context packs from artifacts, working state, beliefs, memory, and recent deltas
- build or refresh evidence dossiers
- track freshness and contradiction
- produce state witnesses when needed

### 10.4 Contract synthesis and admission layer

Responsibilities:

- invoke models in propose-only mode
- synthesize candidate `ExecutionContract`s
- attach evidence cases
- run admissibility checks
- score and select candidates
- downgrade or escalate when no contract is admissible

This layer is the main v0.2 hard cut.

### 10.5 Authorization, approval, and capability layer

Required chain:

```text
candidate contract
-> authorization preflight
-> decision
-> approval if required
-> workspace lease
-> scoped capability grant
```

Responsibilities:

- classify requested action classes
- evaluate policy profile and risk
- return authorize, await approval, downgrade, block, or deny
- mint scoped grants
- enforce revalidation rules
- expose authority gaps to the planner

### 10.6 Execution and observation layer

Responsibilities:

- execute inside lease and grant scope
- checkpoint durable boundaries
- observe target systems after uncertain effects
- preserve recovery at attempt granularity

### 10.7 Receipt, reconciliation, and learning layer

Responsibilities:

- issue receipts
- compare intended / authorized / observed / receipted effects
- revise beliefs
- promote or invalidate memory
- learn contract templates only from reconciled outcomes

### 10.8 Supervision and proof surface

Responsibilities:

- inspect tasks, steps, attempts, contracts, evidence cases, authorization plans, approvals, grants, leases, receipts, reconciliations, beliefs, memory, rollbacks, and proof bundles
- export proof artifacts
- allow approve, deny, retry, cancel, rollback, and revoke-grant operations where supported
- explain what changed, why, with what authority, under which evidence, and whether it can be undone

This layer is part of the trust model, not optional observability.

---

## 11. Execution Lifecycle

### 11.1 Standard lifecycle

A conformant consequential execution path `SHOULD` proceed as follows:

1. An ingress channel creates or resumes a `Task`.
2. The orchestrator selects a ready `Step`.
3. The kernel creates a new `StepAttempt`.
4. The context compiler emits a `context.pack` artifact and refreshes relevant working state, beliefs, memory, and witnesses.
5. The evidence compiler assembles one or more candidate `EvidenceCase`s.
6. The model is invoked in propose-only mode with the compiled context.
7. The model may emit zero or more of:
   - belief assertions
   - draft deliverables
   - evidence-gathering proposals
   - candidate contracts
   - action requests attached to candidate contracts
8. Each consequential proposal is normalized into one or more `ExecutionContract`s.
9. For each candidate contract, the kernel compiles an `AuthorizationPlan`.
10. The kernel runs contract admissibility checks.
11. If no contract is admissible, the kernel `MUST` choose one of:
   - `gather_more_evidence`
   - `reduce_scope`
   - `downgrade_action`
   - `request_authority`
   - `park_and_escalate`
12. If an admissible contract requires approval, the kernel creates a `Decision`, emits an approval packet, and pauses the same attempt.
13. On grant, the kernel revalidates witness state, freshness, policy assumptions, and contract expiry.
14. If still valid, the kernel issues a scoped `CapabilityGrant` and any required `WorkspaceLease`.
15. The executor runs inside the selected contract, lease, and grant scope.
16. Outputs and observations are captured as artifacts.
17. One or more receipts are issued.
18. The attempt enters reconciliation.
19. The kernel emits a `ReconciliationRecord`.
20. Only reconciled outcomes may update durable memory or learned contract templates.
21. Step and task projections are updated from events.

### 11.2 Model authority boundary

The model `MAY` reason, score, propose, revise, or abandon candidate contracts.
The model `MUST NOT` directly execute tools, shell commands, filesystem writes, or network writes.

The kernel interprets model output through typed boundaries.

### 11.3 ActionRequest and ActionContract

`ActionRequest` remains the typed execution proposal object generated from model output or a non-model source.

Suggested structure:

```text
ActionRequest {
  action_request_id
  task_id
  step_id
  attempt_id
  action_class
  target_resources[]
  params_ref
  expected_effects[]
  reversibility_hint
  reason
  proposed_by
  proposed_at
}
```

An `ActionRequest` for consequential execution `MUST` belong to a candidate `ExecutionContract`.

An `ActionContract` is the static action-class contract catalog used to express default execution semantics such as:

- default risk band
- decision requirement
- witness requirement
- receipt requirement
- reconcile strategy
- rollback strategy

An `ActionContract` is action-class metadata.
An `ExecutionContract` is the attempt-scoped commitment that instantiates those rules under current evidence and authority.

### 11.4 Contract synthesis and scoring

The kernel `SHOULD` score candidate contracts on at least four dimensions:

1. task value
2. evidence sufficiency
3. authority satisfiability
4. reversibility and drift exposure

A recommended heuristic is:

```text
score(contract)
= expected_task_value
- authority_cost
- evidence_deficit_penalty
- drift_exposure_penalty
- irreversibility_penalty
+ rollback_coverage_bonus
```

The planner `SHOULD` prefer a smaller admissible contract over a larger in-principle useful but poorly supported contract.

### 11.5 Contract admissibility

A consequential `ExecutionContract` is **admissible** only when all of the following hold:

- `EvidenceCase.status == sufficient`
- `AuthorizationPlan.status in {preflighted, awaiting_approval, authorized}`
- the `drift_budget` satisfies the minimum requirement for the action class
- the `reversibility_class` satisfies the current risk band or policy profile
- no active contradiction or expiry invalidates the evidence case, witness, or approval assumptions

### 11.6 No admissible contract

If no contract is admissible, the kernel `MUST NOT` pretend that autonomy still exists.

It `MUST` choose one of:

- gather more evidence
- reduce scope
- downgrade to a safer action
- request more authority
- park and escalate

This is a defining v0.2 behavior.

### 11.7 Block and resume

Approval and external wait states `MUST NOT` force a full task restart.

The same `StepAttempt` `SHOULD` resume whenever its checkpointed inputs, evidence, and witness assumptions remain valid.

If any of the following drift past the selected contract’s allowance:

- input
- approval packet
- policy assumptions
- evidence freshness
- witness state
- contract expiry

the kernel `MUST` either:

- re-enter admissibility and authorization, or
- supersede the contract and/or attempt

### 11.8 Learning gate

A conformant v0.2 kernel `MUST` treat learning as a gated phase.

Allowed by default:

- working-state updates from any observed attempt
- belief revisions from observed evidence

Blocked until reconciliation:

- durable memory promotion
- contract template promotion
- autonomous reuse of a successful contract shape across tasks

---

## 12. State Machines

### 12.1 Task state machine

Minimum task states remain:

- `created`
- `ready`
- `running`
- `blocked`
- `paused`
- `completed`
- `failed`
- `cancelled`
- `rolled_back`

A task may remain `blocked` due to evidence insufficiency, approval wait, drift invalidation, or reconciliation-required resolution.

### 12.2 Step state machine

Minimum step states remain:

- `planned`
- `ready`
- `running`
- `blocked`
- `succeeded`
- `failed`
- `cancelled`
- `superseded`

A step’s effective state is derived from orchestration state plus the latest relevant attempt and reconciliation result.

### 12.3 StepAttempt state machine

Minimum attempt states for v0.2:

```text
created
-> compiling_context
-> reasoning
-> contracting
-> preflighting
-> awaiting_approval?
-> authorized
-> executing
-> observing
-> receipt_pending
-> reconciling
-> succeeded | failed | superseded | rolled_back | cancelled
```

Rules:

- each new attempt increments `attempt_no`
- each transition `MUST` emit events
- approval pauses apply at attempt granularity
- observation after uncertain execution is first-class
- a contract-sensitive attempt cannot become `succeeded` before reconciliation

### 12.4 ExecutionContract state machine

Suggested states:

```text
draft
-> admissibility_pending
-> approval_pending?
-> authorized
-> executing
-> satisfied | partially_satisfied | violated
-> superseded | expired | abandoned
```

Rules:

- only one active selected contract per attempt by default
- contract supersession `MUST` be durable
- an expired contract is not silently revivable

### 12.5 EvidenceCase state machine

Suggested states:

```text
insufficient -> sufficient
sufficient -> stale | contradicted | expired | superseded
```

Rules:

- evidence freshness and contradiction are stateful facts, not hidden planner judgments
- a stale or contradicted evidence case cannot continue supporting a consequential contract without revalidation

### 12.6 AuthorizationPlan state machine

Suggested states:

```text
draft
-> preflighted
-> awaiting_approval?
-> authorized
-> blocked | invalidated | expired | superseded
```

Rules:

- preflight is planner-visible
- approval does not rescue an invalidated authorization plan
- authorization plan expiry may independently block execution

### 12.7 Reconciliation state machine

A reconciliation record may be treated as:

```text
pending -> satisfied | satisfied_with_downgrade | partial | ambiguous | violated | drifted | unauthorized | rolled_back
```

Rules:

- consequential attempts `MUST` enter reconciliation
- only reconciled states may feed durable learning
- `ambiguous`, `violated`, `drifted`, and `unauthorized` block automatic durable learning

---

## 13. Event Model

### 13.1 Event-backed truth

v0.2 retains append-only event-backed durable state.

Durable objects and projections `MUST` derive from event streams, not ad hoc mutation.

### 13.2 Required event-bearing transitions

The following transitions `MUST` emit durable events:

- contract candidate creation
- contract admission result
- contract supersession
- evidence-case sufficiency updates
- evidence contradiction or staleness
- authorization preflight result
- approval request / grant / deny / invalidate
- grant issuance / expiry / revoke
- lease acquisition / expiry / release
- receipt issuance
- reconciliation result
- memory promotion / invalidation
- contract-template promotion

### 13.3 Causation and correlation

Events `SHOULD` preserve enough linkage to answer:

- which evidence case supported this contract
- which contract requested this approval
- which authorization plan led to this grant
- which receipt fed this reconciliation
- which reconciliation fed this memory promotion

### 13.4 Idempotency

Events related to side effects, grant issuance, approval resolution, receipt issuance, reconciliation issuance, and rollback execution `SHOULD` carry an `idempotency_key`.

### 13.5 Hash linking

A task event stream `SHOULD` include `prev_event_hash` or equivalent tamper-evident sequencing.

### 13.6 Event categories

Suggested categories:

- task
- step
- attempt
- artifact
- working_state
- belief
- memory
- contract
- evidence
- authorization
- decision
- approval
- capability
- workspace
- receipt
- reconciliation
- rollback
- supervision

### 13.7 Projections

The kernel `MUST` support projections for:

- task state
- ready / blocked steps
- active approvals
- active contracts
- evidence sufficiency summaries
- authorization gaps
- receipts and proofs
- reconciliation results
- belief and memory status
- contract-template usage statistics

Projections are read models, not authority.

---

## 14. Artifact and Evidence Model

### 14.1 Artifact classes

Suggested classes remain:

- `source`
- `working`
- `derived`
- `evidence`
- `deliverable`
- `audit`

### 14.2 Suggested artifact kinds

In addition to v0.1 kinds, v0.2 recommends:

- `execution.contract`
- `evidence.case`
- `authorization.plan`
- `contract.packet`
- `reconciliation.record`
- `reconciliation.summary`
- `contract.template`

### 14.3 Addressing

Artifacts `SHOULD` support stable addressing by:

- content hash
- logical URI
- task-local alias when needed

Path identity alone remains insufficient.

### 14.4 Immutability and sealing

Artifact content is immutable once created. Metadata evolution occurs through events.

Artifacts cited by decisions, approvals, contracts, receipts, or reconciliations `SHOULD` be sealed or hash-locked.

### 14.5 Lineage

Artifacts `MUST` support lineage sufficient to answer:

- which attempt produced this artifact
- which contract cited it
- which evidence case incorporated it
- which approval packet summarized it
- which receipt or proof bundle included it
- whether it was promoted into memory
- whether it was used to learn a contract template

### 14.6 EvidenceRef

`EvidenceRef` remains the typed pointer used across beliefs, memory, decisions, approvals, contracts, receipts, and reconciliations.

Suggested structure:

```text
EvidenceRef {
  artifact_id
  selector
  excerpt_hash
  capture_method
  confidence
  trust_tier
}
```

### 14.7 EvidenceCase semantics

An `EvidenceCase` `SHOULD` answer at least:

- what claim or contract it supports
- what evidence supports it
- what contradicts it
- how fresh it must be
- what gaps remain
- how drift-sensitive the claim is
- whether the evidence is sufficient for admission

Evidence sufficiency is not “a model feeling.”
It is a kernel-visible judgment.

---

## 15. Working State, Belief, Memory, and Context

### 15.1 Knowledge layers

Hermit v0.2 distinguishes four layers:

1. **Scratchpad**
   Ephemeral, easy to discard.
2. **WorkingState**
   Durable but task-local execution state.
3. **Belief**
   Revisable working truth with evidence and epistemic metadata.
4. **MemoryRecord**
   Durable cross-task knowledge promoted under governance rules.

### 15.2 WorkingState rules

`WorkingState` remains:

- task-local
- schema-governed
- size-bounded
- compactable
- event-backed

In v0.2, working state `SHOULD` be able to represent:

- active contract pointer
- pending evidence gaps
- pending authority gaps
- selected downgrade path
- rollback availability
- unresolved uncertainties
- drift flags
- carry-forward deltas

Working state `MUST NOT` become an unbounded transcript dump.

### 15.3 Belief rules

Beliefs `MUST` support:

- confidence updates
- freshness updates
- contradiction marking
- supersession
- revocation
- scope-aware coexistence

If a belief supports a consequential contract, the belief `SHOULD` cite an evidence case.

### 15.4 Trust tiers

Suggested trust tiers remain:

- `untrusted`
- `observed`
- `verified`
- `user_asserted`
- `policy_asserted`

Trust tier affects:

- planning influence
- autonomous action eligibility
- memory promotion eligibility
- whether contradiction requires human review

### 15.5 Memory promotion rules

A durable memory write `MUST` include:

- a claim
- a scope
- evidence references or an evidence case reference
- trust tier
- promotion reason
- `learned_from_reconciliation_ref` for consequential learned memory

Cross-task memory promotion is an important action and `MUST` emit a receipt and pass reconciliation.

### 15.6 No bare memory in context

A memory record re-entering model context `MUST` carry:

- scope
- freshness / validation metadata
- evidence or evidence-case linkage
- supersession / invalidation status

If these are absent, the memory `SHOULD` be demoted to a hint or excluded.

### 15.7 ContractTemplate learning

A `ContractTemplate` captures reusable, governed cognition:

- what goal pattern this applies to
- what evidence shape is required
- what authority shape is required
- what reversibility is acceptable
- common failure modes
- when to downgrade
- when to escalate

Template reuse `SHOULD` be conditioned on evidence and authority similarity, not only lexical task similarity.

---

## 16. Contracts, Decisions, Authorization, Approval, and Capability

### 16.1 Contract hierarchy

Hermit v0.2 uses a layered contract model:

1. **Task contract**
   task-level durable boundary
2. **Step contract**
   logical recoverable work-unit boundary
3. **ActionContract**
   action-class default governance metadata
4. **ExecutionContract**
   attempt-scoped dynamic commitment

These layers are complementary.

### 16.2 ActionContract

An `ActionContract` `SHOULD` define at least:

- `action_class`
- `default_risk_band`
- `decision_required`
- `witness_required`
- `receipt_required`
- `reconcile_strategy`
- `rollback_strategy`

Policy profiles may tighten these rules.
They `SHOULD NOT` weaken secure defaults for high-risk classes without explicit override logic.

### 16.3 Decision classes and risk bands

Suggested decision classes:

- `planning`
- `contracting`
- `admission`
- `execution`
- `safety`
- `memory`
- `publishing`
- `rollback`
- `uncertainty_resolution`
- `authority_escalation`
- `reconciliation_resolution`

Suggested risk bands:

- `low`
- `moderate`
- `high`
- `critical`

Risk classification `SHOULD` consider:

- action class
- reversibility
- blast radius
- credential use
- target sensitivity
- evidence quality
- drift exposure
- authority gap size

### 16.4 AuthorizationPlan

The planner `MUST` be able to see:

- whether the candidate contract is authorizable
- whether approval is required
- what witness revalidation is required
- whether scope can be shrunk
- what downgrade path exists
- whether to escalate

That is the role of `AuthorizationPlan`.

### 16.5 Approval surface

An approval surface in v0.2 `MUST` expose a **contract packet** or an equivalent packet containing at least:

- intended effect
- affected resources
- evidence sufficiency summary
- authority scope
- drift expiry
- rollback availability
- fallback / downgrade path

The operator is approving:

> why this can be done now,
> under what evidence and authority,
> for how long that approval remains valid,
> and what happens if it goes wrong.

### 16.6 CapabilityGrant

A `CapabilityGrant` remains execution-time least-privilege authority.

The executor `MUST` refuse actions that exceed:

- action class
- resource scope
- expiry
- usage count
- contract-aligned boundaries

### 16.7 Drift and revalidation

Drift may include:

- input drift
- approval drift
- policy drift
- witness drift
- evidence drift
- contract expiry

If drift exceeds the active contract’s budget, the kernel `MUST`:

- re-enter admissibility and authorization, or
- supersede the contract or attempt

### 16.8 Policy override

Policy override remains a consequential action.

A policy override `MUST` have:

- elevated principal
- explicit decision
- explicit scope
- expiry
- receipt
- reconciliation

---

## 17. Workspace, Environment, Secrets, Drift, and Reversibility

### 17.1 Workspace lease

Execution occurs under a workspace lease when mutable or scoped workspace authority is required.

A lease `SHOULD` define:

- workspace identity
- root path
- holder principal
- mode
- resource scope
- acquisition and expiry

### 17.2 Environment capture

Important attempts `SHOULD` capture environment facts needed for explainability and reconciliation, including when material:

- OS
- shell
- cwd
- network mode
- relevant env whitelist
- tool versions
- repo HEAD
- interpreter/runtime version

### 17.3 Secrets

Secret material `MUST NOT` enter model-visible context by default.

Contract packets and approval packets `SHOULD` expose only the minimum secret-derived semantics required for governance.

### 17.4 State witness

A `StateWitness` or equivalent artifact represents the execution precondition snapshot that matters for delayed or drift-sensitive actions.

Examples:

- repo HEAD
- target file hash
- remote object version
- scheduler job version
- approval packet hash

### 17.5 Reversibility classes

A conformant implementation `SHOULD` distinguish at least:

- `none`
  irreversible in practice
- `compensating_only`
  no true rollback, but compensating action exists
- `bounded_restore`
  rollback possible within a bounded local or scoped domain
- `full_local_restore`
  strong local rollback or reset path exists

`ExecutionContract.reversibility_class` and action-class rollback strategy jointly determine admissibility under risk.

### 17.6 Drift budget

A `drift_budget` `SHOULD` be able to encode at least:

- max witness age
- max approval age
- max evidence age
- whether exact input hash stability is required
- whether policy version must remain constant
- whether target resource version must remain constant

High-risk or low-reversibility contracts `SHOULD` require smaller drift budgets.

---

## 18. Receipts, Reconciliation, Replay, and Rollback

### 18.1 Receipt classes

Suggested receipt classes remain:

- `tool_execution`
- `command_execution`
- `publish`
- `memory_promotion`
- `approval_resolution`
- `rollback`
- `observation_resolution`

### 18.2 Receipt requirements

Each receipt `MUST` answer:

- what was intended
- what contract was active
- what was authorized
- what actually ran
- in which environment it ran
- what changed
- what outputs were produced
- what was observed afterward
- whether rollback is supported

### 18.3 Reconciliation requirements

Each consequential attempt `MUST` produce reconciliation or enter a legal blocked state that explicitly waits for it.

Reconciliation `MUST` compare:

- intended effect
- authorized effect
- observed effect
- receipted effect

### 18.4 Result-class semantics

Suggested meanings:

- `satisfied`
  intended, authorized, observed, and receipted effects align
- `satisfied_with_downgrade`
  the downgraded, authorized contract was satisfied
- `partial`
  some success criteria met, no direct violation, more work remains
- `ambiguous`
  evidence remains insufficient to determine contract validity
- `violated`
  observed or receipted effects exceed or conflict with the contract
- `drifted`
  execution occurred under assumptions invalidated by drift or expiry
- `unauthorized`
  the effect exceeds grant, approval, policy, or contract scope
- `rolled_back`
  an effect occurred, rollback completed, and the final state is reconciled

### 18.5 Durable learning policy

Default policy:

- `satisfied` -> eligible for durable learning
- `satisfied_with_downgrade` -> eligible, but the downgraded path should be what is learned
- `partial` -> may update working state or provisional beliefs; durable learning requires policy
- `ambiguous` -> no automatic durable learning
- `violated` -> no automatic durable learning
- `drifted` -> no automatic durable learning
- `unauthorized` -> no automatic durable learning
- `rolled_back` -> may support rollback-related learning and final-state memory if reconciled, but must not be treated as success of the original effect

### 18.6 Receipt bundles and proof bundles

A `receipt.bundle` or stronger proof bundle `SHOULD` include hashes or references for:

- canonical receipt body
- selected execution contract
- evidence-case summary
- authorization-plan summary
- approval packet
- decision refs
- grant and lease refs
- output artifact hashes
- reconciliation result
- rollback metadata when applicable

### 18.7 Replay classes

A receipt `SHOULD` declare one of:

- `deterministic_replay`
- `idempotent_replay`
- `observe_only`
- `explain_only`

Replayability remains a causal-chain property, not a promise of byte-identical re-execution.

### 18.8 Rollback

Rollback support may remain partial.

If an action is not rollbackable, the receipt `MUST` say so.

If rollback is possible, the system `SHOULD` retain:

- rollback method
- rollback prerequisites
- rollback artifact refs
- rollback result
- rollback receipt
- rollback reconciliation

Rollback itself remains an important action.

---

## 19. Failure, Recovery, and Idempotency

### 19.1 Crash before execution

If an attempt crashes before side-effect dispatch, the kernel `SHOULD` reuse the admitted contract when all assumptions remain valid; otherwise it `MUST` re-enter admission or supersede.

### 19.2 Crash during or after execution

If the executor may have run but durable persistence is incomplete, the kernel `MUST` enter observation semantics and then reconciliation.

### 19.3 Unknown outcome

If the system cannot determine whether an important side effect occurred, it `MUST`:

- issue or complete an uncertainty-bearing receipt when possible
- block unsafe replay
- enter observation
- enter reconciliation

Unknown outcome is a first-class state, not a silent failure.

### 19.4 Retry rules

A retry `MUST` respect action class and contract status:

- readonly and pure computation paths `MAY` replay more freely
- effectful paths `MUST` rely on idempotency, observation, or renewed authority
- stale approvals, stale evidence, or stale witnesses `MUST NOT` silently carry over

### 19.5 Expiry and drift

Approvals, grants, witnesses, evidence cases, and contracts may expire independently.

Expiry or drift in any of them may require:

- revalidation
- downgrade
- scope reduction
- new approval
- contract supersession
- new attempt

### 19.6 Recovery depth

A conformant implementation `SHOULD` prefer the shallowest safe recovery:

1. reuse the same contract and attempt when assumptions remain valid
2. supersede the contract inside the same attempt when only cognition changed
3. supersede the attempt when execution boundary integrity changed
4. escalate to human resolution when contract validity cannot be re-established safely

---

## 20. Concurrency and Consistency

### 20.1 Conservative single-writer default

Hermit remains conservative by default:

- one mutable active attempt per step
- one active selected contract per attempt
- one authoritative reconciliation result per consequential attempt

### 20.2 Parallelism

Readonly evidence gathering `MAY` be parallelized when policy allows.

Parallel effectful execution `SHOULD` remain narrow unless the kernel can preserve explicit contract and authority separation.

### 20.3 Projection non-authority

Projection caches, UI summaries, and compatibility surfaces remain non-authoritative for side effects.

### 20.4 Input incorporation

Newly bound ingress `MUST` first become durable input delta.

It may be incorporated only at durable boundaries such as:

- pre-admission
- post-admission / pre-approval
- post-approval / pre-exec
- post-observation
- pre-next-step compilation

### 20.5 Supersession consistency

When a contract or attempt is superseded, the kernel `MUST` preserve enough linkage to answer:

- what it superseded
- why it was superseded
- what evidence or drift caused it
- whether any side effect already occurred
- whether rollback or reconciliation was required

---

## 21. Supervision and Trust Surface

A supervision surface for v0.2 `SHOULD` support:

- viewing live task state
- viewing ready, running, blocked, and reconciling steps
- inspecting attempts and checkpoints
- inspecting active contracts
- inspecting evidence sufficiency summaries
- inspecting authorization gaps and approval routes
- inspecting active approvals and grants
- inspecting leases and witness state
- inspecting receipts and proof bundles
- inspecting reconciliation results
- inspecting belief revisions and memory promotions
- inspecting learned contract templates
- triggering supported resume, cancel, retry, approve, deny, revoke-grant, and rollback operations

The supervision surface should answer these questions quickly:

- What is Hermit doing right now?
- What contract is active?
- Why is that contract admissible?
- What evidence is it using?
- What authority allows it?
- What is blocked?
- What changed?
- Did the observed effect stay within contract?
- Can this be undone?
- What was learned and why?

---

## 22. Compatibility with Current Hermit

v0.2 is intentionally designed as an additive evolution from the current repository trajectory.

Compatibility principles:

1. `Task`, `Step`, and `StepAttempt` remain the durable work backbone.
2. Existing governance objects remain valid:
   - `Decision`
   - `Approval`
   - `CapabilityGrant`
   - `WorkspaceLease`
3. `Receipt`, proof export, observation, and rollback remain first-class accountability surfaces.
4. `ActionRequest` remains a typed proposal object.
5. Existing action-class contract catalogs or equivalents remain useful as `ActionContract` baselines.
6. `ExecutionContract` adds an attempt-scoped layer; it does not erase action-class metadata.
7. Existing `Approval.requested_action_ref` style fields may coexist during migration with `requested_contract_ref`.
8. Existing `Receipt.action_request_ref` style fields may coexist during migration with `contract_ref`.
9. Existing `BeliefRecord` and `MemoryRecord` surfaces may be extended with epistemic and reconciliation metadata.
10. Existing proof export should grow to include reconciliation rather than replacing receipt and proof primitives.

v0.2 is therefore a **hard cut in semantics**, not a demand to rewrite the entire runtime from scratch.

---

## 23. Suggested Module Layout

Exact file placement may change, but the semantic boundaries should be preserved.

A recommended layout is:

```text
hermit/kernel/
  contracts.py                 # action-class contracts or registry
  execution_contracts.py       # attempt-scoped execution contracts
  evidence_cases.py            # evidence sufficiency and contradiction logic
  authorization_plans.py       # planning-time authority reasoning
  reconciliations.py           # post-receipt reconciliation and learning gate
  context_compiler.py
  knowledge.py
  memory_governance.py
  approvals.py
  decisions.py
  executor.py
  observation.py
  receipts.py
  proofs.py
  rollbacks.py
  controller.py
  store*.py

hermit/identity/
hermit/capabilities/
hermit/workspaces/
```

A compatible implementation may place these boundaries differently so long as:

- action-class contract metadata remains explicit
- execution contracts are durable
- evidence sufficiency is durable
- authorization preflight is planner-visible
- reconciliation and learning gates are durable

---

## 24. Suggested Persistent Records

A conformant v0.2 store `SHOULD` durably support at minimum:

- principals
- tasks
- steps
- step attempts
- events
- artifacts
- working state snapshots or patches
- beliefs
- memory records
- decisions
- approvals
- capability grants
- workspace leases
- receipts
- rollbacks
- execution contracts
- evidence cases
- authorization plans
- reconciliations
- task / step / attempt projections

`ContractTemplate` does not require a separate top-level table if it is represented as a `MemoryRecord` subtype.

An implementation may choose SQL tables, append-only logs plus indexes, or equivalent durable collections, but the semantics above must remain inspectable and replayable.

---

## 25. Conformance Profiles

To keep v0.2 implementable, the spec defines three profiles that preserve the v0.1 naming continuity.

### 25.1 Core profile

A `Core` v0.2 implementation must provide:

- task-first ingress
- step and step-attempt semantics
- append-only event-backed durable state
- artifact-native context compilation
- working state / belief / memory separation
- attempt-scoped `ExecutionContract` for consequential attempts
- `EvidenceCase` sufficiency gating for consequential contracts
- `ReconciliationRecord` gating for durable learning
- session or chat history as projection

### 25.2 Governed profile

A `Governed` v0.2 implementation adds:

- action-class contract metadata or equivalent
- planner-visible `AuthorizationPlan`
- policy profiles and action classification
- contract-packet approval
- scoped capability grants
- workspace leases when required
- witness / evidence / approval drift revalidation
- no ambient authority for effectful execution

### 25.3 Verifiable profile

A `Verifiable` v0.2 implementation adds:

- hash-linked task event streams or equivalent tamper-evident ordering
- sealed receipt bundles
- contract / evidence / authorization / reconciliation linkage in proof export
- verifiability metadata on receipts
- optional signatures or inclusion proofs

A system may claim:

- `Hermit Kernel v0.2 Core`
- `Hermit Kernel v0.2 Core + Governed`
- `Hermit Kernel v0.2 Core + Governed + Verifiable`

---

## 26. Security and Trust Posture

Hermit v0.2 adopts the following trust posture:

- local-first over cloud-first by default
- explicit authority over ambient authority
- evidence-backed cognition over hidden prompt belief
- bounded memory over silent memory authority
- fail-closed on ambiguity for consequential work
- drift-aware supersession over stale continuation
- receipt-backed side-effect closure
- reconciliation-gated durable learning

This is not “safer agent vibes.”
It is a kernel-level trust contract.

---

## 27. Benchmark and Evaluation

To show that v0.2 is a new loop and not only a more cautious v0.1, the recommended benchmark suite is **TrustLoop-Bench**.

### 27.1 Task families

1. **Approval Drift Patch**
   A repo patch is prepared, approval returns late, and HEAD has changed.
   Correct behavior: invalidate the old witness and supersede the old contract.

2. **Bounded-Authority Ops Change**
   Only readonly or narrowly mutable scope is granted.
   Correct behavior: reduce scope, request smaller grant, or downgrade.

3. **Crash + Unknown Outcome Recovery**
   Execution may have occurred before crash.
   Correct behavior: observe, receipt, reconcile, then decide replay.

4. **Contradictory Memory Update**
   Existing durable memory conflicts with new artifact evidence.
   Correct behavior: downgrade or supersede belief / memory rather than refeeding stale memory silently.

5. **Rollback-Qualified Publish**
   Some side effects are rollbackable, some are not.
   Correct behavior: contract choice changes as reversibility changes.

### 27.2 Key metrics

Suggested metrics:

- `contract_satisfaction_rate`
- `unauthorized_effect_rate`
- `stale_authorization_execution_rate`
- `belief_calibration_under_contradiction`
- `rollback_success_rate`
- `mean_recovery_depth`
- `operator_burden_per_successful_task`

These metrics should evaluate whether the kernel optimizes for valid governed cognition, not just action throughput.

---

## 28. Rollout Sequence

To keep scope controlled, the recommended rollout is staged.

### 28.1 Phase 0.2.a

Implement first:

- `ExecutionContract`
- `AuthorizationPlan`
- `StepAttempt` state machine additions:
  - `contracting`
  - `preflighting`
  - `reconciling`

Goal: make contract-first execution visible without blocking on full epistemic upgrades.

### 28.2 Phase 0.2.b

Implement next:

- `EvidenceCase`
- belief and memory epistemic fields
- the hard rule:
  - **no durable learning without reconciliation**

Goal: move the kernel from governed execution toward governed cognition.

### 28.3 Phase 0.2.c

Implement last:

- `ContractTemplate`
- contract scoring and template-conditioned reuse
- `TrustLoop-Bench`
- ablations comparing:
  - no evidence-case gating
  - no authorization-plan visibility
  - no reconciliation-gated learning

Goal: prove the loop change empirically.

---

## 29. Exit Criteria for v0.2

Hermit Kernel Spec v0.2 should be considered materially implemented only when all of the following are true:

1. Consequential attempts select an `ExecutionContract` before execution.
2. Consequential contracts require an `EvidenceCase` sufficiency judgment.
3. Planner-visible `AuthorizationPlan` exists before effectful execution.
4. Approval packets expose contract packets rather than bare action intent alone.
5. Drift invalidates or supersedes stale contracts instead of silently reusing them.
6. Consequential attempts cannot terminate as successful before reconciliation.
7. Durable memory promotion depends on reconciliation.
8. Learned contract templates depend on reconciled outcomes.
9. Proof export or operator inspection can reconstruct contract, evidence, authority, receipt, and reconciliation chains.
10. Unknown side-effect outcomes re-enter observation and reconciliation before replay.
11. Contract-sensitive retries do not silently inherit stale approval, evidence, or witness assumptions.
12. The system can demonstrate meaningful performance on TrustLoop-Bench or an equivalent suite.

---

## 30. Summary

Hermit v0.2 should not be described as:

> same model loop, different operator controls

It should be described as:

> **same trust kernel, new contract loop**

That loop is:

- contract-first
- evidence-governed
- authority-visible
- drift-aware
- reversibility-sensitive
- receipt-closed for effects
- reconciliation-closed for cognition

This is why v0.2 has the right to claim a real shift in agent-kernel semantics.

Not because it “thinks harder,”
and not because it “uses more tools,”
but because it learns to treat valid action as a function of:

- evidence
- authority
- drift
- reversibility
- reconciliation

That is the boundary between a more careful agent runtime and a genuinely different definition of agent intelligence.
