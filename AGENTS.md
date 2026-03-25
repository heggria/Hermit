# AGENTS.md

Instructions for Codex and other agentic tooling working in the Hermit repository. Everything below reflects the current source tree and should be treated as repository-scoped guidance.

## Project Skills

- Service redeploy skill: [`.agents/skills/hermit-service-redeploy/SKILL.md`](.agents/skills/hermit-service-redeploy/SKILL.md)
- README Excalidraw assets skill: [`.agents/skills/hermit-readme-excalidraw-assets/SKILL.md`](.agents/skills/hermit-readme-excalidraw-assets/SKILL.md)
- Self-iteration skill: [`.agents/skills/hermit-iterate/SKILL.md`](.agents/skills/hermit-iterate/SKILL.md)
- Hermit delegation skill: [`.agents/skills/hermit-delegate/SKILL.md`](.agents/skills/hermit-delegate/SKILL.md)
- Team creation skill: [`.agents/skills/hermit-create-team/SKILL.md`](.agents/skills/hermit-create-team/SKILL.md)

Use the delegation skill when:

- submitting governed tasks to Hermit (multi-step, needs approvals, needs receipts/proof)
- the user says "delegate", "let Hermit do it", or "submit to Hermit"
- you need durable execution records, rollback capability, or policy enforcement

Use the service redeploy skill when:

- modifying code in the `hermit serve` execution path
- changing builtin plugins, adapters, scheduler, webhook, or Feishu integration
- changing installation, packaging, or local runtime loading behavior

Use the README Excalidraw assets skill when:

- editing launch-style README diagrams or documentation hero diagrams
- updating `docs/assets/*.excalidraw.json`, `.svg`, or `.png`
- regenerating GitHub-safe PNG assets from Excalidraw-styled SVG diagrams

For this kind of work, the closing step must include local redeployment and verification as defined by that skill. Source edits alone, or a simple reload, are not enough.

Use the self-iteration skill when:

- the user provides a spec file and asks to iterate, or says "iterate on specs/xxx.md"
- running a spec-driven self-iteration workflow (spec → Hermit execution → proof → PR)

Use the team creation skill when:

- the user wants to create a team with multiple roles quickly
- the user describes a team composition (e.g. "3 executors + 1 planner")
- the user says "create team", "build a team", "set up a team"

## Project Overview

Hermit is a local-first governed agent kernel built for durable, inspectable, operator-trust-oriented workflows.

Current core characteristics:

- task-first kernel records and local ledger state
- governed execution with approvals, trust evaluation, permits, receipts, and rollback support
- artifact-aware context and evidence-bound memory primitives
- a `plugin.toml`-driven plugin system with adapters (Feishu, Slack, Telegram), hooks (scheduler, webhook, memory, patrol, etc.), tools, MCP integrations, bundles, and subagents

## Code Quality

- **Linter/Formatter:** Ruff (line-length 100, rules: E, F, I; E501 ignored)
- **Test framework:** pytest with pytest-asyncio (asyncio_mode = "auto") and pytest-xdist for parallel execution
- Pre-commit hooks run ruff check and format

## Common Commands

```bash
make install          # Install with init (runs install.sh)
make test             # Run tests (pytest with parallel via pytest-xdist)
make test-cov         # Run tests with coverage
make lint             # Ruff linting
make format           # Ruff formatting
make verify           # Full verification: version-check, lint, test, package-check, install-check
make check            # Quick check: lint + typecheck + test
make precommit-install # Install git hooks (scripts/git-hooks/)
```

Run a single test file:
```bash
uv run pytest tests/test_some_file.py -q
```

Run a single test:
```bash
uv run pytest tests/test_some_file.py::test_function_name -q
```

Kernel tests (core subset):
```bash
make test-kernel
```

## Development Environment

Use the project scripts for development, debugging, and restarting local environments. Do not maintain separate instructions for manual `venv`, manual `export HERMIT_BASE_DIR`, or hand-built `serve` / `menubar` commands.

Tests:

```bash
make test
```

`pyproject.toml` requires Python `>= 3.11`.

Unified environment control entrypoints:

```bash
scripts/hermit-envctl.sh <prod|dev|test> <up|restart|down|status|logs>
scripts/hermit-watch.sh <prod|dev|test> [--adapter <adapter>]
```

Common examples:

```bash
scripts/hermit-envctl.sh dev restart
scripts/hermit-envctl.sh prod status
scripts/hermit-watch.sh dev
make env-restart ENV=dev
make env-status ENV=prod
make env-watch ENV=dev
```

