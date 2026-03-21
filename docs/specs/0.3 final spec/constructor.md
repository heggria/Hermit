对，**已经很接近了**。
但我建议你做 3 个关键修正，不然后面还是会混：

---

# 你这版，改成下面这套更稳

```text
人
  ↓
Hermit Instance
  ↓
Program / Initiative
  ↓
Team / Milestone Graph
  ↓
Roles
  - owner
  - planner
  - executor
  - verifier
  - benchmarker（可并入 verifier）
  ↓
Worker Pool / Slots
  ↓
Task
  ↓
Step (DAG node)
  ↓
StepAttempt
```

---

# 为什么要改这 3 个地方

## 1）`team` 上面最好再加一层 `Program / Initiative`

因为一个人给 Hermit 的 prompt，往往不只会产生一个 team。
更常见的是：

* 一个高层目标
* 被编译成一个 `Program`
* Program 下有多个 `Milestone Graph / Team`

比如：

**Program: metaloop 自迭代升级**

下面拆成：

* Team A：contract/planning 重构
* Team B：execution integration
* Team C：verification/benchmark

这样才方便后面多 team 并行。

---

## 2）`task` 下面必须再拆 `step / attempt`

这是最关键的。

因为真正扔进 worker 槽位里的，严格来说**不是整个 task**，而是：

* `ready step`
* 或更精确一点：`step attempt`

也就是说：

### `Task`

是整体任务容器

### `Step`

是 DAG 节点

### `StepAttempt`

才是某个节点的一次具体执行实例

所以如果你只写：

> worker（执行槽位）
> task（往槽位里面扔的任务）

会有点太粗。
更准确的是：

> worker slot 里跑的是 `StepAttempt`

---

## 3）`role` 和 `worker` 不是一层东西

你现在写成：

* 角色：owner、planner、executor、verifier
* worker（执行槽位）

这个方向是对的，但最好明确一下：

### Role

是职责抽象

### Worker

是这个角色下的活跃执行实例 / 槽位

例如：

* `executor` 是 role
* `exec_worker_1` 是 worker instance

---

# 我帮你整理成一版正式关系

## 第一层：人

你负责：

* 提目标
* 设边界
* 审高风险决策
* 看结果

---

## 第二层：Hermit Instance

这是整个运行时控制面。

负责：

* 接收目标
* 管理 program / team / task graph
* 调度执行
* 管理 proof / receipt / reconciliation

---

## 第三层：Program / Initiative

这是一个高层目标容器。

例如：

* `metaloop_self_iteration_upgrade`
* `taskos_v1_bootstrap`

它的作用是把“一句 prompt”变成一个可持续推进的工作域。

---

## 第四层：Team / Milestone Graph

这是最适合挂“团队”的层级。

每个 team：

* 有自己的 workspace
* 有自己的 graph
* 有自己的局部目标
* 尽量和别的 team 隔离

你说的：

> workspace 硬隔离，互不干扰，避免 worktree 开销

这个思路总体对。
更准确一点说，是：

**尽量以 team 为单位做执行隔离和上下文隔离。**

但“避免 worktree 开销”不一定总是第一目标，第一目标还是：

* 降低冲突
* 提高可恢复性
* 提高清晰归因

如果你有比 worktree 更轻的 workspace abstraction，也可以。

---

## 第五层：Roles

建议 1.0 前先冻结成：

* `owner`
* `planner`
* `executor`
* `verifier`

其中：

* `benchmarker` 可以先并到 `verifier`
* `researcher` 可以先并到 `planner`
* `reconciler` 可以先并到 `verifier`

### owner

不是人类 owner。
而是 team 内负责：

* 维护局部目标
* 控制 DAG 推进
* 处理升级/阻塞
  本质上接近你前面说的 supervisor。

如果你怕和“Human owner”混淆，我建议改名为：

* `team_lead`
* 或 `graph_owner`

会更清楚。

---

## 第六层：Worker Pool / Slots

这是角色的执行承载层。

例如：

* `planner_worker_1`
* `exec_worker_1`
* `exec_worker_2`
* `verify_worker_1`

这里要限制的是：

* 活跃 worker 数
* 每类角色并发数
* 同 workspace / 同模块冲突域

---

## 第七层：Task / Step / StepAttempt

### Task

整体任务容器
例如：

* “把 plan stage 接入 governed execution”

### Step

DAG 节点
例如：

* inspect current flow
* patch code
* run tests
* run benchmark
* reconcile

### StepAttempt

真正进入 worker 的执行实例
例如：

* `patch_code#attempt_1`
* `run_benchmark#attempt_2`

