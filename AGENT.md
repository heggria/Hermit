# AGENT.md

Hermit 项目的协作开发说明。以下内容以当前源码为准。

## 项目技能

- 服务重部署技能：[`skills/hermit-service-redeploy/SKILL.md`](skills/hermit-service-redeploy/SKILL.md)

适用场景：

- 修改 `hermit serve` 运行链路相关代码
- 修改 builtin plugin、adapter、scheduler、webhook、Feishu 集成
- 修改安装、打包或本地运行时加载方式

执行这类任务时，收尾阶段必须按该技能完成本地重部署与验证，不能只停留在源码修改或单纯 reload。

## 项目概述

Hermit 是一个面向个人工作流的本地优先 AI Agent runtime。

当前核心特征：

- 手写 Anthropic Messages API tool loop
- 文件化长期状态
- `plugin.toml` 驱动的插件体系
- Feishu adapter、scheduler、webhook、MCP、长期记忆、图片记忆

## 开发环境

开发、调试、重启本地环境统一走脚本，不再维护手工 `venv`、手工 `export HERMIT_BASE_DIR`、手工拼 `serve` / `menubar` 的说明。

测试：

```bash
make test
```

`pyproject.toml` 要求 Python `>= 3.11`。不要使用 Python 3.9 解释器运行 CLI。

环境控制统一入口：

```bash
scripts/hermit-envctl.sh <prod|dev|test> <up|restart|down|status|logs>
scripts/hermit-watch.sh <prod|dev|test> [--adapter <adapter>]
```

常用示例：

```bash
scripts/hermit-envctl.sh dev restart
scripts/hermit-envctl.sh prod status
scripts/hermit-watch.sh dev
make env-restart ENV=dev
make env-status ENV=prod
make env-watch ENV=dev
```

说明：

- `scripts/hermit-dev.sh` 现在只是 `dev` 的兼容别名，底层统一转发到 `scripts/hermit-envctl.sh`
- 调试或重启本地环境时，优先使用总控脚本，不要再手动拼 `HERMIT_BASE_DIR + serve + menubar`
- 总控脚本会同时处理对应环境的 `service`、`menubar`、menu app 和基础状态检查
- 进入 CLI、查看配置、查看鉴权时，优先使用 `scripts/hermit-env.sh <env> ...`
- 改 Python 源码时，优先使用 `scripts/hermit-watch.sh <env>` 或 `make env-watch ENV=<env>` 做监听重启；该入口会托管 `serve` 并确保 menubar companion 已启动
- `hermit reload` 只当作优雅重载配置/插件/工具；它不是通用源码热更新

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

## 服务变更收尾规则

凡是会影响本地运行中 `hermit serve` 行为的改动，默认按下面流程收尾：

1. 评估运行中的服务是否来自 repo checkout 还是 `uv tool` 安装副本
2. 如需让安装副本更新，执行 `bash install.sh`
3. 对目标 adapter 执行 reload；如未运行则直接启动服务
4. 检查 `~/.hermit/serve-<adapter>.pid` 和对应进程
5. 检查 `~/.hermit/logs/<adapter>-stdout.log` / `stderr.log` 确认 reload 或启动成功

不要在未完成上述闭环前宣告“已完成”。
