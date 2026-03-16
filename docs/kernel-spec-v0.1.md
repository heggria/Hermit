# Hermit Kernel Spec v0.1

**Status:** Draft
**Last updated:** 2026-03-15

This document defines the **target kernel architecture** for the next major iteration of Hermit.

It is a forward-looking specification, not a full description of the current repository state.

Read this document alongside:

- [`architecture.md`](./architecture.md) for the current implementation
- [`roadmap.md`](./roadmap.md) for current maturity and convergence status

Safe interpretation:

- the current repository already contains real kernel objects and control paths
- this spec defines the stronger target architecture those implementation paths are converging toward
- this document should not be read as a claim that every runtime surface already fully matches the spec

Hermit vNext is not a chat shell with tools. It is a **local-first governed agent kernel** for durable, governed, evidence-bound work.

## 1. Design Position

Hermit Kernel v0.1 is defined as:

A local-first, event-backed agent kernel where durable tasks advance through recoverable step attempts, compile artifact-native context, maintain bounded working state plus evidence-backed beliefs and durable memory, gate side effects through policy and approval, execute with least-privilege capability grants, and close every important action with a structured receipt.


Hermit’s target competitive scope is narrow and intentional:


- local-first

- long-running

- trust-heavy

- developer-grade

- auditable

- explainable after the fact



Hermit does **not** need to be the best agent for every scenario. It needs to be unusually strong for offline-capable, stateful, high-trust work where a user may later ask:


- What exactly happened?

- Why did it happen?

- What evidence was used?

- What authority allowed it?

- What changed?

- Can it be replayed, verified, or rolled back?



## 2. Architectural Thesis

Hermit Kernel v0.1 is built around five architectural theses:


1.
**Tasks are the durable unit of work.** Nothing meaningful begins outside a task.


2.
**Events are the durable unit of truth.** Durable state is not mutated directly; it is derived from an append-only event log.


3.
**Artifacts are the default unit of context and evidence.** Message history is a projection, not the primary substrate.


4.
**Models propose; the kernel authorizes and executes.** The model has reasoning authority, not execution authority.


5.
**Important actions are only complete when they are receipted.** A log line is not proof. A side effect without a receipt is not durably complete.




## 3. Goals

Hermit Kernel v0.1 has ten primary goals:


1. Make every meaningful unit of work start from a `Task`.

2. Make every durable state change flow through immutable `Event`s.

3. Make every recoverable execution boundary explicit as a `StepAttempt`.

4. Make `Artifact` the default unit of context assembly, lineage, and evidence binding.

5. Separate bounded `WorkingState`, revisable `Belief`, and durable `MemoryRecord`.

6. Remove direct model-to-tool execution from the kernel path.

7. Route consequential actions through `Decision`, `Policy`, and `Approval` when required.

8. Execute with explicit, scoped `CapabilityGrant`s instead of ambient authority.

9. Emit a durable `Receipt` for every important action.

10. Ensure every task is either replayable, observable, or explainable after the fact.



## 4. Non-Goals

This version of the spec does **not** require:


- a distributed cluster architecture

- multi-machine consensus

- CRDT-first collaboration

- a stable public API surface

- a fixed storage backend choice

- byte-identical replay for every step

- perfect rollback coverage for all external effects

- multi-tenant ACL completeness

- final cross-device synchronization semantics



The spec prioritizes kernel semantics over deployment scale.

## 5. Normative Language

