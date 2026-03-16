# Repository Layout

This document describes the actual current repository structure and the responsibility boundaries between areas. It is not a “future cleanup plan.”

## Top-Level Structure

```text
.
├── docs/                 文档
├── src/                  Python 源码根目录
├── tests/                测试
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
├── builtin/              内置插件
├── companion/            macOS 菜单栏 companion
├── core/                 runner / session / tools / sandbox
├── plugin/               插件契约、加载器、管理器
├── provider/             provider 协议、实现与 runtime services
├── storage/              原子写、文件锁、JSON store
├── config.py             Settings 与派生路径
├── context.py            基础 system prompt 上下文
├── i18n.py               本地化工具
├── locales/              文案 catalog
├── logging.py            日志配置
└── main.py               CLI 入口
```

## `src/hermit/builtin/`

Directory for builtin plugins. The main ones currently include:

- `memory`
- `image_memory`
- `orchestrator`
- `web_tools`
- `grok`
- `computer_use`
- `scheduler`
- `webhook`
- `github`
- `mcp_loader`
- `feishu`
- `compact`
- `planner`
- `usage`

Each plugin usually contains:

- `plugin.toml`
- `tools.py` / `hooks.py` / `commands.py` / `adapter.py` / `mcp.py`
- `skills/`
- optional `rules/`

## `src/hermit/core/`

Current runtime core layer:

- `agent.py`
- `runner.py`
- `sandbox.py`
- `session.py`
- `tools.py`

This layer does not hold product features. It only holds the shared execution framework.

## `src/hermit/plugin/`

Plugin infrastructure:

- `base.py` defines manifests, hook events, and the command / adapter / subagent contracts
- `loader.py` parses `plugin.toml` and loads entrypoints
- `manager.py` aggregates all plugin assets
- `config.py` resolves plugin variables and templates

## `src/hermit/provider/`

Provider-related code:

- `contracts.py` defines the unified provider contract
- `messages.py` normalizes blocks
- `runtime.py` implements the shared tool loop
- `services.py` builds providers and helper services
- `profiles.py` parses `config.toml`
- `providers/` contains concrete provider implementations

## `src/hermit/companion/`

Separate macOS companion layer:

- `control.py` service control
- `menubar.py` menu bar UI
- `appbundle.py` local app bundle and Login Item handling

It is not part of the plugin system.

## `docs/`

The most important documentation currently in the repository:

- `architecture.md`
- `configuration.md`
- `providers-and-profiles.md`
- `cli-and-operations.md`
- `desktop-companion.md`
- `i18n.md`
- `openclaw-comparison.md`

## `tests/`

Test coverage is already fairly broad. The current focus includes:

- CLI
- config / profile
- provider runtime
- session / memory / hooks
- scheduler / webhook
- Feishu adapter
- companion

This review actually ran:

```bash
uv run pytest -q
```

Result:

- current suite size is large and CI-sharded; use `make test` for the live count

## Known Structural Characteristics in the Current Repository

### `build/` Is a Packaging Artifact, Not Source

The repository currently includes mirrored packaging output under `build/lib/...`. Read and modify the source under `src/hermit/`, not the build artifacts.

### There Are Still a Few Non-Core Files at the Root

For example:

- `beijing_weekend_trip_march2026.md`

These files do not affect runtime behavior, but they are not part of the core project structure.

### The Plugin Layer Is the Main Extension Surface

Most Hermit capabilities are now pushed down into `src/hermit/builtin/` instead of continuing to expand `core/`.

## What Changed in This Version of the Document

- removed “next step suggestion” style content
- rewrote the document around the real current directory structure
- clearly separated source, plugins, companion, tests, and packaging artifacts
