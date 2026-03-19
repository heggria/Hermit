# Frontier Patterns Research Report: Agent Parallel Task Orchestration (2025-2026)

## Executive Summary

This report surveys the frontier of agent parallel task orchestration research from 2025-2026, covering ten major systems/papers and three supplementary research threads. The goal is to extract concrete architectural patterns and design decisions applicable to Hermit's evolution toward a governed Task OS kernel.

**Key findings:**

1. **Topology routing is the new frontier.** AdaptOrch and AgentConductor demonstrate that orchestration topology selection (not model selection) is the dominant performance lever, with 12-23% gains from adaptive topology routing alone.
2. **Communication density must be actively managed.** AgentConductor's edge_budget and Token Coherence's lazy invalidation show 68-95% token savings are achievable through principled communication control.
3. **Verification-driven loops are essential for complex tasks.** VMAO's plan-execute-verify-replan cycle with verifier-driven subgraph reopening is the emerging consensus pattern for quality assurance.
4. **Coordination has measurable costs and diminishing returns.** Formal scaling studies show coordination yields negative returns once single-agent baselines exceed ~45%, and sequential reasoning degrades 39-70% under multi-agent coordination.
5. **Workspace delegation (not just message passing) is emerging.** AWCP's Unix-philosophy file-based workspace projection is a fundamentally different collaboration model that maps directly to Hermit's governed execution.
6. **Durable execution is table stakes.** LangGraph's checkpoint/interrupt/resume and Temporal's deterministic replay represent two mature paradigms that any serious kernel must match or exceed.

---

## 1. AdaptOrch: Topology Routing for Multi-Agent Systems

**Paper:** arXiv:2602.16873 (February 2026) by Geunbin Yu

### Core Technical Mechanism

AdaptOrch argues that as frontier LLMs converge in capability, **orchestration topology -- not model selection -- dominates system-level performance**. The framework dynamically maps task dependency DAGs to one of four canonical topologies.

**Three innovations:**

1. **Performance Convergence Scaling Law** -- formalizes when orchestration selection outweighs individual model capability. As model performance converges, the marginal gain from better orchestration exceeds the gain from a better model.

2. **Topology Routing Algorithm** -- O(|V| + |E|) algorithm that analyzes task decomposition DAGs and selects among:
   - **Parallel**: Independent subtasks executed simultaneously, results synthesized
   - **Sequential**: Linear chain where each step depends on prior output
   - **Hierarchical**: Multi-level coordination with manager/worker decomposition
   - **Hybrid**: Mixed topology combining elements based on subgraph properties

3. **Adaptive Synthesis Protocol** -- provably-terminating protocol for combining parallel agent outputs, using heuristic consistency scoring.

### Key Design Decisions

- Topology selection is a **graph-structural decision**, not a semantic one -- the DAG shape determines the topology, not the task content
- O(|V| + |E|) complexity means routing adds negligible overhead
- Hybrid topology is the default for complex tasks; pure topologies are special cases

### Results

12-23% improvement over static single-topology baselines across coding, reasoning, and retrieval tasks using identical underlying models.

### Applicability to Hermit

Hermit's DAG execution system (`hermit_submit_dag_task`) already supports dependency graphs with `depends_on` and `join_strategy`. The gap is **runtime topology selection** -- currently the user/orchestrator must manually specify the DAG structure. AdaptOrch's routing algorithm could be integrated at the ingress layer to auto-select topology based on task decomposition analysis.

**Concrete adoption patterns:**

- Add a `TopologyRouter` component at `kernel/task/ingress/` that analyzes submitted DAGs
- Implement graph analysis: count independent subgraphs (parallelizable), identify critical paths (sequential), detect hierarchical structures
- Support automatic topology annotation on `StepRecord` for auditing
- The four canonical topologies map to Hermit's existing `join_strategy` values: `all_required` (sequential), `any_sufficient` (parallel racing), `majority` (parallel voting), `best_effort` (parallel with fault tolerance)

---

## 2. AgentConductor: Communication Density Control

**Paper:** arXiv:2602.17100 (February 2026)

### Core Technical Mechanism

AgentConductor uses an RL-optimized LLM orchestrator to dynamically generate interaction topologies per task, with a novel **density-aware topology control** system.

**Key innovations:**

1. **Topological Density Function** -- a mathematical characterization of communication patterns. Density = (actual edges) / (possible edges) in the agent interaction DAG. Higher density = more inter-agent communication = more tokens consumed.

2. **Edge Budget (edge_budget)** -- a hard cap on communication density per difficulty level. The system partitions tasks by difficulty and assigns density upper bounds:
   - Easy tasks: low edge_budget (minimal inter-agent communication)
   - Hard tasks: higher edge_budget (more coordination justified)

3. **Difficulty Interval Partitioning** -- avoids excessive pruning by measuring density bounds per difficulty interval rather than globally. This prevents easy tasks from being over-coordinated and hard tasks from being under-coordinated.

### Key Design Decisions

- Communication is treated as a **budget to be spent**, not an unlimited resource
- Topology is generated end-to-end by the orchestrator, not pre-defined
- Feedback-driven: execution results refine future topology generation via RL

