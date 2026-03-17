# Hermit Memory 健康修复清单

这份清单基于当前仓库实现、`~/.hermit-dev` 运行日志、`~/.hermit-dev/memory/memories.md`、`~/.hermit-dev/memory/session_state.json` 和 `~/.hermit-dev/kernel/state.db` 的实际状态整理。

目标不是“把 memory 再做复杂”，而是让现有机制更可信、更收敛、更容易解释。

## 当前结论

当前 memory 机制**可以工作，但不算完全健康**。

已经确认正常的部分：

- `memory_injected`、`memory_retrieval_*`、`memory_checkpoint_saved` 在真实运行日志中出现，说明注入、检索、checkpoint 主链路是通的
- `memory_write` receipt 在 kernel 中持续生成，且目前观察到的记录均为 `succeeded`
- `memory_records` 和 `memories.md` 当前能同步到一致的 active 集合

已经确认存在的问题：

- provider 鉴权异常时，memory checkpoint/save 会被整体跳过，日志中出现过 `reason=no_auth`
- active durable memory 中存在跨会话的冲突事实同时存活
- 系统级 `<memory_context>` 当前会注入过宽的信息集合，包含高时效、低稳定性内容
- 记忆治理规则主要埋在 hook 和 promotion 流程里，边界还不够清晰

## 修复目标

修复后的 memory 应该满足：

1. 能稳定写入，不因偶发 provider 状态而默默退化成“有时记、有时不记”
2. 能区分“长期约定”和“短期事实”，避免把易过期信息长期塞进系统 prompt
3. 能在跨会话场景下处理冲突和失效，而不是只在同一 conversation 内 supersede
4. 能从日志和状态中快速回答“为什么没记住”“为什么还记着旧信息”
5. 保持架构干净，不把更多策略继续堆到 `hooks.py` 的分支判断里

## 架构原则

### 1. 分离四层职责

建议明确拆成四层职责：

- `extraction`
  - 只负责从消息里提取候选 memory，不决定是否全局注入
- `promotion`
  - 只负责把候选事实变成 belief / durable memory / receipt
- `policy`
  - 只负责决定哪些 category 可以进入全局静态 memory，哪些只能用于 retrieval
- `governance`
  - 只负责 supersede、invalidate、时效衰减、冲突处理

当前问题之一是这些职责大量集中在 `src/hermit/plugins/builtin/hooks/memory/hooks.py`，后续修复应尽量把“规则判断”抽走，而不是继续加 if/else。

### 2. 静态注入和检索注入分开治理

应明确区分两种 memory 用途：

- `static memory`
  - 面向所有会话默认注入
  - 只允许稳定、长期、低歧义信息进入
- `retrieval memory`
  - 仅在当前 prompt 相关时注入
  - 可容纳更高时效性的事实

当前 `memory_context` 过宽，本质上是把 retrieval memory 当成了 static memory。

### 3. “进行中的任务”不应天然等于 durable global memory

`进行中的任务` 这类条目天然高时效、强上下文依赖，不应默认长期全局注入。

建议后续默认策略：

- `用户偏好`、`项目约定`、稳定的 `工具与环境` 可进入 static memory
- `进行中的任务`、一般 `其他`、强时效 `技术决策` 默认只进入 retrieval
- 医疗、财务、市场行情、临时任务状态这类内容默认应视为高时效或高敏感

### 4. supersede 必须允许跨 conversation 生效

当前 supersede 只在同 `conversation_id` 的 active memory 上做主题冲突判定，这会导致“旧事实”和“新事实”在不同会话中同时存活。

需要把 supersede 范围从“同 conversation”升级为“同 subject / 同 memory scope”。

## 修复清单

## Phase 1：先收口 static memory 注入范围

目标：先解决“注入脏”和“把临时事实写进系统 prompt”。

建议改动：

- 为 memory category 增加注入策略，而不是所有 active record 一律进入 `_inject_memory()`
- 新增类似 `memory_scope = static | retrieval_only | local_only` 的字段或映射
- 在 `_inject_memory()` 中只汇总允许静态注入的 categories
- 在 `_inject_relevant_memory()` 中继续允许 broader set 做相关检索

优先级最高的默认策略：

- `用户偏好` -> `static`
- `项目约定` -> `static`
- `工具与环境` -> `static`
- `环境与工具` -> `retrieval_only`
- `技术决策` -> `retrieval_only`
- `进行中的任务` -> `retrieval_only`
- `其他` -> `retrieval_only`

验收标准：

- 当前 `memory_context` 不再包含“已设定每日定时任务”和“当前无任何定时任务”这类条目
- 静态 system prompt 中保留的 memory 主要是偏好、约定、稳定环境信息
- retrieval 仍能在相关问题里命中这些条目

## Phase 2：补上跨会话冲突治理

目标：让 durable memory 能淘汰旧事实，而不是无限积累“都是真的”。

建议改动：

- 为 memory record 引入更明确的 `scope` 概念
  - `global`
  - `conversation`
  - `entity`
  - `workspace`
- supersede 判定不要只按 `conversation_id`
- 对以下类型增加强制冲突规则：
  - 定时任务存在性
  - 当前仓库状态摘要
  - 配置/默认值变更
  - “已完成 / 已删除 / 已关闭”类状态更新
- 为高时效事实引入显式 invalidation，而不是只靠 `supersedes_json`

建议落法：

