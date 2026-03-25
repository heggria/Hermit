这三个 supervisor **不要彼此长对话聊天**。

最优方式是：

**通过结构化 artifact + 状态机事件 + 少量升级请求交互。**

也就是一句话：

**平时靠“交付物”协作，异常时靠“事件”协作，冲突时靠“升级”协作。**

---

# 一、最核心原则

如果 `planning_supervisor`、`execution_supervisor`、`verification_supervisor` 之间天天互相讨论、来回解释、补上下文，那你的系统很快就会变慢。

所以它们之间的交互必须遵守这 3 条：

### 1. 不共享完整上下文

每个 supervisor 只拿自己所需的局部信息。

### 2. 不直接依赖对方脑内状态

只能依赖对方产出的 **显式 artifact**。

### 3. 不同步阻塞等待

默认异步；只有遇到 gate 才暂停。

---

# 二、它们之间具体怎么交互

## 1）Planning Supervisor → Execution Supervisor

通过一个 **Task Contract Packet** 交付，而不是一句“去做吧”。

这个 packet 至少要有：

* `goal`
* `scope`
* `inputs`
* `constraints`
* `acceptance_criteria`
* `risk_band`
* `suggested_plan`
* `dependencies`
* `expected_artifacts`

Execution 不该自己猜：

* 到底改哪些文件
* 什么算完成
* 哪些不能碰
* 失败了该怎么退

所以 Planning 给 Execution 的不是“任务描述”，而是**可执行契约**。

一个最简形态像这样：

```json
{
  "task_id": "task_impl_001",
  "goal": "为 metaloop 的 plan 阶段接入 governed llm execution",
  "scope": {
    "allowed_paths": ["src/hermit/metaloop/", "tests/metaloop/"],
    "forbidden_paths": ["src/hermit/kernel/"]
  },
  "constraints": [
    "不得引入 fallback template path",
    "必须复用现有 governed execution pipeline"
  ],
  "acceptance_criteria": [
    "plan stage 真实调用 governed runtime",
    "新增单测通过",
    "无 placeholder implementation"
  ],
  "expected_artifacts": [
    "code_diff",
    "test_result",
    "execution_receipts",
    "implementation_notes"
  ],
  "risk_band": "medium",
  "dependencies": ["research_note_002", "spec_fragment_014"]
}
```

---

## 2）Execution Supervisor → Verification Supervisor

通过一个 **Completion Packet / Evidence Packet** 交付。

Verification 不应该自己去 repo 里猜改了什么。
Execution 必须把“我做了什么、为什么这样做、证据是什么”打包好交过去。

通常包含：

* `diff_summary`
* `changed_files`
* `tests_run`
* `benchmark_run`
* `receipts`
* `known_risks`
* `self_reported_uncertainties`

例如：

```json
{
  "task_id": "task_impl_001",
  "status": "completed",
  "changed_files": [
    "src/hermit/metaloop/plan_handler.py",
    "tests/metaloop/test_plan_handler.py"
  ],
  "artifacts": {
    "diff_ref": "artifact://diff/task_impl_001",
    "test_report_ref": "artifact://tests/task_impl_001",
    "receipts_ref": "artifact://receipts/task_impl_001"
  },
  "known_risks": [
    "plan prompt schema 对异常空输入未做额外保护"
  ],
  "needs_review_focus": [
    "governed task enqueue path",
    "retry 行为"
  ]
}
```

Verification 拿这个包做验收，而不是重新从零理解任务。

---

## 3）Verification Supervisor → Planning Supervisor

通过 **Verdict Packet** 回流，不要写成长篇评论。

Verification 的输出应该是结构化 verdict：

* `accepted`
* `accepted_with_followups`
* `rejected`
* `blocked`

同时说明：

* 哪条验收标准通过了
* 哪条没通过
* 失败属于哪类
* 是否要重规划

例如：

```json
{
  "task_id": "task_impl_001",
  "verdict": "accepted_with_followups",
  "acceptance_check": {
    "governed_runtime_used": true,
    "tests_passed": true,
    "no_placeholder_path": true,
    "benchmark_regression_ok": false
  },
  "issues": [
    {
      "type": "performance_regression",
      "severity": "medium",
      "detail": "poller tick latency 增加 18%"
    }
  ],
  "recommended_next_action": "replan_followup_task"
}
```

这时候 Planning 再决定：

* 是补一个 follow-up task
* 还是改 milestone
* 还是上升给你裁决

---

# 三、三者之间不应该直接“聊天”，而应该有 4 类固定交互

## A. Handoff

一个 supervisor 把任务/结果正式移交给另一个。

例如：