Notes:

- when debugging or restarting a local environment, use the controller scripts first instead of manually composing `HERMIT_BASE_DIR + serve + menubar`
- the controller scripts handle the matching `service`, `menubar`, menu app, and basic status checks together
- to enter the CLI, inspect config, or check auth, prefer `scripts/hermit-env.sh <env> ...`
- when changing Python source, prefer `scripts/hermit-watch.sh <env>` or `make env-watch ENV=<env>` for watch-and-restart; this entrypoint takes over the environment, keeps a single watcher per adapter, and ensures the menubar companion is running without duplicates
- `scripts/hermit-envctl.sh ... down` and `... restart` also stop the matching watcher so the environment does not unexpectedly come back on the next file change
- `hermit reload` should be treated only as a graceful config/plugin/tool reload; it is not general source hot reload

Example direct service invocation inside the managed environment:

```bash
scripts/hermit-env.sh dev serve --adapter feishu
```

## Directory Layout

```text
src/hermit/
├── apps/                          # macOS companion (menubar, app bundle)
│   └── companion/
├── infra/                         # Infrastructure primitives
│   ├── locking/                   #   FileGuard
│   ├── storage/                   #   JsonStore, atomic_write
│   ├── paths.py                    #   project_root(), project_path() utilities
│   └── system/                    #   i18n, executables
│       └── locales/               #   en-US / zh-CN locale files
├── kernel/                        # Governed execution kernel
│   ├── analytics/                 #   governance metrics engine, health monitor
│   ├── artifacts/                 #   blackboard, lineage, models
│   ├── authority/                 #   grants, identity, workspaces
│   ├── context/                   #   compiler, injection, memory (20+ modules), models
│   ├── execution/                 #   controller, executor, recovery, suspension,
│   │                              #     competition, coordination, self_modify, workers
│   ├── ledger/                    #   events, journal (SQLite), projections
│   ├── policy/                    #   approvals, evaluators, guards, models, permits, trust
│   ├── signals/                   #   evidence signals, steering, signal store
│   ├── task/                      #   models, projections, services, state
│   └── verification/              #   benchmark, receipts, proofs, rollbacks
├── plugins/                       # Plugin system
│   └── builtin/                   #   Built-in plugins (see Builtin Plugins below)
│       ├── adapters/{feishu,slack,telegram}/
│       ├── bundles/{compact,planner,usage}/
│       ├── hooks/{benchmark,decompose,image_memory,memory,metaloop,overnight,patrol,quality,research,scheduler,subtask,trigger,webhook}/
│       ├── mcp/{github,hermit_server,mcp_loader}/
│       ├── subagents/orchestrator/
│       └── tools/{computer_use,file_tools,grok,web_tools}/
├── runtime/                       # Runtime / provider layer
│   ├── assembly/                  #   config, context assembly
│   ├── capability/                #   contracts, loader, registry, resolver (MCP client)
│   ├── control/                   #   runner (orchestration + extracted handlers), lifecycle
│   ├── observation/               #   logging setup
│   └── provider_host/             #   LLM providers, shared contracts, execution runtime
│       ├── llm/                   #     ClaudeProvider, CodexProvider, CodexOAuthProvider
│       ├── shared/                #     contracts (Provider protocol, data classes), images,
│       │                          #       messages (block normalization), profiles (ProfileCatalog)
│       └── execution/             #     AgentRuntime, CommandSandbox, build_provider/build_runtime,
│                                  #       approval/progress/vision services
└── surfaces/                      # User-facing entry points
    └── cli/                       #   Typer CLI dispatcher (main.py, _commands_*.py, _serve.py, tui/)
```

## CLI Fact Sheet

Top-level commands:

- `hermit setup`
- `hermit init`
- `hermit startup-prompt`
- `hermit run`
- `hermit chat`
- `hermit overnight`
- `hermit serve [ADAPTER]`
- `hermit reload [ADAPTER]`
- `hermit sessions`
- `hermit config ...`
- `hermit profiles ...`
- `hermit auth ...`
- `hermit task ...`
- `hermit memory ...`
- `hermit plugin ...`
- `hermit autostart ...`
- `hermit schedule ...`

Core slash commands in `chat`:

- `/new`
- `/history`
- `/help`
- `/quit`
- `/task`

Builtin plugin commands:

- `/compact`
- `/plan`
- `/usage`

Notes:

- `serve` and `reload` take a positional ADAPTER argument (default: feishu)
- only `autostart enable/disable/status` uses `--adapter`

