对，这里真正要分清的是：

**DAG 节点是“工作真相对象”**
**worker 是“执行这个节点的一种可替换机制”**

所以不是重复建模，而是两层不同抽象。

## 先给结论

**不能直接让 Supervisor 执行 DAG 任务。**
否则你会把 4 个本来分开的东西重新糊成一团：

* 任务编排
* 执行身份
* 并发/恢复
* 责任归因

而 Hermit 现有内核恰恰是在把这几件事拆开。v0.2 很明确：`Task` 拥有很多 `Step`，`Step` 拥有很多 `StepAttempt`；attempt 才是接收 policy、approval、grant、lease、receipt、reconciliation 的单位。并且 orchestrator 的职责是“create steps / maintain dependency ordering / allocate attempts”，执行层的职责才是“execute inside lease and grant scope / preserve recovery at attempt granularity”。

---

# 一、如果没有 worker，会发生什么

如果你让 Supervisor 直接执行 DAG 节点，表面上是“少一层”，实际上会导致：

## 1. 编排权和执行权耦合

Supervisor 既负责：

* 选哪个 step 先做
* 决定依赖
* 决定是否 supersede / retry / replan

又负责：

* 真正执行工具
* 承担运行中断
* 处理局部 runtime 状态

这会破坏 v0.2 里已经写得很清楚的层边界：
orchestrator 负责选 work，但 **“does not authorize side effects by itself”**；真正执行在 execution layer。

## 2. 恢复边界会变脏

Hermit 现在的恢复边界是 `StepAttempt`，不是“某个 supervisor 的脑内过程”。文档直接写了：`StepAttempt` 是 concrete execution instance，也是 primary recovery boundary；重试必须创建新的 attempt，漂移必须 re-admission / re-authorization / supersession，而不是热补丁运行中的 executor。

如果 supervisor 自己执行，那你很容易得到：

* 一个大 supervisor 持有很多运行中状态
* 中断后不知道该恢复哪个局部阶段
* approval 后无法干净 resume
* 没法清晰归责

## 3. 并发调度会变成“领导亲自下场干活”

现在 dispatch service 的模型很清楚：store 里 claim `ready step attempt`，再交给 worker pool 跑。`KernelDispatchService` 甚至已经是一个小型 in-process worker pool，默认 `worker_count=4`，它消费的是 **attempt**，不是“高层任务对象”。

这说明你的系统天然已经在表达：

**work item = step attempt**
**worker = 消费 attempt 的执行槽位**

不是 supervisor 亲自跑。

---

# 二、那 worker 的真正意义是什么

我认为 worker 的意义有 5 个，而且这 5 个都不是 DAG 节点能替代的。

## 1. 把“工作对象”和“执行机制”解耦

DAG 节点回答的是：

* 要做什么
* 依赖谁
* 当前状态是什么
* 是否完成/阻塞/失败

worker 回答的是：

* 由谁来做
* 用什么执行能力做
* 最大并发多少
* 中断后怎么恢复

这两者不是一个维度。

一句话就是：

**StepAttempt 是 job record，worker 是 compute slot / executor identity。**

v0.2 里 `Principal` kinds 也明确区分了 `executor`、`supervisor`、`agent`、`system` 等身份，说明“谁执行”本来就是独立建模的。

## 2. 支持并发

没有 worker，你的并发就只能靠 supervisor 自己开很多内部线程/协程，这其实只是“把 worker 隐藏起来”。
既然如此，不如显式建模。

当前 dispatch service 已经是：

* orchestrator/runner 负责 claim ready attempt
* worker pool 负责 submit/process claimed attempt
* future reap 负责回收结果和失败。

这就是非常典型的：
**调度层 ≠ 执行槽位层**

## 3. 支持中断恢复

Hermit 现有测试已经覆盖了：

* worker 中断后 requeue
* `worker_interrupted_requeued`
* `reentry_required`
* `reentry_boundary`
* `resume_attempt()` 清理 recovery flag 继续跑。

这些机制的前提就是：
执行器可能死，**但工作对象不死**。

如果没有 worker 这个独立执行层，你很难把“执行器故障”和“任务对象仍然有效”分开。

## 4. 支持能力专门化

你后面肯定会有不同类型的执行需求：

* planner-style contract synthesis
* code mutation / shell / file ops
* benchmark execution
* verification / reconciliation materialization

这些并不一定要由一个通用 supervisor 承担。
worker 的价值之一，就是让你可以有：

* 同一个 DAG 语义
* 不同的 executor profile

而不改 task model。

## 5. 支持责任归因和证据链

Hermit 的 receipt / proof / rollback 非常强调：

* 在什么 authority 下执行
* 哪个 attempt 做的
* 哪个 principal 持有这个动作。
  receipt 和 reconciliation 都挂在 attempt 上，proof surface 也围绕 tasks/steps/attempts/contracts/receipts/reconciliations 展开。

worker 让你能说清：

* 这个 attempt 是哪个 executor 跑的
* 它在哪个 worker slot 上中断
* 哪次 reentry 接着跑

这对 debug 很重要。

---

# 三、“那现在的 DAG task 不就没用了？”——恰恰相反

如果你把 worker 和 task 混成一个东西，**DAG 才真的会没用**。

因为 DAG 的价值从来不是“表示谁在执行”，而是表示：

