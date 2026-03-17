# AGENTS.md

Instructions for Codex and other agentic tooling working in the Hermit repository. Everything below reflects the current source tree and should be treated as repository-scoped guidance.

## Project Skills

- Service redeploy skill: [`.agents/skills/hermit-service-redeploy/SKILL.md`](.agents/skills/hermit-service-redeploy/SKILL.md)
- README Excalidraw assets skill: [`.agents/skills/hermit-readme-excalidraw-assets/SKILL.md`](.agents/skills/hermit-readme-excalidraw-assets/SKILL.md)

Use it when:

- modifying code in the `hermit serve` execution path
- changing builtin plugins, adapters, scheduler, webhook, or Feishu integration
- changing installation, packaging, or local runtime loading behavior

Use the README Excalidraw assets skill when:

- editing launch-style README diagrams or documentation hero diagrams
- updating `docs/assets/*.excalidraw.json`, `.svg`, or `.png`
- regenerating GitHub-safe PNG assets from Excalidraw-styled SVG diagrams

For this kind of work, the closing step must include local redeployment and verification as defined by that skill. Source edits alone, or a simple reload, are not enough.

## Project Overview

Hermit is a local-first governed agent kernel built for durable, inspectable, operator-trust-oriented workflows.

Current core characteristics:

- task-first kernel records and local ledger state
- governed execution with approvals, decisions, permits, receipts, and rollback support
- artifact-aware context and evidence-bound memory primitives
- a `plugin.toml`-driven plugin system with CLI, Feishu, scheduler, webhook, and MCP surfaces

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
make check            # Quick check: lint + test
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

Kernel convergence tests (core subset):
```bash
make test-kernel-convergence
```

## Development Environment

Use the project scripts for development, debugging, and restarting local environments. Do not maintain separate instructions for manual `venv`, manual `export HERMIT_BASE_DIR`, or hand-built `serve` / `menubar` commands.

Tests:

```bash
make test
```

`pyproject.toml` requires Python `>= 3.13`. Do not run the CLI with Python 3.11.

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
│   └── system/                    #   sandbox, i18n, executables
│       └── locales/               #   en-US / zh-CN locale files
├── kernel/                        # Governed execution kernel
│   ├── artifacts/                 #   lineage, claims, evidence
│   ├── authority/                 #   grants, identity, workspaces
│   ├── context/                   #   compiler, injection, memory governance
│   ├── execution/                 #   controller, executor, recovery, suspension
│   ├── ledger/                    #   events, journal (SQLite), projections
│   ├── policy/                    #   approvals, decisions, permits, evaluators, guards
│   ├── task/                      #   models, projections, services, state
│   └── verification/              #   receipts, proofs, rollbacks
├── plugins/                       # Plugin system
│   └── builtin/                   #   Built-in plugins (see Builtin Plugins below)
│       ├── adapters/feishu/
│       ├── bundles/{compact,planner,usage}/
│       ├── hooks/{image_memory,memory,scheduler,webhook}/
│       ├── mcp/{github,mcp_loader}/
│       ├── subagents/orchestrator/
│       └── tools/{computer_use,grok,web_tools}/
├── runtime/                       # Runtime / provider layer
│   ├── assembly/                  #   config, context assembly
│   ├── capability/                #   contracts, loader, registry, MCP resolver
│   ├── control/                   #   dispatch, lifecycle, runner
│   ├── observation/               #   logging setup
│   └── provider_host/             #   LLM providers (Claude, Codex), execution runtime
└── surfaces/                      # User-facing entry points
    └── cli/                       #   Typer CLI dispatcher (main.py)