## Config and State Directories

Default root directory: `~/.hermit`

Common paths:

- `~/.hermit/.env`
- `~/.hermit/config.toml` — profile catalog and global settings
- `~/.hermit/context.md`
- `~/.hermit/memory/memories.md`
- `~/.hermit/memory/session_state.json`
- `~/.hermit/hooks/` — user-defined hook scripts
- `~/.hermit/plugins/`
- `~/.hermit/rules/`
- `~/.hermit/skills/`
- `~/.hermit/schedules/`
- `~/.hermit/sessions/`
- `~/.hermit/image-memory/`
- `~/.hermit/webhooks.json` — webhook route configuration

Kernel state (task ledger, artifacts, receipts):

- `~/.hermit/kernel/` — kernel state root
- `~/.hermit/kernel/state.db` — SQLite ledger (tasks, events, artifacts, receipts)
- `~/.hermit/kernel/artifacts/` — artifact blob storage

You will also see these at runtime:

- `~/.hermit/serve-<adapter>.pid`
- `~/.hermit/plans/` (created after the first `/plan`)

## Plugin System

Current real entrypoint categories:

- `tools`
- `hooks`
- `commands`
- `subagents`
- `adapter`
- `mcp`

`plugin.toml` example:

```toml
[plugin]
name = "my-plugin"
version = "0.1.0"
description = "示例插件"

[entry]
tools = "tools:register"
commands = "commands:register"
hooks = "hooks:register"
subagents = "subagents:register"
adapter = "adapter:register"
mcp = "mcp:register"
```

Discovery paths:

1. `src/hermit/plugins/builtin/`
2. `~/.hermit/plugins/`

## Hook Events

Current enum values:

- `SYSTEM_PROMPT`
- `REGISTER_TOOLS`
- `SESSION_START`
- `SESSION_END`
- `PRE_RUN`
- `POST_RUN`
- `SERVE_START`
- `SERVE_STOP`
- `DISPATCH_RESULT`
- `SUBTASK_SPAWN`
- `SUBTASK_COMPLETE`

Do not use the old name `SCHEDULE_RESULT` anymore.

## Core Tools

Current core toolset:

- `read_file`
- `write_file`
- `bash`
- `read_hermit_file`
- `write_hermit_file`
- `list_hermit_files`
- `iteration_summary`

In `/plan` mode, only read-only tools remain available and side-effecting tools are disabled.

## Persistence Conventions

Prefer using:

- [`JsonStore.read()`](src/hermit/infra/storage/store.py)
- [`JsonStore.write()`](src/hermit/infra/storage/store.py)
- [`JsonStore.update()`](src/hermit/infra/storage/store.py)
- [`atomic_write()`](src/hermit/infra/storage/atomic.py)
- [`FileGuard.acquire()`](src/hermit/infra/locking/lock.py)

Notes:

- the old-document `JsonStore.load()` / `save()` APIs do not exist
- the session persistence format is single-file JSON, not JSONL

## Runtime and Debugging Guidance

- Start with [`src/hermit/surfaces/cli/main.py`](src/hermit/surfaces/cli/main.py) to confirm the CLI entrypoint
- Then read [`src/hermit/runtime/control/runner/runner.py`](src/hermit/runtime/control/runner/runner.py) to understand the execution path
- Then read [`src/hermit/runtime/capability/registry/manager.py`](src/hermit/runtime/capability/registry/manager.py) to understand capability assembly
- For scheduler / webhook / feishu work, check first whether `DISPATCH_RESULT` is the event you actually need

## Service Change Completion Rules

Any change that affects the behavior of a locally running `hermit serve` process should, by default, be closed out with this flow:

1. Determine whether the running service comes from the repo checkout or the installed `uv tool` copy
2. If the installed copy must be updated, run `bash install.sh`
3. Reload the target adapter, or start the service directly if it is not running
4. Check `~/.hermit/serve-<adapter>.pid` and the matching process
5. Check `~/.hermit/logs/<adapter>-stdout.log` / `stderr.log` to confirm reload or startup succeeded

Do not report “done” before that loop has been fully completed.

## Architecture

### Execution Flow

The core governed execution pipeline (`ToolExecutor.execute` in `kernel/execution/executor/executor.py`):

```
Task → Step → StepAttempt → ActionRequest → EvidenceEnrichment → PolicyEvaluation
  → ApprovalMatching (+ drift detection) → StateWitness → ExecutionContract
  → EvidenceCase → AuthorizationPlan → AdmissibilityCheck → ApprovalGate
  → WorkspaceLease → CapabilityGrant → RevalidationGate → RollbackPlan
  → Execution → Observation → Receipt → Reconciliation → PatternLearning
```

