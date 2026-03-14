# Hermit Configuration

This document explains the current configuration sources, precedence rules, key variables, and state directories in the implementation as it exists today.

Related documents:

- [`architecture.md`](./architecture.md)
- [`providers-and-profiles.md`](./providers-and-profiles.md)
- [`cli-and-operations.md`](./cli-and-operations.md)

## Configuration Sources

Hermit currently has four configuration sources:

1. code defaults
2. profiles in `~/.hermit/config.toml`
3. `.env` in the current working directory
4. `~/.hermit/.env` and shell environment variables

Two implementation details matter here:

- `hermit/main.py` manually loads `~/.hermit/.env` first and writes it into `os.environ`
- an existing variable in the shell is not overwritten by `~/.hermit/.env`

So, in practice, the approximate runtime precedence is:

`defaults < config.toml profile < current-directory .env < ~/.hermit/.env < shell environment variables`

If you want a single named configuration, prefer `config.toml` profiles. If you only need a temporary local override, use shell environment variables.

## Key Paths

By default `HERMIT_BASE_DIR=~/.hermit`, and related paths are derived from it:

| Path | Description |
| --- | --- |
| `~/.hermit/.env` | long-lived local environment variables |
| `~/.hermit/config.toml` | provider profiles and plugin variables |
| `~/.hermit/context.md` | default context file |
| `~/.hermit/memory/memories.md` | main long-term memory file |
| `~/.hermit/memory/session_state.json` | memory runtime state |
| `~/.hermit/sessions/` | active sessions |
| `~/.hermit/sessions/archive/` | archived sessions |
| `~/.hermit/schedules/jobs.json` | scheduled job definitions |
| `~/.hermit/schedules/history.json` | scheduled job history |
| `~/.hermit/plugins/` | installed external plugins |
| `~/.hermit/skills/` | custom skills |
| `~/.hermit/rules/` | rule text |
| `~/.hermit/hooks/` | reserved hooks directory |

## Multi-Environment Isolation

If the same machine is used for development, testing, and real user service, do not share a single `HERMIT_BASE_DIR`.

Recommended layout:

| Environment | `HERMIT_BASE_DIR` |
| --- | --- |
| live user service | `~/.hermit` |
| development | `~/.hermit-dev` |
| testing | `~/.hermit-test` |

At minimum, isolate these:

- `.env`
- `config.toml`
- `memory/`
- `sessions/`
- `logs/`
- `schedules/`
- `plugins/`
- `serve-*.pid`

Otherwise, common cross-environment interference includes:

- a personality / context change in dev affecting live user replies
- a plugin disabled in test also becoming disabled in prod
- logs, pid files, schedules, and sessions all mixed together

Prefer using the repository scripts directly:

```bash
scripts/hermit-env.sh dev serve --adapter feishu
scripts/hermit-env.sh test chat
scripts/hermit-env.sh prod config show
```

If you use `autostart`, non-default base directories also get their own label automatically:

- `com.hermit.serve.feishu`
- `com.hermit.serve.hermit-dev.feishu`
- `com.hermit.serve.hermit-test.feishu`

## Core Configuration Fields

### General Runtime

