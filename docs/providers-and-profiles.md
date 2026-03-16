# Providers And Profiles

This document explains the provider modes Hermit currently supports and how profile selection works in practice.

Hermit's current provider layer is part of the runtime implementation. It should not be confused with the broader kernel thesis, even though both meet in the shared execution path.

## Supported Providers

Hermit currently supports three providers:

- `claude`
- `codex`
- `codex-oauth`

The provider entrypoint is built through `src/hermit/provider/services.py`.

## `claude`

Typical uses:

- direct Anthropic API access
- a Claude-compatible gateway

Direct access:

```bash
HERMIT_PROVIDER=claude
ANTHROPIC_API_KEY=...
```

Gateway usage:

```bash
HERMIT_PROVIDER=claude
HERMIT_AUTH_TOKEN=...
HERMIT_BASE_URL=https://your-gateway.example.com/llm/claude
HERMIT_CUSTOM_HEADERS=X-Biz-Id: hermit
HERMIT_MODEL=claude-3-7-sonnet-latest
```

Compatible aliases still exist for some Claude-specific environment variables.

## `codex`

`codex` mode uses the OpenAI Responses API path. It is not the same thing as reusing local desktop OAuth tokens.

Typical configuration:

```bash
HERMIT_PROVIDER=codex
OPENAI_API_KEY=...
HERMIT_MODEL=gpt-5.4
```

Optional:

```bash
HERMIT_OPENAI_BASE_URL=https://api.openai.com/v1
HERMIT_OPENAI_HEADERS=X-Project: hermit
```

## `codex-oauth`

`codex-oauth` mode is the path that reuses OAuth state from:

```text
~/.codex/auth.json
```

Typical configuration:

```bash
HERMIT_PROVIDER=codex-oauth
HERMIT_MODEL=gpt-5.4
```

Requirements:

- `~/.codex/auth.json` must exist
- it must contain usable token state

## Profiles

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

Practical selection order:

1. `HERMIT_PROFILE` if set
2. otherwise `default_profile`
3. values from the selected profile are applied
4. shell and environment overrides still win on top

Useful commands:

```bash
hermit profiles list
hermit profiles resolve --name codex-local
hermit auth status
hermit config show
```

## Recommended Profile Shapes

### Personal machine, direct Claude

```toml
default_profile = "default"

[profiles.default]
provider = "claude"
model = "claude-3-7-sonnet-latest"
```

### Personal machine, reuse Codex OAuth

```toml
default_profile = "codex-local"

[profiles.codex-local]
provider = "codex-oauth"
model = "gpt-5.4"
max_turns = 60
```

### Team environment, gateway-backed Claude

```toml
default_profile = "work"

[profiles.work]
provider = "claude"
model = "claude-3-7-sonnet-latest"
claude_base_url = "https://gateway.example.com/claude"
claude_headers = "X-Biz-Id: hermit"
```

## What This Means Architecturally

The provider layer matters, but it is not Hermit's main differentiator.

Provider choice changes:

- model backend
- auth source
- request transport

It does not change Hermit's bigger thesis around:

- task-first work
- governed execution
- artifact-native context
- receipts, proofs, and rollback-aware recovery

## Related Docs

- [configuration.md](./configuration.md)
- [cli-and-operations.md](./cli-and-operations.md)
- [architecture.md](./architecture.md)
