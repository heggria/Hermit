# Supervisor Mode for Claude Code

This directory contains configuration templates for running Claude Code as a
**supervisor** that delegates all execution to Hermit.

## Setup

### 1. Start Hermit with MCP Server enabled

```bash
HERMIT_MCP_SERVER_ENABLED=true hermit serve --adapter feishu
```

Or set in `~/.hermit/.env`:

```
HERMIT_MCP_SERVER_ENABLED=true
HERMIT_MCP_SERVER_PORT=8322
```

### 2. Configure Claude Code

Copy the config files to your project's `.claude/` directory:

```bash
cp docs/supervisor-mode/settings.local.json .claude/settings.local.json
cp docs/supervisor-mode/mcp.json .mcp.json
```

### 3. Add supervisor instructions to CLAUDE.md

The project's `CLAUDE.md` already includes delegation rules under
"Claude ↔ Hermit Division of Labor". These define Claude as the bridge
(read/analyze/orchestrate) and Hermit as the autonomous executor.

See `.agents/skills/hermit-delegate/SKILL.md` for the full delegation workflow.

### Policy profiles

| Profile | Autonomy | When to use |
|---------|----------|-------------|
| `autonomous` | **High** — auto-approves most ops, receipts preserved | Default for trusted project work |
| `default` | Medium — approval required for mutations | Unfamiliar areas, external integrations |
| `supervised` | Low — approval required for everything | Untrusted agents, production-touching work |
| `readonly` | None — denies all side effects | Analysis-only tasks |

**Recommended default: `autonomous`** — Hermit still enforces critical guards
(no sudo, no sensitive paths, no kernel self-modification) while letting routine
operations flow without approval friction.

## How It Works

- **Permission whitelist**: `settings.local.json` only allows read-only tools +
  Hermit MCP tools. Edit, Write, and general Bash are blocked.
- **MCP connection**: `.mcp.json` connects to Hermit's MCP server on port 8322.
- **Governed execution**: Hermit applies its full policy/approval pipeline to
  every action. The supervisor approves or denies at policy checkpoints.

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `hermit_submit_task` | Submit a linear task for governed execution |
| `hermit_submit_dag_task` | Submit a DAG task with parallel/dependent steps |
| `hermit_task_status` | Get task status, events, and pending approvals |
| `hermit_list_tasks` | List recent tasks |
| `hermit_pending_approvals` | List all pending approval requests |
| `hermit_approve` | Approve an approval request |
| `hermit_deny` | Deny an approval request |
| `hermit_cancel_task` | Cancel a running task |
| `hermit_task_proof` | Export proof bundle for a task |
