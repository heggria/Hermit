# Hermit

Hermit 是一个本地优先、文件状态优先的个人 AI Agent runtime。它不是厚平台，而是一套可以直接读源码、长期运行、挂接插件和外部通道的 Python runtime。

当前仓库已经落地的核心能力：

- CLI 单次执行与多轮会话
- `claude`、`codex`、`codex-oauth` 三种 provider 模式
- 文件化 session、长期记忆、图片记忆
- `plugin.toml` 驱动的 builtin / external 插件体系
- MCP server 装配与工具注册
- Feishu adapter
- scheduler / webhook / 子 agent 委派
- macOS `launchd` 自启
- macOS 菜单栏 companion

## 项目定位

Hermit 的取舍很明确：

- 核心链路短：CLI、adapter、webhook、scheduler 最终都汇到同一条 runner 链路
- 长期状态可审计：默认保存在 `~/.hermit`
- 插件是第一层扩展面：tools、hooks、commands、subagents、adapter、mcp
- 优先保留手写 runtime，而不是把行为藏进厚框架

如果你想要的是一个容易修改、容易加私有能力、容易排查状态的个人 agent runtime，这个仓库就是朝这个方向设计的。

## 文档导航

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/providers-and-profiles.md`](docs/providers-and-profiles.md)
- [`docs/cli-and-operations.md`](docs/cli-and-operations.md)
- [`docs/desktop-companion.md`](docs/desktop-companion.md)
- [`docs/repository-layout.md`](docs/repository-layout.md)
- [`docs/i18n.md`](docs/i18n.md)
- [`docs/openclaw-comparison.md`](docs/openclaw-comparison.md)
- [`AGENT.md`](AGENT.md)

## 安装

要求：

- Python `>= 3.11`
- 推荐使用 `uv`
- macOS 菜单栏功能需要额外安装 `rumps`

最简单的安装方式：

```bash
make install
```

或：

```bash
bash install.sh
```

安装脚本会：

1. 安装 `uv`（如果不存在）
2. 以 `uv tool` 方式安装 Hermit
3. 初始化 `~/.hermit`
4. 自动把当前 shell 中已有的关键环境变量追加进 `~/.hermit/.env`
5. 在 macOS 上安装菜单栏 companion app bundle

开发环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果需要菜单栏 companion：

```bash
pip install -e ".[dev,macos]"
```

## 开发

如果你是要改 Hermit 本身，而不是只把它当成一个已安装工具用，推荐直接在仓库根目录走 editable install。

### 1. 本地开发环境

推荐流程：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

如果你也要调试 macOS 菜单栏 companion：

```bash
pip install -e ".[dev,macos]"
```

仓库也默认按 `uv` 习惯使用，测试时可直接：

```bash
uv run pytest -q
```

### 2. 开发时的状态目录隔离

开发时不要直接复用生产态的 `~/.hermit`。最简单的做法是给开发环境单独指定 `HERMIT_BASE_DIR`：

```bash
export HERMIT_BASE_DIR=~/.hermit-dev
hermit init
```

仓库内也提供了包装脚本：

```bash
scripts/hermit-env.sh dev chat
scripts/hermit-env.sh dev serve --adapter feishu
scripts/hermit-env.sh dev config show
```

这样可以把这些状态隔离开：

- `.env`
- `config.toml`
- sessions / memory / schedules
- logs / pid
- 已安装外部插件

### 3. 常用开发入口

最常用的几个命令：

```bash
hermit chat
hermit run "解释当前 provider runtime 的结构"
hermit startup-prompt
hermit config show
hermit profiles list
hermit auth status
```

如果你在改长期运行链路，通常会用：

```bash
hermit serve --adapter feishu
hermit reload --adapter feishu
```

其中：

- `startup-prompt` 用来确认最终 system prompt 是否符合预期
- `config show` 用来确认 profile、provider、auth、webhook、scheduler 是否被正确解析
- `reload` 用来验证配置与插件是否能被优雅重建

### 4. 测试方式

仓库当前测试覆盖的重点包括：

- CLI
- config / profile
- provider runtime
- session / memory / hooks
- scheduler / webhook
- Feishu adapter
- macOS companion

常用命令：

```bash
uv run pytest -q
uv run pytest tests/test_cli.py -q
uv run pytest tests/test_config.py tests/test_codex_provider.py -q
```

如果你改的是：

- CLI 命令或启动行为，优先看 `tests/test_cli.py`
- provider / 鉴权 / profile，优先看 `tests/test_config.py`、`tests/test_codex_provider.py`
- 插件装配、hook、tools，优先看 `tests/test_plugin_manager.py`、`tests/test_tools.py`
- 长期记忆与状态存储，优先看 `tests/test_memory_engine.py`、`tests/test_memory_hooks.py`、`tests/test_session.py`

### 5. 代码结构怎么改

顶层结构：

```text
docs/      文档
hermit/    主源码
tests/     测试
skills/    仓库内辅助 skill
```

`hermit/` 下主要分层：

- `main.py`：CLI 入口、workspace 初始化、`serve` / `reload`
- `config.py`：Settings、路径派生、配置兼容与优先级
- `provider/`：provider 协议、实现、runtime services
- `core/`：runner、session、sandbox、tool registry
- `plugin/`：插件契约、解析、加载、管理
- `builtin/`：内置插件
- `companion/`：macOS 菜单栏 companion
- `storage/`：文件锁、原子写、JSON store

一个简单判断标准：

- 通用执行框架放 `core/`
- 模型接入与鉴权放 `provider/`
- 功能扩展优先放 `builtin/`
- 插件基础设施放 `plugin/`
- macOS 控制层放 `companion/`

不要把具体产品能力继续堆进 `core/`。

### 6. 新增功能时的落点

如果你要加的是：

- 新工具、hook、slash command、adapter、MCP 集成：优先做成 `hermit/builtin/<plugin>/`
- 新 provider：放进 `hermit/provider/providers/`，再在 `hermit/provider/services.py` 接入
- 新配置项：先加到 `hermit/config.py`，再补 `config show`、相关文档和测试
- 新 CLI 子命令：加在 `hermit/main.py`，同时补 `tests/test_cli.py`
- 新菜单栏行为：改 `hermit/companion/`

内置插件通常至少包含：

- `plugin.toml`
- `tools.py` / `hooks.py` / `commands.py` / `adapter.py` / `mcp.py` 中的一个或多个
- 需要时再带 `skills/` 或 `rules/`

### 7. 调试建议

查运行时问题时，优先用这几个入口：

```bash
hermit config show
hermit auth status
hermit startup-prompt
hermit plugin list
hermit sessions
```

如果是长期运行问题，再看：

- `~/.hermit-dev/logs/`
- `~/.hermit-dev/serve-<adapter>.pid`
- `~/.hermit-dev/schedules/jobs.json`
- `~/.hermit-dev/schedules/history.json`
- `~/.hermit-dev/sessions/`

如果是菜单栏启动的服务，日志通常会在对应 base dir 的 `logs/` 下。

## 快速开始

初始化 workspace：

```bash
hermit init
```

交互式写入 `~/.hermit/.env`：

```bash
hermit setup
```

查看当前解析后的配置：

```bash
hermit config show
```

开始多轮聊天：

```bash
hermit chat
```

执行单次任务：

```bash
hermit run "总结当前仓库的插件系统"
```

以 Feishu adapter 进入长期运行模式：

```bash
hermit serve --adapter feishu
```

对运行中的服务做优雅重载：

```bash
hermit reload --adapter feishu
```

查看最终启动时注入的 system prompt：

```bash
hermit startup-prompt
```

## Provider 模式

Hermit 当前源码支持三种 provider。

### 1. `claude`

默认模式。可直接使用 Anthropic API，也可走兼容 Claude 接口的 proxy / gateway。

常见变量：

- `ANTHROPIC_API_KEY` 或 `HERMIT_CLAUDE_API_KEY`
- `HERMIT_CLAUDE_AUTH_TOKEN` / `HERMIT_AUTH_TOKEN`
- `HERMIT_CLAUDE_BASE_URL` / `HERMIT_BASE_URL`
- `HERMIT_CLAUDE_HEADERS` / `HERMIT_CUSTOM_HEADERS`

### 2. `codex`

通过 OpenAI Responses API 运行，要求本地存在 OpenAI API key。

常见变量：

- `HERMIT_PROVIDER=codex`
- `HERMIT_OPENAI_API_KEY` 或 `OPENAI_API_KEY`
- `HERMIT_OPENAI_BASE_URL`
- `HERMIT_OPENAI_HEADERS`

如果 `~/.codex/auth.json` 存在但不含本地 API key，Hermit 会明确报错，而不是静默回退。

### 3. `codex-oauth`

读取本机 `~/.codex/auth.json` 中的 access / refresh token，以 OAuth 方式调用。

常见场景：

- 本机已登录 Codex / ChatGPT 桌面体系
- 不想单独管理 OpenAI API key

默认情况下，如果模型名仍是 Claude 前缀，Hermit 会为 Codex 系 provider 自动回退到 `~/.codex/config.toml` 的模型，若未配置则使用 `gpt-5.4`。

更完整说明见 [`docs/providers-and-profiles.md`](docs/providers-and-profiles.md)。

## 配置方式

Hermit 不是单一 `.env` 项目，当前实现有五层来源：

1. 代码默认值
2. `~/.hermit/config.toml` 中的 profile
3. 当前目录 `.env`
4. `~/.hermit/.env`
5. shell 环境变量

实际行为上可以理解为：

- profile 负责定义“命名配置”
- 当前目录 `.env` 适合项目级覆盖
- `~/.hermit/.env` 适合本机长期覆盖
- shell 变量优先级最高

常见命令：

```bash
hermit profiles list
hermit profiles resolve --name codex-local
hermit auth status
hermit config show
```

示例 `config.toml`：

```toml
default_profile = "codex-local"

