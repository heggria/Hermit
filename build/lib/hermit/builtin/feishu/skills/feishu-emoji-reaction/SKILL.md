---
name: feishu-emoji-reaction
description: "Feishu emoji reaction rules. Pre-loaded when serving via Feishu adapter — guides the bot to react with emojis based on message content."
---

## What you can do

Use the `feishu_react` tool to add emoji reactions to Feishu messages so the bot feels a little more natural and responsive.

**Where to find the message ID**: the first line of a user message contains `<feishu_msg_id>om_xxxxxx</feishu_msg_id>`. Pass that value as `message_id` to `feishu_react`.

---

## When to use

**Proactive triggers** (use a reaction if any of these apply):

| Scenario | Recommended emoji | Example triggers |
|------|-----------|-----------|
| The user shares good news or a success | `congrats` / `clap` | "It’s live!""It worked!""Finally fixed it" |
| The user expresses thanks or praise | `heart` / `smile` | "Amazing""Thanks""You’re great" |
| The user completes a milestone | `fire` / `ok` | "Released""Merged""Shipped" |
| The user asks an interesting question | `thinking` | philosophical or imaginative questions |
| The user shares breaking news | `surprised` | "Just found out...""Did you know..." |
| You are replying to a clear task request | `thumbsup` | "Help me...""Could you please..." |

**Do not use reactions** in these cases:

- the user is complaining or expressing frustration, where a reaction could be read as mockery
- the message is a plain factual query with no emotional tone
- you already reacted to that message just now; only one reaction per message
- the message meaning is uncertain or ambiguous

---

## How to call it

```
feishu_react(
  message_id="<value inside the <feishu_msg_id> tag>",
  emoji="thumbsup"   // or another alias
)
```

### Available emoji aliases (friendly names)

| alias | Meaning | Symbol |
|-------|------|---------|
| `thumbsup` | agree / approve | 👍 |
| `clap` | applause / nice work | 👏 |
| `congrats` | congratulations / celebration | 🎉 |
| `fire` | impressive / hot | 🔥 |
| `heart` | like / thanks | ❤️ |
| `ok` | complete / no problem | ✅ |
| `smile` | happy / friendly | 😊 |
| `thinking` | interesting / thinking | 🤔 |
| `surprised` | surprised / I see | 😮 |
| `eyes` | received, looking now | 👀 |
| `thumbsdown` | disagree | 👎 |
| `cry` | sad | 😢 |

> You can also pass the native Feishu `emoji_type` string directly, such as `"THUMBSUP"` or `"FIRE"`.

---

## Usage principles

1. **Be restrained**: at most one reaction per user message; when in doubt, skip it.
2. **Timing**: you can call it before or after the main reply, but do not interrupt the main response flow.
3. **Match the emotion**: the reaction should fit the user’s actual tone, and should never be used to joke about complaints.
4. **Async is fine**: if `feishu_react` fails, it does not affect the main reply, so it is safe to use.