- 新增一个独立的 `MemoryGovernanceService`
- `MemoryRecordService.promote_from_belief()` 只负责 promotion
- supersede / invalidate / scope matching 交给 governance service

验收标准：

- 当“创建任务”和“删除任务”先后发生时，active memory 中最终只保留最新有效状态
- `memory_records` 中不会长期同时保留互斥事实的 active 版本

## Phase 3：把时效性纳入 durable memory 策略

目标：让系统知道“哪些记忆会老”。

建议改动：

- 给 memory category 或 record 增加 `stability_class`
  - `stable`
  - `volatile`
  - `sensitive`
- 对 `volatile` memory 增加 TTL 或 freshness hint
- retrieval 时优先新记录，必要时压低旧记录分数
- static 注入时直接忽略 `volatile`

优先适用对象：

- 股价、热榜、新闻、天气、临时任务、临时文件状态

验收标准：

- 超过 freshness 窗口的 volatile memory 不再进入 static prompt
- retrieval 遇到多个版本时，优先最新且未过期的记录

## Phase 4：把 provider 依赖从 memory 稳定性风险里拆开

目标：避免主 provider 短暂失效时，memory 整条链路一起失明。

当前日志已经证明：

- provider 鉴权异常时，会出现 `memory_checkpoint_skipped reason=no_auth`
- 这虽然是保护行为，但对用户视角来说像“memory 偶尔失灵”

建议改动：

- 区分“主任务执行 provider 不可用”和“memory 提取 provider 不可用”
- 如果后续仍使用同一 provider 做 memory extraction，至少补一个显式健康告警
- 可选方案是让 memory extraction 支持 fallback provider 或延迟补偿任务

低风险优先方案：

- 保留当前跳过逻辑
- 但增加 `memory_degraded` 级别事件
- 在 reload / preflight / health 输出里展示最近 memory 是否连续跳过

验收标准：

- 用户或开发者能从日志/状态里快速看出“memory 没写入是因为 provider 鉴权失败”
- 不再只看到普通 `skipped`，却不知道系统是否处于退化状态

## Phase 5：补齐 memory 可观测性

目标：把“为什么没记住”从猜谜题变成可检查状态。

建议补充：

- `memory_checkpoint_saved`
  - 增加新写入的 category、memory_id、scope 摘要
- `memory_checkpoint_no_entries`
  - 增加 extraction 返回的判空原因
- `memory_injected`
  - 区分 `static_entries` 与 `retrieval_candidates`
- `memory_save_skipped`
  - 对 `no_auth`、`no_messages`、`no_pending_delta` 做更清楚的运营语义
- 新增一个 inspection CLI
  - 例如 `hermit memory status`
  - 展示 active memory 数量、volatile 数量、最近 promotion、最近 supersede、最近 degraded 原因

验收标准：

- 不打开数据库也能知道最近一次 memory promotion 是否成功
- 能快速看出 active memory 中哪些是 static，哪些是 retrieval-only

## Phase 6：控制 mirror file 的角色

目标：让 `memories.md` 成为 mirror，而不是规则来源和状态来源的混合体。

建议原则：

- `kernel.state.db` 中的 `memory_records` 是事实来源
- `memories.md` 是可读镜像
- 注入逻辑优先用 kernel durable memory，而不是直接从 markdown 猜语义

当前代码已经部分朝这个方向走了，但后续应进一步明确：

- markdown 编辑是人工 fallback，不是主治理入口
- supersede / invalidate / scope 信息应主要来自 DB，而不是靠 markdown 注释兜底

验收标准：

- 即使 `memories.md` 被手改，系统也不会把它当作唯一权威来源覆盖 durable state
- bootstrap 只在缺少 durable memory 时发生

## 建议的实现顺序

按“收益最大、改动最稳”的顺序，建议这样做：

1. 收口 static memory 注入范围
2. 增加 memory scope 和 governance service
3. 让跨会话 supersede / invalidation 生效
4. 增加时效性策略
5. 补 observability 和 inspection CLI
6. 再考虑 provider fallback 或补偿式 memory extraction

## 明确暂时不做

为了保持架构干净，这一轮不建议做：

- 不把 memory 变成向量数据库优先架构
- 不引入复杂 embedding pipeline 解决本轮问题
- 不把所有 category 都做成可配置 UI
- 不在 `hooks.py` 里继续堆更多特例判断
- 不让 markdown mirror 继续承担治理规则

## 直接对应到当前代码的修改入口

第一批改动最可能涉及：

- `src/hermit/plugins/builtin/hooks/memory/hooks.py`
- `src/hermit/kernel/context/memory/knowledge.py`
- `src/hermit/kernel/ledger/journal/store.py`
- `src/hermit/kernel/task/models/records.py`
- `tests/unit/plugins/memory/test_memory_hooks.py`
- `tests/unit/plugins/memory/test_memory_engine.py`
- `tests/unit/kernel/test_memory_governance.py`

建议新增的抽象：

- `MemoryGovernanceService`
- `MemoryScope`
- `MemoryStabilityClass`

## 最终验收标准

一轮修复完成后，至少应满足：

1. 当前 active memory 中不存在明显互斥的全局事实同时存活
2. `<memory_context>` 默认不再注入高时效任务状态和杂项事实
3. provider 鉴权异常时，memory 退化状态能被明确观测
4. checkpoint / session_end / retrieval 三段行为都能从日志里解释清楚
5. 新规则主要落在独立治理层，而不是继续扩张 hook 分支复杂度