**Phase details:**
1. **ActionRequest** — `PolicyEngine.build_action_request()` constructs the request from tool + input + context
2. **EvidenceEnrichment** — `PolicyEvidenceEnricher.enrich()` attaches derived evidence to the request
3. **PolicyEvaluation** — `PolicyEngine.evaluate()` produces a `PolicyDecision` (allow / deny / require_approval)
4. **ApprovalMatching** — matches existing approvals; detects drift (supersedes attempt if drifted)
5. **StateWitness** — `WitnessCapture` snapshots pre-execution state for governed actions
6. **ExecutionContract + EvidenceCase + AuthorizationPlan** — synthesized via `_synthesize_contract_loop()` for governed actions; checked for expiry and policy-version drift
7. **AdmissibilityCheck** — `_admissibility_resolution()` validates contract is admissible; blocks if not
8. **ApprovalGate** — if `require_approval`, creates an `Approval` record and suspends the attempt
9. **WorkspaceLease** — `WorkspaceLeaseService` ensures exclusive workspace access
10. **CapabilityGrant** — `CapabilityGrantService.issue()` mints a scoped grant; dispatch denied on failure
11. **RevalidationGate** — `AuthorizationPlanService.revalidate()` checks policy version hasn't drifted since plan creation
12. **RollbackPlan** — `_prepare_rollback_plan()` records rollback strategy and artifact refs
13. **Execution** — `invoke_tool_handler()` runs the tool; uncertain outcomes routed to reconciliation
14. **Observation** — observation ticket handling for async/deferred results
15. **Receipt** — `ReceiptService` issues a receipt with result code, rollback info, and evidence refs
16. **Reconciliation** — `ReconcileService` compares authorized vs observed effects
17. **PatternLearning** — `TaskPatternLearner` records execution patterns for future contract synthesis

Models propose actions; the kernel authorizes, contracts, and witnesses before the executor runs. No direct model-to-tool execution.

### Key Layers

**CLI & Surfaces** (`src/hermit/surfaces/cli/`):
- `main.py` — CLI dispatcher using Typer, imports command modules below
- `_commands_core.py` — Core commands: `hermit run`, `hermit chat`, `hermit setup`, `hermit init`, `hermit sessions`, `hermit config`, `hermit profiles`, `hermit auth`, `hermit startup-prompt`
- `_serve.py` — `hermit serve` and `hermit reload`
- `_commands_task.py` — `hermit task` subcommands
- `_commands_memory.py` — `hermit memory` subcommands
- `_commands_plugin.py` — `hermit plugin` subcommands
- `_commands_schedule.py` — `hermit schedule` subcommands
- `_commands_autostart.py` — `hermit autostart` subcommands
- `_commands_overnight.py` — `hermit overnight`
- `autostart.py` — Autostart management (launchd plist generation)
- `_preflight.py` — Preflight checks (workspace init, env validation)
- `_helpers.py` — Shared CLI helpers (spinners, formatters)
- `tui/` — Terminal UI (interactive TUI mode for `hermit chat --tui`)

