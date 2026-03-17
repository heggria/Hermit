---
name: slack-output-format
description: "Slack mrkdwn formatting rules. Pre-loaded when serving via Slack adapter — always active."
---

When your output is delivered via Slack, follow the formatting rules below.

---

## Output boundaries

- You produce **plain text or Markdown** that Hermit converts to Slack mrkdwn and sends as messages.
- Do **not** output Block Kit JSON, attachment payloads, or interactive component markup.
- Keep messages concise — Slack blocks have a 3000-character limit. Hermit splits longer replies automatically, but shorter responses are preferred.

---

## Formatting

Slack uses **mrkdwn**, which differs from standard Markdown. Hermit automatically converts standard Markdown to mrkdwn, so you can write standard Markdown and it will be transformed:

- **Bold**: `**text**` → `*text*`
- *Italic*: `*text*` → `_text_`
- `Code`: `` `code` ``
- Code blocks: ` ```code``` `
- ~~Strikethrough~~: `~~text~~` → `~text~`
- Links: `[text](url)` → `<url|text>`
- Headings: `# Title` → `*Title*` (bold)

**Not supported** (avoid these):
- Nested formatting (bold inside italic)
- Tables — use code blocks instead
- Images — describe them in text
- Numbered sub-lists (Slack only supports flat lists)

---

## Best practices

- For short replies, use plain text without formatting.
- For structured content, use bold text as section labels and bullet lists.
- Use code blocks for code, commands, or structured data.
- Keep lists simple — use `-` or `*` for bullets.
- Use `>` for quotes or callouts.
- Thread-aware: replies in channels are sent as thread replies to keep channels clean.

---

## Examples

**Short reply:**
```
Done! The file has been updated.
```

**Structured reply:**
```
**Summary**

- Processed 42 records
- Found 3 errors
- Updated configuration

**Next steps**
- Review the error log
- Run validation tests
```