---

# 所以你那版，可以修成这样

```text
人
  ↓
Hermit Instance
  ↓
Program / Initiative
  ↓
Team / Milestone Graph
  - workspace isolation
  - local context boundary
  - local role assembly
  ↓
Roles
  - graph_owner
  - planner
  - executor
  - verifier
  ↓
Worker Pool / Slots
  ↓
Task
  ↓
Step (DAG node)
  ↓
StepAttempt (dispatched into worker slots)
```

---

# 还有一个非常关键的补充

你这版里还少了一个隐藏但很重要的东西：

## Contract / Artifact Layer

因为 team、role、worker、task 之间不是靠“口头理解”连起来的，
而是靠：

* contract
* artifact
* receipt
* verdict
* reconciliation

所以如果要再完整一点，应该是：

```text
人
  ↓
Hermit Instance
  ↓
Program
  ↓
Team / Milestone Graph
  ↓
Roles
  ↓
Worker Pool
  ↓
Task
  ↓
Step
  ↓
StepAttempt
  ↓
Artifacts / Receipts / Verdicts / Reconciliation
```

这个“证据层”其实是 Hermit 的灵魂。

---

# 最后给你一个判断

## 你的理解方向是对的

而且已经接近一个可以冻结的 1.0 前抽象了。

## 我建议你最终冻结成这版

不要停在：

* worker
* task

而要停在：

* **worker slot**
* **step attempt**

因为这才是最贴合 Hermit 当前内核语义的一层。

---

一句话总结：

**对，但建议改成：**
**人 → Hermit Instance → Program → Team/Graph → Roles → Worker Slots → Task → Step → StepAttempt**
并在底下补一层 **Artifacts / Receipts / Reconciliation** 作为真相闭环。

我可以下一条直接把这版画成一张更正式的 Mermaid 架构图。

这时候不要把它当成“新任务”，而要把它当成：

**对现有 Program 的一次 status query / control-plane read。**

也就是说，IM 频道进来的消息，不一定都要编译成新的 Program / Team / Task。
有一类消息应该走 **控制面查询路径**。

# 你应该把 IM 输入分成 3 类

## 1. New Work

用户在发起新目标。
例如：

* “把 metaloop 升级成 governed self-iteration”
* “补一套 benchmark routing”

这类走：
`IM message -> Intent classify -> create/update Program`

## 2. Control Query

用户在问现有 Program 的状态。
例如：

* “查看 Program A 进展”
* “现在卡在哪”
* “哪些 team 在跑”
* “最近失败了什么”
* “给我一个摘要”

这类走：
`IM message -> resolve target Program -> read model / projection -> return status summary`

## 3. Control Command

用户在操作现有 Program。
例如：

* “暂停 Program A”
* “恢复 Team B”
* “只保留 verification lane”
* “提高 benchmark 优先级”

这类走：
`IM message -> resolve target Program -> emit control action`

---

# 所以你这个场景的正确处理方式

用户在 IM 里说：

> 查看某个 Program 的进展

系统应该：

## Step 1：识别这是 Program Status Query

而不是新建任务。

## Step 2：解析目标 Program

比如识别：

* Program id
* 名称
* alias
* 最近活跃 Program
* 当前 IM 会话绑定的 Program

## Step 3：读取 Program 的状态投影

不是去“问某个 worker”，也不是临时让 agent 再总结一遍。
而是读 **Program Read Model / Status Projection**。

## Step 4：返回一个面向人类的状态摘要

例如：

* Program 总体状态
* milestone 完成度
* 活跃 teams
* 当前 blocker
* 最近 receipts / verdicts
* 是否需要你审批

---

# 关键点：必须有“读模型 / 状态投影层”

这是你现在架构里很值得补的一层。

因为 Hermit 底层会有很多细碎对象：

* Program
* Team
* Task
* Step
* StepAttempt
* Receipt
* Reconciliation
* Approval
* Benchmark result

如果你每次都直接从这些底层对象临时拼，会很乱，也很慢。

所以你需要一个：

## Program Status Projection

它是个聚合读模型，专门给 IM / CLI / Dashboard 看进展。

---

# 我建议你给每个 Program 维护下面这些投影字段

```yaml id="egc8v4"
program_status:
  program_id: prog_xxx
  title: metaloop_self_iteration_upgrade
  overall_state: running | blocked | paused | completed | failed
  progress_pct: 42
  current_phase: execution
  active_teams: 2
  queued_tasks: 5
  running_attempts: 3
  blocked_items: 1
  awaiting_human: true
  latest_summary: ...
  latest_risks: ...
  latest_benchmark_status: ...
  last_updated_at: ...
```