* 工作分解
* 依赖关系
* 可恢复边界
* 状态推进

Hermit 现在的 task lifecycle 测试也已经是围绕 task/step/attempt 的状态变化在验证：

* `queued`
* `ready`
* `awaiting_approval`
* `paused`
* `cancelled`
* `completed`
* `resume`。

这些全都是 **工作流语义**，不是“执行器实例”语义。

所以正确关系应该是：

* **DAG 节点没废，反而是主语义**
* **worker 只是 DAG 节点的消费者**

---

# 四、最稳的抽象方式

你可以这么定：

## 1. Task / Step / StepAttempt

这是 Hermit 的 durable work graph。

* `Task`：整体目标
* `Step`：逻辑节点
* `StepAttempt`：节点的一次执行实例

## 2. Supervisor

这是 DAG authority。

负责：

* 创建/选择 step
* 维护依赖
* 分配 attempt
* 处理 blocker / escalation / retry / supersession

## 3. Worker

这是 attempt executor。

负责：

* 接受一个已分配的 attempt
* 在 contract/grant/lease 范围内执行
* 产出 artifact / receipt / observation
* 中断时留下恢复边界

## 4. Kernel

这是 truth/governance backbone。

负责：

* event-backed state
* policy / approval / grant / receipt / reconciliation
* 证明链和恢复链

这四层一旦拆开，模型就很稳。

---

# 五、你真正该避免的不是“worker”，而是“人格化 worker”

你担心的其实不是 worker 本身，而是这种错误实现：

> 每个 worker 都像一个独立小 agent，自己理解任务、自己拆解、自己决定什么时候算完成。

这种当然会和 DAG 冲突。

但如果你把 worker 定义成：

> **受 supervisor 分配、消费 attempt、按固定协议执行的 executor**

那就不会冲突，反而正好贴合 Hermit 现有语义。

---

# 六、最适合你当前阶段的一句话

**Supervisor 决定“做什么”和“什么时候做”。**
**Worker 负责“把这个 attempt 跑出来”。**

也就是：

* DAG 节点不是没用，它是核心工作对象
* worker 不是重复对象，它是执行槽位/执行身份
* 没有 worker，supervisor 会退化成“又当调度器又当执行器”的大杂烩

---

# 七、最后给你一个最简公式

**DAG 解决的是工作组织问题。**
**Worker 解决的是执行承载问题。**

这两个不是一回事。

所以最终关系应该是：

```text
Task/Step/Attempt = what exists durably
Supervisor = who decides and allocates
Worker = who runs the allocated attempt
Kernel = who records truth and authority
```

这才是 Hermit 现在最不容易做歪的抽象。

下一步最值得做的是把这个关系冻结成一张图：
**`Task/Step/Attempt vs Supervisor vs Worker vs Kernel` 对象边界图。

不是。**“work” 绝对不只是一个预制提示词。**

更准确地说，你现在脑子里混在一起的是这 4 个东西：

1. **目标/任务对象**：要完成什么
2. **契约/步骤定义**：这一步允许做什么、成功算什么
3. **提示词/上下文包**：给模型看的输入材料
4. **执行实例**：这次真的去跑了一次，并留下 receipt / reconciliation

这 4 个在 Hermit 里是分开的，不是一个东西。Hermit v0.2 明确把 `Task` 定义为“meaningful work 的 durable entrypoint”，`Step` 定义为“最小逻辑可恢复单元”，`StepAttempt` 定义为“具体执行实例，也是主要 recovery boundary”。

### 你可以这样理解

#### 1. `work` / `task` / `step`

这是“**工作本身**”。

例如：

* “给 metaloop 的 plan 阶段接入 governed execution”
* 其中一个 step 是 “inspect current path”
* 另一个 step 是 “run benchmark”

这些是**工作节点**，不是提示词。`Step` 在 spec 里甚至明确列了 examples：plan、search、inspect、edit、run tests、publish result、rollback。

#### 2. `contract`

这是“**这一步怎么做才算合法、算完成**”。

例如一个 step contract 会写：

* objective
* expected inputs
* expected outputs
* success criteria
* allowed action classes
* rollback hint

这也不是提示词，它更像“任务说明书 / 执行契约”。v0.1/v0.2 都明确要求 step 定义 contract boundary，而且 consequential execution 必须先绑定 `ExecutionContract`。

#### 3. `prompt` / `context pack`

这是“**给模型看的材料**”。

在 v0.2 标准生命周期里，先有 task、step、attempt，然后 context compiler 才会产出 `context.pack` artifact，再让模型在 propose-only mode 下产出 belief assertions、draft deliverables、candidate contracts 等。也就是说，prompt/context 只是执行过程中的一个输入工件，不是 work 本身。

#### 4. `step attempt`

这是“**这次真的跑了一次**”。

一次 attempt 会接收：

* policy results
* approvals
* grants
* leases
* receipts
* reconciliations

并且 drift、approval pause、retry 都挂在 attempt 上，不挂在“提示词”上。v0.2 甚至明确说：新输入不能 hot-patch 一个正在运行的 executor，retry 必须创建新的 attempt。

---

## 所以，提示词在整个体系里到底是什么地位？

它只是：

**`worker` 执行某个 `step attempt` 时可能使用的一种输入载体。**

