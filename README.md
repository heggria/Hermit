# Hermit

Hermit 是一个本地优先的个人 AI Agent runtime。当前仓库的核心特征是：

- 手写 Anthropic Messages API tool loop
- 文件化、可审计的长期状态
- `plugin.toml` 驱动的 builtin / external 插件体系

当前源码中已经落地的能力包括：

- CLI one-shot 与多轮 chat
- 跨 session 长期记忆
- 图片记忆与飞书图片复用
- MCP Server 接入
- Feishu 长连接 adapter
- 定时任务
- 子 agent 委派
- Web 搜索与页面抓取
- Webhook 触发
- macOS 防睡眠与开机自启

## 文档导航

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/openclaw-comparison.md`](docs/openclaw-comparison.md)
- [`AGENT.md`](AGENT.md)

## 安装

要求：

- Python `>= 3.11`
- 推荐使用 [`uv`](https://docs.astral.sh/uv/)

最简单的安装方式：

```bash
make install
```

或：

```bash
bash install.sh
```

安装脚本会：

1. 安装 `uv`（如果系统里没有）
2. 以全局命令形式安装 `hermit`
3. 初始化 `~/.hermit`
4. 将当前 shell 中已有的关键变量写入 `~/.hermit/.env`

开发环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 快速开始

初始化：

```bash
hermit init
```

交互式配置：

```bash
hermit setup
```

开始聊天：

```bash
hermit chat
```

单次执行：

```bash
hermit run "总结当前仓库的插件系统"
```

## CLI 概览

基础命令：

- `hermit setup`
- `hermit init`
- `hermit startup-prompt`
- `hermit sessions`
- `hermit run "提示词"`
- `hermit chat`
- `hermit serve [adapter]`
- `hermit reload [adapter]`

插件管理：

- `hermit plugin list`
- `hermit plugin install <git-url>`
- `hermit plugin remove <name>`
- `hermit plugin info <name>`

自启管理：

- `hermit autostart enable --adapter feishu`
- `hermit autostart disable --adapter feishu`
- `hermit autostart status`

定时任务：

- `hermit schedule list`
- `hermit schedule add --name ... --prompt ... --cron ...`
- `hermit schedule add --name ... --prompt ... --once ...`
- `hermit schedule add --name ... --prompt ... --interval ...`
- `hermit schedule remove <id>`
- `hermit schedule enable <id>`
- `hermit schedule disable <id>`
- `hermit schedule history`

`chat` 中当前真实可用的 core slash commands：

- `/new`
- `/history`
- `/help`
- `/quit`

当前 builtin 插件还会追加：

- `/compact`
- `/plan`
- `/usage`

## 当前 builtin 插件

| 插件名 | 目录 | 入口维度 | 作用 |
| --- | --- | --- | --- |
| `memory` | `hermit/builtin/memory/` | `hooks` | 长期记忆抽取、衰减、合并、注入 |
| `image_memory` | `hermit/builtin/image_memory/` | `hooks` | 图片资产存储、语义分析、检索 |
| `orchestrator` | `hermit/builtin/orchestrator/` | `hooks`, `subagents` | researcher / coder 子 agent |
| `web-tools` | `hermit/builtin/web_tools/` | `tools` | DuckDuckGo Lite 搜索与 URL 抓取 |
| `grok` | `hermit/builtin/grok/` | `tools` | xAI Grok 实时搜索 |
| `scheduler` | `hermit/builtin/scheduler/` | `tools`, `hooks` | 定时任务与结果分发 |
| `webhook` | `hermit/builtin/webhook/` | `tools`, `hooks` | Webhook 路由、签名校验、Agent 触发 |
| `github` | `hermit/builtin/github/` | `mcp` | GitHub MCP builtin 接入 |
| `mcp-loader` | `hermit/builtin/mcp_loader/` | `mcp` | 读取 `mcp.json` / `.mcp.json` |
| `feishu` | `hermit/builtin/feishu/` | `adapter`, `hooks` | 飞书 Adapter、卡片发送、表情工具 |
| `compact` | `hermit/builtin/compact/` | `commands` | 会话压缩 |
| `planner` | `hermit/builtin/planner/` | `commands` | 规划模式 |
| `usage` | `hermit/builtin/usage/` | `commands` | token 用量统计 |

## MCP 配置

Hermit 会读取两个位置的 MCP 配置：

- `~/.hermit/mcp.json`
- `./.mcp.json`

项目级 `./.mcp.json` 会覆盖同名的全局配置。

示例：

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-notion"],
      "env": {
        "NOTION_API_KEY": "your-key"
      }
    },
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

工具会注册成 `mcp__{server}__{tool}`。

## 状态目录

默认状态目录是 `~/.hermit`：

```text
~/.hermit/
├── .env
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

说明：

- `hooks/` 当前只会被创建，不会自动加载目录脚本
- `plans/` 不是初始化时必建目录；只有 `/plan` 首次写计划时才会出现
- `serve-<adapter>.pid` 会在运行 `hermit serve` 时动态写入根目录

## 服务模式

飞书服务：

```bash
hermit serve feishu
```

热重载：

```bash
hermit reload feishu
```

Docker Compose 里也提供了一个最小 Feishu 服务入口：

```bash
docker compose up -d
```

## 测试

```bash
uv run pytest
```

如果你已经在 venv 里装好依赖，也可以：

```bash
pytest
```
