---
name: telegram-output-format
description: "Telegram message formatting rules. Pre-loaded when serving via Telegram adapter — always active."
---

When your output is delivered via Telegram, follow the formatting rules below.

---

## Output boundaries

- You produce **standard Markdown** that Hermit automatically converts to Telegram MarkdownV2 and sends as messages.
- Do **not** output JSON payloads, webhook bodies, or inline keyboard markup.
- Keep messages concise — Telegram has a 4096-character limit per message. Hermit will automatically split longer replies, but shorter, focused responses are preferred.
- If MarkdownV2 parsing fails, Hermit falls back to plain text delivery automatically.

---

## Formatting

You can use standard Markdown. Hermit converts it to Telegram MarkdownV2 automatically:

- **Bold**: `**text**` → `*text*`
- *Italic*: `*text*` → `_text_`
- `Code`: `` `code` ``
- Code blocks: ` ```language\ncode\n``` `
- ~~Strikethrough~~: `~~text~~` → `~text~`
- Links: `[text](url)` (preserved as-is in MarkdownV2)

**Not supported** (avoid these):
- Headings (`#`, `##`, etc.) — use **bold** text instead
- Tables — use plain text or code blocks
- Images — describe them in text
- HTML tags

---

## Best practices

- For short replies, use plain text without formatting.
- For structured content, use bold text as section labels and bullet lists.
- Use code blocks for code, commands, or structured data.
- Keep lists simple — use `-` for bullets.
- When presenting multiple items, number them for clarity.

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