[profiles.codex-local]
provider = "codex-oauth"
model = "gpt-5.4"
max_turns = 60

[profiles.claude-work]
provider = "claude"
model = "claude-3-7-sonnet-latest"
claude_base_url = "https://example.internal/claude"
claude_headers = "X-Biz-Id: workbench"
```

完整字段和优先级说明见 [`docs/configuration.md`](docs/configuration.md)。

## CLI 概览

顶层命令：

- `hermit setup`
- `hermit init`
- `hermit startup-prompt`
- `hermit run "提示词"`
- `hermit chat`
- `hermit serve --adapter feishu`
- `hermit reload --adapter feishu`
- `hermit sessions`
- `hermit plugin ...`
- `hermit autostart ...`
- `hermit schedule ...`
- `hermit config show`
- `hermit profiles list`
- `hermit profiles resolve`
- `hermit auth status`

chat / serve 模式中的 core slash commands：

- `/new`
- `/history`
- `/help`
- `/quit`（仅 CLI）

当前 builtin 插件额外提供：

- `/compact`
- `/plan`
- `/usage`

长期运行模式下，`serve` 会先做一轮环境预检，再启动 adapter、scheduler、webhook 等 `SERVE_START` 生命周期。`reload` 使用 `SIGHUP` 做优雅重载，会重新读取配置、插件和工具，而不是直接粗暴重启进程。

更完整命令参考见 [`docs/cli-and-operations.md`](docs/cli-and-operations.md)。

## Builtin 插件

当前内置插件清单：

| 插件 | 入口维度 | 主要作用 |
| --- | --- | --- |
| `memory` | `hooks` | 长期记忆抽取、检索、衰减、合并 |
| `image_memory` | `hooks` | 图片资产与图片语义记忆 |
| `orchestrator` | `hooks`, `subagents` | researcher / coder 子 agent 委派 |
| `web-tools` | `tools` | Web 搜索与页面抓取 |
| `grok` | `tools` | Grok 实时搜索 |
| `computer_use` | `tools` | macOS screenshot / 鼠标 / 键盘控制 |
| `scheduler` | `tools`, `hooks` | 定时任务与结果广播 |
| `webhook` | `tools`, `hooks` | Webhook 路由与 agent 触发 |
| `github` | `mcp` | GitHub MCP 集成 |
| `mcp-loader` | `mcp` | 从 `.mcp.json` 加载 MCP server |
| `feishu` | `adapter`, `hooks` | 飞书 adapter、回执与工具 |
| `compact` | `commands` | 会话压缩 |
| `planner` | `commands` | 只读规划模式 |
| `usage` | `commands` | token 用量统计 |

外部插件通过 `plugin.toml` 描述，安装位置默认在 `~/.hermit/plugins/`。可通过 `hermit plugin list/install/remove/info` 管理。

## macOS Companion 与自启

Hermit 自带独立的菜单栏 companion，不属于插件体系。

常用入口：

```bash
hermit-menubar --adapter feishu
hermit-menubar-install-app --adapter feishu
```

它负责：

- 查看服务状态与 PID
- Start / Stop / Reload `hermit serve`
- 管理 `launchd` 自启
- 打开配置、日志、Hermit home

自启命令：

```bash
hermit autostart enable --adapter feishu
hermit autostart disable --adapter feishu
hermit autostart status
```

更完整说明见 [`docs/desktop-companion.md`](docs/desktop-companion.md)。

## 状态目录

默认目录是 `~/.hermit`：

```text
~/.hermit/
├── .env
├── config.toml
├── context.md
├── hooks/
├── image-memory/
├── logs/
├── memory/
│   ├── memories.md
│   └── session_state.json
├── plans/
├── plugins/
├── rules/
├── schedules/
│   ├── history.json
│   └── jobs.json
├── serve-<adapter>.pid
├── sessions/
│   └── archive/
└── skills/
```

补充说明：

- `config.toml` 不会由 `hermit init` 自动生成
- `logs/` 主要由 menu bar companion 启动服务时写入
- `plans/` 只会在 `/plan` 首次落盘后出现
- `serve-<adapter>.pid` 只在 `hermit serve` 运行期间存在

## 环境隔离

推荐约定：

- `~/.hermit`：实际用户环境
- `~/.hermit-dev`：本地开发环境
- `~/.hermit-test`：测试 / 联调环境

不要让 `dev` / `test` 复用 `~/.hermit`。否则会共享：

- `.env`
- `config.toml`
- memory / sessions / schedules / logs / pid
- 飞书 bot 配置与服务状态

仓库内提供了环境包装脚本：

```bash
scripts/hermit-env.sh dev serve --adapter feishu
scripts/hermit-env.sh test chat
scripts/hermit-env.sh prod config show
```

菜单栏与自启也有对应包装脚本：

```bash
scripts/hermit-menubar-env.sh dev --adapter feishu
scripts/hermit-menubar-install-env.sh dev --open
scripts/hermit-autostart-env.sh test enable --adapter feishu
```

如果要给多个环境同时开 `launchd` 自启，必须使用不同的 `HERMIT_BASE_DIR`。当前实现会基于 base dir 自动生成不同 label，例如：

- `com.hermit.serve.feishu`
- `com.hermit.serve.hermit-dev.feishu`
- `com.hermit.serve.hermit-test.feishu`

## 调试与验证

运行测试：

```bash
uv run pytest -q
```

查看启动前环境自检：

```bash
hermit serve --adapter feishu
```

查看最终 system prompt：

```bash
hermit startup-prompt
```

查看当前 session 列表：

```bash
hermit sessions
```

## Docker

仓库自带：

- [`Dockerfile`](Dockerfile)
- [`docker-compose.yml`](docker-compose.yml)

compose 示例当前以长期运行的 Feishu adapter 为目标，等价命令是：

```bash
hermit serve --adapter feishu
```

## 许可

MIT