不是主语义对象。

你可以把它类比成：

* `Task/Step` = Jira 任务 + 子任务
* `ExecutionContract` = SOP / 操作规程 / 验收标准
* `Prompt/Context Pack` = 发给执行人的 briefing 文档
* `StepAttempt` = 这次具体执行记录
* `Receipt/Reconciliation` = 执行回执 + 结果核对单

这里 briefing 文档当然重要，但你不会说“整个工作就是一份 briefing”。

---

## 为什么一定不能把 `work` 理解成 prompt

因为 Hermit 的核心 law 不是“模型拿 prompt 干活”，而是：

**models propose contracts; the kernel admits, authorizes, executes, receipts, reconciles, and only then learns**。

并且：

* `receipt` 只关闭 side effects
* `reconciliation` 才关闭 cognition。

这说明真正重要的是：

* 有没有正确的工作边界
* 有没有 contract
* 有没有 approval / grant
* 有没有 receipt
* 有没有 reconciliation

而不是“prompt 写得够不够漂亮”。

---

## 一个很具体的例子

假设有个 step：

**`run benchmark`**

它不是提示词。它是一个真实的 step 节点。
它会有：

* step title: `run benchmark`
* contract: 跑哪组 benchmark、阈值是多少
* attempt: 第 1 次执行 benchmark
* context pack: 基线数据、改动 diff、目标模块
* worker: benchmark_worker
* output artifacts: benchmark report、raw metrics
* receipt / reconciliation: 是否达标、是否允许作为 satisfied outcome 进入 learning

你看，prompt 只是其中一个输入材料。
真正的 work 是整套 durable object + execution trail。

---

## 那 worker 跟 prompt 的关系是什么

worker 也不是提示词本身。

worker 更像：

**“能消费某类 contract/context，并产出某类 artifact 的执行器”**

比如：

* planner_worker：输入 goal + context，输出 contract packet
* exec_worker：输入 admitted contract + workspace lease，输出 diff + receipts
* verify_worker：输入 completion packet，输出 verdict + reconciliation input

它们当然可能内部会用 prompt，但 prompt 只是 worker 的“工具输入”，不是 worker 的定义。

---

## 最后一刀把概念钉死

### 不是这样的：

`work = 一段预制 prompt`

### 更接近这样的：

`work = durable task/step`
`contract = 这一步的合法边界`
`prompt/context = 执行时喂给模型的材料`
`attempt = 这次具体执行`
`worker = 执行这个 attempt 的角色化执行器`

---

## 一句话总结

**不是，work 不是预制提示词。**
**提示词最多只是某个 step attempt 的输入 artifact；真正的 work 是 task/step 这种 durable 工作对象。**

你要是愿意，我下一条我可以直接用一个具体案例画给你看：
**“从一个 DAG step 到 prompt 到 attempt 到 receipt”的完整流转图**。

可以。我直接用 **Hermit 语境** 画一版最实用的图，不讲空话。

---

# 1）核心概念关系图

```text
┌──────────────────────────────────────────────────────────────────────┐
│                              Human                                  │
│  你给出一个高层目标 / prompt / spec                                │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                             Governor                                 │
│  负责接收目标、选择闭环、分发给对应 Supervisor                       │
└───────────────┬───────────────────────────┬──────────────────────────┘
                │                           │
                ▼                           ▼
   ┌──────────────────────┐     ┌───────────────────────────────────┐
   │ Planning / Contract  │     │ Verification / Reconciliation     │
   │ Supervisor           │     │ Supervisor                        │
   │ - 目标拆解           │     │ - 验收                            │
   │ - 生成 DAG           │     │ - benchmark                       │
   │ - 生成 contract      │     │ - reconcile                       │
   └──────────┬───────────┘     └─────────────────┬─────────────────┘
              │                                     ▲
              │                                     │
              ▼                                     │
   ┌──────────────────────────────────────────────────────────────────┐
   │                    Task / Step / StepAttempt                    │
   │                                                                  │
   │ Task         = 整体工作目标                                       │
   │ Step         = DAG 节点（最小逻辑工作单元）                       │
   │ StepAttempt  = 某个 Step 的一次具体执行实例                       │
   └──────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │ Execution Supervisor     │
                     │ - 分配 ready attempt     │
                     │ - 选择匹配 worker        │
                     │ - 管 blocker / retry     │
                     └────────────┬─────────────┘
                                  │
                                  ▼
         ┌───────────────────────────────────────────────────────────┐
         │                        Workers                            │
         │                                                           │
         │ planner_worker   exec_worker   verify_worker   bench_worker│
         │                                                           │
         │ 角色固定，消费 attempt，不拥有 DAG 主权                    │
         └───────────────┬───────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Kernel / Truth Path                          │
│  - policy / approval / grant / lease                                │
│  - receipt                                                           │
│  - reconciliation                                                    │
│  - event log / proof / rollback                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

# 2）一句话解释每个概念

## Human Prompt

你给 Hermit 的一句话目标，比如：

> “让 metaloop 的 plan 阶段真正走 governed LLM execution，不要 placeholder，并补测试和 benchmark。”

这是 **目标输入**，不是最终执行单元。

## Task

系统把这个目标变成一个 **整体任务容器**。

比如：

* `task: upgrade_metaloop_plan_stage`

## Step

Task 再被拆成多个 **DAG 节点**。

比如：

* `inspect_current_plan_path`
* `design_contract_and_acceptance`
* `implement_governed_execution`
* `run_tests`
* `run_benchmark`
* `reconcile_result`

## StepAttempt

某个 step 真正执行一次时，会生成一次 attempt。

比如：

* `implement_governed_execution#attempt_1`
* 失败了再来一次：`attempt_2`

