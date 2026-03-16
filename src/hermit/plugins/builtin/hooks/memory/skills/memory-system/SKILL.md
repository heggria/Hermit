---
name: memory-system
description: Explains how Hermit's cross-session memory works — automatic extraction, checkpointing, scoring, consolidation, retrieval injection, and how to debug it. Read this when the user asks about memory, asks why something wasn't remembered, or when deciding whether to manually write to memories.md.
---

## How Memory Works

Hermit memory is **automatic, score-based, and now split into two phases**:

1. **Checkpoint phase (`POST_RUN`)**: after a normal turn, if the delta messages look memory-worthy, Hermit extracts only the new facts and appends them early.
2. **Settlement phase (`SESSION_END`)**: when the session is closed, Hermit performs the full score update, decay, supersede handling, and consolidation pass.

You still do **not** need to manually write to `memories.md` for normal usage.

### The Automatic Pipeline

```
Each agent turn finishes
  └── POST_RUN hook fires
        └── _checkpoint_memories()
              ├── Reads only unprocessed messages for this session
              ├── Checks trigger conditions
              ├── Extracts only new memories
              └── append_entries() → early checkpoint

Session ends (close_session called)
  └── SESSION_END hook fires
        └── _save_memories()
              ├── Formats the full transcript (up to 16 000 chars)
              ├── Calls extraction model
              ├── Receives JSON: { new_memories, used_keywords }
              ├── record_session()
              │     ├── reference boost / decay
              │     ├── lock / delete
              │     ├── supersede old versions
              │     └── consolidate similar memories
              └── saves to memories.md
```

### When Checkpoint Happens

Checkpoint does **not** run on every turn. It only runs when the new delta looks important enough, for example:

- Explicit memory intent: “记住…”, “以后都…”, “统一使用…”
- Decision / convention changes: port, path, branch, deployment, etc.
- A long enough batch of conversation with at least 2 meaningful user messages
- A message-count batch threshold

Checkpoint is skipped when:

- `session_id` is missing
- `cli-oneshot`
- no new pending messages
- `has_auth == False`
- the delta is below threshold

### When SESSION_END Fires (full settlement)

| Scenario | Saves? |
|---|---|
| `hermit run "..."` completes normally | ✅ |
| `hermit chat` exits via `/quit` or Ctrl+C at the input prompt | ✅ |
| `hermit chat` `/new` resets the session | ✅ |
| `hermit serve --adapter feishu` — session idle past timeout | ✅ (swept every 5 min) |
| `hermit serve --adapter feishu` — adapter stops (Ctrl+C) | ✅ (flush on stop) |
| `webhook` request finishes | ✅ |
| Process killed with SIGKILL | ❌ no graceful close |

### The Score System

Every memory entry has a score (0–10):

| Event | Score change |
|---|---|
| New entry | starts at **5** |
| Keyword from this session matches the entry | **+1** (max 10) |
| Not referenced this session | **−1** (min 0) |
| Score reaches **7** | **locked** 🔒 — never decayed or deleted |
| Score reaches **0** | **deleted** on next save |
| Category "项目约定" | slow decay: −1 every **2 sessions** instead of every session |

### Supersede And Consolidation

Hermit does more than append lines now:

- If a new memory clearly overrides an older one (for example port/path/default changes), the old entry is marked obsolete and removed on save.
- Similar memories in the same category are consolidated during settlement to reduce bloat.
- Each entry can carry metadata in an HTML comment: `updated_at`, `confidence`, `supersedes`.

### What's Injected Into Context

There are now **two** injection paths:

1. `<memory_context>` in `SYSTEM_PROMPT`
   - static background memory
   - up to **3 entries per category**
   - reflects the file at system prompt build time

2. `<relevant_memory>` in `PRE_RUN`
   - dynamically retrieved per user prompt
   - top relevant memories only
   - bounded by a character budget

This means the startup context can still be stale, but each turn can inject fresher task-relevant memory.

---

## How to Interact with Memory

### ✅ Trust the automatic pipeline

For most conversations, do nothing special. The extraction flow will checkpoint important deltas and settle the memory at session end.

### ✅ Use `write_hermit_file` for immediate, urgent facts

When the user says "remember this permanently" or you learn something critical mid-session that you want to guarantee survives (e.g. an important rule), write it directly:

```
write_hermit_file(path="memory/memories.md", ...)
```

Read the current file first (`read_hermit_file`), then append the new entry in the correct format:

```
- [YYYY-MM-DD] [s:8🔒] Your memory content here.
```

Use score **8🔒** for facts the user explicitly asked to lock in. Use **5** for ordinary new entries.

Do **not** store secrets in memory.

### ❌ Don't rewrite the entire file

Never overwrite the whole `memories.md`. Always append or edit individual lines. Rewriting destroys scores and locked entries.

### ✅ Read memories.md when asked about memory state

When the user asks "what do you remember about X?" or "why didn't you remember Y?", use `read_hermit_file(path="memory/memories.md")` to inspect the current file directly — the startup `<memory_context>` may be stale if many sessions have passed.

---

## Why the Agent May Not Remember Something

1. **Score decayed to 0** — the entry wasn't referenced in enough sessions
2. **Session ended abnormally** — process killed with SIGKILL, no SESSION_END fired
3. **`has_auth` was False** — extraction LLM call skipped silently
4. **Extraction LLM missed it** — the entry wasn't prominent enough in the transcript
5. **Startup context is stale** — the static `<memory_context>` reflects an older snapshot; read the file directly for the current state
6. **Checkpoint was skipped** — no auth / no pending delta / below threshold / oneshot session
7. **A newer memory superseded the older one** — the old fact was intentionally replaced

---

## The memories.md Format

```markdown
## 用户偏好

- [2026-03-09] [s:8🔒] 用户偏好中文回答。

## 技术决策

- [2026-03-09] [s:5] pydantic-settings 使用 model_config 加载 .env 文件。

## 项目约定

- [2026-03-09] [s:6] feat/expert-app 分支负责 H5 侧 Expert JSBridge 集成。
- [2026-03-11] [s:5] 默认工作目录固定到 /repo <!--memory:{"updated_at":"2026-03-11","confidence":0.8,"supersedes":["默认工作目录固定到 /old"]}-->
```

Categories (in order): 用户偏好 / 项目约定 / 技术决策 / 环境与工具 / 其他 / 进行中的任务

## Logging And Debugging

Important log events include:

- `memory_injected`
- `memory_retrieval_ranked`
- `memory_retrieval_injected`
- `memory_save_skipped`
- `memory_checkpoint_skipped`
- `memory_checkpoint_saved`
- `memory_extraction_started`
- `memory_extraction_empty`
- `memory_extraction_result`
- `memory_superseded`
- `memory_consolidated`
- `memories_saved`

If a user says memory is not updating, check:

1. `~/.hermit/memory/memories.md`
2. `~/.hermit/memory/session_state.json`
3. Hermit logs for the events above
