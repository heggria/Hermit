---
description: "Real-world scenarios where Hermit's governed agent execution solves problems that conventional agent runtimes cannot."
---

# Use Cases

Every operating system enables capabilities that would be impossible without it. You can't have multi-user systems without process isolation. You can't have reliable storage without filesystem journaling. Here are the capabilities that Hermit -- the AI Task OS -- unlocks.

This document describes six scenarios where governed agent execution is not optional -- it is the difference between shipping AI-assisted automation and being told by legal, compliance, or operations leadership that you cannot.

Each scenario is drawn from environments where "the agent ran some code and it seemed fine" is not an acceptable operational posture.

---

## 1. Regulated Code Changes -- Kernel Audit Trail

**The problem.** In financial services, healthcare, and defense, every production code change must be traceable to an authorization decision, an identity, and a reviewable evidence trail. When an AI agent writes or modifies code, the organization needs to prove *who authorized it*, *what policy governed it*, and *what the agent actually did* -- not just that a model produced some tokens.

**How Hermit solves it.** Every code change flows through the governed execution path: `ActionRequest -> PolicyDecision -> Approval -> WorkspaceLease -> CapabilityGrant -> Execution -> Receipt`. The kernel records each step as a first-class durable object in the local SQLite ledger. Like a filesystem journal, Hermit's event-sourced ledger makes every operation reconstructable. After execution, operators export a proof bundle that auditors can independently verify -- including hash-chained events, Merkle inclusion proofs, and a governance assurance report that classifies every action as authorized, denied, or rolled back.

**Key features used:**
- Policy Engine with guard dispatch chain (filesystem, shell, network, VCS rules)
- Approval workflow with structured approval copy and delegation-aware resolution
- HMAC-SHA256 signed receipts with Merkle inclusion proofs
- Tiered proof export (summary / standard / full) for audit delivery
- Governance assurance reports with boundary enforcement tables
- Rollback with recursive dependency tracking

**Example: exporting an audit-ready proof bundle**

```bash
# Configure receipt signing for tamper-evidence
export HERMIT_PROOF_SIGNING_SECRET="your-signing-secret"

# Run a governed task under the supervised profile
hermit run --policy supervised \
  "Update the interest rate calculation in src/rates/engine.py \
   to use the new regulatory formula"

# Inspect what happened
hermit task list
hermit task show <task_id>
hermit task receipts --task-id <task_id>

# Export the full proof bundle for compliance review
hermit task proof-export <task_id> --detail full

# The exported bundle includes:
#   - Every receipt with HMAC signature
#   - Merkle inclusion proofs per receipt
#   - Capability grants showing scoped authority
#   - Workspace leases showing isolation
#   - Governance assurance report with final verdict
```

---

## 2. Multi-Agent Orchestration -- Process Scheduling & Isolation

**The problem.** Modern development workflows involve multiple AI agents operating concurrently -- one refactoring code, another writing tests, a third reviewing security. Without governance boundaries, these agents share ambient authority, race on shared resources, and produce interleaved changes that no one can attribute or roll back independently.

**How Hermit solves it.** Hermit's kernel treats each agent task as an isolated unit with its own workspace lease, capability grants, and receipt chain. Like process isolation in a multi-user OS, each agent gets its own workspace lease and scoped capabilities -- no shared ambient authority. Workspace leases enforce mutual exclusion -- only one task holds a mutable lease on a given directory at a time. DAG task submission lets you express dependencies between steps, and the kernel handles scheduling, join barriers, and proof aggregation automatically. Each agent's work is independently verifiable.

**Key features used:**
- DAG task submission with dependency-aware step scheduling
- Workspace leases with mutual exclusion, TTL expiry, and orphan reaping
- Per-task capability grants (least-privilege per agent)
- Worker pool with role-bound slot management
- Fork-join concurrency with governed delegation
- DAG proof bundles aggregating receipts across parallel steps

**Example: coordinating three agents via MCP**

```python
# Submit independent tasks in parallel -- each gets isolated authority
hermit_submit(
    description="Refactor src/billing/calculator.py to extract tax logic",
    policy_profile="default"
)

hermit_submit(
    description="Write unit tests for src/billing/calculator.py \
                 with 90% coverage target",
    policy_profile="default"
)

hermit_submit(
    description="Security review of src/billing/ for SQL injection \
                 and input validation gaps",
    policy_profile="readonly"  # Security reviewer needs read-only access only
)
```

