# Hermit 完整 i18n 迭代方案（中英双语）

这份方案基于当前仓库实现整理，目标不是“把字符串机械翻译一遍”，而是让 Hermit 在 `en-US` 和 `zh-CN` 两种语言下都具备一致、可维护、可测试的用户体验。

当前基座已经存在：

- locale 解析与 fallback 在 `hermit/i18n.py`
- 全局 locale 配置在 `hermit/config.py`
- catalog 已存在于 `hermit/locales/en-US.json` 和 `hermit/locales/zh-CN.json`
- 顶层 CLI help、菜单栏 copy、`plugin.toml` 描述已部分接入 `tr()`

但距离“完整 i18n”还有明显缺口：

- `hermit/main.py` 大量子命令 help、交互提示、运行时输出仍是硬编码
- `hermit/companion/control.py`、`hermit/companion/appbundle.py` 仍直接返回英文文案
- `hermit/core/runner.py`、`hermit/context.py`、`hermit/provider/services.py` 仍有大量中文系统文案
- `hermit/builtin/*` 的工具描述、schema 描述、错误消息、列表输出大量未接入 catalog
- Feishu adapter / reply / approval copy 存在中英混写
- 当前 `docs/i18n.md` 仍明确把 system prompt 和 Feishu 业务输出排除在外

## 目标定义

这里的“完全 i18n”建议定义为：

- 所有用户可见的内建文案都能随 locale 在中文和英文之间切换
- 同一 locale 下不再出现明显的中英混杂内建文案
- 缺失翻译不会导致启动失败，但会被测试和 CI 阻止合入
- 命令名、工具名、schema 字段名、环境变量名、协议标签保持稳定，不参与翻译
- 系统生成的人类可读内容也遵守 locale，包括 CLI 输出、Feishu 系统提示、审批文案、菜单栏状态、内建 slash command 帮助

## 边界与原则

建议保留“协议层稳定、展示层本地化”的原则。

应该翻译：

- `help` / `description` / `title` / `summary` / 错误提示 / 状态文本
- CLI 交互提示
- 菜单栏和 companion 文案
- 工具对用户和模型展示的人类可读描述
- 审批文案、Feishu 系统提示、scheduler/webhook/web 工具返回文本
- system prompt 中面向人类语义的自然语言说明

不应该翻译：

- CLI 命令名，例如 `serve`、`schedule`
- slash command 名，例如 `/plan`、`/compact`
- tool name，例如 `schedule_create`
- JSON schema 字段名，例如 `job_id`、`prompt`
- hook 名、内部状态枚举、数据库字段、日志 event name
- XML / HTML / prompt tags，例如 `<hermit_runtime>`

这能保证模型和内部协议稳定，同时让用户可见体验完整切换语言。

## 推荐架构

### 1. 从“全局 JSON 文件”升级到“按域拆分 catalog”

当前只有两个大文件：

- `hermit/locales/en-US.json`
- `hermit/locales/zh-CN.json`

随着覆盖面扩展，建议升级为：

```text
hermit/locales/
  en-US/
    cli.json
    companion.json
    kernel.json
    prompt.json
    tools.json
    feishu.json
    plugin.json
  zh-CN/
    cli.json
    companion.json
    kernel.json
    prompt.json
    tools.json
    feishu.json
    plugin.json
```

`hermit/i18n.py` 负责合并同一 locale 目录下的所有 JSON。这样做的收益是：

- 降低单文件冲突
- 便于按模块推进
- key 的 owner 更清晰
- 后续可给不同模块加 parity test

### 2. 引入轻量 Translator，而不是到处手传 `locale`

当前大部分代码都直接 `tr(key, locale=...)`。完整 i18n 后建议补一层轻量封装，例如：

- `translator_for(settings)`
- `translator_for_locale(locale)`
- `ctx.t("tools.scheduler.created", id=job.id)`

这个对象只做三件事：

- 读取当前 locale
- 包装 `tr()`
- 提供 schema / spec 的递归本地化辅助函数

这样可以避免在 `ToolSpec`、`CommandSpec`、adapter、kernel service 里四处手传 `locale`。

### 3. 把“纯字符串字段”升级成“可本地化字段”

当前以下结构都是纯字符串：

- `ToolSpec.description`
- `CommandSpec.help_text`
- `SubagentSpec.description`
- `AdapterSpec.description`

建议保持兼容前提下支持两种写法：

- 继续支持现有 `description="..."`
- 新增 `description_key="tools.scheduler.create.description"` 之类的可本地化入口

对 `ToolSpec.input_schema` 也建议补一个递归本地化过程，只翻译：

- `description`
- `title`

不修改：

