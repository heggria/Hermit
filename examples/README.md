# Hermit Examples

Hands-on examples for the Hermit governed agent kernel. Each directory is a
self-contained walkthrough you can read or run.

| Example | Description |
|---------|-------------|
| [quick-start](quick-start/) | Minimal task flow: run a task, inspect it, verify its proof chain |
| [approval-workflow](approval-workflow/) | Approval gates, approve/deny decisions, and receipt trails |
| [feishu-bot](feishu-bot/) | Feishu (Lark) messaging adapter integration |
| [scheduled-tasks](scheduled-tasks/) | Cron-driven scheduled task execution |

## Prerequisites

- Python >= 3.13
- Hermit installed (`bash install.sh` or `make install`)
- At least one LLM provider configured (see below)

## Provider Configuration

Hermit reads provider settings from environment variables. Set them in your
shell or in `~/.hermit/.env`:

```bash
# Anthropic
export HERMIT_PROVIDER=claude
export ANTHROPIC_API_KEY=sk-ant-...
export HERMIT_MODEL=claude-sonnet-4-20250514

# OpenAI / Codex
export HERMIT_PROVIDER=codex
export OPENAI_API_KEY=sk-...
export HERMIT_MODEL=gpt-5.4
```

## Running an Example

Each example includes a `README.md` for reading and a `run.sh` script you can
execute:

```bash
cd examples/quick-start
bash run.sh
```

Or make the script executable and run it directly:

```bash
chmod +x examples/quick-start/run.sh
./examples/quick-start/run.sh
```

## State Directory

All Hermit state is stored under `~/.hermit/` by default. Examples do not
modify this default. You can inspect tasks, sessions, and ledger entries there
after running any example.
