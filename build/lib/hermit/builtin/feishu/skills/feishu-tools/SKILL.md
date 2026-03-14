# Feishu Tools Skill

Use this skill when the user asks to interact with Feishu (飞书) resources beyond messaging —
such as creating documents, reading spreadsheets, adding wiki pages, querying Bitable records,
or creating calendar events.

When a task targets Feishu resources, prefer these native Feishu tools over `computer_use`.
Use `computer_use` only when the user explicitly asks to drive the Feishu desktop UI, or when the action is genuinely unavailable through the exposed Feishu APIs.

Messaging guidance:
- replying in the current Feishu thread is usually handled by the adapter's normal reply flow
- proactively sending a message to a known chat or user should use `feishu_send_message`
- if the task is "join a group from an invite/UI flow", there may be no native tool for that step, so UI automation can be a last resort

## Activation Triggers

- "帮我创建一个飞书文档" / "写一个飞书文档"
- "读取这个飞书文档的内容" + URL
- "查一下 Bitable / 多维表格 里的数据"
- "往电子表格写入数据"
- "在知识库里新建一个页面"
- "创建一个日历日程"
- "主动发一条消息到某个群"

## Available Tools

### Document (docx) — scope: `docx:document`

**`feishu_doc_create`** — Create a new Feishu document.
```
feishu_doc_create(title="文档标题", folder_token="")
→ {document_id, url}
```
- `folder_token` is optional. Get it from the Drive folder URL.
- After creating, call `feishu_doc_append` to populate content.

**`feishu_doc_read`** — Read plain text content from a document.
```
feishu_doc_read(document_id="doxcnXXX")
→ {content: "full document text"}
```
- Extract `document_id` from URL: `https://xxx.feishu.cn/docx/<document_id>`

**`feishu_doc_append`** — Append text as new paragraph blocks to a document.
```
feishu_doc_append(document_id="doxcnXXX", content="多行内容\n每行一个段落")
→ {blocks_added: 3}
```
- Each newline in `content` creates a separate paragraph block.
- Typical workflow: `feishu_doc_create` → `feishu_doc_append`

---

### Wiki (知识库) — scope: `wiki:wiki`

**`feishu_wiki_list`** — List wiki spaces or nodes.
```
# List all spaces:
feishu_wiki_list()
→ {spaces: [{space_id, name}, ...]}

# List nodes in a space:
feishu_wiki_list(space_id="7123456")
→ {nodes: [{node_token, title, obj_type, parent_node_token}, ...]}
```
- Always call this first to discover `space_id` and `parent_node_token`.

**`feishu_wiki_create`** — Create a new page in a wiki space.
```
feishu_wiki_create(
    space_id="7123456",
    title="新页面标题",
    parent_node_token="",   # optional, empty = space root
    obj_type="docx"         # docx | doc | sheet | bitable
)
→ {node_token, obj_token, url}
```
- After creating, use `feishu_doc_append(document_id=obj_token, ...)` to add content.

---

### Messaging — scope: `im:message`

**`feishu_send_message`** — Proactively send a text message to a chat or user.
```
feishu_send_message(
    receive_id="oc_xxx",          # chat_id
    receive_id_type="chat_id",    # chat_id | open_id | user_id | email
    text="通知内容"
)
→ {message_id}
```
- For formatted/rich content, prefer the reply mechanism (auto-handled by the bot).
- Use this for proactive notifications to teams or individuals.
- Prefer this over `computer_use` whenever you already know the target `chat_id`, `open_id`, `user_id`, `union_id`, or email.

---

### Bitable (多维表格) — scope: `bitable:app`

**`feishu_bitable_query`** — Query records from a Bitable table.
```
feishu_bitable_query(
    app_token="OXXXbXXXXXXXXX",  # from URL: /base/<app_token>
    table_id="tblXXX",
    filter_field="状态",          # optional
    filter_value="进行中",        # optional
    page_size=20
)
→ {records: [{record_id, fields: {列名: 值}}], has_more}
```
- `app_token` is in the Bitable URL: `https://xxx.feishu.cn/base/<app_token>`
- `table_id` starts with `tbl`. Find it in the Bitable UI or from the URL.

**`feishu_bitable_add`** — Add a new record to a Bitable table.
```
feishu_bitable_add(
    app_token="OXXXbXXXXXXXXX",
    table_id="tblXXX",
    fields={"任务名": "接入飞书工具", "状态": "进行中", "优先级": "高"}
)
→ {record_id}
```
- Column names in `fields` must exactly match the table headers.
- Date fields: use millisecond timestamps (e.g. `1770000000000`).