- `type`
- `required`
- `properties` 的字段名
- `enum` 值

### 4. 将“UI locale”和“用户内容语言”显式区分

建议继续保留 `HERMIT_LOCALE` 作为内建产品文案语言，不自动翻译用户输入内容。

也就是说：

- 用户自己写的 prompt、rules、skills、webhook prompt_template 不做自动翻译
- Hermit 自己生成的提示、帮助、审批文案、菜单栏状态、scheduler 系统提示随 locale 切换

这样能避免“平台文案切英文，但用户业务 prompt 被偷偷改写”的风险。

## 分阶段实施

## Phase 0：基建和守护线

目标：先把 i18n 从“可选能力”变成“可持续约束”。

建议改动：

- 扩展 `hermit/i18n.py`，支持目录式 catalog 加载
- 新增 `scripts/check_i18n.py`
- 新增 placeholder parity 校验，保证中英文 `{name}`、`{pid}` 这类变量一致
- 新增 missing key 校验，禁止 `zh-CN` 或 `en-US` 漏 key
- 为“新增用户可见硬编码字符串”增加 lint 约束或最小化 grep 检查
- 扩展 `tests/test_i18n.py`

验收标准：

- catalog 缺 key 时测试失败
- placeholder 不一致时测试失败
- fallback 仍然可用，不影响程序启动

## Phase 1：补齐 CLI 外壳层

目标：先让本地 CLI 在中英文下看起来是完整产品，而不是半翻译状态。

优先文件：

- `hermit/main.py`
- `hermit/autostart.py`

覆盖内容：

- `Typer(...)` 的 app help
- `typer.Option(..., help=...)`
- `typer.Argument(..., help=...)`
- `setup` 流程提示
- `chat`、`serve`、`reload`、`plugin`、`schedule`、`task` 等命令输出
- 各类 `raise typer.Exit` 前的错误提示

这一阶段完成后，`HERMIT_LOCALE=en-US` 和 `HERMIT_LOCALE=zh-CN` 下执行 `hermit --help`、`hermit schedule --help`、`hermit task --help` 不应再出现大量硬编码异语种字符串。

## Phase 2：补齐 companion 和 menubar

目标：让 macOS companion 的状态、动作、通知语言完全一致。

优先文件：

- `hermit/companion/menubar.py`
- `hermit/companion/control.py`
- `hermit/companion/appbundle.py`

覆盖内容：

- service start/stop/reload 结果
- preflight 失败提取和展示
- login item 开关提示
- 安装 app bundle 成功/失败提示
- profile 切换和 feature toggle 结果

注意点：

- 当前 `menubar.py` 已接 `tr()`，但 `control.py` 和 `appbundle.py` 仍大量返回硬编码字符串
- 这部分改完后，菜单栏 UI 和 shell 输出才能真正同步语言

## Phase 3：补齐 kernel、slash commands、审批和 system prompt 外层文案

目标：把“产品内核对用户说的话”也统一进 i18n。

优先文件：

- `hermit/core/runner.py`
- `hermit/context.py`
- `hermit/kernel/approval_copy.py`
- `hermit/provider/services.py`
- `hermit/builtin/compact/commands.py`
- `hermit/builtin/planner/commands.py`
- `hermit/builtin/usage/commands.py`

覆盖内容：

- `/help`、未知命令提示、新会话提示、history 展示
- base system prompt 里的说明性自然语言
- startup prompt 中插入的 slash command 说明
- approval copy 的 summary/detail/title
- approval LLM formatter prompt
- compact / planner / usage 这类内建 slash command 的帮助文案

关键约束：

- `<hermit_runtime>`、`<self_configuration>` 等标签名保持不变
- slash command 名保持不变
- 只翻译命令的说明文字和用户可见结果

这一阶段是“完整 i18n”最容易被忽略、但用户感知最强的一段。

## Phase 4：补齐工具层和 schema 描述

目标：让 tool registry 暴露给用户和模型的人类可读说明完整双语化。

优先文件：

- `hermit/core/tools.py`
- `hermit/builtin/scheduler/tools.py`
- `hermit/builtin/webhook/tools.py`
- `hermit/builtin/web_tools/tools.py`
- `hermit/builtin/web_tools/search.py`
- `hermit/builtin/web_tools/fetch.py`
- `hermit/builtin/grok/tools.py`
- `hermit/builtin/grok/search.py`
- 其他 `hermit/builtin/*/tools.py`

建议落法：

- `ToolSpec` 增加本地化 description 支持
- 增加 `localize_schema()`，递归处理 `description` / `title`
- 工具 handler 返回的字符串全部走 catalog
- 工具列表、执行历史、错误信息统一用 key 管理

注意风险：

- tool name、schema key、enum 值不能翻译
- 但 `description` 和错误信息可以翻译
- 这部分会影响模型理解，需要保证 en 和 zh 两套描述都准确、简洁、语义对齐

## Phase 5：补齐 Feishu 和业务输出层

目标：让外部 channel 里用户真正看到的系统文案也能切换中英文。

优先文件：

- `hermit/builtin/feishu/adapter.py`
- `hermit/builtin/feishu/reply.py`
- `hermit/builtin/feishu/tools.py`
- `hermit/kernel/approval_copy.py`
- scheduler / webhook 的 Feishu 推送文案

覆盖内容：

- 图片、附件、系统说明等 fallback 文案
- 审批卡片和进度通知
- scheduler 执行结果推送
- webhook / system generated 消息

注意点：

- 这层是“最终用户感知”的重点，不能继续中英混写
- 建议先支持“全局 locale 决定 Feishu 系统文案”，后续再评估是否引入“按 chat / user 的 locale”

## Phase 6：把 system prompt、技能提示和内建模板纳入 i18n

目标：真正把当前 `docs/i18n.md` 里排除的部分收回来，但要控制风险。

优先文件：

- `hermit/context.py`
- `hermit/provider/services.py`
- `hermit/builtin/*/commands.py`
- `hermit/builtin/*/hooks.py`
- 内建 `SKILL.md` 中会直接注入 prompt 的部分

建议策略：

- 只本地化“说明性自然语言”
- 不翻译协议 tag、command 名、tool 名、字段名
- 对于会明显影响模型行为的 prompt，采用“等义双语版本”而不是运行时机器翻译

这一阶段不要追求一次性覆盖所有 skill 文档。更合理的顺序是：

- 先覆盖会直接拼入 system prompt 的内建片段
- 再覆盖始终 preload 的 skill
- 最后再考虑外部插件生态的本地化约定

## 推荐拆分成 5 个实际 PR

为了降低回归风险，建议按下面顺序拆：

1. i18n 基建 PR
2. CLI 与 companion PR
3. kernel / slash command / approval copy PR
4. builtin tools 与 schema PR
5. Feishu 与 prompt 层 PR

这样每一批都能独立回归，不会把“文案替换”和“模型行为变化”混在同一个 PR 里。

## 测试策略

建议新增以下测试层级。

单元测试：

- `tests/test_i18n.py` 扩展 key parity、placeholder parity、目录式 catalog 加载
- `tests/test_approval_copy.py` 验证中英文审批文案
- `tests/test_tool_localization.py` 验证工具描述和 schema 描述会随 locale 切换

CLI 测试：

- 用 `CliRunner` 分别在 `HERMIT_LOCALE=zh-CN` 和 `HERMIT_LOCALE=en-US` 下跑 `--help`
- 校验 `setup`、`schedule`、`task`、`plugin`、`reload` 等关键输出

集成测试：

- 构建 runtime 后验证 slash command 帮助文本随 locale 切换
- 验证 startup system prompt 中的说明性文案切换成功
- 验证 Feishu fallback 文案与 approval 文案切换成功

回归规则：

- 任何新增用户可见字符串必须有 key
- 中英文 catalog 必须同时落地
- 新增格式化变量必须双语保持同名

## 推荐 key 规范

建议继续使用扁平 key，但按域命名：

- `cli.setup.step1.title`
- `cli.schedule.added`
- `companion.service.started`
- `kernel.approval.summary.command`
- `prompt.base.self_configuration`
- `tools.scheduler.create.description`
- `tools.scheduler.schema.prompt.description`
- `feishu.reply.image_only`

不要用整句英文做 key，也不要把 key 命名得过于抽象，例如 `common.text1`。

## 明确不建议做的事

- 不建议把工具名、命令名也翻译成中文别名
- 不建议运行时调用模型自动翻译 catalog
- 不建议只做中文 catalog，把英文当 fallback 继续硬编码在业务代码里
- 不建议先改 prompt 层再改 CLI/companion，因为 prompt 层风险最高
- 不建议把用户自己写入的 prompt_template、skills、rules 自动翻译

## 这版方案的落地顺序结论

如果只选一条最稳的路径，建议按下面顺序推进：

1. 先补 i18n 基建和 CI 守护线
2. 再补 `hermit/main.py` 与 `companion/*`
3. 再补 `core/runner.py`、`approval_copy.py`、`provider/services.py`
4. 再补所有 builtin tool 的描述、schema、错误消息
5. 最后补 Feishu 输出和 system prompt 片段

这样做可以在不破坏现有 agent 行为的前提下，逐步把 Hermit 迭代到真正“中英文都完整可用”的状态。
