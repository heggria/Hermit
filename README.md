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

如果你是第一次参与这个仓库开发，下面这段按“从零开始”写。照着做就能把项目跑起来，并知道改代码应该去哪里。

### 1. 先理解你要开发的是什么

Hermit 不是一个 Web 应用，也不是一个前后端分离项目。它本质上是一个本地运行的 Python agent runtime。

你可以把它理解成：

- `hermit main.py` 提供 CLI 入口
- `provider/` 负责接不同模型
- `core/` 负责通用执行链路
- `plugin/` 负责插件装配
- `builtin/` 放已经做好的内置能力
- `companion/` 是 macOS 菜单栏控制层

所以开发 Hermit，大多数时候不是“启动网页然后点点点”，而是：

1. 配置一个本地开发环境
2. 运行 CLI 命令验证行为
3. 跑测试确认没有改坏
4. 必要时启动 `serve` 验证长期运行链路

### 2. 第一次拉仓库后怎么开始

先进入仓库根目录：

```bash
cd /Users/beta/work/Hermit
```

确认 Python 版本：

```bash
python3.11 --version
```

如果这里没有 `python3.11`，先解决 Python 环境问题，再继续。这个项目要求 Python `>= 3.11`。

### 3. 创建虚拟环境

推荐每个仓库自己有一个虚拟环境，不要把依赖直接装到系统 Python。

执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

激活后，你的终端前面通常会出现 `(.venv)`。

以后每次重新打开终端，只要还在这个仓库里开发，都先执行一次：

```bash
source .venv/bin/activate
```

### 4. 安装开发依赖

最常见的开发安装方式：

```bash
pip install -e ".[dev]"
```

这里的 `-e` 是 editable install，意思是你改了仓库里的 Python 代码，不需要重新安装包，命令会直接使用当前源码。

如果你还要调试 macOS 菜单栏 companion，再执行：

```bash
pip install -e ".[dev,macos]"
```

装完后可以检查命令是否可用：

```bash
hermit --help
```

如果能正常看到命令列表，说明开发安装已经成功。

### 5. 为什么一定要隔离开发环境

Hermit 会把状态写到 `~/.hermit` 下面，比如：

- `.env`
- `config.toml`
- memory
- sessions
- schedules
- logs

如果你开发时也直接用 `~/.hermit`，就容易和你平时真正使用的 Hermit 状态混在一起。

最简单的做法是专门给开发环境一个目录：

```bash
export HERMIT_BASE_DIR=~/.hermit-dev
hermit init
```

这条命令会初始化一个开发专用状态目录。

如果你不想每次手动 export，仓库里已经带了包装脚本：

```bash
scripts/hermit-env.sh dev chat
scripts/hermit-env.sh dev config show
scripts/hermit-env.sh dev serve --adapter feishu
```

对于初学者，建议优先用这些脚本，因为它们会帮你把 `HERMIT_BASE_DIR` 指到合适的位置。

### 6. 第一次把 CLI 跑起来

先初始化开发目录：

```bash
export HERMIT_BASE_DIR=~/.hermit-dev
hermit init
```

然后查看当前配置：

```bash
hermit config show
```

这条命令非常重要。它可以帮你确认：

- 当前 `base_dir` 是不是你想要的开发目录
- 当前 provider 是什么
- model 是什么
- auth 是否可用
- webhook / scheduler 是否开启

如果你只是想先看命令能不能跑通，不一定非要马上连真实模型。很多纯配置、纯测试类命令不需要模型鉴权。

### 7. 第一次进入交互模式

如果你已经准备好了 provider 的鉴权，可以直接：

```bash
hermit chat
```

或者执行单次任务：

```bash
hermit run "解释当前仓库的目录结构"
```

如果报鉴权错误，先检查：

```bash
hermit auth status
```

它会告诉你当前 provider 会使用哪种鉴权来源。

### 8. 第一次改代码前，先知道这些命令最有用

开发时最常用的是下面几条：

```bash
hermit --help
hermit config show
hermit auth status
hermit startup-prompt
hermit plugin list
hermit sessions
```

它们分别适合做这些事：

- `hermit --help`：确认 CLI 子命令是否注册成功
- `hermit config show`：确认配置解析是否正确
- `hermit auth status`：确认鉴权来源是否正确
- `hermit startup-prompt`：确认 system prompt 最终长什么样
- `hermit plugin list`：确认插件有没有被发现和加载
- `hermit sessions`：确认会话是否正常落盘

### 9. 如果你改的是长期运行链路

Hermit 不只有 `chat`，还有长期运行模式：

```bash
hermit serve --adapter feishu
```

这个模式适合验证：

- Feishu adapter
- scheduler
- webhook
- `SERVE_START` / `SERVE_STOP` hooks

如果服务已经在跑，你改了配置或插件，不一定要直接杀进程重启，可以试：

```bash
hermit reload --adapter feishu
```