```python
# Or express dependencies explicitly with a DAG task
hermit_submit_dag_task(
    goal="Refactor billing module with tests and security review",
    nodes=[
        {
            "key": "refactor",
            "kind": "code",
            "title": "Extract tax logic into separate module"
        },
        {
            "key": "test",
            "kind": "code",
            "title": "Write unit tests for refactored module",
            "depends_on": ["refactor"]
        },
        {
            "key": "review",
            "kind": "review",
            "title": "Security review of billing module",
            "depends_on": ["refactor"]
        },
    ],
    policy_profile="default"
)
```

---

## 3. Self-Improving CI/CD -- Self-Upgrading OS

**The problem.** You want AI agents to iterate on your codebase autonomously -- fixing bugs, improving performance, addressing tech debt. But self-modifying code is the highest-risk category of agent work. Without governance, a self-improving agent can silently break invariants, bypass tests, or merge changes that no human reviewed.

**How Hermit solves it.** Hermit's self-iteration pipeline runs every modification through a governed lifecycle: `spec -> parse -> branch -> execute -> verify -> proof-export -> PR`. Like an OS that can patch its own kernel only through a verified update channel, Hermit constrains self-modification behind the same governance that governs all other work. The kernel creates an isolated git worktree, runs staged verification gates (quick smoke tests, affected-file tests, full lint+typecheck+test suite), and only merges when all gates pass. Every mutation is authorized by the policy engine, receipted, and exportable as a proof bundle. If verification fails, the worktree is discarded -- the main branch is never touched.

**Key features used:**
- Self-iteration kernel with workspace isolation (git worktrees)
- Three-level verification gates: `test-quick` (~10s), `test-changed` (~1-3min), `check` (~5-10min)
- Governed merge with conflict detection and `MergeConflictError` handling
- Benchmark verification routing (governance, runtime, integration, template quality profiles)
- Spec queue management for prioritized iteration backlog
- Lessons learned extraction from past iterations

**Example: submitting a self-iteration spec**

```python
# Submit a governed self-improvement iteration
hermit_submit_iteration(
    iterations=[{
        "goal": "Reduce p99 latency in the dispatch coordinator by 30%",
        "priority": "high",
        "research_hints": [
            "Profile current hot paths in execution/coordination/dispatch.py",
            "Check if SQLite queries in the ledger are the bottleneck"
        ]
    }],
    policy_profile="autonomous"
)

# Monitor progress through the lifecycle phases:
#   CREATED -> MODIFYING -> VERIFYING -> MERGING -> COMPLETED (or FAILED)
hermit_iteration_status(iteration_ids=["<iteration_id>"])

# Check what the iteration learned
hermit_lessons_learned(categories=["performance"])

# Review benchmark results against quality thresholds
hermit_benchmark_results(iteration_ids=["<iteration_id>"])
```

```python
# Or manage the iteration backlog via MCP
hermit_spec_queue(action="list")
hermit_spec_queue(
    action="add",
    entries=[
        {"goal": "Migrate all string concatenation to f-strings", "priority": "low"},
        {"goal": "Add retry logic to network-dependent tests", "priority": "normal"}
    ]
)
```

---

## 4. Overnight Autonomous Work -- Background Process Management

**The problem.** Engineering teams want to leverage overnight hours for autonomous agent work -- codebase patrol, tech debt reduction, documentation updates, test gap coverage. But running agents unattended for 8+ hours with full system access and no human in the loop is a liability without guardrails. If something goes wrong at 3 AM, you need to know exactly what happened and be able to undo it.

**How Hermit solves it.** Like cron + systemd, Hermit manages long-running background tasks with policy guardrails -- but adds the governed execution layer that traditional process managers lack. Hermit's overnight mode combines scheduled task execution with policy profiles that constrain what the agent can do without approval. The scheduler runs tasks at configured intervals, each under its own policy profile. The patrol hook proactively scans for code health issues. The overnight dashboard aggregates all activity, and the morning report gives operators a single summary of everything that happened -- tasks completed, approvals consumed, receipts generated, and any actions that were denied or rolled back. Every overnight action is independently rollback-capable.

**Key features used:**
- Scheduler with cron, interval, and one-shot triggers
- Policy profiles constraining overnight authority (`default` blocks destructive operations)
- Patrol hook for proactive code health checks
- Overnight dashboard and morning report aggregation (`hermit overnight`)
- Execution budget management (deadlines, timeouts, max turns)
- Autostart via macOS launchd for unattended startup

**Example: configuring overnight autonomous work**

