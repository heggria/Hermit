# Hermit v0.3 — The Task OS Release

**Your AI agents now run on a real operating system.**

Hermit v0.3 is the largest release since the project's inception: 40+ commits, 506 files changed, and 103,000 lines of new code. This release transforms Hermit from a governed agent runtime into a full **Task Operating System** — with programs, teams, worker pools, competitive deliberation, self-iteration, and a verification pipeline built for production-grade governance.

Just as Unix gave processes a kernel, Hermit gives AI tasks an operating system — with process management, permission models, audit logs, and rollback. Tasks are processes. Capability grants are file permissions. The ledger is `/proc`. Receipts are audit logs. Plugins are kernel modules. The analogy is not decorative — it is the architecture.

Every mutation is still governed. Every action still produces a receipt. Every proof chain is still hash-linked and exportable. But now the kernel can orchestrate dozens of agents, split work across role-bound worker pools, and even improve itself — all under the same governance guarantees.

---

## Highlights

- **Programs and Teams (Process Groups and User Accounts)** — model complex initiatives as hierarchical work graphs with milestones, role slots, and governed lifecycle transitions
- **Worker Pool Manager (CPU Scheduler)** — role-bound, conflict-aware slot management with per-supervisor caps and workspace isolation
- **Competitive Deliberation (Kernel-Space Decision Making)** — multiple candidates propose, critique, and arbitrate before high-risk actions execute
- **Self-Iteration Pipeline** — Hermit can improve itself through a governed research-spec-execute-verify-reconcile lifecycle
- **Benchmark Verification (System Health Monitor)** — promotion gates backed by benchmark profiles, threshold enforcement, and baseline regression detection
- **MCP Supervisor Protocol** — external orchestrators (Claude Code, Cursor, Windsurf) can submit tasks, await completion, and export proofs via Streamable HTTP
- **5,834 tests at 93% coverage** — one of the most extensively tested agent kernels available

---

## Kernel

### Programs and Teams (Process Groups and User Accounts)

The kernel now supports a full organizational hierarchy: **Program -> Team -> Milestone -> Role -> Task**. Programs group related work under a high-level goal. Teams assemble role-bound workers. Milestones define verification checkpoints. The entire structure is durable, governed, and inspectable through the ledger.

- `ProgramRecord` with lifecycle: `draft -> active -> paused -> blocked -> completed | failed`
- `TeamRecord` with role slot specs and milestone graphs
- `MilestoneRecord` with verification gates at each checkpoint
- `ProgramManager` compiles human goals into structured, governable work graphs
- `ProgramToolService` exposes program operations as governed MCP tools

### Task Controller Evolution

The TaskController is now topology-adaptive, verification-driven, approval-parkable, and workspace-aware:

- **Governed Ingress** — all incoming work is classified (new work, status query, control command) and routed through policy before any state mutation
- **Governor** — intent resolution with confidence scoring, bilingual keyword matching, and structured control actions (pause, resume, escalate, promote)
- **Staleness Guard** — watchdog that fails tasks stuck beyond configurable TTL, with state-aware terminal transitions (PAUSED goes to CANCELLED, not FAILED)
- **Status Projections** — real-time task status views derived from the event stream

### Supervisor Protocol

A structured contract layer for multi-agent coordination:

- `TaskContractPacket` — frozen dataclass carrying goal, scope, constraints, acceptance criteria, risk band, and verification requirements
- `CompletionPacket` — structured completion reports with changed files, artifacts, and known risks
- `VerdictPacket` — acceptance verdicts with per-criterion checks and recommended next actions
- `InteractionType` enum: `handoff`, `query`, `escalation`, `feedback`

### Ledger Schema v18

The SQLite event journal has been upgraded to schema v18 with 12 store mixins:

- New mixins: `ProgramStoreMixin`, `KernelTeamStoreMixin`, `SelfIterateStoreMixin`, `CompetitionStoreMixin`, `DelegationStoreMixin`
- All store mixins inherit from `KernelStoreTypingBase` for shared typing
- Mixin-based composition keeps the store extensible without monolithic growth