### Results

- 14.6% accuracy gain (pass@1)
- 13% communication density reduction
- **68% token cost reduction** vs strongest baseline

### Applicability to Hermit

Hermit currently has no communication budget concept. Tasks can spawn unlimited subtasks and inter-step communication is unbounded. AgentConductor's edge_budget maps to a governance primitive.

**Concrete adoption patterns:**

- Add `communication_budget` field to `TaskRecord` or `TaskContract`
- Implement density tracking in the `KernelDispatchService`: count inter-step data flows as edges
- Add `DensityGuard` as a policy evaluator that blocks new step spawning when budget is exhausted
- Map task priority/difficulty to default budgets in the policy profile
- Track density metrics in `KernelStore` for post-hoc analysis
- Expose density in proof bundles for governance auditing

---

## 3. VMAO: Verification-Driven Multi-Agent Orchestration

**Paper:** arXiv:2603.11445 (March 2026) by Zhang et al.

### Core Technical Mechanism

VMAO implements a **plan-execute-verify-replan** loop:

1. **Plan**: Decompose complex query into a DAG of sub-questions with dependency edges
2. **Execute**: Run specialized agents in parallel with automatic context propagation along dependency edges
3. **Verify**: LLM-based verifier evaluates completeness and quality of aggregated results
4. **Replan**: If verification fails, identify gaps and generate new sub-questions to address them

The verifier is the key innovation -- it acts as an **orchestration-level coordination signal**, not just a quality check. Verification failures trigger **subgraph reopening**: specific branches of the DAG are re-planned and re-executed while completed branches are preserved.

### Key Design Decisions

- Verification is structural, not cosmetic -- it drives the control flow
- Configurable stopping conditions balance quality vs resource consumption (max iterations, quality threshold, budget cap)
- Context propagation is automatic along DAG edges -- upstream results flow to downstream agents without manual plumbing
- Sub-questions are the unit of work, not arbitrary "steps"

### Results

Answer completeness improved from 3.1 to 4.2 and source quality from 2.6 to 4.1 (1-5 scale) vs single-agent baselines on 25 expert-curated market research queries.

### Applicability to Hermit

Hermit's receipt/proof system provides the verification infrastructure, but lacks **verifier-driven control flow**. Currently, verification is post-hoc (proof export after completion). VMAO's pattern would make verification an active scheduling input.

**Concrete adoption patterns:**

- Add a `VerificationStep` step kind that runs after each DAG phase completes
- Implement `VerifierGuard` in the policy layer that evaluates step outputs against quality criteria
- Support **subgraph reopening**: when verification fails, spawn new steps targeting identified gaps while preserving completed step receipts
- Add `verification_score` to `StepRecord` for tracking quality across iterations
- Configurable stopping: add `max_replan_iterations` and `quality_threshold` to `TaskContract`
- Map to existing Hermit primitives: verification failure = new `StepAttempt` with `recovery` kind, subgraph reopening = new child steps with `depends_on` pointing to the verification step

---

## 4. DOVA: Deliberation-First Multi-Agent Orchestration

**Paper:** arXiv:2603.13327 (March 2026) by Aaron Shen, Alfred Shen

### Core Technical Mechanism

DOVA introduces a **composable three-phase pipeline** for autonomous research:

1. **Ensemble Phase** -- multiple agents independently produce diverse outputs for the same query, maximizing coverage and perspective diversity
2. **Blackboard Phase** -- results are posted to a shared, transparent blackboard where all agents can read, annotate, and cross-reference findings. The blackboard provides **structured visibility** into the collective knowledge state
3. **Iterative Refinement Phase** -- agents iteratively improve the synthesized output, resolving contradictions and filling gaps identified in the blackboard

**Deliberation-First Orchestration**: Before any tool invocation, the system performs explicit meta-reasoning informed by a persistent user model and entity-aware conversation context. This prevents premature action.

**Adaptive Multi-Tiered Thinking**: A six-level token-budget allocation scheme:
- Simple tasks: minimal token budget (40-60% cost reduction)
- Complex tasks: full reasoning depth preserved
- Budget level selected based on task complexity classification

### Key Design Decisions

- Deliberation before action is enforced architecturally, not just prompted
- The blackboard is a **first-class architectural component**, not an afterthought
- Three phases are composable -- you can use ensemble+blackboard without refinement, or blackboard+refinement without ensemble
- User model persistence enables personalized orchestration across sessions

### Typed Blackboard Architecture

While the paper does not use the exact term "typed blackboard," the blackboard mechanism provides structured transparency with typed fields. The related MACOG paper (arXiv:2510.03902) explicitly implements a **shared-blackboard, finite-state orchestrator layer** where:
- Eight specialized agents write to typed sections of the blackboard
- A Memory Curator agent manages shared state
- The finite-state orchestrator routes work based on blackboard state

BIGMAS (arXiv:2603.15371) extends this with a **centralized shared workspace** inspired by global workspace theory of human cognition, where agents coordinate exclusively through typed workspace channels.

