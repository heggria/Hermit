# Repository Layout

This document describes the actual current repository structure and the responsibility boundaries between areas. It is not a "future cleanup plan."

## Top-Level Structure

```text
.
├── docs/                 文档
├── src/                  Python 源码根目录
├── tests/                测试
├── scripts/              开发和运维脚本
├── .agents/skills/       仓库内附带的辅助 skill
├── README.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── install.sh
└── Makefile
```

## `src/hermit/` Package Structure

```text
src/hermit/
├── apps/                          macOS companion (menubar, app bundle)
│   └── companion/
├── infra/                         Infrastructure primitives
│   ├── locking/                     FileGuard
│   ├── storage/                     JsonStore, atomic_write
│   ├── paths.py                      project_root(), project_path()
│   └── system/                      i18n, executables
│       └── locales/                 en-US / zh-CN locale files
├── kernel/                        Governed execution kernel
│   ├── analytics/                   analytics engine, task metrics, health monitoring
│   ├── artifacts/                   lineage, claims, evidence
│   ├── authority/                   grants, identity, workspaces
│   ├── context/                     compiler, injection, memory governance, models
│   ├── execution/                   controller, executor, coordination, recovery, suspension, competition, self_modify, workers
│   ├── ledger/                      events, journal (SQLite), projections
│   ├── policy/                      approvals, decisions, permits, evaluators, guards, models, trust
│   ├── signals/                     steering signals, consumer, protocol, signal store
│   ├── task/                        models, projections, services, state
│   └── verification/                receipts, proofs, rollbacks, benchmark
├── plugins/                       Plugin system
│   └── builtin/                     Built-in plugins (see below)
├── runtime/                       Runtime / provider layer
│   ├── assembly/                    config, context assembly
│   ├── capability/                  contracts, loader, registry, MCP resolver
│   ├── control/                     dispatch, lifecycle, runner
│   ├── observation/                 logging setup
│   └── provider_host/               LLM providers, execution runtime, shared contracts
│       ├── execution/                 AgentRuntime, CommandSandbox, factory services, approval/progress/vision services
│       ├── llm/                       ClaudeProvider, CodexProvider, CodexOAuthProvider
│       └── shared/                    Provider protocol & data classes, profiles, messages, images
└── surfaces/                      User-facing entry points
    └── cli/                         Typer CLI dispatcher (main.py)
```

## `src/hermit/plugins/builtin/`

Directory for builtin plugins, organized by entrypoint category:

- `adapters/` — messaging adapters
  - `feishu/` — Feishu messaging adapter
  - `slack/` — Slack messaging adapter
  - `telegram/` — Telegram messaging adapter
- `bundles/` — slash command bundles
  - `compact/` — `/compact` command
  - `planner/` — `/plan` command
  - `usage/` — `/usage` command
- `hooks/` — event-driven hooks
  - `benchmark/` — Benchmark hooks
  - `decompose/` — Task decomposition hooks
  - `image_memory/` — Image memory hooks
  - `memory/` — Memory system with evidence governance
  - `metaloop/` — Meta-loop hooks
  - `overnight/` — Overnight execution hooks
  - `patrol/` — Patrol hooks
  - `quality/` — Quality hooks
  - `research/` — Research hooks
  - `scheduler/` — Scheduled task execution
  - `subtask/` — Subtask hooks
  - `trigger/` — Trigger hooks
  - `webhook/` — HTTP webhook receiver with signature verification
- `mcp/` — MCP integrations
  - `github/` — GitHub MCP integration
  - `hermit_server/` — Hermit MCP server
  - `mcp_loader/` — MCP server loader
- `subagents/` — subagent plugins
  - `orchestrator/` — Subagent orchestration
- `tools/` — tool plugins
  - `computer_use/` — Computer use capabilities
  - `file_tools/` — File operation tools
  - `grok/` — Grok search
  - `web_tools/` — Web search/scraping

Each plugin usually contains:

- `plugin.toml`
- `tools.py` / `hooks.py` / `commands.py` / `adapter.py` / `mcp.py`
- `skills/`
- optional `rules/`

## `src/hermit/runtime/`

Runtime layer organized into sub-packages:

- `assembly/` — config and context assembly
- `capability/` — plugin contracts, loader, registry (tools, plugins), MCP resolver
- `control/` — AgentRunner, session lifecycle, budget management, dispatch
- `observation/` — logging configuration
- `provider_host/` — LLM providers, execution runtime, shared contracts
  - `execution/` — AgentRuntime & AgentResult, CommandSandbox (budget-aware subprocess execution), build_provider/build_runtime factory (services.py), LLMApprovalFormatter (approval_services.py), LLMProgressSummarizer (progress_services.py), VisionAnalysisService & StructuredExtractionService (vision_services.py)
  - `llm/` — ClaudeProvider (claude.py), CodexProvider & CodexOAuthProvider & CodexOAuthTokenManager (codex.py)
  - `shared/` — Provider protocol & data classes (contracts.py), ProfileCatalog & TOML config resolution (profiles.py), block normalization & internal tool context handling (messages.py), image compression/preparation (images.py)

## `src/hermit/kernel/`

Governed execution kernel with layered sub-packages:

- `task/` — TaskRecord models, TaskController, ingress routing, projections, state continuation
- `ledger/` — KernelStore (SQLite journal), event store, ledger projections
- `execution/` — ToolExecutor, execution contracts, coordination (dispatch, observation), recovery (reconciliations), competition (multi-candidate evaluation, deliberation), self_modify (iteration, merger, verifier), workers (pool management)
- `policy/` — approvals, decisions, permits (authorization plans), evaluators, guards (rules), models (enums, policy models), trust (scoring, trust models)
- `verification/` — receipt issuance, proof generation, rollback execution, benchmark (registry, routing)
- `context/` — context compiler, provider input injection, memory governance, models (context models)
- `artifacts/` — artifact models, lineage, claims, evidence cases
- `authority/` — identity, workspaces, capability grants
- `analytics/` — analytics engine, task metrics, health monitoring
- `signals/` — steering signals, consumer, protocol, signal store

## `src/hermit/infra/`

Infrastructure primitives:

- `storage/` — JsonStore, atomic_write for file-based JSON persistence
- `locking/` — FileGuard for file-based locking
- `paths.py` — project_root(), project_path() workspace path utilities
- `system/` — i18n, executables, locale catalogs

## `src/hermit/apps/companion/`

Separate macOS companion layer:

- `control.py` — service control
- `menubar.py` — menu bar UI
- `appbundle.py` — local app bundle and Login Item handling

It is not part of the plugin system.

## `docs/`

The most important documentation currently in the repository:

- `architecture.md`
- `configuration.md`
- `providers-and-profiles.md`
- `cli-and-operations.md`
- `desktop-companion.md`
- `i18n.md`
- `kernel-spec-v0.1.md`
- `kernel-conformance-matrix-v0.1.md`
- `kernel-conformance-matrix-v0.2-core.md`

## `tests/`

Tests are organized into `unit/` and `integration/` sub-directories. Coverage includes:

- CLI and config/profile
- provider runtime
- session / memory / hooks
- scheduler / webhook
- Feishu adapter
- companion
- kernel task lifecycle, policy, executor, proofs

Run tests with:

```bash
make test
```

## The Plugin Layer Is the Main Extension Surface

Most Hermit capabilities are pushed down into `src/hermit/plugins/builtin/` instead of continuing to expand the runtime core.
