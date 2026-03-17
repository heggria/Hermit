---
description: "Configure Hermit: environment variables, context files, memory settings, plugin directories, provider profiles, and runtime options."
---

# Hermit Configuration

This document explains how Hermit is configured today: where settings come from, how profiles work, and where local-first state is stored.

This is a current-implementation document.

## Configuration Sources

Hermit currently reads configuration from four places:

1. code defaults
2. `~/.hermit/config.toml` profiles
3. `.env` in the current working directory
4. `~/.hermit/.env` plus shell environment variables

Two practical details matter:

- `~/.hermit/.env` is loaded into the process early
- values already present in the shell are not overwritten by that file

Approximate effective precedence:

`defaults < profile values < cwd .env < ~/.hermit/.env < shell environment`

## Key Paths

By default, `HERMIT_BASE_DIR=~/.hermit`.

Common paths:

| Path | Purpose |
| --- | --- |
| `~/.hermit/.env` | long-lived local environment |
| `~/.hermit/config.toml` | profiles and plugin variables |
| `~/.hermit/kernel/state.db` | kernel ledger database |
| `~/.hermit/memory/` | memory mirror and state |
| `~/.hermit/sessions/` | session files |
| `~/.hermit/schedules/` | scheduler state |
| `~/.hermit/plugins/` | installed plugins |
| `~/.hermit/skills/` | custom skills |
| `~/.hermit/rules/` | local rules |

This split matters because Hermit is not only prompt-and-transcript state. It also persists kernel records locally.

## Multi-Environment Isolation

Do not share one base directory across live, dev, and test environments.

Suggested layout:

| Environment | Base dir |
| --- | --- |
| live | `~/.hermit` |
| dev | `~/.hermit-dev` |
| test | `~/.hermit-test` |

Prefer the environment helpers:

```bash
scripts/hermit-env.sh dev chat
scripts/hermit-env.sh dev serve --adapter feishu
scripts/hermit-env.sh prod config show
```

This avoids mixing:

- credentials
- sessions
- schedules
- logs
- kernel state

## Core Runtime Fields

Important current fields include:

| Config | Default | Purpose |
| --- | --- | --- |
| `HERMIT_BASE_DIR` | `~/.hermit` | state root |
| `HERMIT_MODEL` | provider-dependent default | active model |
| `HERMIT_MAX_TOKENS` | `2048` | max output per request |
| `HERMIT_MAX_TURNS` | `100` | max tool-loop turns |
| `HERMIT_TOOL_OUTPUT_LIMIT` | `4000` | tool output truncation |
| `HERMIT_LOG_LEVEL` | `INFO` | runtime log level |
| `HERMIT_SANDBOX_MODE` | `l0` | command sandbox mode |
| `HERMIT_COMMAND_TIMEOUT_SECONDS` | `30` | bash timeout |
| `HERMIT_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | session idle timeout |

## Provider Fields

Hermit currently supports:

- `claude`
- `codex`
- `codex-oauth`

Typical examples:

```bash
HERMIT_PROVIDER=claude
ANTHROPIC_API_KEY=...
```

```bash
HERMIT_PROVIDER=codex
OPENAI_API_KEY=...
HERMIT_MODEL=gpt-5.4
```

```bash
HERMIT_PROVIDER=codex-oauth
HERMIT_MODEL=gpt-5.4
```

Provider-specific details are documented in [providers-and-profiles.md](./providers-and-profiles.md).

## Feishu, Scheduler, And Webhook

Important service-related fields include:

| Config | Purpose |
| --- | --- |
| `HERMIT_FEISHU_APP_ID` | Feishu adapter ID |
| `HERMIT_FEISHU_APP_SECRET` | Feishu adapter secret |
| `HERMIT_FEISHU_THREAD_PROGRESS` | thread progress behavior |
| `HERMIT_SCHEDULER_ENABLED` | scheduler master switch |
| `HERMIT_SCHEDULER_CATCH_UP` | catch-up behavior on startup |
| `HERMIT_WEBHOOK_ENABLED` | webhook server master switch |
| `HERMIT_WEBHOOK_HOST` | webhook bind host |
| `HERMIT_WEBHOOK_PORT` | webhook bind port |

## `config.toml` Profiles

Profiles live in:

```text
~/.hermit/config.toml
```

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

At runtime, the active profile is selected from:

1. `HERMIT_PROFILE` if set
2. otherwise `default_profile`

Useful inspection commands:

```bash
hermit profiles list
hermit profiles resolve --name codex-local
```

## Plugin Variables

`config.toml` also carries plugin variables:

```toml
[plugins.github.variables]
github_pat = "ghp_xxx"
github_mcp_url = "https://api.githubcopilot.com/mcp/"
```

These are used during plugin loading and template rendering.

## Useful Inspection Commands

```bash
hermit config show
hermit profiles list
hermit profiles resolve --name codex-local
hermit auth status
```

If Hermit behaves unexpectedly, these are usually the best first commands to run.

## Related Docs

- [providers-and-profiles.md](./providers-and-profiles.md)
- [cli-and-operations.md](./cli-and-operations.md)
- [status-and-compatibility.md](./status-and-compatibility.md)