The keywords `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are to be interpreted in RFC 2119 style usage.

## 6. Kernel Invariants

The following are hard constraints for v0.1.

### 6.1 Durable State


1.
**No direct durable mutation.** Every durable state change `MUST` be recorded as one or more events before projections are updated.


2.
**No orphan durable objects.** Every `Step`, `StepAttempt`, `Decision`, `Approval`, `CapabilityGrant`, `Receipt`, `Belief`, and `MemoryRecord` `MUST` belong to a task and `MUST` be attributable to a principal.


3.
**No silent overwrite of revisable knowledge.** `Belief` and `MemoryRecord` changes `MUST` be versioned or superseding; they `MUST NOT` be silently overwritten in place.




### 6.2 Execution Authority


1.
**No direct model-to-tool execution.** The model `MUST NOT` invoke executors directly. It may emit proposals, plans, assertions, and action requests.


2.
**No ambient authority.** Executors `MUST NOT` rely on broad process-level authority alone. Effectful execution `MUST` be bound to a scoped `CapabilityGrant` or an equivalent constrained execution record.


3.
**No irreversible action without a decision.** Destructive, external, credentialed, publish, payment, push-like, or policy-override actions `MUST` have a `Decision` record before execution.


4.
**No high-risk action without policy.** Policy evaluation is part of the kernel path, not an optional UI feature.


5.
**No delayed high-risk action without witness revalidation.** If approval arrives after a pause for a high-risk write-like action, the kernel `MUST` revalidate a `StateWitness` or equivalent execution preconditions before running the executor.




### 6.3 Knowledge and Context


1.
**No memory write without evidence.** Durable memory promotion `MUST` cite evidence references. Unsupported writes `MUST` degrade to working state or scratchpad.


2.
**No unbounded working state growth.** Working execution state `MUST` be schema-governed and size-bounded. Transcript append alone is not a conformant state strategy.


3.
**No secret material in model context by default.** Secret values `MUST NOT` enter model-visible context unless an explicit policy profile allows it.




### 6.4 Audit and Recovery


1.
**Every important action produces a receipt.** If no receipt exists, the action is not considered durably complete.


2.
**No silent replay of uncertain side effects.** If an effect may have run but the outcome is unknown, the kernel `MUST` enter observation or resolution semantics before re-executing.


3.
**Every task is replayable or explainable.** The kernel `MUST` retain enough information to replay inputs, observe outputs, or reconstruct the decision and evidence chain.




## 7. Important Actions

The following action classes are considered important by default and `MUST` produce receipts:


- local write

- local delete

- command execution

- VCS mutation

- network write

- credentialed API call

- publication

- payment or spending

- durable memory promotion

- policy override

- rollback execution

- approval resolution for consequential actions



A policy profile `MAY` add additional action classes to this set.

## 8. First-Class Objects

Hermit Kernel v0.1 is centered on twelve first-class objects.

### 8.1 Task

A `Task` is the durable entrypoint for work.

A task represents:


- an explicit goal

- a lifecycle state

- a priority

- an owner

- a policy profile

- a task contract boundary

- a step graph boundary



A task `MUST` exist before execution begins. All ingress channels such as CLI, scheduler, webhook, Feishu, remote panel, or future adapters `MUST` create or resume a task instead of invoking the runtime directly.

Minimum fields:


- `task_id`

- `title`

- `goal`

- `status`

- `priority`

- `owner_principal_id`

- `policy_profile_ref`

- `task_contract_ref`

- `created_at`

- `updated_at`



Recommended fields:


- `parent_task_id`

- `depends_on`

- `labels`

- `deadline_at`

- `workspace_hint`

- `requested_by`

- `source_channel`

- `parallelism_mode`



Clarification:

- `parent_task_id` is for decomposition or derived-task lineage
- `parent_task_id` `MUST NOT` be used as the conversational carry-forward mechanism for follow-up questions

### 8.1.2 IngressRecord

An `IngressRecord` is the durable record for each inbound free-form message before it changes task state.

Minimum fields:

- `ingress_id`
- `conversation_id`
- `source_channel`
- `raw_text`
- `normalized_text`
- `status`
- `resolution`
- `created_at`
- `updated_at`

Recommended fields:

- `actor`
- `prompt_ref`
- `reply_to_ref`
- `quoted_message_ref`
- `explicit_task_ref`
- `referenced_artifact_refs`
- `chosen_task_id`
- `parent_task_id`
- `confidence`
- `margin`
- `rationale_ref` or embedded rationale payload

Semantics:

- every free-form ingress `MUST` be durably recorded before it mutates task, step, or attempt state
- ingress binding `MUST` be explainable from candidates and rationale
- unresolved ambiguity is a legal kernel state; `pending_disambiguation` is not an error
- adapters and product surfaces `MAY` choose whether to auto-bind, ask, or defer, but the core kernel `MUST NOT` pretend a unique binding exists when it does not

### 8.1.3 Ingress Binding Semantics

Ingress binding is not a binary `continue vs new` heuristic. The kernel `SHOULD` support at least these outcomes:

- `control`
- `approval`
- `append_note`
- `fork_child`
- `start_new_root`
- `chat_only`
- `pending_disambiguation`

Recommended binding priority:

1. explicit task, approval, receipt, or command target
2. adapter reply target or quoted-message target
3. pending approval correlation
4. current focus task
5. ranked candidate open tasks
6. `fork_child`
7. `start_new_root`
8. `pending_disambiguation`

Related-task semantics:

- `append_note` mutates the current task input
- `fork_child` creates a new child task related to the bound task without inheriting the full working state
- `start_new_root` creates a new unrelated root task in the same conversation container

### 8.1.1 Continuation Anchors

Kernel ingress `MUST` distinguish between active-task continuation and terminal-outcome continuation.

Rules:

- a terminal task is any task in `completed`, `failed`, or `cancelled`
- a terminal task `MUST NOT` be implicitly reopened by a follow-up message
- when a follow-up refers to a terminal outcome, the kernel `MUST` create a new task
- that new task `MUST` carry a structured `continuation_anchor` rather than a raw transcript pointer

Minimum continuation anchor fields:

- `anchor_task_id`
- `anchor_kind`
- `selection_reason`
- `outcome_status`
- `outcome_summary`
- `source_artifact_refs`

For the v0.1 continuation flow, `anchor_kind` is `completed_outcome`.

Durability requirement:

- the continuation anchor `SHOULD` be written into ingress metadata and into the event-backed task creation payload so projections and context packs can be rebuilt without transcript replay

### 8.2 Step

A `Step` is the smallest logical recoverable unit within a task.

Examples:


- plan

- search

- inspect

- edit

- run tests

- prepare patch

- await approval

- publish result

- observe remote state

- rollback



A step is a logical work unit, not a concrete run attempt. A step `MUST` define a contract boundary.

Minimum fields:


- `step_id`

- `task_id`

- `kind`

- `title`

- `contract_ref`

- `status`

- `depends_on`

- `max_attempts`

- `created_at`

- `updated_at`



A step contract `SHOULD` define:


- objective

- expected inputs

- expected outputs

- success criteria

- allowed action classes

- rollback hint when applicable



### 8.3 StepAttempt

A `StepAttempt` is the concrete execution instance of a step.

This is the primary recovery boundary for durable execution.

Minimum fields:


- `attempt_id`

- `task_id`

- `step_id`

- `attempt_no`

- `status`

- `context_pack_ref`

- `working_state_ref`

- `workspace_lease_ref`

- `idempotency_key`

- `started_at`

- `finished_at`



Recommended fields:


- `resume_from_ref`

- `executor_mode`

- `policy_version`

- `state_witness_ref`

- `environment_ref`



Semantics:


- A step may have multiple attempts over time.

- Only one mutable active attempt per step is allowed unless a future version explicitly supports concurrent attempts.

- Approval pauses and resumptions attach to a specific step attempt.

- A retry `MUST` create a new attempt number.

- An attempt is the unit that receives policy results, approvals, grants, and receipts.

- An attempt `MAY` carry execution-phase metadata such as `planning`, `policy_pending`, `awaiting_approval`, `authorized_pre_exec`, `executing`, `observing`, or `settling`.

- Newly bound task input `MUST` first become task input delta and `MUST NOT` hot-patch an executor already in flight.

- If input, approval packet, policy assumptions, or witness assumptions drift past the current checkpoint, the kernel `MUST` re-enter policy or supersede the attempt instead of silently mutating execution state.



### 8.4 Event

An `Event` is the kernel source of truth.

Events are immutable records describing state transitions or externally relevant observations. Projected views such as task summaries, approval queues, artifact listings, belief views, memory views, and session projections `MUST` derive from events.

Minimum fields:


- `event_id`

- `schema_version`

- `event_type`

- `entity_type`

- `entity_id`

- `task_id`

- `step_id` when applicable

- `attempt_id` when applicable

- `task_seq`

- `occurred_at`

- `actor_type`

- `actor_id`

- `payload`

- `causation_id`

- `correlation_id`



Recommended fields:


- `idempotency_key`

- `prev_event_hash`

- `workspace_id`

- `principal_scope`

- `policy_profile_version`



### 8.5 Artifact

An `Artifact` is the canonical container for work products, observations, and evidence.

Artifacts are not limited to files on disk. An artifact may refer to a file, blob, JSON document, remote snapshot, content-addressed bundle, or sealed proof packet.

Minimum fields:


- `artifact_id`

- `class`

- `kind`

- `uri`

- `content_hash`

- `media_type`

- `byte_size`

- `created_at`

- `producer`

- `retention_class`

- `trust_tier`

- `sensitivity_class`



Recommended fields:


- `sealed_at`

- `expires_at`

- `lineage_ref`

- `task_local_alias`



Lifecycle events:


- `created`

- `referenced`

- `sealed`

- `promoted`

- `compacted`

- `expired`



### 8.6 Belief

A `Belief` captures what the system currently treats as true enough to reason with inside or near a task boundary.

Beliefs are provisional, revisable, and evidence-bound. Beliefs are **not** durable memory by default.

Minimum fields:


- `belief_id`

- `task_id`

- `claim`

- `scope`

- `evidence_refs`

- `confidence`

- `trust_tier`

- `status`

- `created_at`



Recommended fields:


- `step_id`

- `attempt_id`

- `supersedes`

- `contradicts`

- `expires_at`

- `structured_assertion`



Belief statuses:


- `active`

- `superseded`

- `contradicted`

- `revoked`

- `expired`



### 8.7 MemoryRecord

A `MemoryRecord` is durable knowledge expected to survive task boundaries.

Minimum fields:


- `memory_id`

- `claim`

- `scope`

- `evidence_refs`

- `trust_tier`

- `promotion_reason`

- `status`

- `created_at`



Recommended fields:


- `promoted_from_belief_id`

- `retention_class`

- `invalidated_at`

- `supersedes`

- `structured_assertion`



Memory statuses:


- `active`

- `invalidated`

- `revoked`

- `expired`



A memory record `MUST NOT` be hard-deleted as the default correction path. Invalidation or supersession is the default correction path.

### 8.8 Decision

A `Decision` records a consequential judgment made by the system or a human.

Examples:


- choose plan B

- ignore stale memory

- proceed with destructive cleanup

- request approval before push

- downgrade execution to readonly

- resolve unknown outcome via observation

- promote belief to durable memory



Minimum fields:


- `decision_id`

- `task_id`

- `step_id`

- `attempt_id` when applicable

- `decision_type`

- `summary`

- `rationale`

- `evidence_refs`

- `risk_level`

- `decided_by`

- `reversible`

- `created_at`



Recommended fields:


- `alternatives_considered`

- `policy_override_ref`

- `effective_until`

- `constraints_ref`



### 8.9 Approval

An `Approval` is a first-class execution object, not a UI-only affordance.

Approvals represent pauses in the task graph where progress is blocked pending a human or policy-authorized resolution.

Minimum fields:


- `approval_id`

- `task_id`

- `step_id`

- `attempt_id`

- `status`

- `approval_type`

- `requested_action_ref`

- `approval_packet_ref`

- `requested_at`

- `resolved_at`

- `resolved_by`



Recommended fields:


- `expires_at`

- `constraints_ref`

- `state_witness_ref`

- `policy_result_ref`



Statuses:


- `pending`

- `granted`

- `denied`

- `expired`

- `cancelled`

- `invalidated`



### 8.10 Receipt

A `Receipt` is the durable proof record for an important action.

A receipt is not a raw log line. It is a structured proof object with pointers to inputs, authority, environment, outputs, and observed results.

Minimum fields:


- `receipt_id`

- `task_id`

- `step_id`

- `attempt_id`

- `receipt_class`

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


- `decision_ref`

- `rollback_ref`

- `replay_class`

- `verifiability`

- `signer_ref`

- `signature`

- `receipt_bundle_ref`



### 8.11 Principal

A `Principal` represents an attributable actor.

Principal types include:


- `user`

- `supervisor`

- `agent`

- `service`

- `scheduler`

- `webhook`

- `policy_engine`

- `executor`

- `system`



Minimum fields:


- `principal_id`

- `principal_type`

- `display_name`

- `created_at`



Recommended fields:


- `authn_context_ref`

- `labels`

- `external_identity_ref`



### 8.12 CapabilityGrant

A `CapabilityGrant` is a scoped authority record that authorizes a specific execution envelope.

Minimum fields:


- `grant_id`

- `task_id`

- `step_id`

- `attempt_id`

- `issued_to_principal_id`

- `issued_by_principal_id`

- `action_class`

- `resource_scope`

- `issued_at`

- `expires_at`

- `max_uses`



Recommended fields:


- `constraints_ref`

- `approval_ref`

- `policy_result_ref`

- `revoked_at`

- `consumed_at`



Semantics:


- Grants are least-privilege by default.

- A grant is bound to a task and attempt.

- Executors `MUST` refuse operations outside grant scope.

- Expired or revoked grants are invalid even if an earlier approval existed.



## 9. Object Relationships

The kernel object graph follows these rules:


1. A `Task` owns many `Step`s.

2. A `Step` owns many `StepAttempt`s across retries or resumptions.

3. `Event`s describe lifecycle transitions of all durable objects.

4. `Artifact`s are produced and consumed by attempts.

5. `Belief`s and `MemoryRecord`s cite evidence from artifacts, not just raw text.

6. `Decision`s, `Approval`s, `CapabilityGrant`s, and `Receipt`s attach to a task and, when applicable, to a concrete attempt.

7. `Session` or chat history is a projection derived from tasks, events, and selected artifacts; it is not the source of truth.



## 10. Layered Architecture

Hermit Kernel v0.1 is split into six layers.

### 10.1 Control Plane

Responsibilities:


- accept ingress from CLI, scheduler, webhook, Feishu, remote panel, or future adapters

- create, resume, cancel, pause, and reprioritize tasks

- publish events

- expose supervision and inspection interfaces



The control plane `MUST NOT` contain model reasoning logic.

### 10.2 Task and Step Orchestrator

Responsibilities:


- create steps from task contracts

- maintain dependency ordering

- select ready steps

- allocate attempts

- enforce single-writer task semantics by default



This layer coordinates work selection, not tool execution.

### 10.3 Durable Execution Engine

Responsibilities:


- acquire workspace leases

- compile context packs

- invoke models in propose-only or constrained reasoning modes

- normalize action requests

- checkpoint after each durable boundary

- recover from interruption using the event log



Recovery happens at `StepAttempt` granularity.

### 10.4 Policy, Approval, and Capability Layer

Required chain:

`Model output -> ActionRequest -> PolicyEngine -> ApprovalEngine -> CapabilityGrant -> Executor`

Responsibilities:


- classify requested actions

- evaluate policy profile and risk

- return `allow`, `require_approval`, `deny`, or `downgrade`

- mint scoped capability grants

- block and resume the same attempt

- revalidate witnesses for delayed actions



### 10.5 Artifact, Knowledge, and Context Layer

Subcomponents:


- `Artifact Store`

- `Working State Store`

- `Belief Store`

- `Memory Store`

- `Decision Ledger`

- `Context Compiler`



Responsibilities:


- store work products

- maintain evidence lineage

- separate working state, belief, and durable memory

- compile minimal context packs

- compile structured carry-forward from continuation anchors when present

- compact and seal evidence-bearing outputs



### 10.6 Supervision and Proof Surface

Responsibilities:


- inspect tasks, steps, attempts, approvals, grants, receipts, beliefs, memory, and decisions

- expose artifact lineage and context manifests

- export proof bundles

- allow approve, deny, cancel, retry, rollback, and revoke-grant operations where supported

- explain what changed, why, and with which authority



This layer is part of the trust model, not just observability.

## 11. Execution Lifecycle

### 11.1 Standard Lifecycle

A conformant effectful execution path `SHOULD` proceed as follows:


1. An ingress channel creates or resumes a `Task`.

2. The orchestrator selects a ready `Step`.

3. The engine acquires a `WorkspaceLease` and creates a `StepAttempt`.

4. The context compiler emits a `context.pack` artifact and checkpoints it.

5. The model is invoked with the compiled context.

6. The model may emit zero or more of:

  - plan updates

  - belief assertions

  - draft deliverables

  - action requests




7. Each action request is normalized into an `ActionRequest`.

8. Policy evaluates the request.

9. Policy returns one of:

  - `allow`

  - `require_approval`

  - `deny`

  - `downgrade`




10. If approval is required, the attempt is paused without executing the side effect.

11. On grant, the kernel revalidates witness state when required.

12. If authorized, a scoped `CapabilityGrant` is issued.

13. The executor runs inside the lease and grant scope.

14. Outputs and observations are captured as artifacts.

15. A receipt is issued.

16. Step and task projections are updated from events.



### 11.2 Model Authority Boundary

The model `MAY` reason, propose, or revise beliefs. The model `MUST NOT` directly execute tools, shell commands, filesystem writes, or network writes.

The kernel interprets model output through typed boundaries.

### 11.3 ActionRequest

`ActionRequest` is a typed execution proposal generated from model output or a non-model source.

Suggested structure:
 `ActionRequest {   action_request_id   task_id   step_id   attempt_id   action_class   target_resources[]   params_ref   expected_effects[]   reversibility_hint   reason   proposed_by   proposed_at } `
An action request is `SHOULD` be stored as an artifact of kind `action.request`.

### 11.4 Downgrade Semantics

If policy returns `downgrade`, the result `MUST` include either:


- a rewritten lower-risk action request, or

- an explicit restricted execution mode



Examples:


- mutation request downgraded to readonly inspection

- network write downgraded to network read

- publish downgraded to draft artifact generation



### 11.5 Block and Resume

Approval or external wait states `MUST NOT` force a full task restart. The same `StepAttempt` `MUST` resume whenever its checkpointed inputs remain valid.

If witness drift, policy drift, or input invalidation occurs, the system `MUST` either:


- re-enter policy evaluation, or

- supersede the attempt with a new attempt

Clarifications:

- a newly bound ingress first becomes task input delta; it is not direct executor mutation
- the kernel `MUST` absorb new input only at durable boundaries such as pre-policy, post-policy, pre-exec, post-observation, or pre-next-step compilation
- input drift, approval-packet drift, and witness drift `MAY` produce different rationale, but they share the same recovery shape: recompile, re-enter policy, or supersede



## 12. State Machines

### 12.1 Task State Machine

Minimum task states:


- `created`

- `ready`

- `running`

- `blocked`

- `paused`

- `completed`

- `failed`

- `cancelled`

- `rolled_back`



Allowed transitions:


- `created -> ready`

- `ready -> running`

- `running -> blocked`

- `blocked -> running`

- `running -> paused`

- `paused -> ready`

- `running -> completed`

- `running -> failed`

- `ready|running|blocked|paused -> cancelled`

- `completed|failed -> rolled_back` when supported



### 12.2 Step State Machine

Minimum step states:


- `planned`

- `ready`

- `running`

- `blocked`

- `succeeded`

- `failed`

- `cancelled`

- `superseded`



A step’s effective state is derived from its latest relevant attempt plus orchestration state.

### 12.3 StepAttempt State Machine

Minimum attempt states:


- `created`

- `leased`

- `compiling_context`

- `reasoning`

- `awaiting_policy`

- `awaiting_approval`

- `authorized`

- `executing`

- `observing`

- `receipt_pending`

- `succeeded`

- `failed`

- `cancelled`

- `superseded`



Rules:


- Each new attempt increments `attempt_no`.

- Each attempt state transition `MUST` emit events.

- Approval pauses apply at attempt granularity.

- Observation after uncertain execution is a first-class attempt phase.



### 12.4 Approval Blocking Semantics

When policy returns `require_approval`:


1. the engine emits `approval.requested`

2. the current attempt transitions to `awaiting_approval`

3. execution state is checkpointed

4. no effectful executor runs yet

5. on grant or deny, the same attempt resumes or terminates unless invalidated



For delayed high-risk actions, approval resumption `MUST` include witness validation before authorization.

## 13. Event Model

### 13.1 Principles

The event log is append-only. Events are immutable after commit.

Events `MUST` be:


- attributable

- causally linked

- schema-versioned

- projection-friendly



Events `SHOULD` be:


- totally ordered within a task

- hash-linked within a task

- deduplicable where side effects are involved



### 13.2 Ordering

The kernel `MUST` provide a monotonically increasing `task_seq` per task.

A global total order across all tasks is not required for v0.1.

### 13.3 Idempotency

Events related to side effects, grant issuance, approval resolution, executor dispatch, and receipt issuance `SHOULD` carry an `idempotency_key`.

If duplicate submission is detected, the kernel `MUST` either:


- reuse the original durable result, or

- emit a dedupe event instead of replaying the side effect



### 13.4 Hash Linking

A task event stream `SHOULD` include `prev_event_hash` to support tamper-evident sequencing.

A conformant implementation that omits hash linking `MUST` still preserve append-only semantics and durable ordering within the task.

### 13.5 Event Categories

Suggested categories:


- task events

- step events

- attempt events

- artifact events

- working state events

- belief events

- memory events

- decision events

- approval events

- capability events

- receipt events

- workspace events

- policy events

- supervision events



### 13.6 Projections

The kernel `MUST` support projections.

Minimum projections:


- task summary view

- step queue view

- approval inbox

- active grant view

- artifact catalog

- working state view

- belief view

- memory view

- decision timeline

- receipt ledger

- session/chat projection

- conversation focus view



Projection rebuild from the event log `SHOULD` be possible without bespoke repair logic.

Conversation focus view rules:

- one conversation `MAY` have many open tasks
- one conversation `MUST` have at most one implicit focus task
- background progress `MUST NOT` automatically steal focus
- focus changes `SHOULD` be projection-rebuildable from ingress binding, explicit task switch, task lifecycle events, and adapter reply targeting

## 14. Artifact and Evidence Model

### 14.1 Artifact Classes

Suggested classes:


- `source`

- `working`

- `derived`

- `evidence`

- `deliverable`

- `audit`



Suggested kinds:


- `context.pack`

- `action.request`

- `policy.result`

- `approval.packet`

- `state.witness`

- `environment.snapshot`

- `workspace.snapshot`

- `search.bundle`

- `web.snapshot`

- `file.snapshot`

- `patch`

- `diff`

- `command.transcript`

- `test.report`

- `image`

- `binary.attachment`

- `belief.extract`

- `memory.promotion`

- `receipt.bundle`



### 14.2 Addressing

Artifacts `SHOULD` support stable addressing by:


- content hash

- logical URI

- task-local alias when needed



Path identity alone is insufficient for trust-sensitive workflows.

### 14.3 Immutability and Sealing

Artifact content is immutable once created. Metadata evolution `MUST` occur through events.

Artifacts cited by a decision, approval, or receipt `SHOULD` be sealed or hash-locked.

### 14.4 Lineage

Artifacts `MUST` support lineage sufficient to answer:


- which attempt produced this artifact

- which inputs contributed to it

- which later decisions cited it

- which beliefs referenced it

- whether it was promoted into durable memory

- whether it was included in a receipt bundle



### 14.5 EvidenceRef

`EvidenceRef` is the typed pointer used by beliefs, memory, decisions, approvals, and receipts.

Suggested structure:
 `EvidenceRef {   artifact_id   selector   excerpt_hash   capture_method   confidence   trust_tier } `
A selector `MAY` be:


- byte range

- line range

- JSON path

- DOM selector

- timestamp span

- structured record identifier



## 15. Working State, Belief, Memory, and Context

### 15.1 Knowledge Layers

Hermit v0.1 distinguishes four layers:


1.
**Scratchpad** Ephemeral, non-durable, easy to discard.


2.
**WorkingState** Durable but task-local execution state. Bounded, schema-governed, and cheap to revise.


3.
**Belief** Evidence-backed working truth used for reasoning. Revisable and versioned.


4.
**MemoryRecord** Durable cross-task knowledge promoted with evidence and trust metadata.




### 15.2 WorkingState

`WorkingState` is not the same as transcript history.

WorkingState `MUST` be:


- task-local

- schema-governed

- size-bounded

- event-backed

- compactable



WorkingState `SHOULD` include only items such as:


- active objective decomposition

- current constraints

- selected plan pointer

- pending questions

- resource handles

- expected outputs

- execution-local caches

- unresolved uncertainties



WorkingState `MUST NOT` become an unbounded append-only dump of prior conversation.

### 15.3 Belief Rules

Beliefs `MUST` support:


- confidence updates

- supersession

- contradiction marking

- revocation

- scope-aware coexistence



Contradiction does not imply deletion. Two beliefs may coexist if scope differs or uncertainty remains unresolved.

### 15.4 Trust Tiers

Suggested trust tiers:


- `untrusted`

- `observed`

- `verified`

- `user_asserted`

- `policy_asserted`



Trust tier affects:


- whether a claim may influence planning

- whether a claim may trigger autonomous action

- whether a claim may be promoted to durable memory

- whether contradiction requires human review



### 15.5 Memory Promotion Rules

A durable memory write `MUST` include:


- a claim

- a scope

- evidence references

- trust tier

- promotion reason



Memory promotion `SHOULD` also include:


- source belief or artifact

- invalidation policy

- retention class



Cross-task memory promotion is an important action and `MUST` emit a receipt.

### 15.6 Context Compiler

The context compiler produces the minimal execution pack for an attempt.

Inputs may include:


- task contract

- step contract

- active policy profile

- working state

- selected beliefs

- eligible durable memory

- relevant artifacts

- active decisions

- workspace snapshot refs

- prior receipts when relevant

- bound ingress deltas since the last durable boundary

- focus task summary when conversation routing depends on implicit focus



Outputs `MUST` be:


- compact enough to keep model context focused

- traceable back to artifact and belief identifiers

- deterministic enough to explain later



The output `SHOULD` be stored as a `context.pack` artifact.

### 15.7 Context Precedence

A recommended precedence order is:


1. task contract

2. step contract

3. active policy constraints

4. active decisions

5. working state

6. selected beliefs

7. durable memory

8. relevant artifacts

9. bound ingress deltas and focus summary

10. session/chat projection only when necessary



### 15.8 Session History

Message history may exist, but it is a projection and convenience layer. It is not the primary state substrate.

## 16. Decisions, Policy, Approval, and Capability

### 16.1 Decision Classes

Suggested classes:


- `planning`

- `execution`

- `safety`

- `memory`

- `publishing`

- `rollback`

- `uncertainty_resolution`



### 16.2 Risk Bands

Suggested risk bands:


- `low`

- `moderate`

- `high`

- `critical`



Risk classification `SHOULD` consider:


- action class

- resource sensitivity

- credential usage

- reversibility

- blast radius

- uncertainty of target state

- provenance quality of supporting evidence



### 16.3 Policy Profiles

A policy profile defines execution constraints for a task.

Examples:


- `readonly_analysis`

- `local_edit_allowed`

- `network_read_allowed`

- `repo_mutation_gated`

- `external_publish_gated`

- `destructive_denied`



Policy profiles `MUST` be visible on the task object.

### 16.4 Action Classes

Suggested action classes:


- read local

- write local

- delete local

- execute command

- network read

- network write

- credentialed API call

- VCS mutation

- publication

- payment or spending

- durable memory write

- rollback



### 16.5 Policy Results

Policy evaluation returns exactly one of:


- `allow`

- `require_approval`

- `deny`

- `downgrade`



A policy result `SHOULD` include:


- action summary

- risk summary

- relevant policy profile version

- affected resources

- reasoning summary

- required witness or revalidation conditions



### 16.6 Capability Grants

A capability grant is minted only after:


- direct allow, or

- approval grant plus any required witness validation



A capability grant `MUST` be scoped by at least:


- task

- attempt

- action class

- resource scope

- expiry

- usage limit



A grant `SHOULD` also encode:


- network egress allowlist

- filesystem path constraints

- repo/ref constraints

- secret handles allowed at execution time



### 16.7 Approval Packets

Approval requests `SHOULD` contain:


- action summary

- risk summary

- relevant decision rationale

- evidence refs

- expected effect

- impacted resources

- rollback availability

- expiry rules

- state witness when required



Approval packets are artifacts and should be inspectable later.

If a newly bound ingress changes the action summary, risk, target resources, or other policy-relevant inputs, the prior approval packet `MUST NOT` be reused silently. The kernel `MUST` re-enter policy or create a superseding attempt.

### 16.8 State Witness

A `state.witness` artifact captures execution-time preconditions for delayed or high-risk actions.

It `SHOULD` include:


- target resource fingerprints

- relevant versions or hashes

- observed preconditions

- observation time

- witness expiry

- observing principal



For the following action classes, witness validation `MUST` run on delayed execution unless policy explicitly waives it:


- local write

- local delete

- VCS mutation

- network write

- credentialed API call

- publication

- rollback



If witness validation fails, the previous authorization `MUST NOT` be treated as sufficient. The kernel `MUST` re-enter policy or create a superseding attempt.

Approval-packet drift, witness drift, and newly bound input are distinct causes, but all three `MUST` resolve through durable re-entry or supersession rather than in-memory mutation of a running executor.

### 16.9 Policy Override

Policy override is a consequential action.

A policy override `MUST` have:


- an elevated principal

- an explicit decision

- a receipt

- a clear scope and expiry



## 17. Workspace, Environment, and Secrets

### 17.1 Workspace Lease

Execution occurs under a workspace lease.

A lease `SHOULD` define:


- `lease_id`

- `task_id`

- `attempt_id`

- `workspace_id`

- `root_path`

- `holder_principal_id`

- `acquired_at`

- `expires_at`

- `mode`

- `resource_scope`



Suggested lease modes:


- `readonly`

- `mutable`

- `isolated`

- `external_effects_disabled`



### 17.2 Environment Capture

Important attempts `SHOULD` capture environment facts needed for explainability:


- OS

- shell

- cwd

- relevant env whitelist

- network mode

- tool versions when material

- repo HEAD when material

- interpreter/runtime version



These may be referenced via an `environment.snapshot` artifact.

### 17.3 Secret Handling

Secrets `MUST` be handled by reference where possible.

Rules:


- raw secret values `MUST NOT` be inserted into context packs by default

- executors `SHOULD` resolve secret handles at execution time

- receipts and artifacts `MUST` redact secret material

- policy `MAY` allow controlled secret exposure only under explicit profile constraints



## 18. Receipts, Replay, and Rollback

### 18.1 Receipt Classes

Suggested receipt classes:


- `tool_execution`

- `command_execution`

- `publish`

- `memory_promotion`

- `approval_resolution`

- `rollback`

- `observation_resolution`



### 18.2 Receipt Requirements

Each receipt `MUST` answer:


- what was intended

- what was authorized

- what actually ran

- in which environment it ran

- what changed

- what outputs were produced

- what was observed afterward

- whether rollback is supported



### 18.3 Verifiability Levels

Suggested `verifiability` levels:


- `hash_only`

- `hash_chained`

- `signed`

- `signed_with_inclusion_proof`



A base v0.1 implementation `MUST` support at least `hash_only`. A stronger implementation `SHOULD` support signed receipts and hash-linked task streams.

### 18.4 Receipt Bundles

A `receipt.bundle` artifact `SHOULD` include:


- canonical receipt body

- referenced input and output hashes

- environment summary

- policy result hash

- approval packet hash

- capability grant hash

- decision ref

- replay metadata

- rollback metadata when applicable



### 18.5 Replay Classes

A receipt `SHOULD` declare one of:


- `deterministic_replay`

- `idempotent_replay`

- `observe_only`

- `explain_only`



Replayability does not require byte-identical re-execution for every step. It requires either replayable inputs or an explainability bundle that reconstructs the causal chain.

### 18.6 Rollback

Rollback support may be partial.

If an action is not rollbackable, the receipt `MUST` say so. If an action is rollbackable, the receipt `SHOULD` include:


- rollback method

- rollback prerequisites

- rollback artifact refs

- rollback result when executed



Rollback itself is an important action and requires its own receipt.

## 19. Failure, Recovery, and Idempotency

### 19.1 Crash Before Durable Commit

If the process crashes before an event commit, no durable state change is assumed.

### 19.2 Crash After Commit but Before Dispatch

If authorization has been durably recorded but the executor has not run, the attempt `MUST` resume from the last checkpoint and `MUST NOT` emit a duplicate grant unnecessarily.

### 19.3 Crash During or After Dispatch

If the executor may have run but outcome persistence is incomplete, the kernel `MUST` enter `observing` semantics.

The kernel `MUST` prefer:


1. idempotent re-query of executor state

2. target-system observation

3. durable executor transcript reconciliation

4. human resolution when needed



### 19.4 Unknown Outcome

If the system cannot determine whether an important side effect occurred, it `MUST` issue a receipt with an uncertainty-bearing `result_code`, such as `unknown_outcome`, and block unsafe automatic replay.

Unknown outcome is not a silent failure mode. It is a first-class state that requires resolution.

### 19.5 Retry Rules

A retry `MUST` respect action class:


- readonly and pure computations `MAY` replay more freely

- effectful or destructive actions `MUST` rely on idempotency, observation, or renewed decision authority

- stale approvals `MUST NOT` silently carry over without revalidation when witness or policy drift occurred



### 19.6 Expiry and Drift

Approvals, grants, and witnesses may expire independently.

On resume, the kernel `MUST` check:


- approval expiry

- grant expiry

- witness validity

- policy profile version compatibility



## 20. Concurrency and Consistency

### 20.1 Default Consistency Model

Hermit v0.1 uses **single-writer per task** semantics by default.

This means:


- only one orchestration path may durably mutate a task at a time

- projections may lag

- authorization decisions `MUST` rely on the event log, not on stale projections



### 20.2 Parallel Steps

Parallel execution `MAY` be supported only when all of the following hold:


- the task is marked as parallelizable

- steps are dependency-independent

- resource scopes are disjoint or explicitly lockable

- policy permits concurrent execution



### 20.3 Leases and Locks

Mutable attempts `SHOULD` use leases with expiry. A lost lease `MUST` prevent silent continuation.

### 20.4 Projection Lag

Projection lag is acceptable for read views. Projection lag `MUST NOT` authorize side effects.

## 21. Supervision and Trust Surface

The supervision surface should support:


- viewing live task state

- viewing ready, running, and blocked steps

- inspecting attempts and checkpoints

- inspecting active approvals

- inspecting active grants

- inspecting artifact lineage

- inspecting context pack manifests

- inspecting belief revisions

- inspecting memory promotions and invalidations

- inspecting decision chains

- inspecting receipts and proof bundles

- triggering supported resume, cancel, retry, approve, deny, revoke-grant, and rollback operations



The supervision surface should answer these questions quickly:


- What is Hermit doing right now?

- Why is it doing that?

- What authority allows it?

- What evidence is it using?

- What exactly was sent to the model?

- What is blocked?

- What changed?

- Can this be undone?

- Can this be verified independently?



## 22. Compatibility with Current Hermit

This section maps current modules to their target role in the new kernel.


-
`runner` Target role: ingress adapter boundary and attempt execution facade, not primary state authority.


-
`runtime` Target role: model interaction loop inside the durable execution engine.


-
`scheduler` Target role: task creation, wake-up, and step selection source, not a direct agent invoker.


-
`session` Target role: projection for chat UX and compatibility, not source of truth.


-
plugin and tool registry Target role: action request normalizer plus executor registry behind policy and grants.




### 22.1 Required Structural Shift

Current Hermit centers execution around:


- session lookup

- prompt assembly

- message history

- runtime loop

- direct tool dispatch



Kernel v0.1 centers execution around:


- task creation or resume

- step scheduling

- step attempt creation

- context compilation from artifacts, working state, beliefs, and memory

- action request normalization

- policy and approval gates

- capability grant issuance

- receipt issuance

- projection rebuild from events



## 23. Suggested Module Layout

The target module map is:
 `src/hermit/control/ hermit/tasks/ hermit/steps/ hermit/execution/ hermit/events/ hermit/artifacts/ hermit/evidence/ hermit/working_state/ hermit/beliefs/ hermit/memory/ hermit/decisions/ hermit/policy/ hermit/approvals/ hermit/capabilities/ hermit/receipts/ hermit/proofs/ hermit/workspaces/ hermit/context/ hermit/supervision/ hermit/projections/ hermit/identity/ `
This layout is conceptual for v0.1. Exact file placement may change, but module boundaries should preserve the same semantics.

## 24. Suggested Persistent Records

v0.1 should at minimum support durable records for:


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

- receipts

- workspace leases

- task projections

- step projections



The event log is primary. Other tables or collections may be projections, indexes, or specialized stores.

## 25. Conformance Profiles

To keep v0.1 implementable, the spec defines three conformance profiles.

### 25.1 Core Profile

A `Core` implementation must provide:


- task-first ingress

- step and step attempt semantics

- append-only event-backed durable state

- artifact-native context compilation

- working state, belief, and memory separation

- no direct model-to-tool execution

- receipts for important actions

- session as projection



### 25.2 Governed Profile

A `Governed` implementation adds:


- policy profiles

- action classification

- approval blocking and resume

- capability grants

- witness revalidation for delayed high-risk actions

- no ambient authority for effectful execution



### 25.3 Verifiable Profile

A `Verifiable` implementation adds:


- hash-linked task event streams

- sealed receipt bundles

- verifiability metadata on receipts

- optional signatures or inclusion proofs

- exportable proof artifacts



A system may claim `Hermit Kernel v0.1 Core`, `Core + Governed`, or `Core + Governed + Verifiable`.

## 26. Security and Trust Posture

The kernel is designed around zero-trust memory and governed execution.

Security-relevant principles:


- memory is not trusted merely because it exists

- evidence and trust tier matter

- models do not hold execution authority

- policy runs before side effects

- approvals are part of execution semantics

- capability grants bound authority to scope and time

- delayed actions must revalidate world state when required

- receipts exist for auditability and post-hoc proof

- rollback metadata is explicit, not implied



This is a trust model, not a UI enhancement.

## 27. Exit Criteria for v0.1

Hermit Kernel Spec v0.1 should be considered materially implemented only when all of the following are true:


1. Every ingress path creates or resumes a task.

2. Durable state changes are event-backed.

3. Steps and step attempts are recoverable without rerunning a whole conversation by default.

4. Direct model-to-tool execution is removed from the kernel path.

5. Context packs are compiled from artifacts, working state, beliefs, and memory rather than raw transcript alone.

6. Policy gates high-risk actions before execution.

7. Approvals can pause and resume the same attempt.

8. High-risk delayed actions revalidate witness state before execution.

9. Effectful execution runs through scoped authority rather than ambient authority alone.

10. Durable memory writes require evidence and trust metadata.

11. Important actions emit receipts.

12. Unknown side-effect outcomes are surfaced explicitly and not silently replayed.

13. Session history is demoted to a projection.

14. The supervision surface can explain what happened, why, with what evidence, and under what authority.

15. Every free-form ingress is durably recorded before task mutation.

16. Conversation focus and ingress binding are projection-rebuildable from durable ingress records plus events.



## 28. Summary

Hermit Kernel v0.1 is defined as:


A local-first agent kernel where durable tasks advance through recoverable step attempts over an event log, compile artifact-native context from bounded working state plus evidence-backed beliefs and memory, treat model output as proposals rather than execution authority, gate risky actions through policy, approval, witness revalidation, and scoped capability grants, and close the trust loop with receipts, replay metadata, and rollback semantics.


That definition is the architectural contract for the next kernel.

## Appendix A. Non-Normative Design Rationale
