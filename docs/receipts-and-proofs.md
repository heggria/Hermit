---
description: "Receipts, proof bundles, rollback, and benchmark verification in Hermit."
---

# Receipts And Proofs

Hermit draws a hard line between "a tool ran" and "an important action is durably complete."

A log line is not enough.

For important actions, Hermit records a `Receipt` that ties together the task, step attempt, inputs, outputs, policy result, authority references, and outcome summary. Proof services then turn those records into proof summaries and exportable proof bundles.

The point is not cryptographic theater. The point is operational truth:

- what happened
- why it happened
- what evidence and authority were involved
- what changed
- whether the result can be verified or rolled back

## Why Receipts Exist

Most agent runtimes stop their accountability story at the tool loop.

Hermit takes a stronger view:

**an important action is not complete just because the executor returned**

For important actions, the kernel should retain:

- input references
- output references
- policy result
- approval reference
- decision reference
- capability grant reference
- workspace lease reference
- execution environment reference
- result code and summary
- rollback relationship when supported

## What Counts As An Important Action

The target rule is broad:

- local write
- local delete
- command execution
- VCS mutation
- network write
- credentialed API call
- publication
- memory promotion
- rollback execution

The current implementation already treats consequential effectful actions as governed and receipt-worthy. Coverage depth will continue to expand.

## Receipt Objects

The current codebase already defines a receipt record with fields for:

- task, step, and step attempt identity
- action type
- input and output artifact references
- policy result
- approval, decision, capability grant, and workspace lease references
- witness references
- proof metadata
- rollback support and rollback status

This is why Hermit can already say it is receipt-aware in a concrete sense.

## Proofs

Hermit's proof layer currently includes:

- task proof summaries
- receipt bundles with context manifests
- tiered proof export (summary, standard, full)
- event-chain verification with hash-linked events
- Merkle inclusion proofs over receipt bundles
- DAG proof bundles for multi-step task topologies
- proof anchoring to external stores (local log, git notes)
- governance assurance reports
- chain completeness analysis
- HMAC (Hash-based Message Authentication Code) SHA256 signing with configurable proof modes

> **WARNING:** Receipt signing is disabled by default. Without `HERMIT_PROOF_SIGNING_SECRET`, receipts are not HMAC-signed and cannot provide tamper-evidence guarantees. For production use, configure a signing secret.

These are available through the current CLI:

```bash
hermit task proof <task_id>
hermit task proof-export <task_id>
```

The kernel ledger also maintains event hash chaining, which supports verification-oriented reasoning over task history.

### Proof Export Detail Levels

The `export_task_proof` method supports three verbosity tiers:

- **summary** (~5-20 KB): Core verification data, chain status, receipt refs, decision refs, Merkle root, and chain completeness percentages only.
- **standard** (~50-200 KB): Summary plus full governance records including capability grants, workspace leases, execution contracts, evidence cases, authorization plans, and reconciliations.
- **full** (can be MBs): Everything in standard plus receipt bundles, context manifests, artifact hash index, and per-receipt Merkle inclusion proofs.

### Proof Modes

The proof system supports four progressive modes:

1. **hash_only** — Baseline: events exist but no receipt bundles.
2. **hash_chained** — Receipt bundles sealed with hash-linked events and artifact content hashes.
3. **signed** — Bundles signed with HMAC-SHA256 via the `HERMIT_PROOF_SIGNING_SECRET` environment variable.
4. **signed_with_inclusion_proof** — Signed bundles with Merkle tree inclusion proofs for every receipt.

Signing is configured through two environment variables:

- `HERMIT_PROOF_SIGNING_SECRET` — the HMAC key
- `HERMIT_PROOF_SIGNING_KEY_ID` — optional key identifier (defaults to `local-hmac`)

### Merkle Inclusion Proofs

The `merkle.py` module builds a binary Merkle tree over receipt bundles using SHA-256 of canonical JSON. Each receipt gets a sibling-path inclusion proof that allows independent verification of receipt membership without replaying the full bundle list. Odd-length tree levels duplicate the last node as padding.

### DAG Proof Bundles

For DAG-structured tasks (tasks with multi-step dependency graphs), `DAGProofService` collects all receipts organized by step topology. The resulting `DAGProofBundle` includes:

- step receipts grouped by step ID
- root step IDs (no dependencies)
- leaf step IDs (no downstream dependents)
- join events (`step.dependency_satisfied`)

### Proof Anchoring

Proof hashes can be anchored to external stores for tamper-evident persistence via `AnchorService`. Two built-in anchor methods are available:

- **LocalLogAnchor** — Appends proof hashes to a local JSONL file with hash chaining between entries. Each entry records the proof hash, task ID, timestamp, and previous anchor hash.
- **GitNoteAnchor** — Writes proof hashes as git notes on the HEAD commit under the `hermit-proofs` notes ref.

Both methods support anchor verification: given a `ProofAnchor`, the method checks that the recorded proof hash matches the current evidence.

### Governance Assurance Reports

The `governance_report.py` module extracts and classifies governance events from a proof bundle, producing a structured `GovernanceEvents` object and a human-readable markdown report. The report covers:

- Executive summary (total governed actions, denied, allowed with receipt, rollback capable, boundary violations prevented)
- Boundary enforcement table (denied actions with risk levels)
- Authorized executions table (receipts with rollback status)
- Chain integrity assessment
- Coverage assessment
- Final verdict: CLEAN EXECUTION, GOVERNANCE ENFORCED, or INTEGRITY COMPROMISED

### Chain Completeness

For each receipt, the proof system checks whether the full governance chain is present: execution contract, evidence case, authorization plan, and reconciliation. The completeness report shows per-receipt gaps and an overall completeness percentage.

## Observation And Uncertain Outcomes

An important action should not be blindly replayed when the outcome is uncertain.

Hermit's kernel direction explicitly makes room for:

- observation
- resolution
- reconcile paths
- unknown outcome handling

This matters because a side effect that may already have run is not safely handled by "just try again."

## Rollback

Rollback in Hermit is a first-class object and action path.

Current reality:

- rollback support exists for supported receipt classes: file restore, git revert/reset, and memory invalidation
- rollback is not universal
- rollback itself is consequential and should be tracked as part of the durable story
- rollback follows the full governed path: decision, capability grant, workspace lease, execution, receipt

Operator command:

```bash
hermit task rollback <receipt_id>
```

That means the repo has more than an abstract promise of recovery. It already contains the recovery object model and executable path for selected cases.

### Rollback Strategies

Three rollback strategies are currently implemented:

- **file_restore** — Restores a file to its pre-action state (or deletes it if it did not exist before). Used for `write_local` and `patch_file` actions.
- **git_revert_or_reset** — Hard-resets a git repository to the pre-action HEAD commit. Used for `vcs_mutation` actions. Refuses to proceed if the repo is dirty.

  > **CAUTION:** `git reset --hard` permanently discards all commits after the target. The service refuses to proceed if the repository has uncommitted changes, but committed work after the rollback target will be lost.
- **supersede_or_invalidate** — Invalidates memory records and beliefs created by a `memory_write` action.

### Recursive Rollback Planning

The `RollbackDependencyTracker` traces receipt chains via output_refs/input_refs overlap to build a dependency graph. When rolling back receipt A, any receipt B whose input_refs overlap with A's output_refs must be rolled back first.

The tracker produces a `RollbackPlan` with:

- `execution_order` — leaf-first (reverse topological order) so downstream effects are undone before their causes
- `nodes` — dependency graph of `DependentReceipt` objects with depth and dependent IDs
- `manual_review_ids` — receipts that do not support automatic rollback
- `cycle_detected` — flag for circular dependencies

`RollbackPlanExecution` records the result of executing a plan: succeeded, failed, and skipped receipt IDs with per-receipt results.

## Benchmark Verification

The verification module includes a benchmark subsystem for verification-driven quality gates.

### Benchmark Profiles

Four built-in benchmark profiles are registered by default:

- **trustloop_governance** — Validates approval/decision/receipt chain integrity after governance-mutation tasks. Metrics: chain_integrity, approval_latency_ms, receipt_coverage.
- **runtime_perf** — Measures executor throughput and latency for runtime-critical paths. Metrics: p50_latency_ms, p99_latency_ms, throughput_ops.
- **integration_regression** — Runs surface-integration regression suite for adapters and plugins. Metrics: pass_rate, regression_count.
- **template_quality** — Evaluates quality of learned templates via coverage and accuracy metrics. Metrics: template_accuracy, coverage_delta.

### Task Family Classification

`BenchmarkRoutingService` classifies tasks into families based on affected paths and action classes:

- `governance_mutation` — kernel/policy/, kernel/authority/ paths
- `runtime_perf` — execution/, runtime/, dispatch/ paths
- `surface_integration` — surfaces/, cli/, adapters/ paths
- `learning_template` — memory/, template/, pattern/ paths

### Benchmark Routing

Benchmarks are triggered only for tasks with `high` or `critical` risk bands. The routing service:

1. Reads verification requirements from the execution contract
2. Classifies the task family
3. Routes to the matching benchmark profile
4. Creates a benchmark run
5. Evaluates metrics against thresholds (lower-is-better for latency/error metrics, higher-is-better for others)
6. Produces a `BenchmarkVerdict` with pass/fail, regressions, and improvements
7. Formats the verdict for reconciliation input

## What Can Be Claimed Today

Safe claims:

- Hermit already ships receipts with HMAC-SHA256 signing support
- Hermit already ships proof summaries, tiered proof export (summary/standard/full), and Merkle inclusion proofs
- Hermit already has event-chain verification with hash-linked events and chain completeness analysis
- Hermit already supports rollback for selected receipts with recursive dependency tracking
- Hermit already has proof anchoring to local logs and git notes
- Hermit already has governance assurance reports with boundary enforcement tracking
- Hermit already has benchmark verification routing with four built-in profiles and threshold evaluation
- Hermit already supports DAG proof bundles for multi-step task topologies

Claims that should stay careful:

- "verifiable execution" is directionally true, but still maturing
- proof coverage and rollback coverage are not complete across all action classes
- benchmark execution is routing-ready but depends on external runner commands

## Why This Matters

Agents become more useful as they do more consequential work.

They also become less trustworthy if the only durable record is a chat transcript and a pile of logs.

Hermit's receipt and proof path is its answer to that tradeoff.