---

## Execution

### Worker Pool Manager (CPU Scheduler)

Thread-safe, role-bound worker slot management with four layers of admission control:

1. **Per-role limits** — each `WorkerRole` has a fixed number of concurrent slots
2. **Global active cap** — optional ceiling across all roles
3. **Per-supervisor limit** — caps how many slots one supervisor may hold
4. **Conflict-domain limits** — prevents multiple workers from operating on the same workspace simultaneously

### Competitive Deliberation (Kernel-Space Decision Making)

For high-risk decisions, the kernel now runs a formal deliberation round:

- **Triggers**: high-risk planning, high-risk patches, ambiguous specs, benchmark disputes, post-execution review
- **Candidates** submit structured proposals with cost/risk/reward estimates and contract drafts
- **Critics** raise typed, severity-graded critiques against proposals
- **Arbitration** produces a governed decision with recorded rationale
- **Debate bundles** capture the full deliberation for audit and replay

### Fork-Join Concurrency

Self-spawning subtasks with governed delegation:

- Tasks can fork child subtasks with independent governance paths
- Join barriers support multiple strategies: `all_required`, `any_sufficient`, `majority`, `best_effort`
- Data flow bindings wire upstream outputs to downstream inputs
- Pool dispatch routes work to the right worker slots based on role and availability

### DAG Execution

Workspace-aware DAG execution with best-effort cascades:

- Topology-adaptive dispatch that respects workspace leases
- Dispatch recovery handles drift and partial failures gracefully
- Auto-park coordination for approval-blocked DAG nodes
- Observation hooks for real-time DAG execution monitoring

### Dispatch Recovery

Robust recovery for real-world failure modes:

- Drift detection when execution state diverges from plan
- Reconciliation executor for post-failure state cleanup
- Best-effort cascade semantics — partial DAG completion is a valid outcome
- Execution contracts enforce pre/post-conditions around every step attempt

---

## Verification

### Benchmark Framework (System Health Monitor)

A new verification subsystem that gates promotions on measured quality:

- **Benchmark Profiles** — named configurations with metrics, thresholds, and baseline references per task family (`governance_mutation`, `runtime_perf`, `surface_integration`, `learning_template`)
- **Benchmark Registry** — profile storage and lookup
- **Benchmark Routing** — maps tasks to appropriate benchmark profiles based on task family classification
- **Benchmark Runs** — captured with raw metrics, threshold results, environment tags, and commit references
- **Benchmark Verdicts** — `satisfied` or `violated`, integrated into the reconciliation pipeline

### Execution Contracts

Formal pre/post-condition enforcement around governed execution:

- Contracts define what must be true before and after each step attempt
- Contract verification produces evidence that feeds into proof bundles
- Failed contracts trigger reconciliation, not silent continuation

### Proof and Receipt Improvements

- Receipt handler improvements for more reliable HMAC-SHA256 signing
- Reconciliation executor integration with benchmark verdicts
- Proof export now covers the expanded execution topology (programs, teams, milestones)

---

## Self-Iteration

Hermit can now improve itself through a governed pipeline:

### Iteration Kernel

A strict state machine driving self-improvement iterations:

```
draft -> admitted -> researching -> specifying -> executing -> verifying -> reconciling -> accepted | rejected
```

- **Admission control** — risk band validation, seed chain depth limits (max 10), budget checks
- **Promotion gates** — benchmark pass + replay verification + reconciliation clean before any change is accepted
- **Lesson extraction** — every iteration produces a `IterationLessonPack` that feeds into future iterations
- **Next-seed generation** — accepted iterations can spawn follow-up iterations, forming governed improvement chains

### Iteration Bridge

Connects the iteration kernel to the task execution layer:

- Workspace isolation for self-modification (changes happen in isolated workspaces)
- Merge verification before promotion to system capability
- Governed self-surgery — the kernel applies its own governance rules to its own modifications

### Spec Queue

Backlog management for self-improvement specs:

- `hermit_spec_queue` MCP tool for listing, adding, removing, and reprioritizing specs
- Priority-based scheduling with status tracking
- Integration with `hermit_submit_iteration` for pipeline admission