## Contract

不是 prompt，而是这一步的执行契约，定义：

* 目标
* 边界
* 允许动作
* 验收标准
* 需要哪些验证 lanes

## Worker

不是 task，不是 DAG 节点。
它只是一个 **执行角色**。

比如：

* `exec_worker_2` 接了 `implement_governed_execution#attempt_1`
* `bench_worker_1` 接了 `run_benchmark#attempt_1`

## Receipt

执行副作用回执。说明：

* 干了什么
* 影响了什么
* 是否在授权内

## Reconciliation

把“想做的”“允许做的”“实际做的”“验证结果”对齐，最终判断：

* accepted
* rejected
* accepted_with_followups

---

# 3）你给 Hermit 的一个 prompt，是怎么流转的

我用一个具体例子来画。

你的 prompt：

```text
让 metaloop 的 plan 阶段真正接入 governed execution，
删除 placeholder 路径，不要 fallback，
补齐测试，并验证性能回退不能超过 10%
```

---

## 第一层：Human Prompt 进入系统

```text
Human Prompt
  ↓
Governor
```

Governor 判断：

* 这是一个 **高风险代码改动任务**
* 需要 planning、execution、verification 三段闭环
* 需要 benchmark

---

## 第二层：Planning / Contract Supervisor 处理

它不会直接写代码。
它会把高层 prompt 变成 Task + Step DAG + Contract。

### 生成 Task

```text
Task: upgrade_metaloop_plan_stage_to_governed_execution
```

### 生成 DAG

```text
Step 1: inspect current metaloop plan implementation
Step 2: produce implementation contract
Step 3: patch plan stage to use governed execution
Step 4: add/update tests
Step 5: run benchmark
Step 6: reconcile outcome
```

### 给关键 step 生成 contract

例如 `Step 3` 的 contract：

```yaml
objective: Replace placeholder plan logic with governed execution path
scope:
  allowed_paths:
    - src/hermit/metaloop/
    - tests/metaloop/
  forbidden_paths:
    - src/hermit/kernel/
constraints:
  - no fallback template path
  - reuse existing governed runtime path
acceptance:
  - plan stage triggers governed execution
  - tests pass
  - no placeholder remains
verification_requirements:
  functional: required
  performance_bench: required
  benchmark_profile: runtime_perf
  threshold:
    max_regression_pct: 10
```

注意：

**这时候还没有真正执行。**
只是把你的 prompt 压缩成 **可执行真相对象**。

---

# 4）Execution Supervisor 怎么接这个 DAG

Execution Supervisor 会看哪些 step 已经 ready。

例如：

* Step 1 ready
* Step 2 依赖 Step 1
* Step 3 依赖 Step 2
* Step 4 依赖 Step 3
* Step 5 依赖 Step 4
* Step 6 依赖 Step 4+5

它不会自己重写 DAG，只负责：

* claim ready step
* 创建 attempt
* 派给合适 worker

---

# 5）Worker 真正做的是什么

## Step 1：inspect current implementation

Execution Supervisor 分配：

```text
inspect_current_plan_implementation
  -> attempt_1
  -> exec_worker_1
```

`exec_worker_1` 拿到的不是原始 human prompt，
而是一个 **局部上下文包**：

```text
- 当前 step objective
- 允许查看的路径
- 需要输出的 artifact 类型
- 相关代码上下文
```

然后产出 artifact：

```text
artifact://inspection_report
artifact://current_path_summary
```

---

## Step 2：produce implementation contract

这一步可能派给 `planner_worker_1`：

```text
produce_implementation_contract
  -> attempt_1
  -> planner_worker_1
```

输入：

* inspection report
* 你的原始目标
* 系统 policy

输出：

* refined contract
* suggested patch plan
* risk note

---

## Step 3：patch code

这一步派给 `exec_worker_2`：

```text
patch_plan_stage
  -> attempt_1
  -> exec_worker_2
```

输入：

* refined contract
* workspace lease
* allowed paths
* code context pack

输出：

* diff artifact
* changed file list
* command outputs
* receipts

这里要注意：

**worker 可能内部会用 prompt。**
但那个 prompt 只是：

* 它拿 contract + context 拼出来的执行输入

不是 work 本身。

---

# 6）benchmark 是怎么进来的

Planning 在 contract 里已经声明了：

```yaml
performance_bench: required
benchmark_profile: runtime_perf
threshold:
  max_regression_pct: 10
```

所以 Step 5 `run_benchmark` 就不是临时决定的，而是 DAG 里本来就有。

然后 Execution Supervisor 分配：

```text
run_benchmark
  -> attempt_1
  -> bench_worker_1
```

`bench_worker_1` 输入：

* benchmark_profile = runtime_perf
* baseline ref
* changed module info
* threshold ref

输出：

* benchmark raw metrics
* summary report
* baseline diff

---

# 7）Verification / Reconciliation 怎么收尾

