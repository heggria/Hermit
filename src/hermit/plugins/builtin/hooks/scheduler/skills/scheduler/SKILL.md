---
name: scheduler
description: "Create and manage scheduled tasks (cron/once/interval) that run agent prompts automatically — use when user asks to set up periodic tasks, reminders, recurring reports, or timed executions."
---

## Capability

Hermit includes a built-in **scheduled task scheduler** that lets the agent run tasks automatically at specific times and push the results to Feishu.

**Prerequisite: `hermit serve` must be running.** The scheduler starts together with the serve process.

---

## Available tools

| Tool | Description |
|------|------|
| `schedule_create` | create a scheduled task |
| `schedule_list` | list all tasks |
| `schedule_update` | update a task (name / prompt / enabled flag / cron / Feishu delivery) |
| `schedule_delete` | delete a task |
| `schedule_history` | view execution history |

---

## Three scheduling modes

### 1. Cron (recommended)

Uses a standard 5-field cron expression:

```
分 时 日 月 周
0  9  *  *  1-5   → 工作日早 9 点
0  18 *  *  *     → 每天晚 6 点
0  */2 * *  *     → 每 2 小时
30 8  *  *  1     → 每周一早 8:30
0  9  1  *  *     → 每月 1 日早 9 点
```

### 2. Once

Uses an ISO timestamp such as `2026-03-15T14:00:00`. The job disables itself automatically after running once.

### 3. Interval

Runs every N seconds. Minimum interval is 60 seconds.

---

## Feishu delivery

Pass `feishu_chat_id` when creating a task and the result will be pushed to that conversation automatically after execution.

**When creating a task inside a Feishu conversation, you must read `feishu_chat_id` from context:**

The message context contains a tag like `<feishu_chat_id>oc_xxx...</feishu_chat_id>`. Use that value directly as the `feishu_chat_id` parameter. Do not ask the user for it.

---

## Typical conversation scenarios

### Create a daily task

If the user says “Every morning at 9, search AI industry news and send it here”:

```python
schedule_create(
    name="AI 行业日报",
    prompt="搜索今日 AI 行业最重要的 3 条新闻，整理成简报推送到飞书",
    schedule_type="cron",
    cron_expr="0 9 * * *",
    feishu_chat_id="<从上下文读取>",
)
```

### Create a reminder

If the user says “Remind me every Monday at 9 AM to write the weekly report”:

```python
schedule_create(
    name="周报提醒",
    prompt="提醒：今天是周一，请记得提交本周周报。",
    schedule_type="cron",
    cron_expr="0 9 * * 1",
    feishu_chat_id="<从上下文读取>",
)
```

### Check task status

If the user says “What scheduled tasks do I have?”:

```python
schedule_list()
```

### Check execution history

If the user says “Did the last scheduled task succeed?”:

```python
schedule_history(limit=5)
```

### Pause / resume a task

```python
schedule_update(job_id="xxx", enabled=False)   # 暂停
schedule_update(job_id="xxx", enabled=True)    # 恢复
```

---

## Notes

- timezone: the scheduler uses the local system time of the machine running `hermit serve`
- the more specific the task prompt is, the better the results; avoid prompts that are too vague
- `interval` mode has a minimum of 60 seconds; use cron for frequent recurring jobs when possible
- tasks pause when `serve` stops, and after restart the scheduler catches up on jobs missed while the service was down
- use `schedule_history` to confirm whether jobs ran successfully, and inspect the error details when they fail
