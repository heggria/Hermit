# Hermit 配置文档

相关文档：

- [`architecture.md`](./architecture.md)
- [`openclaw-comparison.md`](./openclaw-comparison.md)

## 配置来源与优先级

当前实现不是单一 `.env` 模式，而是三层叠加：

1. 代码默认值
2. 当前工作目录 `.env`（由 `pydantic-settings` 读取）
3. `~/.hermit/.env` 与 shell 环境变量

实际细节：

- 启动时 [`hermit/main.py`](../hermit/main.py) 会先把 `~/.hermit/.env` 注入到 `os.environ`
- 如果 shell 里已经有同名变量，shell 值优先，不会被覆盖
- `Settings` 随后再读取当前目录 `.env`

因此在运行时可以近似理解为：

- 默认值 < 当前目录 `.env` < `~/.hermit/.env` < shell 环境变量

## 核心配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` / `HERMIT_ANTHROPIC_API_KEY` | 无 | Anthropic API Key |
| `HERMIT_AUTH_TOKEN` | 无 | Bearer Token 模式 |
| `HERMIT_BASE_URL` | 无 | 自定义 Anthropic 兼容网关 |
| `HERMIT_CUSTOM_HEADERS` | 无 | 自定义请求头，格式 `Key: Value, Key2: Value2` |
| `HERMIT_MODEL` | `claude-3-7-sonnet-latest` | 默认模型 |
| `HERMIT_MAX_TOKENS` | `2048` | 单次请求 `max_tokens` |
| `HERMIT_MAX_TURNS` | `100` | 单次 tool loop 最大轮数 |
| `HERMIT_TOOL_OUTPUT_LIMIT` | `4000` | 工具输出截断字符数 |
| `HERMIT_THINKING_BUDGET` | `0` | thinking budget；`0` 为关闭 |
| `HERMIT_IMAGE_MODEL` | 空 | 图片分析模型；为空时由上层逻辑回退到主模型 |
| `HERMIT_IMAGE_CONTEXT_LIMIT` | `3` | 注入的近期图片上下文上限 |
| `HERMIT_PREVENT_SLEEP` | `true` | macOS 下启用 `caffeinate -i` |
| `HERMIT_LOG_LEVEL` | `INFO` | 日志级别 |
| `HERMIT_SANDBOX_MODE` | `l0` | `l0` / `l1` |
| `HERMIT_COMMAND_TIMEOUT_SECONDS` | `30` | `bash` 工具超时 |
| `HERMIT_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | session 空闲超时 |
| `HERMIT_BASE_DIR` | `~/.hermit` | 状态目录根路径 |
| `HERMIT_SCHEDULER_ENABLED` | `true` | scheduler 总开关 |
| `HERMIT_SCHEDULER_CATCH_UP` | `true` | serve 启动时是否补跑错过任务 |
| `HERMIT_SCHEDULER_FEISHU_CHAT_ID` | 空 | scheduler / reload 主动通知目标 |

## `~/.hermit` 目录结构

启动后会确保这些目录存在：

```text
~/.hermit/
├── context.md
├── hooks/
├── image-memory/
├── memory/
│   ├── memories.md
│   └── session_state.json
├── plugins/
├── rules/
├── schedules/
├── sessions/
│   └── archive/
└── skills/
```

补充说明：

- `.env` 通常由 `setup` 或 `install.sh` 生成，但不属于 `_ensure_workspace()` 强制创建清单
- `plans/` 由 `/plan` 首次写文件时创建
- `serve-<adapter>.pid` 在 `hermit serve` 运行时创建
- `hooks/` 当前只做目录保留，不会自动执行其中的脚本

## 启动时注入的上下文

[`hermit/context.py`](../hermit/context.py) 会把这些路径和参数注入基础 system prompt：

- `current_working_directory`
- `hermit_base_dir`
- `memory_file`
- `session_state_file`
- `context_file`
- `skills_dir`
- `rules_dir`
- `hooks_dir`
- `plugins_dir`
- `image_memory_dir`
- `default_model`
- `max_tokens`
- `max_turns`
- `sandbox_mode`

随后 [`PluginManager.build_system_prompt()`](../hermit/plugin/manager.py) 会继续拼接：

- rules 文本
- skill catalog 或预加载 skill 全文
- 各插件在 `SYSTEM_PROMPT` hook 返回的动态片段

## 插件体系

当前插件支持的真实入口维度：

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
version = "1.0.0"
description = "插件说明"

[entry]
tools = "tools:register"
commands = "commands:register"
hooks = "hooks:register"
subagents = "subagents:register"
adapter = "adapter:register"
mcp = "mcp:register"
```