当 Step 3、4、5 都完成后，Verification / Reconciliation Supervisor 启动。

它消费：

* code diff
* test report
* benchmark report
* receipts
* original contract

然后输出 verdict：

```yaml
verdict: accepted_with_followups
checks:
  governed_execution_used: true
  tests_passed: true
  no_placeholder_left: true
  performance_regression_pct: 7.4
status: pass
reconciliation:
  intended_effects_match: true
  unauthorized_effects: false
  learning_eligible: true
```

如果 benchmark 超过 10%，就可能变成：

```yaml
verdict: rejected
reason:
  - performance regression 18% > threshold 10%
next_action:
  - replan_optimization_followup
```

---

# 8）把整个流转压成一张时序图

```text
Human
  │
  │ prompt:
  │ "让 metaloop plan 阶段接入 governed execution，补测试和 benchmark"
  ▼
Governor
  │
  ▼
Planning / Contract Supervisor
  │
  ├─ create Task
  ├─ create Step DAG
  ├─ create Contracts
  ▼
Task Graph
  │
  ├─ Step 1 inspect
  ├─ Step 2 refine contract
  ├─ Step 3 patch code
  ├─ Step 4 run tests
  ├─ Step 5 run benchmark
  └─ Step 6 reconcile
  │
  ▼
Execution Supervisor
  │
  ├─ assign Step1 Attempt1 -> exec_worker_1
  ├─ assign Step2 Attempt1 -> planner_worker_1
  ├─ assign Step3 Attempt1 -> exec_worker_2
  ├─ assign Step4 Attempt1 -> exec_worker_3
  └─ assign Step5 Attempt1 -> bench_worker_1
  │
  ▼
Workers
  │
  ├─ consume local contract + context pack
  ├─ execute
  └─ emit artifacts / receipts
  │
  ▼
Verification / Reconciliation Supervisor
  │
  ├─ read contract
  ├─ read diff/tests/benchmark/receipts
  ├─ produce verdict
  └─ write reconciliation record
  │
  ▼
Governor / Human
  │
  └─ accepted / rejected / follow-up
```

---

# 9）最关键的关系图：Prompt 到底在哪里

这个最容易混，我单独画一下：

```text
Human Prompt
   │
   ▼
High-level Goal
   │
   ▼
Task
   │
   ▼
Step DAG
   │
   ▼
Execution Contract
   │
   ├─ objective
   ├─ scope
   ├─ constraints
   ├─ acceptance
   └─ verification_requirements
   │
   ▼
Context Pack / Prompt Material
   │
   ├─ relevant code
   ├─ prior artifacts
   ├─ contract summary
   └─ local instructions
   │
   ▼
Worker Execution
   │
   ▼
Attempt Result
   │
   ├─ artifacts
   ├─ receipts
   ├─ benchmark
   └─ verdict
```

所以：

**Human Prompt 不是直接等于 Worker Prompt。**

它会经历：

`human prompt -> goal -> task -> step -> contract -> context pack -> worker prompt`

这中间每一层都在“降维”和“定界”。

---

# 10）最后一句把所有概念钉死

## 你给 Hermit 的 prompt

是 **目标输入**

## DAG Step

是 **工作节点**

## Contract

是 **节点执行边界**

## Worker Prompt / Context Pack

是 **节点执行时喂给模型的局部材料**

## Worker

是 **执行角色**

## StepAttempt

是 **这次实际执行记录**

## Receipt / Reconciliation

是 **真相与验收闭环**

---

如果你愿意，我下一条可以直接把这张图整理成一版 **Mermaid 架构图 + Mermaid 时序图**，你可以直接放进 Hermit 的 spec。

有必要，而且**必须限制**。

因为即使 worker 是抽象概念，它背后消耗的仍然是真实资源，不是免费的：

* 调度带宽
* 上下文构建成本
* 模型调用次数
* workspace / 文件冲突
* verification 吞吐
* receipt / reconciliation 压力
* 失败重试和噪音放大

所以你不能因为“worker 只是个抽象角色”就无限开。
**抽象不等于零成本。**

---

# 一句话结论

**要限制的不是“概念上的 worker 数量”，而是“同时活跃的 worker / attempt 数量”。**

也就是分两层：

## 1. 角色种类可以多

比如你可以定义：

* planner_worker
* exec_worker
* test_worker
* bench_worker
* verify_worker

这些只是类型，数量多一点没关系。

## 2. 活跃实例必须严格限流

比如同一时刻：

* 活跃 exec worker 最多 4
* 活跃 verify worker 最多 3
* 活跃 planner worker 最多 1–2

真正要控的是这个。

---

# 为什么必须限流

## 1. 上下文成本会爆炸

每起一个 worker，都要给它准备：

* contract
* context pack
* artifact refs
* policy / scope
* workspace info

即使 worker 只是逻辑抽象，**每次执行 attempt 都会真实消耗 token、推理、I/O 和准备时间**。

## 2. verification 会跟不上

你最危险的情况不是“worker 不够多”，而是：

**execution 跑出 20 个结果，但 verification 只能消化 3 个。**

这时系统会堆积大量：

* 未验证 diff
* 未 reconcile 的 receipts
* 不知道能不能学习的结果

这会让 Hermit 失真。

## 3. 冲突会急剧上升