再细一点还可以有：

```yaml id="eh32c8"
program_projection:
  milestones:
    - id: m1
      title: contract synthesis
      state: completed
    - id: m2
      title: governed execution integration
      state: running
    - id: m3
      title: benchmark & reconcile
      state: pending

  active_teams:
    - team_id: team_exec
      state: running
      workspace: ws_exec
      active_workers: 2

  blockers:
    - type: approval_required
      detail: high-risk kernel path mutation pending

  recent_events:
    - event: task_completed
    - event: benchmark_failed
    - event: retry_scheduled
```

---

# IM 查询时的流转应该是这样

```text id="jpk1bq"
IM Message
  ↓
Ingress Adapter
  ↓
Intent Classifier
  ├─ New Work
  ├─ Status Query
  └─ Control Command
        ↓
if Status Query:
  resolve Program
        ↓
  read Program Status Projection
        ↓
  optional summarizer
        ↓
  reply to IM
```

---

# 这里有一个非常重要的原则

**状态查询尽量不要走执行 worker。**

也就是说，别这样做：

* 用户问“Program 进展”
* 你临时拉起一个 planner worker
* 它去翻任务、读 receipts、再帮你总结

这会有 3 个问题：

### 1. 慢

每次都重新扫描一遍底层对象。

### 2. 不稳定

不同 worker 总结口径会漂。

### 3. 浪费

状态查询本质上是 read，不该触发重执行。

---

# 正确做法：查询走 read path，执行走 write path

你可以把 Hermit 分成两条路：

## Write Path

用于真实推进工作：

* create program
* create task
* dispatch attempt
* record receipt
* reconcile outcome

## Read Path

用于展示当前进展：

* program status
* team status
* task summary
* blockers
* approvals pending
* benchmark status

这其实非常像 CQRS 思路，但你不用搞得太重。
只要有稳定的 projection 就够了。

---

# 如果用户只说“看一下进展”，没说 Program 名怎么办

这时候 IM 层可以做 Program resolution 策略：

## 优先级建议

1. 当前会话绑定 Program
2. 最近活跃 Program
3. 当前用户最近关注的 Program
4. 如果有歧义，再列出候选

例如回复：

```text
当前你最可能是在看这两个 Program：

1. metaloop_self_iteration_upgrade
2. benchmark_routing_spec

我先给你展示最近活跃的：metaloop_self_iteration_upgrade
```

但如果你想更强约束，也可以要求：

* 每个 IM thread 绑定一个 Program
* 或消息里必须带 `@program:xxx`

这样更稳。

---

# 你应该返回什么内容给用户

不要只返回“42% 完成”。

最实用的是固定格式：

## Program 进展摘要

* 当前状态：Running / Blocked / Awaiting Approval
* 当前 phase：Planning / Execution / Verification
* 已完成 milestone：2/5
* 活跃 team：2
* 活跃 attempts：3
* 当前 blocker：xxx
* 最近一次重要结果：xxx
* 是否需要你操作：Yes/No

例如：

```text
Program: metaloop_self_iteration_upgrade

状态：Running
当前阶段：Execution
进度：2/5 milestones completed
活跃团队：Execution Team、Verification Team
当前活跃 attempts：3
阻塞项：1 个（kernel path mutation 等待高风险审批）
最近结果：
- plan stage governed path patch 已提交
- tests 已通过
- benchmark 首轮回归 12%，高于阈值 10%
下一步：
- 进行性能 follow-up patch
是否需要你处理：
- 需要审批 1 项高风险修改
```

---

# 再往前一步：支持不同粒度的查询

你后面最好支持 4 个层级的 IM 查询：

## Program 级

“查看 Program A 进展”

## Team 级

“Execution Team 现在在干嘛”

## Task 级

“Task X 为什么卡住了”

## Attempt 级

“benchmark attempt 失败原因是什么”

这样用户查进展就不会总是一个粗粒度摘要。

---

# 最推荐的对象关系补充

你现在架构里应该再加一层：

```text id="v4z6v6"
人
  ↓
IM / CLI / API Surface
  ↓
Hermit Instance
  ├─ Write Path
  │    Program / Team / Task / Attempt / Receipt / Reconciliation
  │
  └─ Read Path
       Program Status Projection
       Team Status Projection
       Task Status Projection
       Approval Queue Projection
```

这个 `Read Path` 非常关键。
没有它，你所有“查看进展”的体验都会很别扭。

---

# 一句话结论

如果你通过 IM 频道要求“查看某个 Program 的进展”，

