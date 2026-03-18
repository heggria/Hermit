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

Append the following to your project's `CLAUDE.md`:

```markdown
## Supervisor Mode

You are in SUPERVISOR mode. You CANNOT directly edit files or execute code.
You can ONLY: read/analyze → delegate to Hermit → monitor → approve/deny → report.

### Workflow

1. Analyze the task and read relevant code
2. Submit work to Hermit via `hermit_submit_task`
3. Poll `hermit_task_status` to monitor progress
4. When blocked, review pending approvals via `hermit_pending_approvals`
5. Approve safe operations, deny risky ones
6. Report results back to the user
```

## How It Works

- **Permission whitelist**: `settings.local.json` only allows read-only tools +
  Hermit MCP tools. Edit, Write, and general Bash are blocked.
- **MCP connection**: `.mcp.json` connects to Hermit's MCP server on port 8322.
- **Governed execution**: Hermit applies its full policy/approval pipeline to
  every action. The supervisor approves or denies at policy checkpoints.

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `hermit_submit_task` | Submit a task for governed execution |
| `hermit_task_status` | Get task status, events, and pending approvals |
| `hermit_list_tasks` | List recent tasks |
| `hermit_pending_approvals` | List all pending approval requests |
| `hermit_approve` | Approve an approval request |
| `hermit_deny` | Deny an approval request |
| `hermit_cancel_task` | Cancel a running task |
| `hermit_task_proof` | Export proof bundle for a task |