```bash
# Schedule nightly code patrol at 1 AM
hermit schedule add --name "nightly-patrol" \
  --cron "0 1 * * *" \
  --prompt "Run code health patrol: check for dead imports, \
          unused variables, test coverage gaps, and dependency \
          vulnerabilities" \
  --policy default

# Schedule weekly documentation refresh on Sundays at 2 AM
hermit schedule add --name "weekly-docs" \
  --cron "0 2 * * 0" \
  --prompt "Review all public API docstrings for accuracy against \
          current implementations" \
  --policy readonly

# Enable autostart so Hermit launches at login without intervention
hermit autostart enable --adapter feishu

# === Next morning ===

# Get the overnight activity report
hermit overnight

# Sample output:
#   Overnight Report (2026-03-20 01:00 - 2026-03-21 08:00)
#   -------------------------------------------------------
#   Tasks completed: 3
#   Tasks failed: 0
#   Approvals consumed: 2 (auto-approved by delegation policy)
#   Receipts generated: 17
#   Actions denied: 1 (attempted write to /etc/hosts -- blocked)
#   Rollback-capable receipts: 14
#
#   Run `hermit task list` for details.

# Inspect any specific task from the overnight run
hermit task show <task_id>
hermit task proof <task_id>
```

---

## 5. Enterprise Chat Ops -- Multi-Terminal Access

**The problem.** Teams collaborate in Feishu, Slack, or Telegram. They want AI agents integrated into these channels -- triaging issues, running diagnostics, deploying patches. But enterprise chat is where convenience meets risk: a casual message should not trigger an unreviewed production deployment, and every agent action in a shared channel needs to be attributable and reversible.

**How Hermit solves it.** Hermit's adapter system connects to Feishu, Slack (Socket Mode), and Telegram as long-running services via `hermit serve`. Like a multi-user OS exposing the same kernel through multiple terminals, every chat channel is just another ingress surface into the same governed execution path as CLI tasks. When an agent needs to perform a consequential action, the kernel blocks execution and surfaces an approval request directly in the chat channel. The operator approves or denies in-channel. Every action is receipted, and the team can inspect task history and proof bundles through the CLI or MCP interface.

**Key features used:**
- Feishu, Slack, and Telegram adapters via `hermit serve <adapter>`
- In-channel approval workflows with structured approval copy
- Webhook receiver with HMAC signature verification for external triggers
- Delegation-aware approvals (parent task policies propagate to subtasks)
- Trust scoring from historical execution data (advisory risk adjustments)
- Plugin system for custom team-specific tools and hooks

**Example: governed operations through Slack**

```bash
# Start Hermit as a long-running Slack service
hermit serve slack
```

A typical interaction in the Slack channel:

```
User:     @hermit deploy the hotfix from branch fix/rate-limit to staging

Hermit:   I'll deploy branch fix/rate-limit to staging.
          This requires approval for the following actions:

          ┌─────────────────────────────────────────────────┐
          │  APPROVAL REQUIRED -- HIGH RISK                 │
          │                                                 │
          │  Action: Shell command execution                │
          │  Command: git checkout fix/rate-limit &&        │
          │           ./scripts/deploy.sh staging           │
          │  Resource scope: repo, network                  │
          │  Risk level: high                               │
          │  Fingerprint: sha256:a7f3e2...                  │
          │                                                 │
          │  Reply "approve" to proceed.                    │
          │  Reply "deny <reason>" to block.                │
          └─────────────────────────────────────────────────┘

User:     approve

Hermit:   Deployed fix/rate-limit to staging successfully.
          Receipt: rcpt_a7f3e2b1
          Task: task_9c4d8e5a
          Duration: 47s

          Run `hermit task proof task_9c4d8e5a` for the full
          audit trail.
```

```bash
# Configure webhook routes for CI/CD-triggered tasks
cat > ~/.hermit/webhooks.json << 'EOF'
{
  "routes": [
    {
      "path": "/ci/deploy",
      "secret": "whsec_your_webhook_secret",
      "task_template": "Deploy {payload.ref} to {payload.environment}",
      "profile": "supervised"
    },
    {
      "path": "/ci/rollback",
      "secret": "whsec_your_webhook_secret",
      "task_template": "Rollback deployment {payload.deployment_id}",
      "profile": "supervised"
    }
  ]
}
EOF

# Enable the webhook server
export HERMIT_WEBHOOK_ENABLED=true
export HERMIT_WEBHOOK_PORT=8443
```

---

## 6. Compliance-First Development -- System Attestation

**The problem.** SOX, SOC 2, ISO 27001, and HIPAA audits require organizations to demonstrate that every system change follows a documented control process, that access is scoped to least privilege, and that a verifiable chain of evidence exists from authorization to execution. When AI agents make changes, the compliance burden does not disappear -- it intensifies, because the agent's decision-making process must itself be auditable.