**正确做法不是新建任务，也不是临时拉 worker 去分析，**
而是：

**把它识别成对现有 Program 的状态查询，走 read-model / status-projection 路径，直接返回 Program 的聚合进展摘要。**

如果你愿意，我下一条可以直接给你补一版：

**`IM -> Program Status Query` 的完整流转图 + Program Status Projection 字段设计。**

对，**可以这么理解，而且这是一个非常对的顶层切法**。

但我建议你说得再精确一点：

**Hermit 顶层 = 控制面（Control Plane） + 执行面（Execution Plane）**
同时再补一句：
**两者之间靠 contract / artifact / event / receipt / reconciliation 连接。**

不然很容易又退回成“一个大 agent 系统”。

---

# 最简定义

## 1. 控制面

负责决定：

* 要做什么
* 先做什么
* 谁来做
* 允许做到什么程度
* 结果怎么算完成
* 出问题怎么暂停 / 重试 / 升级

也就是：

* Program / Team / Task / DAG 管理
* role/worker 调度
* policy / approval / admission
* read model / status query
* reconciliation / learning gate

## 2. 执行面

负责真的去做：

* 调研
* 读代码
* 改代码
* 跑测试
* 跑 benchmark
* 生成 artifact
* 回传 receipt / result

也就是所有具体 attempt 的承载层。

---

# 你可以把之前那些概念直接塞进去

## 控制面包含

* Human ingress
* IM / CLI / API surface
* Governor
* Program / Initiative manager
* Team / Milestone graph manager
* Roles / worker pool manager
* Task / Step / StepAttempt lifecycle manager
* Policy / approval / grant / lease
* Status projection / query
* Reconciliation / learning decision

## 执行面包含

* planner worker
* exec worker
* verify worker
* bench worker
* 未来的 research / rollback / doc / migration worker
* 各类具体工具调用
* shell / file / test / benchmark / patch 等 effectful work

---

# 但有个关键点

**控制面不直接做 effectful execution。**
**执行面不拥有最终调度权和授权权。**

这是边界的核心。

也就是：

* 控制面可以生成 contract
* 控制面可以批准 / 拒绝
* 控制面可以分配 attempt
* 但控制面不能偷偷自己改文件、跑副作用

同时：

* 执行面可以执行 attempt
* 但执行面不能自己随意扩张 scope、改 DAG、篡改 acceptance

这条边界一旦稳住，Hermit 才会有治理感。

---

# 我建议你再补一个“真相层”的意识

虽然顶层可以分成两面，
但内部其实还有一个非常关键的脊柱：

## 真相脊柱 / Evidence Spine

贯穿控制面和执行面的中间层：

* contract
* artifact
* event log
* receipt
* verdict
* reconciliation

因为控制面不是靠“猜”执行面发生了什么，
执行面也不是靠“口头汇报”让控制面相信。

两边靠的是这条证据脊柱。

所以更完整一点可以说：

```text
Hermit
  = Control Plane
  + Execution Plane
  + Evidence Spine
```

但如果你只想做一级抽象，对外说：

**顶层分为控制面和执行面**

完全没问题。

---

# 一张最简图

```text id="z7l3f2"
Human / IM / CLI / API
          │
          ▼
   ┌───────────────┐
   │ Control Plane │
   │               │
   │ - Governor    │
   │ - Program     │
   │ - Team/DAG    │
   │ - Policy      │
   │ - Approval    │
   │ - Scheduling  │
   │ - Status Read │
   └──────┬────────┘
          │
          │ contract / event / artifact refs
          ▼
   ┌───────────────┐
   │ Execution     │
   │ Plane         │
   │               │
   │ - planner wk  │
   │ - exec wk     │
   │ - verify wk   │
   │ - bench wk    │
   └──────┬────────┘
          │
          │ receipt / artifact / verdict
          ▼
   ┌───────────────┐
   │ Reconcile /   │
   │ Truth Path    │
   └───────────────┘
```

---

# 用一句更工程化的话来钉死

**控制面负责编排、授权、观测、裁决。**
**执行面负责承载具体 step attempt 的运行。**

---

# 你现在这套最稳的顶层口径

我建议你正式用这句：

> **Hermit 是一个 contract-first 的 AI task OS，顶层由控制面与执行面组成：控制面负责任务图、治理与状态读写，执行面负责在受限 contract 下运行具体 attempts，并通过 receipts / reconciliation 回流真相。**

这句已经很像可以进 spec 的定义了。

我可以下一条直接帮你把它展开成一版：
**Hermit 顶层架构冻结稿（Control Plane / Execution Plane / Evidence Spine）**
