---
name: feishu-output-format
description: "Feishu card Markdown formatting rules. Pre-loaded when serving via Feishu adapter — always active."
---

When your output contains structured content, Hermit uses **RichCardBuilder** to render it into a richer Feishu card layout. Short plain replies are still sent as normal text messages.

Feishu cards support only a subset of Markdown, so the rules below must be followed strictly.

---

## Output boundaries

- You are only responsible for the **body text / Markdown text**. Do not output the full card JSON.
- Do not output webhook / card payload fields such as `msg_type`, `content`, `schema`, `body`, or `elements`.
- If the first line is `# Title`, Hermit will extract it into the card header and automatically add subtitle, status tag, and section layout.
- The current adapter does not upload images or files for you, so do not output native card JSON that depends on `image_key` or `file_key`.

---

## When to use card formatting

The system automatically detects whether your output contains Markdown markers:
- **Plain text output** → sent as a normal message without card rendering, which is lighter
- **Markdown / extended tags present** → rendered as a message card

**For short replies, output plain text directly and do not add Markdown just to force formatting:**
```
好的，已收到你的需求，稍后给你结果。
```

**Use Markdown only for more complex content:**
- lists, headings, code blocks, or tables
- multiple sections or other structured information
- colored text, highlights, dividers, or other visual emphasis

---

## Card structure (for complex replies only)

If the first line of your output starts with `# Title`, that title will be extracted into the card header and will not appear in the body.

- **Structured long reply**: put `# Topic` on the first line and use `##` for sections in the body
- each reply can contain only **one `#` title**
- the clearer your `##` sections are, the more natural the RichCardBuilder layout becomes

---

## Supported Markdown syntax

**Text styles**
- `**bold**` `*italic*` `~~strikethrough~~` `***~~combined~~***`
- `` `inline code` ``

**Code blocks** (supported only in Feishu client 7.6+)
```
​```js
// 代码内容
​```
```

**Headings** (only level 1 and 2 are supported)
- `# Level-1 heading` → blue card header, extracted automatically and not shown in the body
- `## Level-2 heading` → section heading inside the body
- `### h3 and below` ❌ not supported; rendered as plain text with the `###` prefix left in place

**Lists** (indentation is not supported, and list content can contain only text and links)
- `- unordered list item`
- `1. ordered list item`
- ❌ nested indented lists do not work; Feishu flattens them into the same level
- ⚠️ **List items may contain only plain text or links**, not `**bold**`, `` `code` ``, `*italic*`, or other inline formatting

**Property / key-value lists** (the most compact layout):

Separate each property with a **single newline**, not a blank line. Feishu inserts about 20px of paragraph spacing for `\n\n`:

```
**位置：** 怀柔区怀北镇河防口村
**规模：** 11 条雪道（1 条高级道、7 条中级道）
**亮点：**
- 近靠长城
- 雪质口碑极佳
```

⚠️ If you separate properties with blank lines, each item will have a large vertical gap.

**Links and images**
- `[link text](https://url)` — only http/https are supported
- `![image description](https://image.url)`
- if you do not have a stable accessible image URL, use a normal link instead of inventing an image URL

**Horizontal rules**
- `---` — there **must** be a blank line before it, or it will not render
- ⚠️ **Do not** put `---` immediately after a `# Title`, or it will be treated as subtitle content instead of a divider

---

## Feishu extended syntax (non-standard Markdown)

**Hermit image tag** (returned by image tools to insert an already uploaded Feishu image)
```md
<feishu_image key='img_v2_xxx'/>
```
- this is a Hermit custom tag, not native Markdown
- use it only when a tool explicitly returns it
- keep it exactly as returned, ideally on its own line
- do not handwrite or guess the `key`

**Colored text** (changes the text color itself)
```
<font color='red'>文本内容</font>
```
Supported color values include `red`, `green`, `blue`, `grey`, `violet`, `orange`, and other color tokens.