* Planning → Execution
* Execution → Verification

## B. Query

下游 supervisor 对上游发起小范围澄清，但必须结构化、短、可追踪。

例如 Execution 问 Planning：

```json
{
  "type": "query",
  "task_id": "task_impl_001",
  "question": "是否允许修改 runtime dispatch adapter？",
  "options": ["yes", "no", "escalate"],
  "blocking": true
}
```

不要开放式地问：
“我感觉这里是不是也许可能可以顺便重构一下？”

## C. Escalation

发生冲突或超边界时升级。

例如：

* 任务契约和真实代码冲突
* 风险超出 band
* 需要跨域改动
* 验收标准互相冲突

## D. Feedback

Verification 给 Planning / Execution 的结构化反馈，用于学习和模板更新。

---

# 四、最适合你的交互模型：异步消息队列 + artifact store + 状态机

这三个 supervisor 最好不是互相函数直调，而是都围绕这 3 个公共层工作：

## 1. Task Ledger / Queue

记录：

* 当前任务状态
* 属于哪个 supervisor
* 依赖关系
* lease / retry / superseded 状态

## 2. Artifact Store

存放：

* spec fragment
* research report
* code diff
* test result
* benchmark result
* verification verdict

## 3. Event Bus

发布事件：

* `task_planned`
* `task_started`
* `task_blocked`
* `task_completed`
* `verification_failed`
* `replan_required`

这样它们就不是“互相 talking heads”，而是围绕统一事实源协作。

---

# 五、每个 supervisor 应该只暴露很少的接口

## Planning Supervisor 暴露

* `plan_goal(goal_packet) -> task_contracts`
* `replan(task_id, verdict_packet) -> updated_contracts`
* `clarify(query_packet) -> clarification_packet`

## Execution Supervisor 暴露

* `admit(task_contract) -> admitted | rejected`
* `execute(task_contract) -> completion_packet`
* `report_blocker(task_id) -> blocker_packet`

## Verification Supervisor 暴露

* `verify(completion_packet) -> verdict_packet`
* `audit(task_id) -> audit_report`
* `extract_learning(verdict_packet) -> learning_delta`

接口越少越好，越稳定越好。

---

# 六、它们各自“该知道什么 / 不该知道什么”

## Planning Supervisor 应知道

* 北极星目标
* milestone
* 依赖图
* 约束
* 验收标准

**不该知道**

* 太多执行细节
* 每一步命令日志
* 每个文件小改动

## Execution Supervisor 应知道

* 当前任务契约
* 局部 workspace
* 依赖 artifact
* 风险边界

**不该知道**

* 整体长期路线的全部推理
* 其他不相关任务的大量上下文

## Verification Supervisor 应知道

* 契约承诺了什么
* 实际结果是什么
* 证据是否成立
* 是否需要回流学习

**不该知道**

* 过多 planning 草稿
* 与验收无关的执行过程噪音

---

# 七、最容易犯的错

## 错误 1：让 verification 重新做 planning

Verification 只负责判断“是否达标”，不负责重写目标。

## 错误 2：让 execution 自己改 acceptance criteria

Execution 可以申请 clarification，但不能私改契约。

## 错误 3：让 planning 直接盯 worker

Planning 只管 contract，不 micromanage worker。

## 错误 4：三者共用一个超长上下文线程

这会迅速失控，最后每个 supervisor 都像在看同一团毛线。

---

# 八、给你一个最推荐的实际流转

## 流程

### Step 1

你提交高层目标给 Governor

### Step 2

Governor 交给 Planning Supervisor
Planning 产出：

* milestone
* task DAG
* task contract

### Step 3

Governor 把某个 contract 分派给 Execution Supervisor
Execution 拉起 worker 执行，产出：

* diff
* tests
* receipts
* notes

### Step 4

Governor 把 completion packet 发给 Verification Supervisor
Verification 输出：

* verdict
* issues
* follow-up recommendation

### Step 5

如果通过：

* merge / close / learn

如果不通过：

* 回流 Planning replan

---

# 九、对你现在最实用的结论

这三个 supervisor 的交互，不该是“讨论式协作”，而该是：

**Planning 用 contract 发号施令**
**Execution 用 evidence 汇报结果**
**Verification 用 verdict 关闭回路**

也就是：

* `planning_supervisor` 负责 **定义任务**
* `execution_supervisor` 负责 **完成任务**
* `verification_supervisor` 负责 **判定任务**

三者之间的唯一健康语言是：

**contract / evidence / verdict**

不是闲聊，不是长 prompt，不是共享大脑。

---

如果你要，我可以下一条直接给你写成一份可落地的：