**How Hermit solves it.** Hermit's governance is not a reporting layer bolted onto a permissive runtime. It is the execution primitive. Like kernel attestation (TPM/Secure Boot), Hermit generates cryptographic proof that governance was enforced -- not just that it was configured. The kernel enforces the control chain *before* execution, not after. Every consequential action produces a receipt signed with HMAC-SHA256, linked to the policy decision that authorized it, the capability grant that scoped it, and the workspace lease that isolated it. Proof bundles can be anchored to external stores (local JSONL with hash chaining, or git notes) for tamper-evident persistence. The governance assurance report provides a pre-formatted audit artifact with boundary enforcement tables, authorized execution lists, chain integrity assessment, and a final verdict.

**Key features used:**
- Hash-chained event sourcing in the kernel ledger (append-only, tamper-evident)
- Four proof modes: `hash_only` -> `hash_chained` -> `signed` -> `signed_with_inclusion_proof`
- Proof anchoring to local log (hash-chained JSONL) and git notes
- Governance assurance reports with compliance-ready verdicts
- Chain completeness analysis (execution contract, evidence case, authorization plan, reconciliation)
- 21 canonical action classes for precise access control categorization
- Authorization plans with preflight verification, revalidation, and drift detection

**Example: continuous compliance evidence generation**

```bash
# Enable signing for tamper-evident receipts
export HERMIT_PROOF_SIGNING_SECRET="$(openssl rand -base64 32)"
export HERMIT_PROOF_SIGNING_KEY_ID="prod-2026-q1"

# Run development tasks under the supervised profile
hermit run --policy supervised \
  "Implement the new patient data encryption module \
   per HIPAA requirements in spec/HIPAA-2026-003"

# After completion, generate the compliance artifact
hermit task proof-export <task_id> --detail full
```

The full export produces a governance assurance report:

```
# Governance Assurance Report

## Executive Summary
- Total governed actions: 14
- Denied (boundary enforced): 2
- Allowed with receipt: 12
- Rollback-capable: 9
- Boundary violations prevented: 2

## Boundary Enforcement
| Action               | Risk     | Reason                        |
|----------------------|----------|-------------------------------|
| write /etc/hosts     | critical | kernel_path_write_denied      |
| curl | sh            | critical | dangerous_command_pattern     |

## Authorized Executions
| Receipt      | Action          | Policy Decision    | Rollback |
|--------------|-----------------|--------------------|----------|
| rcpt_a1b2c3  | write_local     | allow_with_receipt | yes      |
| rcpt_d4e5f6  | execute_command | allow_with_receipt | yes      |
| rcpt_g7h8i9  | vcs_mutation    | approved (high)    | yes      |
| ...          | ...             | ...                | ...      |

## Chain Integrity
Event chain: 47 events, 47 hash-verified, 0 gaps
Receipt chain: 12 receipts, 12 signed, 0 unsigned

## Chain Completeness
- Execution contracts: 12/12 (100%)
- Evidence cases: 12/12 (100%)
- Authorization plans: 12/12 (100%)
- Reconciliations: 11/12 (92%)

## Verdict: CLEAN EXECUTION
All actions were authorized, receipted, and verifiable.
No integrity gaps detected.
```

```bash
# View the proof summary for a completed task
hermit task proof <task_id>

# Export the full proof bundle for long-term retention
hermit task proof-export <task_id>
```

---

## Why This Matters

Every scenario above shares the same underlying requirement: **the agent's work must be inspectable, attributable, and recoverable after the fact.**

Most agent runtimes treat governance as a UX feature -- a confirmation dialog before a tool call. Hermit treats it as an execution primitive. The difference becomes visible when someone asks:

- *"Which agent made this change, and who authorized it?"*
  The kernel ledger has the answer: task ID, principal, approval record, capability grant.

- *"Can we prove that no unauthorized actions were taken during the overnight run?"*
  Export the proof bundle. The governance assurance report has a verdict.

- *"If the agent's change caused a regression, can we surgically roll it back?"*
  Recursive rollback planning traces receipt dependencies and undoes them leaf-first.

- *"Does our AI-assisted development process satisfy our SOC 2 control requirements?"*
  Hash-chained events, signed receipts, Merkle proofs, and anchored proof bundles are the evidence.

If your agent runtime cannot answer these questions from durable records, you are one audit away from shutting down your AI automation program.

These capabilities aren't possible with agent frameworks alone -- just as multi-user computing wasn't possible before operating systems.

Hermit is built so the answer is always in the ledger.

---

## See Also

- [Getting Started](./getting-started.md)
- [Architecture](./architecture.md)
- [MCP Integration](./mcp-integration.md)
- [FAQ](./faq.md)
