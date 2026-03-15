# AGENTS.md

Instructions for Codex and other agentic tooling working in the Hermit repository. Everything below reflects the current source tree and should be treated as repository-scoped guidance.

## Project Skills

- Service redeploy skill: [`skills/hermit-service-redeploy/SKILL.md`](skills/hermit-service-redeploy/SKILL.md)
- README Excalidraw assets skill: [`skills/hermit-readme-excalidraw-assets/SKILL.md`](skills/hermit-readme-excalidraw-assets/SKILL.md)

Use it when:

- modifying code in the `hermit serve` execution path
- changing builtin plugins, adapters, scheduler, webhook, or Feishu integration
- changing installation, packaging, or local runtime loading behavior

Use the README Excalidraw assets skill when:

- editing launch-style README diagrams or documentation hero diagrams
- updating `docs/site/assets/*.excalidraw.json`, `.svg`, or `.png`
- regenerating GitHub-safe PNG assets from Excalidraw-styled SVG diagrams

For this kind of work, the closing step must include local redeployment and verification as defined by that skill. Source edits alone, or a simple reload, are not enough.

## Project Overview

Hermit is a local-first governed agent kernel built for durable, inspectable, operator-trust-oriented workflows.

Current core characteristics:

- task-first kernel records and local ledger state
- governed execution with approvals, decisions, permits, receipts, and rollback support
- artifact-aware context and evidence-bound memory primitives
- a `plugin.toml`-driven plugin system with CLI, Feishu, scheduler, webhook, and MCP surfaces

## Development Environment

Use the project scripts for development, debugging, and restarting local environments. Do not maintain separate instructions for manual `venv`, manual `export HERMIT_BASE_DIR`, or hand-built `serve` / `menubar` commands.

Tests:

```bash
make test
```

`pyproject.toml` requires Python `>= 3.11`. Do not run the CLI with Python 3.9.

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

- `scripts/hermit-dev.sh` is now only a compatibility alias for `dev`; internally it forwards to `scripts/hermit-envctl.sh`
- when debugging or restarting a local environment, use the controller scripts first instead of manually composing `HERMIT_BASE_DIR + serve + menubar`
- the controller scripts handle the matching `service`, `menubar`, menu app, and basic status checks together
- to enter the CLI, inspect config, or check auth, prefer `scripts/hermit-env.sh <env> ...`
- when changing Python source, prefer `scripts/hermit-watch.sh <env>` or `make env-watch ENV=<env>` for watch-and-restart; this entrypoint manages `serve` and ensures the menubar companion is running
- `hermit reload` should be treated only as a graceful config/plugin/tool reload; it is not general source hot reload

Example direct service invocation inside the managed environment:

```bash
scripts/hermit-env.sh dev serve --adapter feishu
```

## Directory Layout

```text
hermit/
├── builtin/
│   ├── compact/
│   ├── feishu/
│   ├── github/
│   ├── grok/
│   ├── image_memory/
│   ├── mcp_loader/
│   ├── memory/
│   ├── orchestrator/
│   ├── planner/
│   ├── scheduler/
│   ├── usage/
│   ├── web_tools/
│   └── webhook/
├── core/
├── plugin/
├── storage/
├── autostart.py
├── config.py
├── context.py
├── logging.py
└── main.py
```

Additional notes:

- `hermit/plugins/` is a legacy compatibility layer; the current primary implementation is `hermit/plugin/`
- `hermit/core/orchestrator.py` still exists, but current subagent functionality comes from the builtin `orchestrator` plugin

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

- `serve` and `reload` currently use positional adapter arguments, not `--adapter`
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

1. `hermit/builtin/`
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

- [`JsonStore.read()`](hermit/storage/store.py)
- [`JsonStore.write()`](hermit/storage/store.py)
- [`JsonStore.update()`](hermit/storage/store.py)
- [`atomic_write()`](hermit/storage/atomic.py)
- [`FileGuard.acquire()`](hermit/storage/lock.py)

Notes:

- the old-document `JsonStore.load()` / `save()` APIs do not exist
- the session persistence format is single-file JSON, not JSONL

## Runtime and Debugging Guidance

- Start with [`hermit/main.py`](hermit/main.py) to confirm the CLI entrypoint
- Then read [`hermit/core/runner.py`](hermit/core/runner.py) to understand the execution path
- Then read [`hermit/plugin/manager.py`](hermit/plugin/manager.py) to understand capability assembly
- For scheduler / webhook / feishu work, check first whether `DISPATCH_RESULT` is the event you actually need

## Service Change Completion Rules

Any change that affects the behavior of a locally running `hermit serve` process should, by default, be closed out with this flow:

1. Determine whether the running service comes from the repo checkout or the installed `uv tool` copy
2. If the installed copy must be updated, run `bash install.sh`
3. Reload the target adapter, or start the service directly if it is not running
4. Check `~/.hermit/serve-<adapter>.pid` and the matching process
5. Check `~/.hermit/logs/<adapter>-stdout.log` / `stderr.log` to confirm reload or startup succeeded

Do not report “done” before that loop has been fully completed.