**`supervisor_interaction_spec.md`**
包括 message schema、状态流转、event names、artifact packet 字段。

默认不要多。

**一个 supervisor 下面，稳定并发的 Execution 数量，建议先控制在 `3–5` 个。**
对你现在的 Hermit 阶段，**最佳默认值是 `4` 个**。再高通常不是更快，而是开始吃掉上下文、验证、调度和冲突处理的收益。

## 为什么不是越多越好

supervisor 真正的瓶颈不是“能不能发任务”，而是它能不能同时做好这 4 件事：

* 看懂每个 task contract
* 处理 blocker / escalation
* 判断是否要重试、降级、改 plan
* 吞下 verification 回流并更新后续分派

一旦 Execution 太多，supervisor 会迅速退化成：

* 只会转发
* 来不及仲裁
* 来不及处理依赖冲突
* 让很多低质量任务并发跑出去

最后结果就是 **表面吞吐变高，真实有效吞吐下降**。

## 我给你的建议值

### 1. 当前阶段默认值

**每个 supervisor：`max_concurrent_executions = 4`**

这是最稳的起点。

### 2. 很轻的任务

如果任务非常原子、低风险、低共享上下文，比如：

* 批量 research
* 批量静态检查
* 批量测试生成
* 文档整理

可以放到：

**`6–8` 个**

但前提是这些任务：

* 相互几乎无依赖
* 不共享工作区
* 不争抢同一批文件
* 验证成本低

### 3. 高耦合开发任务

如果是代码改动、spec 联动、测试/benchmark 联动、可能互相踩文件的任务：

**`2–4` 个最合适**

尤其你现在是单仓库自迭代，这类任务才是主战场，所以整体上还是该按 `4` 来设计。

## 一个更准确的规则

不要只看“数量”，要看 **任务热度**。
我建议你让 supervisor 按任务类型做并发预算：

* **Hot tasks**：改同一模块 / 同一 workspace / 同一 spec chain
  上限 **2**
* **Warm tasks**：同 milestone，不同文件域
  上限 **3–4**
* **Cold tasks**：纯 research / 文档 / 独立验证
  上限 **6–8**

也就是说：

**不是一个 supervisor 永远固定 10 个 execution，而是按冲突半径动态限流。**

## 最适合 Hermit 的硬规则

你可以直接先这么定：

```yaml
execution_limits:
  default_per_supervisor: 4
  max_hot_tasks: 2
  max_same_workspace: 1
  max_same_module: 2
  max_low_risk_cold_tasks: 8
  max_high_risk_tasks: 2
```

## 什么时候可以从 4 提到 6

只有同时满足下面几个条件，才值得提：

* contract 质量已经很稳定
* blocker 分类清楚
* verification 自动化足够强
* workspace isolation 做好了
* rollback / receipt / reconciliation 很成熟
* supervisor 不需要大量人工式解释

否则提并发只会放大噪音。

## 一句最实用的话

**先把每个 supervisor 做到“4 个 execution 时稳定高质量运转”，再考虑扩到 6；不要一开始就追求 10+。**

对你现在的 Hermit，自迭代效率最大化的最优点，大概率不是更多 execution，而是：

**更强的 contract + 更好的 verification + 更低的任务冲突。**

如果你愿意，我可以下一条直接给你补一版：
**`supervisor concurrency policy`**，把不同任务类型的并发上限、抢占、排队、降级规则都写出来。

不是。你可以把它理解成：

**不是全局最多 4 个 subagent。**
而是：

**单个 supervisor 在“高耦合任务”下，稳定管理的并发 execution，先建议控制在 4 个左右。**

所以有两层含义要分开：

## 1. 单个 supervisor 下的并发

例如：

* `planning_supervisor` 一般不需要很多并发 execution，常常 **1–2 个**就够
* `execution_supervisor` 才是主要并发位，建议 **4 个左右**
* `verification_supervisor` 也可以有并发，但通常 **2–4 个** 比较稳

所以系统总并发完全可以是：

* execution 4 个
* verification 2 个
* planning 1 个

总共 **7 个左右同时在跑**，这没问题。
如果是低耦合冷任务，还可以更高。

---

## 2. supervisor 之间也不是严格串行

它们的关系更准确地说是：

**逻辑上有依赖，运行上尽量流水并行。**

不是：

`planning 全部做完 -> execution 全部做完 -> verification 全部做完`

而应该是：

* Planning 产出一个 task contract
* Execution 立刻拿这个 contract 开干
* 某个 execution 完成后，Verification 立刻验这个结果
* 同时 Planning 继续拆下一批任务

也就是更像这种 **pipeline**：