---

## MCP

### Hermit MCP Server

A new builtin plugin exposes the full kernel via Streamable HTTP for supervisor agents:

- **Task tools**: `hermit_submit`, `hermit_submit_dag_task`, `hermit_task_status`, `hermit_list_tasks`, `hermit_await_completion`, `hermit_cancel_task`, `hermit_task_output`, `hermit_task_proof`
- **Approval flow**: `hermit_pending_approvals`, `hermit_approve`, `hermit_deny`
- **Self-iteration**: `hermit_submit_iteration`, `hermit_spec_queue`, `hermit_iteration_status`
- **Observability**: `hermit_metrics`, `hermit_benchmark_results`, `hermit_lessons_learned`

Any MCP-capable client (Claude Code, Cursor, VS Code Copilot, custom tooling) can now orchestrate Hermit as a governed execution backend.

### Supervisor Extensions

- `hermit_await_completion` blocks server-side until tasks reach terminal state — no polling needed
- `hermit_task_output` returns structured execution summaries with receipts and effects
- `hermit_task_proof` exports tiered proof bundles (summary/standard/full) with Merkle inclusion proofs

---

## Plugins

### Memory System (24 Modules)

The memory subsystem has been rebuilt from the ground up with 24 specialized modules:

- **Episodic memory** — experience-based recall with temporal decay
- **Procedural memory** — learned action patterns and templates
- **Knowledge graph** — entity-relationship storage with graph traversal
- **Hybrid retrieval** — combined semantic + token-index search with reranking
- **Confidence scoring** — evidence-weighted belief tracking
- **Anti-pattern detection** — identifies and flags recurring failure modes
- **Memory quality assessment** — governance-aware quality scoring
- **Taxonomy classification** — hierarchical topic organization
- **Consolidation** — periodic memory compaction with evidence preservation
- **Decay models** — time-based relevance adjustment
- **Working memory** — active context window management
- **Lineage tracking** — full provenance chain for every memory entry

All memory promotion still requires evidence references. The governance philosophy is unchanged; the implementation is now production-grade.

### New Hooks

- **benchmark** — benchmark runner and iteration learner
- **decompose** — intelligent task decomposition and spec generation
- **metaloop** — meta-loop lifecycle management and subtask completion
- **quality** — governed code review and test skeleton generation
- **research** — auto-research across codebase, web, docs, and git history
- **subtask** — subtask spawning support with governed delegation
- **trigger** — evidence-backed task generation from execution results

### Adapter Improvements

- Feishu adapter stability fixes (terminal check, regex escaping, scheduler reload)
- Slack adapter (Socket Mode) now available
- Telegram adapter available

---

## Infrastructure

### Python 3.13+

Hermit now requires Python 3.13+ (`pyproject.toml` specifies `>= 3.13`).

### i18n

All hardcoded Chinese strings have been extracted to the i18n system. Full locale support for `en-US` and `zh-CN` via structured locale files.

### Test Suite

- **5,834 tests** passing
- **93% code coverage**
- pytest-xdist parallel execution
- Comprehensive unit tests for all new kernel modules (deliberation, iteration, programs, teams, workers, benchmarks, staleness guard, governor, pool dispatch, supervisor protocol)

---

## Breaking Changes

### Ledger Schema Migration

The kernel ledger has been upgraded from schema v10 to **schema v18**. Hermit will attempt automatic migration from supported schema versions. If migration fails:

```bash
# Back up your existing state
cp -r ~/.hermit/kernel ~/.hermit/kernel.backup

# Reinitialize (loses task history)
rm ~/.hermit/kernel/state.db
hermit init
```

### KernelStore API

The `KernelStore` has been refactored into a mixin-based architecture. If you have custom code that directly imports from `store.py`:

- `store_records.py` methods have changed signatures for API consistency
- `store_v2.py` introduces execution contracts, evidence cases, and authorization plans
- New store mixins (`store_programs.py`, `store_teams.py`) must be included if you subclass KernelStore

### Hook Event Changes