插件发现顺序：

1. `hermit/builtin/`
2. `~/.hermit/plugins/`

## 内置插件清单

| 插件 | 入口维度 | 说明 |
| --- | --- | --- |
| `memory` | `hooks` | 长期记忆 |
| `image_memory` | `hooks` | 图片记忆 |
| `orchestrator` | `hooks`, `subagents` | 子 agent |
| `web-tools` | `tools` | Web 搜索与抓取 |
| `grok` | `tools` | Grok 实时搜索 |
| `scheduler` | `tools`, `hooks` | 定时任务 |
| `webhook` | `tools`, `hooks` | Webhook |
| `github` | `mcp` | GitHub MCP |
| `mcp-loader` | `mcp` | 外部 MCP 配置加载 |
| `feishu` | `adapter`, `hooks` | 飞书 Adapter |
| `compact` | `commands` | 会话压缩 |
| `planner` | `commands` | 规划模式 |
| `usage` | `commands` | token 统计 |

## Hook 事件

当前有效事件：

- `SYSTEM_PROMPT`
- `REGISTER_TOOLS`
- `SESSION_START`
- `SESSION_END`
- `PRE_RUN`
- `POST_RUN`
- `SERVE_START`
- `SERVE_STOP`
- `DISPATCH_RESULT`

旧设计里的 `SCHEDULE_RESULT` 已经被统一的 `DISPATCH_RESULT` 取代。

## Skills 系统

Skills 使用渐进式披露：

1. 启动时只注入 `<available_skills>`
2. 需要时通过 `read_skill` 工具加载完整 `SKILL.md`
3. skill 中引用的脚本 / 文件再按需读取

当前 skill 来源有三类：

- `hermit/builtin/<plugin>/skills/<name>/SKILL.md`
- `~/.hermit/skills/<name>/SKILL.md`
- `~/.hermit/plugins/<plugin>/skills/<name>/SKILL.md`

## MCP Server 配置

支持两个位置：

- `~/.hermit/mcp.json`
- `./.mcp.json`

项目级配置覆盖全局同名 server。

示例：

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-notion"],
      "env": { "NOTION_API_KEY": "your-key" }
    },
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer your-token" }
    }
  }
}
```

MCP 工具命名格式：

```text
mcp__notion__search
mcp__github__create_issue
```

## Adapter 与服务模式

当前内置 Adapter 只有 Feishu。

启动方式：

```bash
hermit serve feishu
```

热重载：

```bash
hermit reload feishu
```

注意：

- `serve` / `reload` 的 `adapter` 在当前 Typer 定义里是位置参数，不是 `--adapter` 选项
- `autostart enable/disable` 才是 `--adapter` 选项风格

Feishu 相关变量：

- `HERMIT_FEISHU_APP_ID`
- `HERMIT_FEISHU_APP_SECRET`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

后两者只是兼容旧变量名。

## Scheduler

CLI 管理命令：

- `hermit schedule list`
- `hermit schedule add --name ... --prompt ... --cron ...`
- `hermit schedule add --name ... --prompt ... --once ...`
- `hermit schedule add --name ... --prompt ... --interval ...`
- `hermit schedule remove <id>`
- `hermit schedule enable <id>`
- `hermit schedule disable <id>`
- `hermit schedule history`

存储位置：

- `~/.hermit/schedules/jobs.json`
- `~/.hermit/schedules/history.json`
- `~/.hermit/schedules/logs/`

调度线程只会在 `hermit serve ...` 时启动。

## 持久化原语

对应实现：

- [`hermit/storage/atomic.py`](../hermit/storage/atomic.py)
- [`hermit/storage/lock.py`](../hermit/storage/lock.py)
- [`hermit/storage/store.py`](../hermit/storage/store.py)

`JsonStore` 当前公开方法是：

- `read()`
- `write()`
- `update()`

不是旧文档里写的 `load()` / `save()`。