### Applicability to Hermit

Hermit has artifact lineage and evidence-bound memory, but no **shared blackboard** primitive. The blackboard pattern maps to a governed workspace where multiple steps can read/write structured state.

**Concrete adoption patterns:**

- Implement `TaskBlackboard` as a typed key-value store scoped to a task, stored in `KernelStore`
- Define blackboard schema per task type: `findings`, `contradictions`, `gaps`, `synthesis`
- Add `blackboard_read` and `blackboard_write` as governed operations with receipts
- Support blackboard subscriptions: steps can watch for new entries and trigger reactively
- The three-phase pipeline maps to Hermit DAG phases: ensemble steps (parallel, `any_sufficient`), blackboard synthesis step, refinement steps (sequential, iterative)
- Deliberation-first maps to a `PlanningStep` kind that must complete before any `ExecutionStep` in a task

---

## 5. AWCP: Workspace Delegation Protocol

**Paper:** arXiv:2602.20493 (February 2026) by Nie, Guo, Chen, Zhou, Zhang

### Core Technical Mechanism

AWCP addresses a fundamental limitation of current agent protocols: **agents can only exchange messages, not workspaces**. Instead of sending data through messages and requiring the receiving agent to reconstruct context, AWCP **projects the delegator's workspace** to the executor.

**Architecture:**

1. **Control Plane** -- lightweight protocol handling delegation lifecycle: initiate, project, monitor, revoke
2. **Transport Plane** -- pluggable transport mechanisms (filesystem sync, cloud storage, SSH, etc.)
3. **Workspace Projection** -- the delegator creates a scoped view of its workspace (specific files, tools, permissions) and projects it to the executor
4. **Unmodified Local Toolchains** -- the executor operates on projected files using its own tools without modification

**Unix Philosophy**: Everything is a file. Agents collaborate through shared filesystems rather than message passing. This eliminates the error-prone process of reconstructing context from messages.

### Key Design Decisions

- **Asymmetric collaboration**: Delegator and executor have different capabilities and permissions
- **Separation of control and transport**: Control plane is standardized; transport is pluggable
- **MCP integration**: Reference implementation includes MCP tool integration, making AWCP tools available through the MCP protocol
- **Scoped projection**: Delegators control exactly what is visible to executors (least-privilege workspace access)

### Applicability to Hermit

This maps directly to Hermit's governed execution model. Currently, Hermit tasks operate in a shared filesystem context. AWCP's workspace projection would enable:

**Concrete adoption patterns:**

- Implement `WorkspaceProjection` in `kernel/authority/` that creates scoped workspace views per task
- Each task gets a projected workspace: specific files/directories + specific tools + specific permissions
- Workspace projection creates a receipt: what was projected, to whom, for how long
- Revocation is a governed operation: delegator can revoke workspace access, triggering task suspension
- Map to existing Hermit primitives: `CapabilityGrant` already scopes tool access; extend it to scope filesystem access
- Support workspace inheritance: child tasks inherit parent workspace projection with possible narrowing
- AWCP's control/transport separation maps to Hermit's policy/execution separation

---

## 6. LangGraph: Durable Execution Patterns (2025-2026)

**Source:** LangGraph documentation (docs.langchain.com)

### Core Technical Mechanism

LangGraph implements **checkpoint-based durable execution** with graph-structured agent workflows.

**Checkpoint System:**
- State captured as snapshots at each **super-step boundary** (one execution cycle where all scheduled nodes run in parallel)
- `StateSnapshot` contains: `values` (channel state), `next` (scheduled nodes), `config` (thread/checkpoint IDs), `metadata` (source, step counter), `parent_config` (previous checkpoint), `tasks` (pending with interrupt data)
- Thread-based state management: `thread_id` is the primary key for all state operations

**Interrupt/Resume:**
- `interrupt()` raises a special exception that saves state and pauses execution
- Resume via `Command(resume=value)` with the same `thread_id`
- **Critical constraint**: entire node restarts from the beginning on resume, not from the interrupt point
- Side effects before interrupts must be idempotent
- Multiple parallel interrupts supported via interrupt ID mapping

**Time-Travel:**
- `get_state_history()` returns chronologically-ordered snapshots
- Replay from any prior `checkpoint_id` -- re-runs downstream nodes while skipping completed ones
- `update_state()` creates new checkpoint without modifying originals (immutable history)

**Fault Tolerance:**
- Failed node mid-super-step: pending writes from completed nodes preserved
- Resume skips completed nodes, preventing duplicate operations

**Cross-Thread Memory:**
- `Store` interface for persistent memory spanning threads/conversations
- Namespace-based: `(user_id, "memories")`
- Supports semantic search with embedding integration

### Key Design Decisions

- **Graph-first**: Execution flows are explicitly modeled as graphs with nodes and edges
- **Super-step parallelism**: Nodes within a super-step execute in parallel; dependencies are expressed as graph edges
- **Immutable checkpoints**: History is append-only; updates create new checkpoints
- **Checkpointer pluggability**: InMemory, SQLite, PostgreSQL, CosmosDB backends
- **Serialization**: JSON-first with optional pickle fallback and AES encryption

