# Repository Layout

This document describes the actual current repository structure and the responsibility boundaries between areas. It is not a "future cleanup plan."

## Top-Level Structure

```text
.
├── docs/                 文档
├── src/                  Python 源码根目录
├── tests/                测试
├── scripts/              开发和运维脚本
├── skills/               仓库内附带的辅助 skill
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
│   └── system/                      sandbox, i18n, executables
│       └── locales/                 en-US / zh-CN locale files
├── kernel/                        Governed execution kernel
│   ├── artifacts/                   lineage, claims, evidence
│   ├── authority/                   grants, identity, workspaces
│   ├── context/                     compiler, injection, memory governance
│   ├── execution/                   controller, executor, recovery, suspension
│   ├── ledger/                      events, journal (SQLite), projections
│   ├── policy/                      approvals, decisions, permits, evaluators, guards
│   ├── task/                        models, projections, services, state
│   └── verification/                receipts, proofs, rollbacks
├── plugins/                       Plugin system
│   └── builtin/                     Built-in plugins (see below)
├── runtime/                       Runtime / provider layer
│   ├── assembly/                    config, context assembly
│   ├── capability/                  contracts, loader, registry, MCP resolver
│   ├── control/                     dispatch, lifecycle, runner
│   ├── observation/                 logging setup
│   └── provider_host/               LLM providers (Claude, Codex), execution runtime
└── surfaces/                      User-facing entry points
    └── cli/                         Typer CLI dispatcher (main.py)
```

## `src/hermit/plugins/builtin/`

Directory for builtin plugins, organized by entrypoint category:

- `adapters/` — messaging adapters
  - `feishu/` — Feishu messaging adapter
- `bundles/` — slash command bundles
  - `compact/` — `/compact` command
  - `planner/` — `/plan` command
  - `usage/` — `/usage` command
- `hooks/` — event-driven hooks
  - `image_memory/` — Image memory hooks
  - `memory/` — Memory system with evidence governance
  - `scheduler/` — Scheduled task execution
  - `webhook/` — HTTP webhook receiver with signature verification
- `mcp/` — MCP integrations
  - `github/` — GitHub MCP integration
  - `mcp_loader/` — MCP server loader
- `subagents/` — subagent plugins
  - `orchestrator/` — Subagent orchestration
- `tools/` — tool plugins
  - `computer_use/` — Computer use capabilities
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
- `provider_host/` — LLM provider implementations (Claude, Codex), execution runtime, shared contracts and message normalization

## `src/hermit/kernel/`

Governed execution kernel with layered sub-packages:

- `task/` — TaskRecord models, TaskController, ingress routing, projections, state continuation
- `ledger/` — KernelStore (SQLite journal), event store, ledger projections
- `execution/` — ToolExecutor, execution contracts, coordination (dispatch, observation), recovery (reconciliations)
- `policy/` — approvals, decisions, permits (authorization plans), evaluators, guards (rules)
- `verification/` — receipt issuance, proof generation, rollback execution
- `context/` — context compiler, provider input injection, memory governance
- `artifacts/` — artifact models, lineage, claims, evidence cases
- `authority/` — identity, workspaces, capability grants

## `src/hermit/infra/`

Infrastructure primitives:

- `storage/` — JsonStore, atomic_write for file-based JSON persistence
- `locking/` — FileGuard for file-based locking
- `system/` — sandbox, i18n, executables, locale catalogs

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