---

### Sheets (电子表格) — scope: `sheets:spreadsheet`

**`feishu_sheet_read`** — Read cell values from a spreadsheet.
```
feishu_sheet_read(
    spreadsheet_token="shtXXX",   # from URL: /sheets/<spreadsheet_token>
    range="A1:D10"                # or "SheetId!A1:D10"
)
→ {values: [["col1", "col2"], ["val1", "val2"]]}
```
- `spreadsheet_token` is in the URL: `https://xxx.feishu.cn/sheets/<spreadsheet_token>`
- Range format: `A1:C10` (first sheet) or `402cb1!A1:C10` (specific sheet by ID).

**`feishu_sheet_write`** — Write values to a spreadsheet range.
```
feishu_sheet_write(
    spreadsheet_token="shtXXX",
    range="A1:C3",
    values=[
        ["姓名", "分数", "城市"],
        ["张三", 95, "北京"],
        ["李四", 87, "上海"]
    ]
)
→ {rows_written: 3}
```
- Overwrites existing data in the range.
- Each inner list is one row; values can be strings, numbers, or booleans.

---

### Calendar (日历) — scope: `calendar:calendar`

**`feishu_calendar_create`** — Create a calendar event in the bot's primary calendar.
```
feishu_calendar_create(
    summary="需求对齐会",
    start_time="1770641576",      # Unix seconds (NOT milliseconds)
    end_time="1770645176",
    description="讨论 Q2 规划",   # optional
    timezone="Asia/Shanghai"      # default
)
→ {event_id, app_link}
```
- Convert datetime to Unix timestamp: `int(datetime(2026, 3, 15, 10, 0).timestamp())`
- The event is created in the bot application's calendar, not a personal calendar.

---

## Token Extraction Guide

| Resource | URL Pattern | Token Field |
|----------|------------|-------------|
| Document | `feishu.cn/docx/<document_id>` | `document_id` |
| Spreadsheet | `feishu.cn/sheets/<spreadsheet_token>` | `spreadsheet_token` |
| Bitable | `feishu.cn/base/<app_token>` | `app_token` |
| Wiki node | Node link in wiki | Use `feishu_wiki_list` |
| Chat | Feishu chat settings | `chat_id` (starts with `oc_`) |

---

## Permission Setup (One-Time)

If a tool returns `code 99991663` or a permission error, the bot app is missing a scope.

Steps to fix:
1. Go to [飞书开放平台](https://open.feishu.cn) → App Management → select your app
2. Navigate to: **Permissions & Scopes**
3. Enable the required scope(s) listed below:

| Scope | Required For |
|-------|-------------|
| `docx:document` | Create / append to documents |
| `docx:document:readonly` | Read documents |
| `wiki:wiki` | List / create wiki nodes |
| `bitable:app` | Query / add Bitable records |
| `sheets:spreadsheet` | Read / write spreadsheets |
| `calendar:calendar` | Create calendar events |
| `im:message` | Send proactive messages |

4. Publish a new version of the app (审核发布 or 直接发布 for internal apps).
5. Retry the request.

---

## Common Patterns

### Create a Feishu document with content
```
1. feishu_doc_create(title="Q2 总结报告")
   → document_id = "doxcnABC123"
2. feishu_doc_append(document_id="doxcnABC123", content="## 背景\n本报告...\n\n## 结论\n...")
```

### Create a Wiki page with content
```
1. feishu_wiki_list()                          → find space_id
2. feishu_wiki_list(space_id="7123456")        → find parent_node_token (optional)
3. feishu_wiki_create(space_id="7123456", title="新文章")
   → obj_token = "doxcnXXX"
4. feishu_doc_append(document_id="doxcnXXX", content="文章正文...")
```

### Query Bitable and summarize
```
1. feishu_bitable_query(app_token="...", table_id="...", filter_field="状态", filter_value="进行中")
   → {records: [...]}
2. Summarize the records in your reply.
```

### Write analysis results to a spreadsheet
```
1. feishu_sheet_write(spreadsheet_token="...", range="A1:C4", values=[
       ["指标", "本周", "上周"],
       ["DAU", 12500, 11800],
       ["留存率", "72%", "69%"],
   ])
```