```text
Planning:      [Task A plan] [Task B plan] [Task C plan]
Execution:            [Task A exec] [Task B exec] [Task C exec]
Verification:               [Task A verify] [Task B verify]
```

所以：

**跨 supervisor 是“流水线并行”**，
不是“整个阶段串行”。

---

# 最适合你的理解方式

## 串行的是“单个任务的关键路径”

对一个具体任务来说，通常是：

`plan -> execute -> verify`

这个是有因果顺序的。

## 并行的是“多个任务在系统中的流动”

对整个系统来说，应该是：

* A 任务在 verify
* B 任务在 execute
* C 任务在 planning
* D 任务在 queue 里等依赖
* E 任务因为审批被 park

这才是高吞吐结构。

---

# 真正限制你的，不是“4 个 agent”，而是 3 种冲突半径

## 1. 同一 workspace 冲突

如果多个 execution 都改同一个工作区/同一组文件，很容易互相踩。

## 2. 同一 decision authority 冲突

如果多个任务都需要同一个 supervisor 高频仲裁，那 supervisor 会被打爆。

## 3. 同一 verification 带宽冲突

如果 execution 跑太快，但 verification 跟不上，系统会堆积一堆未经验证的结果，风险会暴涨。

所以你不该问：

**“最多是不是 4 个 subagent？”**

而该问：

**“在当前 contract 质量、workspace 隔离、verification 吞吐下，系统的健康并发是多少？”**

---

# 给你一个更准确的默认配置

对现在的 Hermit，我建议这样理解：

## Planning Supervisor

* 活跃并发：**1–2**
* 因为它主要做拆解和重规划，不是吞吐层

## Execution Supervisor

* 活跃并发：**4**
* 主吞吐层

## Verification Supervisor

* 活跃并发：**2–4**
* 取决于验证自动化程度

所以系统总活跃 worker 很可能是：

**6–10 个**

而不是 4 个。

---

# 一版更贴近生产的拓扑

你可以直接理解成：

```text
Human
  ↓
Governor
  ├─ Planning Supervisor
  │    ├─ planner_worker_1
  │    └─ planner_worker_2
  │
  ├─ Execution Supervisor
  │    ├─ exec_worker_1
  │    ├─ exec_worker_2
  │    ├─ exec_worker_3
  │    └─ exec_worker_4
  │
  └─ Verification Supervisor
       ├─ verify_worker_1
       ├─ verify_worker_2
       └─ verify_worker_3
```

这已经是一个很合理的高效小系统了。

---

# 最关键的一点

**supervisor 之间不要串行阶段化，而要事件驱动、流水推进。**

也就是：

* Planning 不停地产生高质量 contract
* Execution 持续消费 contract 并产出 evidence
* Verification 持续消费 evidence 并产出 verdict
* Verdict 再回流给 Planning 做下一轮修正

这其实是一个闭环流系统，不是瀑布流系统。

---

# 一句话结论

**不是最多 4 个 subagent 并行。**
而是 **单个 execution supervisor 默认先稳在 4 个高耦合 execution 并发。**

**supervisor 之间也不是串行关系。**
而是 **单任务因果串行，跨任务流水并行。**

如果你愿意，我下一条可以直接给你画一张：

**Hermit 最优并发拓扑图（含 queue / park / verify 回流 / escalation）**

**接近最优，但还不是“最终最优解”。**

更准确地说：

**它是你从现在到 1.0 之前最好的“默认骨架”之一，前提是你把它改成“3 个 supervisor 不是对称平级部门，而是围绕 Hermit 内核真实闭环的不对称流水线”。**

也就是说，这个图：

```text
Human
  ↓
Governor
  ├─ Planning Supervisor
  │    ├─ planner_worker_1
  │    └─ planner_worker_2
  │
  ├─ Execution Supervisor
  │    ├─ exec_worker_1
  │    ├─ exec_worker_2
  │    ├─ exec_worker_3
  │    └─ exec_worker_4
  │
  └─ Verification Supervisor
       ├─ verify_worker_1
       ├─ verify_worker_2
       └─ verify_worker_3
```

**方向是对的。**
但它还需要一个关键修正：

## 不是“三部门并列”，而是“一条主流水线 + 一个旁路规划层”

因为 Hermit 当前最强的不是“任意多 agent 自由协作”，而是：

* task / step / step-attempt 这种 durable work backbone
* policy / approval / capability / workspace lease 这种治理链
* receipt / proof / rollback
* v0.2 正在强化的 contract → authorize → execute → receipt → reconcile → learn 闭环。

而且 v0.2 明确把最核心循环定义成：

