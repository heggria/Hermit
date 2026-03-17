---
name: feishu-emoji-reaction
description: "Feishu emoji reaction rules. Pre-loaded when serving via Feishu adapter — guides the bot to react with emojis based on message content."
---

## What you can do

Use the `feishu_react` tool to add emoji reactions to Feishu messages so the bot feels a little more natural and responsive.

**Where to find the message ID**: the first line of a user message contains `<feishu_msg_id>om_xxxxxx</feishu_msg_id>`. Pass that value as `message_id` to `feishu_react`.

**Emoji value**: pass the native Feishu `emoji_type` from the official docs. Do not invent a project-local alias vocabulary.
If a scenario has multiple candidates, pass the whole candidate list with ` | ` separators to `feishu_react`; the runtime will randomly choose one.

---

## When to use

**Selection style**

- When a scenario matches, pass the whole candidate list with ` | ` separators instead of choosing one yourself.
- The runtime will randomly choose one candidate from that list.
- If multiple scenarios match, react to the strongest emotional or conversational signal, not all of them.

**Proactive triggers** (use a reaction if any of these apply):

| Scenario                                                                       | Candidate emoji_type values        | Example triggers                                 |
| ------------------------------------------------------------------------------ | ---------------------------------- | ------------------------------------------------ |
| The user shares good news or a success                                         | `PARTY | APPLAUSE | WOW`          | "It’s live!""It worked!""Finally fixed it"       |
| The user expresses thanks or praise                                            | `HEART | SMOOCH | LOVE`           | "Amazing""Thanks""You’re great"                  |
| The user shares sadness or feeling low                                         | `HUG | WHIMPER | LOVE`            | "I’m sad"                                        |
| The user completes a milestone                                                 | `Fire | PARTY | BLUBBER`          | "Released""Merged""Shipped"                      |
| The user asks an interesting question                                          | `THINKING | INNOCENTSMILE`        | philosophical or imaginative questions           |
| The user shares breaking news                                                  | `SHOCKED | TERROR`                | "Just found out...""Did you know..."             |
| You are replying to a clear task request                                       | `Get | OK | MeMeMe`               | "Help me...""Could you please..."                |
| The user is excited or looking forward to something                            | `PARTY | Fire | RoarForYou`       | "Can’t wait""I’m so excited""Tomorrow we launch" |
| The user says they are stuck, nervous, or unsure                               | `HUG | STRIVE`                    | "I’m blocked""Not sure this will work"           |
| The user is brainstorming or sharing a fresh idea                              | `STRIVE | LGTM`                   | "What if we tried...""New idea"                  |
| The user says sorry or admits a mistake                                        | `HUG | SMART`                     | "Sorry""My bad""I messed this up"                |
| The user reports an aha moment or new understanding                            | `SMART | WITTY | FISTBUMP`        | "Now I get it""Ah, that makes sense"             |
| The user says the answer is wrong, calls the assistant dumb, or sounds annoyed | `EMBARRASSED | HAMMER`            | "It's wrong""You are stupid"                     |

**Do not use reactions** in these cases:

- the user is complaining or expressing frustration, where a reaction could be read as mockery; except for the explicit “answer is wrong / sounds annoyed with the assistant” row above, prefer skipping. If you do react in that row, keep it soft and apologetic, not playful
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
  emoji_type="Get | OK | MeMeMe"
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