### Applicability to Hermit

Hermit's `KernelStore` (SQLite journal) and receipt system provide similar durability guarantees but with different trade-offs:

| Dimension | LangGraph | Hermit |
|-----------|-----------|--------|
| State model | Graph checkpoint snapshots | Event-sourced journal entries |
| Parallelism | Super-step (batch) | Per-step (streaming) |
| Interrupt model | Special exception + node restart | Approval-based blocking |
| History | Immutable checkpoint chain | Append-only event log |
| Recovery | Skip completed nodes | Receipt-based reconciliation |
| Governance | None (pure execution) | Policy/approval/receipt chain |

**Concrete adoption patterns:**

- Hermit's event-sourced model is architecturally superior for governance (every state change is an auditable event). LangGraph's checkpoint model is simpler but less granular.
- Adopt LangGraph's **time-travel** concept: add `replay_from(step_id)` to `TaskController` that reconstructs task state from journal events up to a point and re-executes from there
- Adopt the **super-step** concept for DAG execution: group independent steps into super-steps, execute in parallel, checkpoint at super-step boundaries
- LangGraph's `Store` (cross-thread memory with semantic search) maps to Hermit's evidence-bound memory. Hermit's is stronger (evidence governance) but could benefit from LangGraph's namespace model.

---

## 7. Temporal for Agent Workflows

**Source:** Temporal documentation and blog posts

### Core Technical Mechanism

Temporal provides **durable code execution** -- write normal imperative code and the platform guarantees it runs to completion despite failures, restarts, and infrastructure issues.

**Key distinction from graph-based systems (e.g., LangGraph):**

- **Durable code** (Temporal): Write ordinary functions/methods. The platform records execution history and replays deterministic code on recovery. Non-deterministic operations (LLM calls, tool invocations) are isolated as **Activities**.
- **Static graphs** (LangGraph): Define execution as a graph with nodes and edges. The platform checkpoints graph state.

**Workflow/Activity separation:**
- **Workflows**: Deterministic orchestration code. Must be replay-safe (no random, no clock, no I/O). Holds state naturally in local variables.
- **Activities**: Non-deterministic operations (LLM API calls, tool execution, file I/O). Automatically retried with configurable policies.

**Child Workflows:**
- Parent workflows spawn child workflows for hierarchical decomposition
- `execute_child_workflow()`: start and wait for completion
- `start_child_workflow()`: start and get handle for async monitoring
- `parent_close_policy`: controls whether children terminate or continue when parent closes (TERMINATE vs ABANDON)

**Human-in-the-Loop:**
- Workflow Updates enable interactive patterns: workflow pauses, awaits human signal, resumes
- Natural fit for approval workflows

**Durability guarantees:**
- Automatic retries with exponential backoff
- Timeouts at multiple levels (start-to-close, schedule-to-start, schedule-to-close, heartbeat)
- Workflow history persisted for replay and debugging
- "Code for the happy path only" -- Temporal handles edge cases

### Key Design Decisions

- **Code over configuration**: Orchestration logic is regular code, not YAML/JSON/graph definitions
- **Deterministic replay**: The execution model, not developer discipline, enforces replay safety
- **Activity isolation**: Side effects are explicitly bounded in Activities
- **Single-entity guarantee**: Each workflow ID has exactly one active execution (natural mutex)

### Applicability to Hermit

Hermit's execution model shares Temporal's philosophy (governed execution with receipts) but implements it differently:

| Dimension | Temporal | Hermit |
|-----------|----------|--------|
| Orchestration | Deterministic workflow code | Event-sourced task/step pipeline |
| Side effects | Activities with retry | Governed tool execution with receipts |
| State | Implicit in workflow variables | Explicit in `TaskRecord`/`StepRecord` |
| Recovery | Deterministic replay | Receipt-based reconciliation |
| Human-in-loop | Workflow Updates/Signals | Approval pipeline |
| Hierarchy | Child Workflows | Parent-child task hierarchy |

**Concrete adoption patterns:**

- Adopt Temporal's **Activity isolation** pattern: classify Hermit step kinds into deterministic (routing, aggregation) and non-deterministic (tool execution, LLM calls). Only non-deterministic steps require full governance overhead.
- Adopt the **parent_close_policy** concept: Hermit's parent-child task relationship needs explicit policy for what happens to children when parent completes/fails/cancels. Current behavior: children are recalled. Add support for ABANDON (children continue independently) and TERMINATE (children cancelled immediately).
- Adopt **heartbeat timeouts**: Hermit tasks can hang indefinitely. Add heartbeat expectations to `TaskContract` -- if a step doesn't report progress within N seconds, mark it failed and trigger recovery.
- The **single-entity guarantee** (one active workflow per ID) maps to Hermit's task locking via `FileGuard`. Ensure this is enforced at the kernel level, not just infrastructure.

---

## 8. Anthropic Agent Teams: Cross-Session Parallel Coordination

**Source:** Anthropic engineering blog -- "How we built a multi-agent research system"

### Core Technical Mechanism

