# Scheduled Tasks

Run tasks on a cron schedule. The scheduler plugin fires at configured
intervals, creates kernel tasks from prompts, and records the results in the
ledger like any other governed execution.

## How It Works

```
Cron trigger fires
  → Scheduler plugin creates a task from the schedule's prompt
    → Task enters the governed pipeline (policy, execution, receipts)
      → Result is dispatched via the DISPATCH_RESULT hook event
```

Scheduled tasks are regular kernel tasks. They go through the same policy
evaluation, produce the same receipts and proofs, and appear in
`hermit task list` alongside manually triggered tasks.

## Setup

### 1. Configure a Schedule

Schedules are stored in `~/.hermit/schedules/`. Create or copy a schedule
config:

```bash
mkdir -p ~/.hermit/schedules
cp schedule.example.json ~/.hermit/schedules/daily-summary.json
```

Edit the file to set your desired cron expression and prompt.

### 2. Schedule Config Format

```json
{
  "name": "daily-summary",
  "cron": "0 9 * * *",
  "prompt": "Summarize yesterday's activity and create a task record",
  "enabled": true
}
```

| Field | Description |
|-------|-------------|
| `name` | Unique identifier for the schedule |
| `cron` | Standard cron expression (minute, hour, day, month, weekday) |
| `prompt` | The prompt sent to the agent when the schedule fires |
| `enabled` | Set to `false` to disable without deleting |

### 3. Start the Service

The scheduler runs as part of `hermit serve`:

```bash
hermit serve --adapter feishu
```

Or via the environment controller:

```bash
scripts/hermit-envctl.sh prod up
```

The scheduler plugin activates on `SERVE_START` and begins evaluating cron
expressions.

### 4. Manage Schedules via CLI

```bash
# List configured schedules
hermit schedule list

# Enable or disable a schedule
hermit schedule enable daily-summary
hermit schedule disable daily-summary
```

## Inspecting Results

When a schedule fires, a task is created and executed. Inspect it the same way
as any other task:

```bash
# List recent tasks — scheduled tasks appear here
hermit task list

# Show full execution trace
hermit task show <task-id>

# Verify proof chain
hermit task proof <task-id>
```

## Common Cron Expressions

| Expression | Meaning |
|------------|---------|
| `0 9 * * *` | Every day at 09:00 |
| `0 9 * * 1-5` | Weekdays at 09:00 |
| `*/30 * * * *` | Every 30 minutes |
| `0 0 1 * *` | First day of every month at midnight |
| `0 18 * * 5` | Every Friday at 18:00 |

## Hook Event

When a scheduled task completes, the `DISPATCH_RESULT` hook event is emitted.
Other plugins (e.g., the webhook plugin) can listen for this event to forward
results to external systems.
