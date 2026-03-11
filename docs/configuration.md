# Hermit 配置文档

这份文档说明当前实现里的配置来源、优先级、关键变量和状态目录。

相关文档：

- [`architecture.md`](./architecture.md)
- [`providers-and-profiles.md`](./providers-and-profiles.md)
- [`cli-and-operations.md`](./cli-and-operations.md)

## 配置来源

Hermit 当前有四类配置来源：

1. 代码默认值
2. `~/.hermit/config.toml` 中的 profile
3. 当前工作目录 `.env`
4. `~/.hermit/.env` 与 shell 环境变量

其中有两个实现细节很重要：

- `hermit/main.py` 会先手动读取 `~/.hermit/.env`，写入 `os.environ`
- shell 中已存在的同名变量不会被 `~/.hermit/.env` 覆盖

因此运行时的近似优先级可以理解为：

`默认值 < config.toml profile < 当前目录 .env < ~/.hermit/.env < shell 环境变量`

如果你只想维护一套“命名配置”，请优先用 `config.toml` profile；如果你只想做本机临时覆盖，用 shell env 即可。

## 关键路径

默认 `HERMIT_BASE_DIR=~/.hermit`，相关路径都从这里派生：

| 路径 | 说明 |
| --- | --- |
| `~/.hermit/.env` | 本机长期环境变量 |
| `~/.hermit/config.toml` | provider profile 与 plugin variables |
| `~/.hermit/context.md` | 默认上下文文件 |
| `~/.hermit/memory/memories.md` | 长期记忆主文件 |
| `~/.hermit/memory/session_state.json` | memory 运行状态 |
| `~/.hermit/sessions/` | 活跃 session |
| `~/.hermit/sessions/archive/` | 归档 session |
| `~/.hermit/schedules/jobs.json` | 定时任务定义 |
| `~/.hermit/schedules/history.json` | 定时任务历史 |
| `~/.hermit/plugins/` | 已安装外部插件 |
| `~/.hermit/skills/` | 自定义 skills |
| `~/.hermit/rules/` | 规则文本 |
| `~/.hermit/hooks/` | 预留 hooks 目录 |

## 多环境隔离建议

如果同一台机器同时承担开发、测试和实际用户服务，不要共用一个 `HERMIT_BASE_DIR`。

推荐目录：

| 环境 | `HERMIT_BASE_DIR` |
| --- | --- |
| 实际用户 | `~/.hermit` |
| 开发 | `~/.hermit-dev` |
| 测试 | `~/.hermit-test` |

至少要隔离这些状态：

- `.env`
- `config.toml`
- `memory/`
- `sessions/`
- `logs/`
- `schedules/`
- `plugins/`
- `serve-*.pid`

否则常见干扰包括：

- dev 改了人格 / context，实际用户回复跟着变
- test 禁了 plugin，prod 也被禁
- 日志、pid、定时任务、session 全部混在一起

推荐直接用仓库脚本：

```bash
scripts/hermit-env.sh dev serve --adapter feishu
scripts/hermit-env.sh test chat
scripts/hermit-env.sh prod config show
```

如果使用 `autostart`，非默认目录也会自动生成独立 label：

- `com.hermit.serve.feishu`
- `com.hermit.serve.hermit-dev.feishu`
- `com.hermit.serve.hermit-test.feishu`

## 核心配置项