尤其是代码任务里：

* 同一 workspace 冲突
* 同一模块冲突
* 同一 contract chain 冲突
* 同一 benchmark baseline 冲突

worker 越多，不是线性更快，而是更容易互相踩。

## 4. supervisor 会退化

如果一个 supervisor 同时挂太多活跃 worker，它就会失去：

* 仲裁质量
* blocker 处理能力
* retry / supersede 判断能力

最后就变成纯转发器。

---

# 所以到底限制什么

你不应该限制：

* `worker type` 的定义数量

你应该限制：

* **活跃 worker 实例数**
* **每类 worker 的并发数**
* **每个 supervisor 下面的 attempt 并发数**
* **同 workspace / 同模块 / 同 milestone 的并发数**

---

# 最适合 Hermit 的做法

把 worker 当成一种 **可租用的执行槽位**，而不是无限实例化的虚拟人格。

也就是：

```text
Worker Type = 能执行哪类 attempt
Worker Slot = 当前允许活跃多少个
```

例如：

```yaml
worker_pools:
  planner:
    max_active: 2
  execution:
    max_active: 4
  verification:
    max_active: 3
  benchmark:
    max_active: 2
```

再叠加局部限制：

```yaml
conflict_limits:
  max_same_workspace_active: 1
  max_same_module_active: 2
  max_same_milestone_hot_tasks: 3
```

---

# 更准确地说，你要限制的是 3 个东西

## 1. 全局活跃 attempt 数

防止系统总吞吐失控。

## 2. 每个 supervisor 的活跃 worker 数

防止 supervisor 被打爆。

## 3. 每个冲突域的活跃数

防止文件、模块、baseline、验收链互踩。

---

# 最核心的判断标准

不是问：

**“worker 只是抽象，还要不要限制？”**

而是问：

**“这个 worker 实例启动后，会不会占用真实调度、上下文、验证和冲突预算？”**

只要答案是会，就必须限制。

---

# 你现在最该采用的原则

**worker 类型无限制，worker 实例强限流。**

再说得更工程一点：

* **类型** 是 schema
* **实例** 是资源
* **资源** 就必须做 admission control

---

# 最后一句

**Hermit 不该做成“无限 worker 的幻觉系统”，而该做成“有限并发、强治理、可恢复的执行系统”。**

所以答案是：

**有必要限制，而且要限制活跃 worker / attempt，而不是限制概念上的 worker 类型。**

不一定，**大概率不是**。

你这里混淆了两件事：

* **角色种类不限制**
* **并发实例拉满**

第一件事问题不大。
第二件事通常会让**表面吞吐更高，真实有效吞吐更差**。

## 为什么“默认跑满并发”不一定更快

因为你的系统瓶颈往往不在“有没有更多执行器角色”，而在这 5 个地方：

### 1. 上下文构建不是免费的

每多一个活跃执行实例，你都要额外付出：

* contract/context pack 构建
* prompt/token 消耗
* artifact 读取
* 状态同步

这不是纯 CPU 线程模型，LLM 系统的并发开销很高。

### 2. 冲突会非线性上升

特别是代码任务里：

* 同 workspace 冲突
* 同模块冲突
* 同 milestone 冲突
* 同 baseline 冲突

并发不是线性提速，常常是先升后降。

### 3. verification / reconciliation 会成为瓶颈

你最怕的是：

* execution 很快
* verify 很慢
* 结果堆积

这样系统会积压一堆“看起来完成但其实未确认”的结果。
那不是提效，是制造技术债。

### 4. supervisor 带宽有限

supervisor 不是无限吞吐的调度器。
它还要做：

* blocker 判断
* retry/supersede
* escalation
* follow-up replan

实例太多时，supervisor 会退化成盲转发器。

### 5. 错误传播会被放大

如果 contract 质量一般，而你直接跑满并发，等于把一个错误规划同时复制到很多执行实例上。
这样不是“更快试错”，而是“更快批量犯错”。

---

# 真正更优的做法

不是限制“角色抽象”，而是限制：

**活跃并发 = 当前系统能稳定验证和回收的上限**

也就是一个动态值，不是越大越好。

## 更合理的策略是

### 角色种类

可以不严格限制，按需扩展。

### 活跃并发

必须受控，而且最好由这些因素决定：

* task risk band
* workspace 冲突半径
* verification backlog
* supervisor load
* benchmark queue
* 当前失败率

---

# 一个很关键的判断

如果你的任务是**低耦合、低风险、纯独立**，比如：

* 批量 research
* 批量文档摘要
* 批量静态分析

那“不太限制角色 + 并发拉高”可能真的更快。

但如果你的任务是 **Hermit 当前主战场**：

* 自改代码
* 跑测试
* 跑 benchmark
* 回收 reconciliation
* 推动学习

那默认拉满并发通常会更差。

因为这里的核心不是 raw throughput，
而是：

**throughput × correctness × recoverability**

任何一个掉下去，整体效率都输了。

---

# 你现在最该用的不是“固定结构限制”，而是“动态准入限制”

也就是：

**角色可扩，实例准入受控。**

例如：