```

## CLI Fact Sheet

Top-level commands:

- `hermit setup`
- `hermit init`
- `hermit startup-prompt`
- `hermit run`
- `hermit chat`
- `hermit serve --adapter <adapter>`
- `hermit reload --adapter <adapter>`
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

Builtin plugin commands:

- `/compact`
- `/plan`
- `/usage`

Notes:

- `serve` and `reload` currently use `--adapter`
- only `autostart enable/disable/status` uses `--adapter`

## Config and State Directories

Default root directory: `~/.hermit`

Common paths:

- `~/.hermit/.env`
- `~/.hermit/context.md`
- `~/.hermit/memory/memories.md`
- `~/.hermit/memory/session_state.json`
- `~/.hermit/plugins/`
- `~/.hermit/rules/`
- `~/.hermit/skills/`
- `~/.hermit/schedules/`
- `~/.hermit/sessions/`
- `~/.hermit/image-memory/`

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

Do not use the old name `SCHEDULE_RESULT` anymore.

## Core Tools

Current core toolset:

- `read_file`
- `write_file`
- `bash`
- `read_hermit_file`
- `write_hermit_file`
- `list_hermit_files`

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

The core governed execution path:
`Task → Step → StepAttempt → Policy → Approval → CapabilityGrant → Execution → Receipt → Proof/Rollback`

Models propose actions, the kernel authorizes, then the executor runs. No direct model-to-tool execution.

### Key Layers

**CLI & Surfaces** (`src/hermit/surfaces/cli/`):
- `main.py` — CLI dispatcher using Typer: `hermit chat`, `hermit run`, `hermit serve`, `hermit task`, `hermit memory`, `hermit config`, etc.
- `autostart.py` — Autostart management

**Runtime** (`src/hermit/runtime/`):
- `control/runner/runner.py` — AgentRunner: unified orchestration for CLI and adapters. Manages session + agent + plugin hooks + background services
- `control/lifecycle/session.py` — SessionManager for conversation state
- `control/lifecycle/budgets.py` — Token budget management
- `capability/registry/manager.py` — PluginManager: plugin discovery, tool registration, hook dispatch, MCP integration
- `capability/registry/tools.py` — ToolRegistry and tool execution
- `capability/contracts/base.py` — PluginManifest, HookEvent, SubagentSpec, AdapterSpec
- `capability/loader/loader.py` — Plugin discovery and loading
- `capability/resolver/mcp_client.py` — MCP client resolver
- `assembly/config.py` / `context.py` — Config and context assembly
- `provider_host/` — LLM provider implementations (Claude, Codex), AgentRuntime, services

**Task Kernel** (`src/hermit/kernel/`):
- `task/` — TaskRecord models, TaskController, ingress routing, projections, state
- `ledger/journal/store.py` — KernelStore: SQLite-backed persistent ledger
- `execution/executor/executor.py` — ToolExecutor: governed tool execution with policy evaluation
- `execution/coordination/dispatch.py` — KernelDispatchService for async governed execution
- `policy/approvals/` — Approval workflow and policy enforcement
- `policy/permits/` — Authorization plans
- `verification/receipts/` / `proofs/` — Receipt issuance, proof generation and verification
- `verification/rollbacks/` — Rollback execution for supported receipts
- `context/compiler/` — Artifact-native context assembly
- `context/memory/governance.py` — Evidence-bound memory governance
- `artifacts/` — Artifact lineage, claims, evidence
- `authority/` — Identity, workspaces, capability grants

**Plugin System** (`src/hermit/plugins/`):
- Each plugin has a `plugin.toml` manifest defining entry points, tools, hooks, variables, and skills
- Discovery paths: `src/hermit/plugins/builtin/` and `~/.hermit/plugins/`

**Builtin Plugins** (`src/hermit/plugins/builtin/`):
- `adapters/feishu/` — Feishu messaging adapter
- `hooks/webhook/` — HTTP webhook receiver with signature verification
- `hooks/scheduler/` — Scheduled task execution
- `hooks/memory/` — Memory system with evidence governance
- `hooks/image_memory/` — Image memory hooks
- `mcp/github/` — GitHub MCP integration
- `mcp/mcp_loader/` — MCP server loader
- `tools/web_tools/` — Web search/scraping
- `tools/grok/` — Grok search
- `tools/computer_use/` — Computer use capabilities
- `bundles/compact/` — Compact command
- `bundles/planner/` — Plan command
- `bundles/usage/` — Usage command
- `subagents/orchestrator/` — Subagent orchestration

**Infrastructure** (`src/hermit/infra/`):
- `storage/` — JsonStore, atomic_write for file-based JSON persistence
- `locking/` — FileGuard for file-based locking
- `system/` — Sandbox, i18n, executables

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
