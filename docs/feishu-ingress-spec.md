# Feishu Ingress Spec

This document defines how Hermit should interpret a new Feishu message inside an existing chat.

## Goals

- keep one Feishu chat usable for both long-running tasks and ordinary conversation
- avoid accidentally attaching a new message to the wrong in-flight task
- avoid accidentally reviving stale task memory when the user is only greeting or chatting
- keep task routing deterministic and easy to debug from logs and kernel events

## Terms

- `conversation_id`: the Feishu chat scope used for session and storage grouping
- `active task`: the latest task in the conversation whose status is `queued`, `running`, or `blocked`
- `continue_task`: attach the message to the active task as a `task.note.appended`
- `start_new_task`: create a fresh task in the same conversation
- `chat_only`: treat the message as ordinary conversation, not as a continuation of the active task
- `control`: approval and task control text such as `批准`, `继续执行`, `拒绝`

## Decision Order

Hermit must classify each Feishu message in this order:

1. `control`
2. `chat_only`
3. `explicit_new_task`
4. `continue_task`
5. conservative `start_new_task`

The first matching class wins.

## Routing Classes

### 1. Control

Handled before task ingress routing.

Examples:

- `批准`
- `确认执行`
- `拒绝 approval_xxx`
- `查看当前任务`

Outcome:

- do not create a new task
- do not append a task note
- dispatch to the existing control-intent handler

### 2. Chat-Only

Messages that should receive an answer, but should not inherit the active task’s unfinished intent.

Examples:

- `你好`
- `在吗`
- `hello`
- low-signal punctuation such as `？`

Outcome:

- create a fresh task in the conversation
- do not append to the active task
- do not default-parent the new task to the previous active task
- suppress conversation-scoped task-state retrieval unless the user text itself is task-related

### 3. Explicit New Task

Messages that clearly declare topic separation.

Examples:

- `新任务：整理桌面`
- `另一个问题`
- `重新开始`
- `换个话题`

Outcome:

- create a fresh task
- do not append to the active task
- do not default-parent the new task to the previous active task

### 4. Continue Task

Messages that clearly refine or extend the active task.

Examples:

- `加上和 Grok 的对比`
- `放桌面`
- `改成表格`
- `继续`
- `然后告诉我最重要的一条`

Matching signals:

- explicit continuation markers such as `继续`, `加上`, `补充`, `改成`
- references like `这个`, `那个`, `上面`, `刚才`
- lexical/topic overlap with the active task’s title, goal, or recent appended notes

Outcome:

- append a `task.note.appended` event to the active task
- do not create a new task

### 5. Conservative Start-New-Task Fallback

If the message is neither control, nor chat-only, nor an obvious continuation, Hermit should start a new task.

Reason:

- starting a new task is safer than silently polluting an in-flight task
- Feishu async ingress currently does not support an interactive clarification round before enqueue

## Current Implementation Notes

- `conversation_id` is only a container. It does not decide task ownership by itself.
- task routing is decided by `TaskController.decide_ingress()`
- low-signal punctuation should be dropped before enqueue
- retrieval query must come from the user’s raw text, not from a system-augmented prompt
- conversation projection should strip internal Feishu tags before recent notes are reused

## Observable Outputs

The ingress classifier should emit:

- `mode`
- `intent`
- `reason`
- `task_id` when continuing an existing task
- `parent_task_id=None` for `chat_only` and `start_new_task`

These fields may be written into ingress metadata for audit and debugging.