- `SCHEDULE_RESULT` has been removed. Use `DISPATCH_RESULT` instead.
- New events: `SUBTASK_SPAWN`, `SUBTASK_COMPLETE`

### Task State Machine

- New state: `blocked` (for approval-blocked tasks)
- PAUSED tasks now transition to CANCELLED (not FAILED) when timed out by the staleness guard

---

## Migration Guide

### From v0.2.x

1. **Update Python**: Ensure you have Python >= 3.13
2. **Update Hermit**:
   ```bash
   cd /path/to/hermit
   git checkout release/0.3
   make install
   ```
3. **Check ledger migration**: Hermit will auto-migrate on first run. Check logs for migration warnings.
4. **Update hook references**: Replace any `SCHEDULE_RESULT` references with `DISPATCH_RESULT`
5. **Update custom store code**: If you extend `KernelStore`, update to the new mixin architecture

### New MCP Integration

To use Hermit as a governed backend from Claude Code or other MCP clients, add the Hermit MCP server to your client configuration. See `src/hermit/plugins/builtin/mcp/hermit_server/` for setup details.

---

## Philosophy

Hermit v0.3 is built on a conviction: **AI agents need operating systems, not frameworks.**

Every computing platform eventually needed an operating system. Mainframes got them in the 1960s. Personal computers got them in the 1980s. Mobile devices got them in the 2000s. AI agents are the next computing platform — and they need an OS too. Not a framework that hands you building blocks and wishes you luck, but a kernel that interposes on every privileged operation with governance, accountability, and recoverability.

When an agent modifies your filesystem, deploys to production, or rewrites its own code, you need more than a try/catch — you need approvals, receipts, proof chains, and rollback. These are not novel ideas. They are the same ideas that drove operating system design for sixty years: isolation, least privilege, audit trails, and controlled access to shared resources. Hermit applies them to the agent domain.

### The OS Analogy

The mapping between traditional OS concepts and Hermit's architecture is not metaphorical — it is structural:

| OS Concept | Hermit Equivalent |
|---|---|
| Process | Task |
| System call | Governed action |
| File permissions | Capability grants |
| Process isolation | Workspace leases |
| Audit log | Receipts + proofs |
| Kernel module | Plugin |
| `/proc` filesystem | Kernel ledger |
| Package manager | Plugin manager |
| Process groups | Programs |
| User accounts | Teams and roles |
| CPU scheduler | Worker pool manager |
| `fork()` / `wait()` | Fork-join concurrency |

This release pushes that conviction further than ever. Hermit can now orchestrate teams of agents, run competitive deliberation before high-risk decisions, and even improve itself — all under the same governance guarantees that protect a single file write.

The self-iteration pipeline is perhaps the most meaningful addition. A kernel that can modify itself, but only through its own governance gates — admission control, benchmark verification, reconciliation, and promotion — is not just a technical achievement. It is a statement about how autonomous systems should work: not by removing constraints, but by making constraints intelligent enough to permit safe evolution.

Every receipt is still signed. Every proof chain is still hash-linked. Every mutation is still governed. The kernel trusts no one — not even itself.

---

## What's Next

Hermit v0.4 will focus on:

- **Multi-node coordination** — distributed kernel instances with consensus-based governance
- **Operator dashboard** — real-time visibility into programs, teams, and worker pools
- **Proof anchoring** — external anchoring of proof bundles to git, blockchain, or transparency logs
- **Plugin marketplace** — community-contributed hooks, tools, and adapters

---

## Contributors

Hermit v0.3 was built by the Hermit core team and contributors, with significant portions of the kernel code written, tested, and verified through Hermit's own governed self-iteration pipeline.

To get started:

```bash
# Install
brew install heggria/tap/hermit-agent
# or
pip install hermit-agent

# Initialize
hermit init
hermit chat
```

Star us on GitHub. Join the Discord. File an issue. Or better yet — submit a spec and let Hermit iterate on it.

---

## See Also

- [Getting Started](./getting-started.md)
- [Architecture](./architecture.md)
- [MCP Integration](./mcp-integration.md)