`reload` 会发送 `SIGHUP`，让正在运行的服务做一次优雅重载，重新读取配置、插件和工具。

### 10. 最适合新手的测试顺序

不要一上来就跑最复杂的长期运行场景。推荐顺序是：

1. 先跑 CLI 测试
2. 再跑配置和 provider 测试
3. 最后再跑全量测试

命令：

```bash
uv run pytest tests/test_cli.py -q
uv run pytest tests/test_config.py tests/test_codex_provider.py -q
uv run pytest -q
```

如果你没装 `uv`，也可以直接：

```bash
pytest tests/test_cli.py -q
pytest -q
```

但这个仓库整体是偏 `uv` 工作流的，所以优先推荐 `uv run pytest`。

### 11. 改不同类型的代码，应该先看哪些测试

如果你改的是：

- CLI 命令、初始化、启动参数：先看 `tests/test_cli.py`
- 配置优先级、profile、环境变量：先看 `tests/test_config.py`
- Codex / OpenAI / OAuth：先看 `tests/test_codex_provider.py`、`tests/test_provider_runtime_services.py`
- memory / session / hooks：先看 `tests/test_memory_engine.py`、`tests/test_memory_hooks.py`、`tests/test_session.py`
- scheduler：先看 `tests/test_scheduler.py`、`tests/test_scheduler_dispatch.py`
- webhook：先看 `tests/test_webhook_server.py`
- Feishu：先看 `tests/test_feishu_dispatcher.py`、`tests/test_companion_control.py`
- 菜单栏 app：先看 `tests/test_companion_menubar.py`、`tests/test_companion_appbundle.py`

一个很实用的习惯是：先打开相关测试文件，看它想保证什么行为，再去改实现。

### 12. 改代码时，模块应该放哪里

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

简单记法：

- 通用执行框架放 `core/`
- 模型接入与鉴权放 `provider/`
- 功能扩展优先放 `builtin/`
- 插件基础设施放 `plugin/`
- macOS 控制层放 `companion/`
- 文件持久化相关放 `storage/`

不要把具体业务能力继续堆进 `core/`，否则后面会越来越难维护。

### 13. 新增一个功能时，常见落点

如果你要加的是：

- 新工具、hook、slash command、adapter、MCP 集成：优先做成 `hermit/builtin/<plugin>/`
- 新 provider：放进 `hermit/provider/providers/`，再在 `hermit/provider/services.py` 接入
- 新配置项：先加到 `hermit/config.py`，再补 `config show`、文档和测试
- 新 CLI 子命令：加在 `hermit/main.py`，同时补 `tests/test_cli.py`
- 新菜单栏行为：改 `hermit/companion/`

内置插件通常至少包含：

- `plugin.toml`
- `tools.py` / `hooks.py` / `commands.py` / `adapter.py` / `mcp.py` 中的一个或多个
- 需要时再带 `skills/` 或 `rules/`

如果你是新手，最稳妥的方法是先找一个现有 builtin 插件照着抄结构，不要从空白目录开始想象。

### 14. 一个推荐的新手开发流程

假设你要改一个 CLI 行为，建议这样做：

1. 打开相关测试文件，例如 `tests/test_cli.py`
2. 找到最接近你需求的测试
3. 先看当前实现在哪个文件，通常是 `hermit/main.py`
4. 改实现
5. 先跑相关测试
6. 再跑一次全量测试
7. 最后手动执行一遍 CLI 命令确认输出

如果你要改 provider / 配置链路，也是同样思路：

1. 先看测试
2. 再看 `config.py` 和 `provider/services.py`
3. 改实现
4. 跑相关测试
5. 用 `hermit config show` 和 `hermit auth status` 手动确认

### 15. 遇到问题时先查哪里

查运行时问题时，优先用这几个入口：

```bash
hermit config show
hermit auth status
hermit startup-prompt
hermit plugin list
hermit sessions
```

如果是长期运行问题，再看开发目录下这些文件：

- `~/.hermit-dev/logs/`
- `~/.hermit-dev/serve-<adapter>.pid`
- `~/.hermit-dev/schedules/jobs.json`
- `~/.hermit-dev/schedules/history.json`
- `~/.hermit-dev/sessions/`
- `~/.hermit-dev/memory/memories.md`

如果是菜单栏启动的服务，日志通常会在对应 base dir 的 `logs/` 下。

### 16. 给完全没接触过项目的人一个最短上手路径

如果你只想最快开始开发，按这个顺序做：

```bash
cd /Users/beta/work/Hermit
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export HERMIT_BASE_DIR=~/.hermit-dev
hermit init
uv run pytest tests/test_cli.py -q
hermit config show
hermit --help
```

做到这里，你已经完成了：

- 开发环境安装
- 开发态状态目录隔离
- 基本测试验证
- CLI 可用性验证

接下来再去改代码，会稳很多。

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