Anthropic's system uses an **orchestrator-worker architecture** with two levels of parallelism:

1. **Lead agent spawning**: The lead agent analyzes a query, develops a strategy, and spawns 3-5 subagents in parallel
2. **Subagent tool execution**: Each subagent uses 3+ tools in parallel within its own execution context

**Effort scaling by complexity:**
- Simple fact-finding: 1 agent, 3-10 tool calls
- Direct comparisons: 2-4 subagents, 10-15 calls each
- Complex research: 10+ subagents

**Current limitations (acknowledged by Anthropic):**
- Lead agents execute subagents **synchronously** -- they wait for each batch to complete before proceeding
- Asynchronous execution remains future work due to complexity in "result coordination, state consistency, and error propagation"
- Context window management: agents save plans to Memory to persist context when windows exceed 200K tokens

### Key Design Decisions

- **Claude Opus as lead, Claude Sonnet as workers** -- cheaper, faster models for worker tasks (aligns with Hermit's model selection guidance)
- **Explicit task boundaries**: each subagent receives specific objectives, output format, tool guidance, and scope constraints
- **No cross-subagent communication**: subagents are isolated; only the lead agent synthesizes results

### Results

Multi-agent system with Opus lead + Sonnet workers outperformed single-agent Opus by **90.2%** on internal research eval. Research time reduced by **up to 90%** for complex queries.

### Applicability to Hermit

Hermit already implements this pattern at the MCP level (Claude as orchestrator, Hermit as executor). The gap is **subagent-to-subagent communication** and **asynchronous completion**.

**Concrete adoption patterns:**

- Implement **async subagent completion notification**: instead of synchronous wait, use Hermit's `hermit_await_completion` pattern -- submit all subagents, await any completion, process results, submit follow-up work
- Add **effort estimation** to task ingress: classify incoming tasks by complexity and auto-scale parallelism (1 vs 5 vs 10+ subtasks)
- Implement **result synthesis** as a first-class step kind: after parallel steps complete, a `SynthesisStep` aggregates and reconciles outputs
- Track subagent isolation in governance: each subtask gets its own receipt chain, and the parent task's proof bundle aggregates child proofs

---

## 9. Google A2A Protocol: Agent-to-Agent Communication

**Source:** Google A2A specification and developer blog

### Core Technical Mechanism

A2A (Agent-to-Agent) is a protocol for inter-agent collaboration, complementary to MCP:

**Protocol stack:**
- Built on HTTP, Server-Sent Events (SSE), and JSON-RPC
- Designed for enterprise integration with existing infrastructure

**Core concepts:**

1. **Agent Cards** -- JSON capability advertisements that agents publish for discovery. A client agent queries Agent Cards to find the best remote agent for a task.

2. **Task Lifecycle** -- tasks are the unit of work between agents:
   - Tasks can complete immediately or run long-duration
   - Task outputs are called **artifacts** (fully formed content with specified types)
   - Status synchronization maintained between client and remote agents

3. **Communication Format** -- messages use **parts** (content units like images, text, structured data) with negotiable content types. Supports rich UI: iframes, video, web forms.

4. **Two Agent Roles:**
   - **Client agents**: formulate and communicate tasks
   - **Remote agents**: execute tasks and return artifacts

### A2A vs MCP: Protocol Layering

| Dimension | MCP | A2A |
|-----------|-----|-----|
| Purpose | Tool/context provisioning | Agent-to-agent collaboration |
| Model | Client-server (agent uses tools) | Peer-to-peer (agent delegates to agent) |
| Granularity | Individual tool calls | Complete tasks |
| State | Stateless tool invocations | Stateful task lifecycle |
| Discovery | Tool manifests | Agent Cards |
| Output | Tool results | Artifacts (typed, rich) |

**Complementary relationship**: MCP gives an agent tools and context; A2A lets agents delegate entire tasks to other agents. An agent might use MCP to access a database and A2A to delegate analysis to a specialist agent.

### Applicability to Hermit

Hermit currently uses MCP for tool integration. A2A would enable Hermit to:
- Accept task delegations from external agents (Hermit as A2A remote agent)
- Delegate tasks to external specialist agents (Hermit as A2A client)

**Concrete adoption patterns:**

- Implement Hermit **Agent Card** generation: publish Hermit's task capabilities (governed execution, parallel DAG, approval workflows) as an A2A Agent Card
- Map A2A task lifecycle to Hermit's task model: A2A task creation = `enqueue_task()`, A2A artifacts = Hermit receipts
- Implement A2A adapter in `plugins/builtin/adapters/`: handle incoming A2A requests and map to kernel task operations
- Support A2A **outbound delegation**: when Hermit identifies a subtask it cannot execute locally, delegate via A2A to an external agent
- A2A's artifact model maps to Hermit's artifact lineage system -- external artifacts get lineage tracking just like internal ones

---

## 10. Coordination Tax and Monotonicity

### Formal Research Findings

Three major papers establish the quantitative framework for understanding when coordination helps vs hurts:

#### "Towards a Science of Scaling Agent Systems" (arXiv:2512.08296)

**Core findings with formal models:**

1. **Capability Saturation Threshold (~45%)**: Coordination yields diminishing or negative returns once single-agent baseline performance exceeds approximately 45%. Below this threshold, coordination helps; above it, the overhead exceeds the benefit.

2. **Tool-Coordination Trade-off**: Under fixed computational budgets, tool-heavy tasks suffer disproportionately from multi-agent overhead. The more tools a task requires, the less benefit from adding agents.

3. **Topology-Dependent Error Amplification**:
   - Independent agents (no coordination): errors amplify **17.2x**
   - Centralized coordination: errors contained to **4.4x** amplification
   - Coordination structure fundamentally shapes reliability

4. **Task-Type Results**:
   - Parallelizable tasks: centralized coordination improves by **80.8%**
   - Web navigation: decentralized coordination improves by **+9.2%**
   - Sequential reasoning: **all** multi-agent variants degrade performance by **39-70%**

5. **Predictive Model**: R^2=0.524 cross-validated model predicts optimal coordination strategy for 87% of held-out configurations.

#### Silo-Bench: Communication-Reasoning Gap (arXiv:2603.01045)

**Core finding:** Agents spontaneously form task-appropriate coordination topologies and exchange information actively, yet **systematically fail to synthesize distributed state into correct answers**. The bottleneck is not communication but **distributed reasoning**.

**Implication:** Adding more agents and more communication does not help if the synthesis step is fundamentally limited. Coordination overhead compounds with scale, "eventually eliminating parallelization gains entirely."

#### Token Coherence Protocol (arXiv:2603.15183)

**Core mechanism:** Maps MESI cache coherence protocol (from multiprocessor systems) to artifact synchronization in multi-agent LLM orchestration.

**Innovation:** Lazy invalidation -- instead of broadcasting every state change to all agents, only notify agents when they actually need the updated state. Reduces O(n x S x |D|) to O((n + W) x |D|).

**Results:**
- 95.0% token savings at low variance threshold (V=0.05)
- 84.2% savings even at high variance (V=0.50)
- Protocol verified via TLA+ across ~2,400 states
- Compatible with LangGraph, CrewAI, AutoGen

### Synthesis: When Coordination Is Necessary vs Wasteful

| Condition | Coordination Value | Recommendation |
|-----------|-------------------|----------------|
| Single-agent baseline < 45% | Positive | Add coordination |
| Single-agent baseline > 45% | Negative | Single agent or minimal coordination |
| Task is parallelizable | High (up to 80.8%) | Centralized coordination |
| Task is sequential reasoning | Strongly negative (-39 to -70%) | Single agent only |
| Task requires distributed information | Depends on synthesis quality | Coordinate but invest in synthesis step |
| Tool-heavy tasks | Low (overhead dominates) | Minimize coordination overhead |
| Many agents with shared state | High cost | Use lazy invalidation (Token Coherence) |

### Applicability to Hermit

This research provides the **theoretical foundation for Hermit's task decomposition decisions**.

**Concrete adoption patterns:**

- Implement **coordination cost estimation** at task ingress: before spawning subtasks, estimate whether coordination will help based on task type classification
- Add a **monotonicity check**: if a task's estimated single-agent completion rate exceeds 45%, default to single-task execution unless explicitly overridden
- Implement **Token Coherence** for multi-step tasks sharing artifacts: lazy invalidation of shared context between steps, reducing inter-step communication overhead
- Add **error amplification tracking** to governance metrics: measure how error rates scale with coordination structure
- Support **coordination budget** alongside communication budget: limit the total coordination overhead (measured in tokens, latency, or steps) per task
- The Communication-Reasoning Gap finding suggests investing in better **synthesis steps** rather than more/better coordination infrastructure

---

## Supplementary Research

### OpenAI Codex: Batch-Read Patterns for Codebase Exploration

**Source:** OpenAI Codex product documentation and observed patterns

OpenAI Codex operates in sandboxed cloud environments where each task gets:
- A full clone of the repository
- An isolated container with internet disabled after setup
- Parallel task execution across multiple containers

**Codebase exploration patterns observed:**

1. **Broad-then-narrow**: Initial broad search (file listing, grep for patterns) followed by targeted file reads
2. **Parallel file reads**: Multiple files read simultaneously to build context
3. **Speculative search**: Launch multiple search strategies in parallel, use first successful result
4. **AST-aware exploration**: Parse imports/dependencies to follow code paths rather than searching blindly

**Batch processing model:**
- Multiple tasks submitted simultaneously, each in its own container
- No cross-task state sharing (full isolation)
- Results aggregated by the orchestrating system
- Each container starts from clean repository state

**Applicability to Hermit:**

- Hermit's task isolation model mirrors Codex's container isolation
- Adopt **speculative parallel search**: when exploring a codebase, submit multiple search strategies as parallel subtasks
- The clean-state-per-container model maps to AWCP's workspace projection: each task gets a scoped workspace snapshot
- Batch submission pattern already supported by Hermit's `hermit_submit_task` parallelism

### Typed Blackboard Research (2025-2026)

No papers explicitly use the term "typed blackboard" in the 2025-2026 literature. However, three systems implement the concept under different names:

1. **MACOG** (arXiv:2510.03902) -- "shared-blackboard, finite-state orchestrator layer" with eight specialized agents writing to structured sections. The blackboard is implicitly typed by agent role (Architect writes architecture, Security Prover writes security findings, etc.).

2. **BIGMAS** (arXiv:2603.15371) -- "centralized shared workspace" based on global workspace theory. Agents coordinate exclusively through the workspace, with a global Orchestrator reading the complete shared state for routing decisions. The workspace is typed by contribution category.

3. **DOVA** (arXiv:2603.13327) -- "blackboard transparency" as part of the three-phase pipeline. The blackboard provides structured visibility into collective knowledge state, with typed fields for findings, contradictions, and gaps.

**Emerging pattern:** The typed blackboard is converging toward a **structured, governed, append-only workspace** where:
- Each field has a defined schema (type)
- Writes are attributed to specific agents (provenance)
- Reads are tracked (access governance)
- The orchestrator uses blackboard state for routing decisions

**Applicability to Hermit:**

This maps directly to a `TaskBlackboard` primitive:
```
TaskBlackboard:
  task_id: str
  sections:
    findings: List[Finding]        # typed: source, confidence, evidence_ref
    contradictions: List[Conflict] # typed: finding_a, finding_b, resolution
    gaps: List[Gap]                # typed: question, priority, assigned_step
    synthesis: Optional[Synthesis] # typed: summary, sources, confidence
  access_log: List[AccessEvent]    # who read/wrote what, when
```

### Workspace-Aware Agent Delegation (2025-2026)

Beyond AWCP, the workspace delegation pattern appears in:

1. **Hermit's own worktree isolation** -- the existing `competition` and `delegation` models in Hermit use git worktrees for workspace isolation. This is workspace-aware delegation at the VCS level.

2. **Claude Code worktrees** -- Claude Code's `EnterWorktree`/`ExitWorktree` provide session-scoped workspace isolation with cleanup semantics.

3. **Codex containers** -- OpenAI Codex gives each task a full repository clone in an isolated container. This is maximal workspace isolation (full copy, no sharing).

**Spectrum of workspace delegation:**

| Pattern | Isolation | Sharing | Overhead | Use Case |
|---------|-----------|---------|----------|----------|
| Shared filesystem | None | Full | Minimal | Trusted, sequential tasks |
| Scoped projection (AWCP) | Per-file | Controlled | Low | Complementary skills |
| Git worktree | Branch-level | Via merge | Medium | Parallel code changes |
| Container clone (Codex) | Full | None | High | Untrusted, parallel tasks |

**Applicability to Hermit:**

Hermit should support the full spectrum, selecting isolation level based on task requirements and policy profile:
- `autonomous` policy: shared filesystem (trust the agent)
- `default` policy: scoped projection (controlled access)
- `supervised` policy: worktree isolation (full isolation, merge on approval)
- `readonly` policy: read-only projection (observation only)

---

## Cross-Cutting Synthesis: Patterns for Hermit Kernel Evolution

### Pattern 1: Adaptive Topology Router

**From:** AdaptOrch, AgentConductor, Anthropic scaling research

**Implementation:**

```
TaskIngress → TopologyAnalyzer → TopologyRouter → DAG Construction → Execution
                                      |
                                      v
                              MonotonicityCheck
                              (skip multi-agent if single-agent baseline > 45%)
```

The topology router examines the task goal, decomposes it into subtasks, analyzes dependency structure, and selects the optimal topology. It also applies the monotonicity check to avoid wasteful coordination.

### Pattern 2: Communication-Governed Execution

**From:** AgentConductor, Token Coherence, coordination tax research

**Implementation:**

```
TaskContract:
  communication_budget: int        # max inter-step tokens
  coordination_budget: int         # max coordination overhead
  density_cap: float               # max edge density in DAG

DensityGuard (policy evaluator):
  on_step_spawn: check density < density_cap
  on_inter_step_message: check communication_budget not exhausted
  on_coordination_event: check coordination_budget not exhausted

ArtifactCoherence (lazy invalidation):
  on_artifact_write: mark dependent steps as stale (do not broadcast)
  on_step_read: check staleness, send update only if needed
```

### Pattern 3: Verification-Driven Scheduling

**From:** VMAO, DOVA deliberation-first

**Implementation:**

```
DAG Phase 1 (parallel execution)
  → Verification Step (evaluate completeness)
    → if PASS: proceed to next phase
    → if FAIL: subgraph reopening
      → identify gaps
      → spawn new steps targeting gaps
      → re-execute failed subgraph
      → re-verify
  → Deliberation Gate (meta-reasoning before action)
    → assess whether current plan is adequate
    → if not: replan before executing
```

### Pattern 4: Typed Blackboard Workspace

**From:** DOVA, MACOG, BIGMAS

**Implementation:**

```
TaskBlackboard:
  schema: defined per task type
  sections: typed key-value stores with provenance
  access_governance: read/write receipts
  orchestrator_view: complete blackboard state for routing

Integration with Hermit:
  blackboard_write → governed operation → receipt
  blackboard_read → access logged → used in evidence chain
  routing_decision → based on blackboard state → auditable
```

### Pattern 5: Workspace Projection

**From:** AWCP, Codex containers, Hermit worktrees

**Implementation:**

```
WorkspaceProjection:
  source: parent task workspace
  scope: file patterns, tool allowlist, permission level
  transport: filesystem (local), sync (remote), clone (isolated)
  lifecycle: create → active → revoke
  governance: projection receipt, access log, revocation receipt

Policy-driven selection:
  autonomous → shared filesystem
  default → scoped projection
  supervised → worktree isolation
  readonly → read-only projection
```

### Pattern 6: Durable Execution with Time-Travel

**From:** LangGraph checkpoints, Temporal replay

**Implementation:**

```
Hermit already has event-sourced journal. Add:

replay_from(step_id):
  1. Read journal events up to step_id
  2. Reconstruct task state at that point
  3. Re-execute from step_id forward
  4. New execution creates new events (does not overwrite)

super_step_checkpoint():
  1. Group independent steps into super-steps
  2. Execute super-step in parallel
  3. At super-step boundary: journal checkpoint event
  4. On recovery: skip completed super-steps

heartbeat_timeout:
  1. Steps must report progress within configured interval
  2. No heartbeat → mark failed → trigger recovery
  3. Recovery = new StepAttempt, not step restart
```

---

## Priority Ranking for Hermit Adoption

Based on impact, feasibility, and alignment with Hermit's existing architecture:

| Priority | Pattern | Impact | Effort | Rationale |
|----------|---------|--------|--------|-----------|
| P0 | Verification-driven scheduling | High | Medium | Directly improves output quality; builds on existing receipt system |
| P0 | Monotonicity check | High | Low | Prevents wasteful coordination; simple policy guard |
| P1 | Communication budget | High | Medium | Governance primitive for token cost control |
| P1 | Typed blackboard | High | Medium | Enables ensemble+refinement patterns; new primitive |
| P1 | Heartbeat timeout | High | Low | Prevents hung tasks; simple addition to TaskContract |
| P2 | Topology router | Medium | High | Requires task analysis infrastructure; significant new code |
| P2 | Workspace projection | Medium | High | Extends existing authority system; complex lifecycle |
| P2 | Time-travel replay | Medium | Medium | Event-sourced journal makes this feasible; debugging value |
| P3 | Token Coherence | Medium | High | Significant protocol work; most valuable at scale |
| P3 | A2A adapter | Low-Medium | Medium | External interop; value depends on ecosystem adoption |

---

## References

### Papers

1. **AdaptOrch** -- Yu, G. "Task-Adaptive Multi-Agent Orchestration." arXiv:2602.16873, February 2026.
2. **AgentConductor** -- "Topology Evolution for Multi-Agent Competition-Level Code Generation." arXiv:2602.17100, February 2026.
3. **VMAO** -- Zhang et al. "Verified Multi-Agent Orchestration: A Plan-Execute-Verify-Replan Framework." arXiv:2603.11445, March 2026.
4. **DOVA** -- Shen, A. & Shen, A. "Deliberation-First Multi-Agent Orchestration for Autonomous Research Automation." arXiv:2603.13327, March 2026.
5. **AWCP** -- Nie, X. et al. "A Workspace Delegation Protocol for Deep-Engagement Collaboration across Remote Agents." arXiv:2602.20493, February 2026.
6. **BIGMAS** -- "Brain-Inspired Graph-Based Multi-Agent Systems." arXiv:2603.15371, March 2026.
7. **MACOG** -- "Multi-Agent Code-Orchestrated Generation." arXiv:2510.03902, October 2025.
8. **Scaling Agent Systems** -- "Towards a Science of Scaling Agent Systems." arXiv:2512.08296, December 2025.
9. **Silo-Bench** -- "Communication-Reasoning Gap in Multi-Agent LLM Systems." arXiv:2603.01045, March 2026.
10. **Token Coherence** -- "MESI-Protocol Artifact Synchronization for LLM Orchestration." arXiv:2603.15183, March 2026.
11. **HiVA** -- "Self-Organized Hierarchical Agents with Semantic-Topological Evolution." arXiv:2509.00189, September 2025.
12. **AgentNet** -- "Dynamic Agent Graph Topology." arXiv:2504.00587, April 2025.

### Systems and Protocols

13. **LangGraph** -- LangChain. Persistence, checkpointing, interrupt/resume documentation. docs.langchain.com, 2025-2026.
14. **Temporal** -- Temporal Technologies. AI agent workflow patterns. temporal.io, 2025-2026.
15. **Google A2A** -- Google. Agent-to-Agent Protocol specification. google.github.io/A2A, 2025.
16. **Anthropic Agent Teams** -- Anthropic. "How We Built a Multi-Agent Research System." anthropic.com/engineering, 2025.
17. **OpenAI Codex** -- OpenAI. Cloud-based agent with sandboxed execution. openai.com/codex, 2025.