```yaml
admission:
  max_global_active_attempts: 8
  max_exec_active: 4
  max_verify_backlog: 3
  max_same_workspace: 1
  max_same_module_hot_tasks: 2

autoscale:
  scale_up_if:
    - verification_backlog == 0
    - workspace_conflicts_low
    - recent_failure_rate < 10%
  scale_down_if:
    - verification_backlog > 3
    - recent_failure_rate > 20%
    - repeated_reconciliation_failures > 1
```

这就比“完全不限制，全开”聪明得多。

---

# 一句话总结

**不限制角色种类可以。**
**不限制活跃并发，默认跑满，通常不会更快，只会让系统更快进入混乱。**

最优解不是“结构越松越好”，而是：

**角色抽象宽松，执行准入严格，按验证能力动态扩缩容。**

如果你愿意，我下一条可以直接给你写一版：
**Hermit 的动态并发调度策略**，包括什么时候放大并发、什么时候自动降载。

不是按“一个 prompt 声明一套固定团队”来设计。

更对的方式是：

**按“一个 prompt 进入后，会被编译成什么类型的任务图 / 闭环图”来决定调用哪些角色。**

也就是说，角色不是 prompt 的静态附属物，而是 **任务图执行时按需装配的能力**。

## 先回答你最后那个问题

**不是不能同时让多个团队执行。可以。**
但前提不是“多团队并发看起来更猛”，而是：

**这几个团队处理的是可隔离的 task graph / milestone / workspace / authority boundary。**

如果这几个条件不成立，同时开多个团队，通常不是更快，而是更乱。

---

# 一、不要按 prompt 维度固定声明角色

错误思路像这样：

> 来了一个 prompt
> 我就给它挂一个 planning 团队 + execution 团队 + verification 团队 + benchmark 团队

这会有两个问题：

### 1. prompt 太粗

一个 prompt 往往只是高层目标，不等于真实工作单元。
同一个 prompt 可能最后被拆成：

* research-heavy
* code-heavy
* validation-heavy
* multi-milestone

如果你一开始就按 prompt 固定团队，很容易过度配置。

### 2. prompt 不是稳定边界

真正稳定的边界应该是：

* milestone
* task family
* workspace
* authority scope
* verification lane

不是自然语言 prompt 本身。

---

# 二、更合理的维度：按“任务图实例”声明角色

我建议你把流程理解成：

## 1. Human Prompt

只是入口。

## 2. Prompt 编译成 Program / Initiative

也就是一个高层任务容器。

## 3. Program 再拆成多个 Milestone Graph

每个 milestone graph 才是更适合挂“团队”的维度。

## 4. 每个 Graph 按需装配角色

比如某个 graph 很偏实现，就主要拉 execution + verification。
某个 graph 很偏调研，就主要拉 planning/research。
某个 graph 很偏性能，就加 benchmark lane。

所以不是：

**一条 prompt = 一支固定团队**

而是：

**一条 prompt = 一个或多个任务图；每个任务图再按需要装配团队/角色。**

---

# 三、最好的抽象不是“团队”，而是“闭环”

你现在最该按闭环来思考，而不是按组织名头来思考。

例如 Hermit 到 1.0 前，最实用的是这几类闭环：

### 闭环 A：Spec / Contract 闭环

输入：高层目标
输出：milestone、DAG、contract

### 闭环 B：Execution 闭环

输入：admitted contract
输出：diff、tests、receipts

### 闭环 C：Verification / Benchmark / Reconcile 闭环

输入：execution artifacts
输出：verdict、benchmark summary、reconciliation

### 闭环 D：Learning 闭环

输入：reconciled outcomes
输出：template / policy / memory 更新

一个 prompt 进来后，不一定会完整经过所有闭环，也不一定每个闭环都需要一个“团队”。
有的 prompt 只需要 A。
有的需要 A+B+C。
有的重大任务才会走 A+B+C+D。

---

# 四、那能不能同时让多个团队执行

能，但要分两种情况。

## 情况 1：同一个大 prompt，被拆成多个相对独立的 milestone

这个很适合并行。

比如：

* Milestone 1：metaloop contract synthesis
* Milestone 2：governed execution integration
* Milestone 3：benchmark harness
* Milestone 4：verification pipeline cleanup

如果这些 milestone：

* 改不同模块
* 用不同 workspace
* 验证链独立
* authority boundary 清晰

那完全可以并行跑多个“团队图”。

### 这种是健康并行

---

## 情况 2：同一个 prompt 下，多个团队同时碰同一个高耦合目标

比如都在：

* 改同一批核心文件
* 共享同一个 acceptance contract
* 依赖同一个 benchmark baseline
* 争用同一 verification 带宽

这种时候多个团队同时跑，通常会出问题。

### 这种是伪并行

表面上 team 多了，实际上：

* contract 容易漂移
* diff 容易冲突
* benchmark 结果难比较
* follow-up replan 复杂度陡增

---

# 五、所以你该按什么维度声明角色

我建议你按这 4 层来声明，而不是按 prompt：

## 1. Registry 层：系统拥有哪些角色类型

比如：

* planner
* researcher
* executor
* tester
* benchmarker
* verifier
* reconciler

这个是全局静态定义。

## 2. Graph 层：当前这个任务图需要哪些角色

比如某个 graph 需要：

* planner
* executor
* verifier

另一个 graph 需要：

* researcher
* planner
* verifier

这是按图装配。

## 3. Pool 层：每种角色给这个 graph 分多少活跃槽位