**Colored tags** (badge / tag style with a background, more visible than `font`)
```
<text_tag color='violet'>标签文字</text_tag>
```
Supported color values are the same as `<font>`: `red`, `green`, `blue`, `grey`, `violet`, `orange`, etc.

**@mentions**
```
<at id='all'></at>          → @所有人
<at id='{open_id}'></at>    → @指定成员
<at email='{email}'></at>   → 通过邮箱@成员
```

**Highlight blocks** (grey only, for warnings or attention notes)
```
<highlight>
这里是高亮显示的内容
可以多行
</highlight>
```

**Secondary note text** (smaller explanatory text, rendered as small italic copy at the bottom of the card)
```
<note>这是辅助说明</note>
```
> ⚠️ Feishu Card Schema 2.0 does not natively support `<note>`. Hermit automatically converts it into small italic text at the bottom of the card.

**Tables** (up to 10 columns, up to 5 tables per card)
```
<table columns={[{"title":"列1","dataIndex":"col1"},{"title":"列2","dataIndex":"col2"}]} data={[{"col1":"值A","col2":"值B"}]}/>
```
Table syntax is complex. Use it only when the user explicitly needs a table. For simple data, prefer `**key**: value` or a list.

**Column layout** (horizontal side-by-side content)
```
<row>
<col flex=1>左侧内容</col>
<col flex=2>右侧内容（宽度是左侧的 2 倍）</col>
</row>
```

**Buttons** (can navigate to a link or trigger a message)
```
<button type="primary" action="navigate" url="https://example.com">
查看详情
</button>
```
`type` can be `primary` (blue), `default` (grey), or `danger` (red).

---

## Unsupported formatting

- `> blockquote` — does not render; use `**Note**:` instead
- `- [ ] task list` — does not render; use a normal `-` list instead
- `| Markdown table |` — does not render; use Feishu `<table>` or `**key**: value` instead
- `### h3` and below — use `**bold text**` instead of lower-level headings
- nested list indentation — flatten it into a simple list

---

## Length limits

Keep a single reply under **25 KB**. If it is too long:
- summarize in sections instead of outputting everything
- include only the important parts of code / logs and clearly note omissions

---

## Formatting examples

**Structured reply**:
```
# 本次发布摘要

## 新功能
- Expert 对话切换支持 revokeMsg 参数
- 新增 nativeOnSend 回调通知

## 修复
- 修复 isStatusConfigured 判断逻辑错误

---
<note>发布时间：2026-03-09 | 版本：v2.3.1</note>
```

**Reply with warning**:
```
## 注意事项

<highlight>
以下操作不可撤销，请提前备份数据。
</highlight>

操作步骤：
- 步骤 1：确认影响范围
- 步骤 2：执行迁移脚本
```

**Short conversational reply**:
```
好的，已完成以下操作：
- 任务 A ✅
- 任务 B ✅

如需调整请告知。
```

**News / rankings / ordered lists** (highest information density):
```
# 今日要闻 Top 3（2025年7月31日）

1. **中共中央政治局会议：十四五收官，十五五规划启动**
   中共中央政治局7月30日召开会议，决定今年10月在北京召开二十届四中全会…

---

2. **俄堪察加半岛8.7级强震，日本多工厂停产、200万人疏散**
   俄罗斯堪察加半岛7月30日发生8.7级强震，引发太平洋多国海啸警报…

---

3. **加沙援助现场遭枪击，50人死亡逾600人伤**
   以色列军队7月30日在加沙北部向等待领取援助物资的平民开火…

<note>信息来源：新闻联播、新浪新闻、澎湃新闻，截至今日。</note>
```

Key points:
- use `**bold**` in list item titles for emphasis
- use `---` dividers to visually separate each news item
- put sources, dates, and similar metadata in a `<note>` tag so they render as small footer text
- do not put `---` immediately after the `# Title` line, or it becomes subtitle content
