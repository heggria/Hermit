# AGENT.md

Hermit 项目的协作开发说明。以下内容以当前源码为准。

## 项目概述

Hermit 是一个面向个人工作流的本地优先 AI Agent runtime。

当前核心特征：

- 手写 Anthropic Messages API tool loop
- 文件化长期状态
- `plugin.toml` 驱动的插件体系
- Feishu adapter、scheduler、webhook、MCP、长期记忆、图片记忆

## 开发环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

测试：

```bash
uv run pytest
```

`pyproject.toml` 要求 Python `>= 3.11`。不要使用 Python 3.9 解释器运行 CLI。

## 目录结构

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

补充：

- `hermit/plugins/` 是旧兼容层；当前主实现使用 `hermit/plugin/`
- `hermit/core/orchestrator.py` 存在，但当前 subagent 能力来自 builtin `orchestrator` 插件

## CLI 事实表

顶层命令：

- `hermit setup`
- `hermit init`
- `hermit startup-prompt`
- `hermit run`
- `hermit chat`
- `hermit serve [adapter]`
- `hermit reload [adapter]`
- `hermit sessions`
- `hermit plugin ...`
- `hermit autostart ...`
- `hermit schedule ...`

`chat` 的 core slash commands：

- `/new`
- `/history`
- `/help`
- `/quit`

builtin 插件命令：

- `/compact`
- `/plan`
- `/usage`

注意：

- `serve` 和 `reload` 当前是位置参数风格，不是 `--adapter`
- `autostart enable/disable/status` 才使用 `--adapter`

## 配置与状态目录

默认根目录：`~/.hermit`

常见路径：

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

运行时还会出现：

- `~/.hermit/serve-<adapter>.pid`
- `~/.hermit/plans/`（首次 `/plan` 后）

## 插件系统

当前真实入口维度：

- `tools`
- `hooks`
- `commands`
- `subagents`
- `adapter`
- `mcp`

`plugin.toml` 示例：

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

发现路径：

1. `hermit/builtin/`
2. `~/.hermit/plugins/`

## Hook 事件

当前枚举值：

- `SYSTEM_PROMPT`
- `REGISTER_TOOLS`
- `SESSION_START`
- `SESSION_END`
- `PRE_RUN`
- `POST_RUN`
- `SERVE_START`
- `SERVE_STOP`
- `DISPATCH_RESULT`

不要再用旧名字 `SCHEDULE_RESULT`。

## 核心工具

当前核心工具集：

- `read_file`
- `write_file`
- `bash`
- `read_hermit_file`
- `write_hermit_file`
- `list_hermit_files`

其中只读工具会在 `/plan` 模式下保留，副作用工具会被禁用。

## 持久化约定

推荐优先使用：

- [`JsonStore.read()`](hermit/storage/store.py)
- [`JsonStore.write()`](hermit/storage/store.py)
- [`JsonStore.update()`](hermit/storage/store.py)
- [`atomic_write()`](hermit/storage/atomic.py)
- [`FileGuard.acquire()`](hermit/storage/lock.py)

注意：

- 旧文档中的 `JsonStore.load()` / `save()` 不存在
- session 持久化格式是单文件 JSON，不是 JSONL

## 运行与调试建议

- 先看 [`hermit/main.py`](hermit/main.py) 确认 CLI 入口
- 再看 [`hermit/core/runner.py`](hermit/core/runner.py) 理解执行链路
- 再看 [`hermit/plugin/manager.py`](hermit/plugin/manager.py) 理解能力装配
- 涉及 scheduler / webhook / feishu 时，优先检查 `DISPATCH_RESULT` 是否是你要挂接的事件