| Config | Default | Description |
| --- | --- | --- |
| `HERMIT_BASE_DIR` | `~/.hermit` | state directory root |
| `HERMIT_MODEL` | `claude-3-7-sonnet-latest` | default model name |
| `HERMIT_MAX_TOKENS` | `2048` | max output per request |
| `HERMIT_MAX_TURNS` | `100` | max turns in a single tool loop |
| `HERMIT_TOOL_OUTPUT_LIMIT` | `4000` | tool result truncation limit in characters |
| `HERMIT_THINKING_BUDGET` | `0` | thinking budget, where `0` means disabled |
| `HERMIT_IMAGE_MODEL` | empty | image analysis model; if empty, higher-level fallback is used |
| `HERMIT_IMAGE_CONTEXT_LIMIT` | `3` | max number of image context items injected |
| `HERMIT_PREVENT_SLEEP` | `true` | call `caffeinate -i` on macOS |
| `HERMIT_LOG_LEVEL` | `INFO` | log level |
| `HERMIT_SANDBOX_MODE` | `l0` | command sandbox mode |
| `HERMIT_COMMAND_TIMEOUT_SECONDS` | `30` | timeout for the `bash` tool |
| `HERMIT_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | session idle timeout |
| `HERMIT_LOCALE` | inferred from system environment | localization language for CLI / companion |

### Claude Provider

| Config | Description |
| --- | --- |
| `HERMIT_PROVIDER=claude` | default provider |
| `ANTHROPIC_API_KEY` / `HERMIT_CLAUDE_API_KEY` | direct Anthropic API access |
| `HERMIT_CLAUDE_AUTH_TOKEN` / `HERMIT_AUTH_TOKEN` | bearer token for a Claude-compatible gateway |
| `HERMIT_CLAUDE_BASE_URL` / `HERMIT_BASE_URL` | Claude-compatible gateway URL |
| `HERMIT_CLAUDE_HEADERS` / `HERMIT_CUSTOM_HEADERS` | extra request headers in `Key: Value, Key2: Value2` format |

### Codex / OpenAI Provider

| Config | Description |
| --- | --- |
| `HERMIT_PROVIDER=codex` | OpenAI Responses API mode |
| `HERMIT_OPENAI_API_KEY` / `OPENAI_API_KEY` | OpenAI API key |
| `HERMIT_OPENAI_BASE_URL` | OpenAI-compatible base URL |
| `HERMIT_OPENAI_HEADERS` | extra request headers |
| `HERMIT_PROVIDER=codex-oauth` | OAuth mode based on `~/.codex/auth.json` |
| `HERMIT_CODEX_COMMAND` | defaults to `codex`; kept for related workflows |

### Feishu / Scheduler / Webhook

| Config | Default | Description |
| --- | --- | --- |
| `HERMIT_FEISHU_APP_ID` / `FEISHU_APP_ID` | empty | Feishu App ID |
| `HERMIT_FEISHU_APP_SECRET` / `FEISHU_APP_SECRET` | empty | Feishu App Secret |
| `HERMIT_FEISHU_THREAD_PROGRESS` | `true` | enable thread progress cards |
| `HERMIT_SCHEDULER_ENABLED` | `true` | scheduler master switch |
| `HERMIT_SCHEDULER_CATCH_UP` | `true` | run missed jobs when the service starts |
| `HERMIT_SCHEDULER_FEISHU_CHAT_ID` | empty | default scheduler / reload notification target |
| `HERMIT_WEBHOOK_ENABLED` | `true` | enable webhook server in serve mode |
| `HERMIT_WEBHOOK_HOST` | `0.0.0.0` | webhook bind address |
| `HERMIT_WEBHOOK_PORT` | `8321` | webhook bind port |

## `.env.example`

The repository root [`.env.example`](../.env.example) currently covers only the most common Claude / Feishu setup.

If you use `codex` or `codex-oauth`, add:

```bash
HERMIT_PROVIDER=codex
HERMIT_OPENAI_API_KEY=sk-...
HERMIT_MODEL=gpt-5.4
```

Or define a profile in `config.toml`.

If you want the builtin GitHub MCP plugin, the common environment variables are:

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
GITHUB_MCP_URL=https://api.githubcopilot.com/mcp/
```

## `config.toml` Profiles

Hermit supports defining profiles in `~/.hermit/config.toml`.

Example:

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

Fields that a profile can override are listed in `PROFILE_FIELDS` inside [`hermit/provider/profiles.py`](../hermit/provider/profiles.py), including:

- provider / model
- token and base URL
- sandbox / timeout
- Feishu / scheduler / webhook related fields

## Plugin Variables

Besides profiles, `config.toml` also supports plugin variables:

```toml
[plugins.github.variables]
github_pat = "ghp_xxx"
github_mcp_url = "https://api.githubcopilot.com/mcp/"
```

These variables are injected when a plugin loads and can be referenced by templates in `plugin.toml`.

Common uses:

- GitHub MCP tokens
- custom MCP URLs
- plugin-specific private configuration

## Useful Inspection Commands

Show the fully resolved config:

```bash
hermit config show
```

Show profiles:

```bash
hermit profiles list
hermit profiles resolve --name codex-local
```

Show the auth source currently used by the active provider:

```bash
hermit auth status
```

## Context Injected into the System Prompt at Startup

[`hermit/context.py`](../hermit/context.py) writes these values into the base system prompt:

- current working directory
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

After that, `PluginManager.build_system_prompt()` appends:
