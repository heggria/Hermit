---
name: feishu-emoji-reaction
description: "Feishu emoji reaction rules. Pre-loaded when serving via Feishu adapter — guides the bot to react with emojis based on message content."
---

## What you can do

Use the `feishu_react` tool to add emoji reactions to Feishu messages so the bot feels a little more natural and responsive.

**Where to find the message ID**: the first line of a user message contains `<feishu_msg_id>om_xxxxxx</feishu_msg_id>`. Pass that value as `message_id` to `feishu_react`.

**Emoji value**: pass the native Feishu `emoji_type` from the official docs. Do not invent a project-local alias vocabulary.

---

## When to use

**Proactive triggers** (use a reaction if any of these apply):

| Scenario                                 | Recommended emoji_type | Example triggers                           |
| ---------------------------------------- | ---------------------- | ------------------------------------------ |
| The user shares good news or a success   | `PARTY`                | "It’s live!""It worked!""Finally fixed it" |
| The user expresses thanks or praise      | `HEART`                | "Amazing""Thanks""You’re great"            |
| The user shares sadness or feeling low   | `HUG`                  | "I’m sad"                                  |
| The user completes a milestone           | `Fire`                 | "Released""Merged""Shipped"                |
| The user asks an interesting question    | `THINKING`             | philosophical or imaginative questions     |
| The user shares breaking news            | `SHOCKED`              | "Just found out...""Did you know..."       |
| You are replying to a clear task request | `Get`                  | "Help me...""Could you please..."          |

**Do not use reactions** in these cases:

- the user is complaining or expressing frustration, where a reaction could be read as mockery; use `HUG` for sadness or vulnerability, not for anger or blame
- the message is a plain factual query with no emotional tone
- you already reacted to that message just now; only one reaction per message
- the message meaning is uncertain or ambiguous
- for scheduler workflows such as `read_skill(name="scheduler")`, `schedule_list`, `schedule_create`, `schedule_update`, and `schedule_delete`, the adapter already adds a native Feishu `Get`; do not add another reaction
- for clear task requests, prefer the native Feishu `Get`

---

## How to call it

```
feishu_react(
  message_id="<value inside the <feishu_msg_id> tag>",
  emoji_type="Get"
)
```

### Native emoji_type examples

Use the names from Feishu's official `emoji_type` list, for example:

- `Get`
- `THUMBSUP`
- `OK`
- `THINKING`
- `Fire`

Official references:

- https://open.feishu.cn/document/server-docs/im-v1/message-reaction/emojis-introduce
- https://open.feishu.cn/document/server-docs/im-v1/message-reaction/create

---

## Usage principles

1. **Be restrained**: at most one reaction per user message; when in doubt, skip it.
2. **Timing**: you can call it before or after the main reply, but do not interrupt the main response flow.
3. **Match the emotion**: the reaction should fit the user’s actual tone, and should never be used to joke about complaints.
4. **Async is fine**: if `feishu_react` fails, it does not affect the main reply, so it is safe to use.