### 通用运行时

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `HERMIT_BASE_DIR` | `~/.hermit` | 状态目录根 |
| `HERMIT_MODEL` | `claude-3-7-sonnet-latest` | 默认模型名 |
| `HERMIT_MAX_TOKENS` | `2048` | 单次请求最大输出 |
| `HERMIT_MAX_TURNS` | `100` | 单次 tool loop 最大轮数 |
| `HERMIT_TOOL_OUTPUT_LIMIT` | `4000` | 工具结果截断字符数 |
| `HERMIT_THINKING_BUDGET` | `0` | thinking budget，`0` 代表关闭 |
| `HERMIT_IMAGE_MODEL` | 空 | 图像分析模型；为空时走上层回退 |
| `HERMIT_IMAGE_CONTEXT_LIMIT` | `3` | 注入图片上下文上限 |
| `HERMIT_PREVENT_SLEEP` | `true` | macOS 上调用 `caffeinate -i` |
| `HERMIT_LOG_LEVEL` | `INFO` | 日志级别 |
| `HERMIT_SANDBOX_MODE` | `l0` | 命令沙箱模式 |
| `HERMIT_COMMAND_TIMEOUT_SECONDS` | `30` | `bash` 工具超时 |
| `HERMIT_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | session 空闲超时 |
| `HERMIT_LOCALE` | 从系统环境推断 | CLI / companion 本地化语言 |

### Claude provider

| 配置项 | 说明 |
| --- | --- |
| `HERMIT_PROVIDER=claude` | 默认 provider |
| `ANTHROPIC_API_KEY` / `HERMIT_CLAUDE_API_KEY` | 直连 Anthropic API |
| `HERMIT_CLAUDE_AUTH_TOKEN` / `HERMIT_AUTH_TOKEN` | Claude 兼容网关 Bearer token |
| `HERMIT_CLAUDE_BASE_URL` / `HERMIT_BASE_URL` | Claude 兼容网关地址 |
| `HERMIT_CLAUDE_HEADERS` / `HERMIT_CUSTOM_HEADERS` | 额外请求头，格式 `Key: Value, Key2: Value2` |

### Codex / OpenAI provider

| 配置项 | 说明 |
| --- | --- |
| `HERMIT_PROVIDER=codex` | OpenAI Responses API 模式 |
| `HERMIT_OPENAI_API_KEY` / `OPENAI_API_KEY` | OpenAI API key |
| `HERMIT_OPENAI_BASE_URL` | OpenAI 兼容 base URL |
| `HERMIT_OPENAI_HEADERS` | 额外请求头 |
| `HERMIT_PROVIDER=codex-oauth` | 基于 `~/.codex/auth.json` 的 OAuth 模式 |
| `HERMIT_CODEX_COMMAND` | 默认为 `codex`，保留给相关工作流 |

### Feishu / 调度 / webhook

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `HERMIT_FEISHU_APP_ID` / `FEISHU_APP_ID` | 空 | 飞书 App ID |
| `HERMIT_FEISHU_APP_SECRET` / `FEISHU_APP_SECRET` | 空 | 飞书 App Secret |
| `HERMIT_FEISHU_THREAD_PROGRESS` | `true` | 启用线程进度卡片 |
| `HERMIT_FEISHU_REACTION_ENABLED` | `true` | 启用自动 reaction |
| `HERMIT_FEISHU_REACTION_ACK` | `EYES` | 接单 reaction |
| `HERMIT_FEISHU_REACTION_DONE` | 空 | 完成 reaction |
| `HERMIT_SCHEDULER_ENABLED` | `true` | scheduler 总开关 |
| `HERMIT_SCHEDULER_CATCH_UP` | `true` | 服务启动时补跑错过任务 |
| `HERMIT_SCHEDULER_FEISHU_CHAT_ID` | 空 | scheduler / reload 默认通知目标 |
| `HERMIT_WEBHOOK_ENABLED` | `true` | serve 模式下启用 webhook server |
| `HERMIT_WEBHOOK_HOST` | `0.0.0.0` | webhook 绑定地址 |
| `HERMIT_WEBHOOK_PORT` | `8321` | webhook 绑定端口 |

## `.env.example`

仓库根目录的 [`.env.example`](../.env.example) 目前只覆盖最常见的 Claude / Feishu 场景。

如果你使用 `codex` 或 `codex-oauth`，建议再补充：

```bash
HERMIT_PROVIDER=codex
HERMIT_OPENAI_API_KEY=sk-...
HERMIT_MODEL=gpt-5.4
```

或在 `config.toml` 里定义 profile。

如果你要启用内置 GitHub MCP 插件，常见环境变量是：

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
GITHUB_MCP_URL=https://api.githubcopilot.com/mcp/
```

## `config.toml` profile

Hermit 支持在 `~/.hermit/config.toml` 中定义 profile。

示例：

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

可被 profile 覆盖的字段见 [`hermit/provider/profiles.py`](../hermit/provider/profiles.py) 中的 `PROFILE_FIELDS`，其中包括：

- provider / model
- token 与 base URL
- sandbox / timeout
- Feishu / scheduler / webhook 相关字段

## plugin variables

除了 profile 外，`config.toml` 还支持插件变量：

```toml
[plugins.github.variables]
github_pat = "ghp_xxx"
github_mcp_url = "https://api.githubcopilot.com/mcp/"
```

这类变量会在插件加载时注入，并可被 `plugin.toml` 中的模板引用。

常见用途：

- GitHub MCP token
- 自定义 MCP URL
- 私有插件自己的配置项

## 实用检查命令

查看完整解析结果：

```bash
hermit config show
```

查看 profiles：

```bash
hermit profiles list
hermit profiles resolve --name codex-local
```

查看当前 provider 使用的鉴权来源：

```bash
hermit auth status
```

## 启动时注入到 system prompt 的上下文

[`hermit/context.py`](../hermit/context.py) 会把这些内容写入基础 system prompt：

- 当前工作目录
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

随后 `PluginManager.build_system_prompt()` 还会继续拼接：

- rules 文本
- skill catalog
- 预加载 skills 正文
- `SYSTEM_PROMPT` hooks 的动态片段

## 状态目录创建行为

`hermit init` / `hermit setup` / 常规启动会确保这些目录存在：

```text
~/.hermit/
├── memory/
├── skills/
├── rules/
├── hooks/
├── plugins/
├── sessions/
└── image-memory/
```

补充说明：

- `context.md` 会被自动创建默认模板
- `memory/memories.md` 会在首次初始化时创建
- `schedules/` 在使用 scheduler 命令时创建
- `config.toml` 默认不会自动生成，菜单栏 companion 打开配置时才会生成默认模板

## 配置审查结论

这轮核对里，之前文档里存在两个主要偏差：

1. 旧文档把 provider 仍写成 Anthropic 单一路径，但当前代码实际已经支持 `claude`、`codex`、`codex-oauth`
2. 旧文档对 `config.toml` profile 和 plugin variables 说明不足，导致真实配置面大量“只在源码里存在”

这份文档已经按当前实现重写。