**Runtime** (`src/hermit/runtime/`):
- `control/runner/runner.py` — AgentRunner: unified orchestration for CLI and adapters. Manages session + agent + plugin hooks + background services. Delegates to extracted handler modules: `task_executor.py`, `message_compiler.py`, `async_dispatcher.py`, `approval_resolver.py`, `session_context_builder.py`, `control_actions.py`, `utils.py`
- `control/lifecycle/session.py` — SessionManager for conversation state
- `control/lifecycle/budgets.py` — Execution budget management (deadlines, timeouts)
- `capability/registry/manager.py` — PluginManager: plugin discovery, tool registration, hook dispatch, MCP integration
- `capability/registry/tools.py` — ToolRegistry and tool execution
- `capability/contracts/base.py` — PluginManifest, HookEvent, SubagentSpec, AdapterSpec, McpServerSpec, McpToolGovernance, CommandSpec, PluginVariableSpec, AdapterProtocol, PluginContext
- `capability/contracts/hooks.py` — HooksEngine: priority-based hook dispatch with signature-adaptive calling
- `capability/contracts/kernel_services.py` — KernelServiceProvider protocol, KernelServiceRegistry for kernel-plugin decoupling
- `capability/contracts/rules.py` — Rule file loading from plugin rule directories
- `capability/contracts/skills.py` — SkillDefinition model, skill loading from plugin skill directories
- `capability/loader/loader.py` — Plugin discovery and loading
- `capability/loader/config.py` — Plugin variable resolution and template rendering
- `capability/registry/skill_loader.py` — SkillLoader: read_skill tool registration and handler
- `capability/registry/subagent_executor.py` — SubagentExecutor: delegation tool construction and governed subagent execution
- `capability/registry/system_prompt_builder.py` — SystemPromptBuilder: system prompt assembly with rules, skills, and hooks
- `capability/resolver/mcp_client.py` — McpClientManager: MCP server connection lifecycle, tool discovery, and governed call routing via background event loop
- `assembly/config.py` / `context.py` — Config and context assembly
- `provider_host/llm/` — ClaudeProvider (`claude.py`), CodexProvider + CodexOAuthProvider + CodexOAuthTokenManager (`codex.py`)
- `provider_host/shared/contracts.py` — Provider protocol, ProviderRequest/Response/Event, UsageMetrics, ToolCall/ToolResult
- `provider_host/shared/images.py` — Image compression/preparation for provider messages
- `provider_host/shared/messages.py` — Block normalization, internal tool context handling
- `provider_host/shared/profiles.py` — ProfileCatalog, ResolvedProfile, TOML config resolution
- `provider_host/execution/runtime.py` — AgentRuntime, AgentResult
- `provider_host/execution/sandbox.py` — CommandSandbox for budget-aware subprocess execution
- `provider_host/execution/services.py` — build_provider, build_runtime, build_background_runtime factories
- `provider_host/execution/approval_services.py` — LLMApprovalFormatter, build_approval_copy_service
- `provider_host/execution/progress_services.py` — LLMProgressSummarizer
**Task Kernel** (`src/hermit/kernel/`):
- `task/` — TaskRecord models, TaskController, ingress routing, projections, state
- `ledger/` — Persistent ledger with hash-chained event sourcing (schema v18), mixin-based KernelStore
  - `journal/store.py` — KernelStore: composed of 12 mixins (Task, Ledger, Projection, Scheduler, Record, V2, Signal, Competition, Delegation, SelfIterate, Program, Team)
  - `journal/store_tasks.py` — KernelTaskStoreMixin: task lifecycle persistence
  - `journal/store_records.py` — KernelStoreRecordMixin: generic record storage
  - `journal/store_v2.py` — KernelV2StoreMixin: execution contracts, evidence cases, authorization plans, reconciliations
  - `journal/store_scheduler.py` — KernelSchedulerStoreMixin: scheduled job persistence
  - `journal/store_self_iterate.py` — SelfIterateStoreMixin: self-iteration state
  - `journal/store_programs.py` — ProgramStoreMixin: program/workflow storage
  - `journal/store_teams.py` — KernelTeamStoreMixin: team coordination
  - `journal/store_types.py` — KernelStoreTypingBase: shared typing base for all mixins
  - `journal/store_support.py` — Utility functions (canonical JSON, SHA-256, UNSET sentinel)
  - `events/store_ledger.py` — KernelLedgerStoreMixin: artifacts, approvals, beliefs, decisions, memories, receipts, rollbacks, capability grants, principals, workspace leases
  - `projections/store_projection.py` — KernelProjectionStoreMixin: build/cache full task projections from event stream