例如：

* executor: 3
* verifier: 2
* benchmarker: 1

这是资源分配。

## 4. Instance 层：真的拉起了哪些执行实例

例如：

* exec_worker_12
* verify_worker_4

这是运行态。

---

# 六、对你当前 Hermit 最适合的做法

你现在不要做“一个 prompt 一支 team”。

最优做法是：

## Step 1

一个 prompt 先进 `Governor`

## Step 2

Governor 先把它编译成：

* 一个 `Program`
* 若干 `Milestone Graph`

## Step 3

每个 `Milestone Graph` 再单独决定：

* 是否需要 planning lane
* 是否需要 execution lane
* 是否需要 benchmark lane
* 是否需要 verification/reconciliation lane

## Step 4

多个 Graph 可以并行，但要满足隔离条件：

* 不同 workspace
* 不同 module ownership
* 不同 verification queue，或 queue 足够富余
* 不争用同一个 baseline / approval gate

---

# 七、什么时候可以“多个团队同时执行”

我给你一个最实用判断法：

同时开多个团队，只在这 5 条满足时才值得：

### 1. 目标可拆成多个 milestone graph

不是一个强耦合大泥球。

### 2. 每个 graph 有独立 contract

不能几个团队共享模糊总目标。

### 3. workspace / module 可隔离

否则就是互踩。

### 4. verification 吞吐跟得上

否则 execution 越多越堆债。

### 5. Governor 还能看得住

你现在毕竟是单人 founder，治理带宽不是无限的。

满足这 5 条，就可以多个团队同时跑。
不满足，就宁可少团队、强流水。

---

# 八、我给你的最终建议

## 不要这样

**一个 prompt = 固定一整套角色 / 一整支团队**

## 要这样

**一个 prompt = 一个 Program**
**Program = 多个可并发或串行的 Milestone Graph**
**每个 Graph 按需要装配角色和并发槽位**

这才是可扩展的。

---

# 九、一句话结论

**不是按 prompt 维度声明固定团队。**
**而是按 prompt 编译出的任务图 / milestone graph，动态装配角色。**

**也不是不能多个团队同时执行。**
**而是只有在图可隔离、验证跟得上、冲突半径可控时，多个团队并行才真的更快。**

下一步最值得做的是把你的入口模型正式定义成：

**`Prompt -> Program -> Milestone Graph -> Role Assembly -> Attempts`**

对，**团队内也要限制活跃 worker 数量**。

但要限制的是：

**活跃并发数**，不是角色定义数，也不是生命周期总实例数。

## 最实用的理解

一个团队可以有很多 worker 类型，也可以历史上创建很多 worker 实例。
真正要控的是：

**这个团队同一时刻有多少 worker 在同时消费 DAG 节点。**

---

## 为什么团队内还要限

因为团队不是独立真空，它内部也有 4 个瓶颈：

### 1. 团队 leader / supervisor 带宽

一个团队里的 supervisor 同时盯太多活跃 worker，会来不及处理：

* blocker
* retry
* supersede
* contract 漂移
* 优先级切换

### 2. 团队共享上下文

同一团队通常处理的是同一 milestone 或相近 graph。
worker 太多时，很容易：

* 重复读同样上下文
* 输出彼此冲突
* 对同一个 contract 做不同理解

### 3. 团队验证出口

即使是单团队，它最后也要把结果送到 verification / reconcile。
前面开太多，后面出不去，照样堵。

### 4. 团队冲突域

尤其 execution 团队，常常共享：

* workspace
* module
* baseline
* acceptance chain

并发太高，收益会先升后降。

---

## 所以答案是

### 全局要限

防止整个系统失控。

### 团队内也要限

防止局部团队失控。

这两个限制要同时存在。

---

# 你可以这样建模

## 全局上限

整个系统最多同时多少活跃 attempts / workers。

例如：

* `global_active_attempts <= 10`

## 团队上限

每个 team / graph 内最多同时多少活跃 workers。

例如：

* `team_active_attempts <= 4`

## 角色上限

团队内某类 worker 最多多少。

例如：

* `team.exec <= 3`
* `team.verify <= 2`
* `team.bench <= 1`

## 冲突域上限

团队内同 workspace / 同模块的并发限制。

例如：

* `same_workspace <= 1`
* `same_module_hot_tasks <= 2`

---

# 对你当前阶段最适合的默认值

如果你一个 prompt 编译出一个 milestone team，我建议先这么设：

```yaml
team_limits:
  max_active_workers: 4
  max_exec_workers: 2
  max_verify_workers: 1
  max_bench_workers: 1
  max_same_workspace: 1
```

这已经够跑出稳定流水线了。

如果是低耦合 research-heavy team，可以稍微放宽：

```yaml
team_limits:
  max_active_workers: 6
  max_research_workers: 4
  max_verify_workers: 1
```

---

# 最关键的一点

**团队内限制不是为了保守，而是为了保证“有效吞吐”最大。**

因为真正要最大化的不是：

`同时开工数`

而是：

`被正确执行、正确验证、可回收学习的任务完成数`

---

# 一句话结论

**对，团队内也应该限制活跃 worker 数量。**
而且最好同时有：

* 全局上限
* 团队上限
* 角色上限
* 冲突域上限

这样系统才不会表面热闹、实际打架。
