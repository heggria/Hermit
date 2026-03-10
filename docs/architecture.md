# Hermit 架构说明

## 总览

Hermit 的运行时主链路很短：

```text
CLI / Adapter / Scheduler / Webhook
                 |
                 v
            AgentRunner
                 |
                 +--> SessionManager
                 +--> PluginManager
                 |      +--> hooks / skills / rules / tools / commands / adapters / subagents / MCP
                 |
                 v
            ClaudeAgent
                 |
                 +--> ToolRegistry
                 +--> Anthropic Messages API
```

核心目标不是做一个厚平台，而是维持：

- 可读的 tool loop
- 统一的插件装配层
- 文件化状态

## 启动与执行链路

### 1. CLI 入口

主入口在 [`hermit/main.py`](../hermit/main.py)。

启动后会：

1. 预加载 `~/.hermit/.env` 到进程环境
2. 构造 `Settings`
3. 确保工作目录 `~/.hermit` 的核心子目录存在
4. 扫描 builtin 和外部插件
5. 注册核心工具、插件工具、插件命令、subagent 和 MCP
6. 构建最终 system prompt

### 2. Session 编排

[`AgentRunner`](../hermit/core/runner.py) 是统一调度层。

它负责：

- slash command 分发
- session 读取 / 保存 / 归档
- `PRE_RUN` / `POST_RUN` / `SESSION_START` / `SESSION_END` Hook 编排
- 将普通输入送入 `ClaudeAgent`

当前 core slash commands 只有：

- `/new`
- `/history`
- `/help`
- `/quit`

插件命令通过 `runner.add_command()` 注入，所以 `/compact`、`/plan`、`/usage` 不是 core，而是 builtin plugin 提供。

### 3. Model loop

[`ClaudeAgent`](../hermit/core/agent.py) 实现手写 Anthropic Messages API tool loop。

主要职责：

- 组装 messages payload
- 注入工具 schema
- 处理 thinking budget
- 执行 `tool_use`
- 截断工具输出
- 统计 usage

Hermit 当前没有 provider 抽象层；这个 loop 是围绕 Anthropic 接口直接实现的。

## 核心模块

### [`hermit/core/agent.py`](../hermit/core/agent.py)

- Anthropic 调用与 block 规范化
- tool loop
- usage 统计

### [`hermit/core/runner.py`](../hermit/core/runner.py)

- 输入分发
- slash command 执行
- session 生命周期
- hook 编排

### [`hermit/core/session.py`](../hermit/core/session.py)

- session 使用单个 JSON 文件持久化
- 空闲超时后归档到 `sessions/archive/`
- 当前不是 JSONL

### [`hermit/core/tools.py`](../hermit/core/tools.py)

核心工具集当前固定包含：

- `read_file`
- `write_file`
- `bash`
- `read_hermit_file`
- `write_hermit_file`
- `list_hermit_files`

其中只读能力会被 `readonly_only=True` 过滤，用于 `/plan` 的只读规划模式。

### [`hermit/plugin/manager.py`](../hermit/plugin/manager.py)

- 发现并加载插件
- 聚合 skills / rules / tools / commands / adapters / subagents / MCP
- 构建最终 system prompt
- 启动和关闭 MCP

### [`hermit/plugin/loader.py`](../hermit/plugin/loader.py)

- 解析 `plugin.toml`
- 根据 `[entry]` 中的 `module:function` 调用插件入口
- builtin 插件通过 `hermit.builtin.<dir>.<module>` 导入

### [`hermit/storage/`](../hermit/storage)

- [`atomic.py`](../hermit/storage/atomic.py): 原子写
- [`lock.py`](../hermit/storage/lock.py): `FileGuard`
- [`store.py`](../hermit/storage/store.py): `JsonStore`

## 插件模型

插件契约来自 `plugin.toml`。当前被实际使用的入口维度有：

- `tools`
- `hooks`
- `commands`
- `subagents`
- `adapter`
- `mcp`

示例：

```toml
[plugin]
name = "my-plugin"
version = "0.1.0"
description = "Example plugin"

[entry]
tools = "tools:register"
hooks = "hooks:register"
commands = "commands:register"
subagents = "subagents:register"
adapter = "adapter:register"
mcp = "mcp:register"
```

builtin 与 external plugin 使用同一套结构；差别只在 `builtin = true` 时导入路径不同。

## Hook 事件

当前 `HookEvent` 枚举只有这些值：

- `SYSTEM_PROMPT`
- `REGISTER_TOOLS`
- `SESSION_START`
- `SESSION_END`
- `PRE_RUN`
- `POST_RUN`
- `SERVE_START`
- `SERVE_STOP`
- `DISPATCH_RESULT`

注意：

- 旧文档里的 `SCHEDULE_RESULT` 在当前实现中已经不存在
- scheduler、webhook、reload 通知现在统一走 `DISPATCH_RESULT`

## 当前 builtin 插件在架构中的角色

### `memory`

- `SESSION_END` 提取长期记忆
- `SYSTEM_PROMPT` 注入记忆上下文

### `image_memory`

- 保存图片资产与元数据
- 为近期图片注入语义上下文
- 为飞书图片工作流提供复用能力

### `orchestrator`

- 注册 researcher / coder subagent
- 暴露 `delegate_<name>` 工具

### `scheduler`

- 维护 `schedules/jobs.json`
- 在 `SERVE_START` 时启动后台线程
- 执行完成后触发 `DISPATCH_RESULT`

### `webhook`

- 提供 HTTP Webhook 入口
- 触发 Agent 运行
- 执行完成后触发 `DISPATCH_RESULT`

### `feishu`

- 提供 Adapter
- 注册飞书相关工具
- 订阅 `DISPATCH_RESULT`，将结果主动推送回飞书

### `github` / `mcp-loader`

- 负责 MCP server 规格收集
- 最终由 `PluginManager.start_mcp_servers()` 统一连接并注册工具

## 状态模型

Hermit 的长期状态默认放在 `~/.hermit`：

```text
~/.hermit/
├── .env
├── context.md
├── hooks/
├── image-memory/
├── memory/
├── plugins/
├── rules/
├── schedules/
├── sessions/
└── skills/
```

其中：

- `plans/` 只会在 `/plan` 首次生成计划文件时出现
- `serve-<adapter>.pid` 是运行期文件，不是初始化骨架

## 一句话定位

Hermit 是一个本地优先的个人 AI Agent runtime：核心层只保留 session、tool loop 和插件装配，记忆、图片、MCP、Adapter、Scheduler、Webhook、Subagent 等能力都通过统一插件模型向外扩展。