- `analytics/` — AnalyticsEngine for governance metrics, health monitor
- `signals/` — Evidence signals, steering protocol, signal store
- `execution/executor/executor.py` — ToolExecutor: governed tool execution with policy evaluation
- `execution/coordination/` — KernelDispatchService, join barriers, pool dispatch, prioritizer
- `execution/competition/` — Competitive candidate evaluation and deliberation
- `execution/self_modify/` — Self-iteration kernel, workspace isolation, merge verification
- `execution/workers/` — Worker pool with role-bound slot management
- `execution/recovery/` — Failure recovery and retry logic
- `execution/controller/` — ActionContract definitions, SupervisionService, pattern and template learners
- `execution/suspension/` — Git worktree snapshot for task suspension and resumption
- `policy/approvals/` — Approval workflow, decision recording, and approval copy rendering
- `policy/evaluators/` — PolicyEngine, action request derivation, evidence enrichment
- `policy/guards/` — Guard rules dispatch chain (readonly, filesystem, shell, network, attachment, planning, governance)
- `policy/models/` — ActionRequest, PolicyDecision, PolicyObligations, PolicyReason, Verdict/ActionClass enums
- `policy/permits/` — Authorization plans
- `policy/trust/` — Trust scoring, risk adjustment
- `verification/benchmark/` — Benchmark models, profile registry, and routing service for verification-driven quality gates
- `verification/receipts/` — Receipt issuance with HMAC-SHA256 signing
- `verification/proofs/` — Proof summaries, tiered export (summary/standard/full), Merkle inclusion proofs, DAG proof bundles, proof anchoring (local log, git notes), governance assurance reports, chain completeness analysis
- `verification/rollbacks/` — Rollback execution, recursive dependency tracking, leaf-first rollback planning
- `context/compiler/` — ContextCompiler producing ContextPack v3 with hybrid retrieval (semantic + token-index)
- `context/injection/` — ProviderInputCompiler for LLM provider input assembly
- `context/memory/` — Full memory subsystem (24 modules): governance, hybrid retrieval, episodic, procedural, knowledge graph, embeddings, decay, consolidation, confidence scoring, reflection, lineage, anti-pattern detection, quality assessment, taxonomy, reranker, token index, working memory
- `artifacts/` — Artifact blackboard, lineage tracking, models
- `authority/` — Identity, workspaces, capability grants

**Plugin System** (`src/hermit/plugins/`):
- Each plugin has a `plugin.toml` manifest defining entry points, tools, hooks, variables, and skills
- Discovery paths: `src/hermit/plugins/builtin/` and `~/.hermit/plugins/`

**Builtin Plugins** (`src/hermit/plugins/builtin/`):
- `adapters/feishu/` — Feishu messaging adapter
- `adapters/slack/` — Slack messaging adapter (Socket Mode)
- `adapters/telegram/` — Telegram messaging adapter
- `hooks/benchmark/` — Benchmark runner and iteration learner
- `hooks/decompose/` — Intelligent task decomposition and spec generation
- `hooks/image_memory/` — Image memory hooks
- `hooks/memory/` — Memory system with evidence governance
- `hooks/metaloop/` — Meta-loop lifecycle management and subtask completion
- `hooks/overnight/` — Overnight dashboard and morning report aggregation
- `hooks/patrol/` — Scheduled proactive code health checks
- `hooks/quality/` — Governed code review and test skeleton generation
- `hooks/research/` — Auto-research across codebase, web, docs, and git history
- `hooks/scheduler/` — Scheduled task execution
- `hooks/subtask/` — Subtask spawning support
- `hooks/trigger/` — Evidence-backed task generation from execution results
- `hooks/webhook/` — HTTP webhook receiver with signature verification
- `mcp/github/` — GitHub MCP integration
- `mcp/hermit_server/` — Hermit MCP server exposing kernel tools via Streamable HTTP for supervisor agents
- `mcp/mcp_loader/` — MCP server loader
- `tools/computer_use/` — Computer use capabilities
- `tools/file_tools/` — Governed file tools (read_file, glob_files)
- `tools/grok/` — Grok search
- `tools/web_tools/` — Web search/scraping
- `bundles/compact/` — Compact command
- `bundles/planner/` — Plan command
- `bundles/usage/` — Usage command
- `subagents/orchestrator/` — Subagent orchestration

**Infrastructure** (`src/hermit/infra/`):
- `storage/` — JsonStore, atomic_write for file-based JSON persistence
- `locking/` — FileGuard for file-based locking
- `system/` — i18n, executables
- `paths.py` — project_root(), project_path() path utilities

**Apps** (`src/hermit/apps/`):
- `companion/` — macOS menubar companion, app bundle, control

### Key Design Principles

- **Task-first:** All meaningful work flows through durable Task objects
- **Event sourcing:** Durable state derived from append-only event logs
- **Artifact-native context:** Context compiled from artifacts, not just message history
- **Evidence-bound memory:** Memory promotion requires evidence references
- **Scoped authority:** CapabilityGrants for least-privilege execution
- **Receipt-aware:** Every important action produces receipts and proof bundles

### i18n

Locale support via `src/hermit/infra/system/i18n.py`: English (en-US) and Simplified Chinese (zh-CN). Locale files in `src/hermit/infra/system/locales/`.

## Contributing Direction

Hermit is converging from a local-first agent runtime toward a governed agent kernel. Contributions should strengthen kernel semantics (task lifecycle, policy/approval flow, receipts/proofs, rollback coverage) rather than adding chat-plus-tools features. Always distinguish between current implementation and target architecture (see `docs/kernel-spec-v0.1.md`).