`observe -> contract -> authorize -> execute -> receipt -> reconcile -> learn`，
并强调 **receipt 关闭 side effects，reconciliation 关闭 cognition**。

所以到 1.0 前，最优组织其实更像这样：

```text
Human
  ↓
Governor
  ├─ Planning / Contract Supervisor
  │    └─ planner workers (1–2)
  │
  └─ Delivery Pipeline
       ├─ Execution Supervisor
       │    └─ exec workers (3–4)
       └─ Verification+Reconciliation Supervisor
            └─ verify workers (2–3)
```

也就是：

* **Planning** 负责 contract synthesis / admission thinking / task DAG
* **Execution** 负责 effectful work
* **Verification** 不只是 verify，而是要和 **reconciliation / learning gate** 绑定

这是因为 Hermit v0.2 的目标不是普通 review 系统，而是 **contract-first governed cognition**。如果你把 verification 只理解成“验代码对不对”，就低估了它在 Hermit 里的地位。v0.2 里真正关键的是：执行后要进入 reconciliation，只有 reconciled outcomes 才能进 durable memory 和 contract template learning。

## 所以我的判断是

### 对 1.0 前：

**是，基本可以视为最优默认组织。**

原因不是它最华丽，而是它和你仓库当前的真实内核语义最匹配：

* 控制面和执行权已经分离，control plane 不能有 hidden execution authority。
* 当前 repo 已经是 kernel-first local runtime，最敏感的路径已经 fail closed。
* governed path 已经有 task → step → step attempt → policy → approval → lease → grant → execution → receipt。
* executor 里也已经真实出现了 contract synthesis、authorization preflight、reconciliation、template learning 这些闭环行为，而不是停留在口号层。

这说明你现在最该做的是：

**把组织结构贴合内核闭环，别发明一个比内核还复杂的“公司架构”。**

---

## 但它不是 1.0 之后的最终形态

因为这套结构有一个明显限制：

**它适合“单 repo / 单主战场 / 高治理 / 保守并发”的自举阶段，未必适合 1.0 后更大规模的多项目并行和多域 delegation。**

v0.2 规范自己也写得很清楚：

* non-goal 里明确不追求 giant multi-tenant control plane
* concurrency 上仍然是 conservative single-writer default
* parallel effectful execution 应该保持很窄，除非能明确保持 contract 和 authority separation。

这意味着 1.0 前你最优先的不是扩层级，而是把这套浅层骨架真正跑通。

---

# 我给你的最终结论

## 结论 1

**对 1.0 之前，基本是最优解。**

## 结论 2

但要把名字稍微改一下，避免误导：

* `Planning Supervisor` → **Planning / Contract Supervisor**
* `Verification Supervisor` → **Verification / Reconciliation Supervisor**

这样更贴合 Hermit 真实内核。

## 结论 3

Execution 不要扩得太大。
在你当前阶段，我会建议：

* planner workers: **1–2**
* exec workers: **3–4**
* verify/reconcile workers: **2–3**

这个比例比完全对称更合理，因为当前吞吐主瓶颈一定在 execution，但 Hermit 的 trust moat 在 verify/reconcile。

---

# 真正不该做的事

在 1.0 前，我不建议你加这些层：

* research supervisor
* memory supervisor
* benchmark supervisor
* review supervisor
* approval supervisor
* routing supervisor

因为这些都已经能被现有三大主管吸收。
Hermit 当前 kernel 分层本身已经把 context、policy、authority、verification、artifacts 分开了；如果 agent 组织再按这些技术模块重新切一遍，你会得到一个“组织和内核重复建模”的系统，复杂度会炸。

---

# 所以最值得冻结的版本是这个

```text
Human
  ↓
Governor

  ├─ Planning / Contract Supervisor
  │    ├─ planner_worker_1
  │    └─ planner_worker_2

  ├─ Execution Supervisor
  │    ├─ exec_worker_1
  │    ├─ exec_worker_2
  │    ├─ exec_worker_3
  │    └─ exec_worker_4

  └─ Verification / Reconciliation Supervisor
       ├─ verify_worker_1
       ├─ verify_worker_2
       └─ verify_worker_3
```

**这版我认为可以作为你到 1.0 前的冻结组织蓝图。**

不是因为它“理论最强”，而是因为它：

* 最贴合 Hermit 当前 kernel-first 现实
* 最贴合 v0.2 的 contract-first loop
* 最不容易把你单人的精力摊薄
* 最容易做出真正的自举提效闭环。

下一步最值钱的不是继续争论层级，而是把这套结构写成一份冻结 spec：
**角色职责、输入输出 artifact、event names、并发上限、升级条件、park/resume 规则。**
