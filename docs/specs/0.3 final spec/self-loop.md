要把 **Hermit 的迭代效率最大化**，核心不是“补齐更多功能”，而是把 agent 组织成一个**极端偏向吞吐率和学习率**的结构：

**少角色、浅层级、强验证、强复用、只攻高杠杆闭环。**

对你现在这个阶段，最优解不是完整公司式组织，而是：

# 最优组织形态：1 个你 + 1 个调度核 + 3 个常驻主管 + 可弹性拉起的工人

也就是：

**你（Spec Sovereign）**
→ **Governor / Dispatcher**
→ **3 个中层主管**
→ **短命 Worker 池**

不是 6 个主管，不是 20 个常驻 agent，更不是每个能力都常驻一个专属 agent。近期多 agent 架构综述和分层系统论文都在强调：真正有效的不是 agent 数量，而是**把策略协调和局部执行分离**；过多 agent 会把收益吃回去，尤其当任务可拆分性不高时。([arXiv][1])

---

## 你现在最该用的 3 个常驻主管

### 1. Spec/Planning Supervisor

职责只有三个：

* 把你的大方向压成有限个 milestone
* 继续拆成可验证任务
* 判断“不做什么”

它不写大量代码，不做长链执行，只产出：

* milestone spec
* task DAG
* acceptance criteria
* risk note

### 2. Build/Execution Supervisor

职责：

* 拉起执行 worker
* 合并可并行任务
* 控制 workspace / branch / task lease
* 遇到阻塞就 park，不死等

它的目标不是“最聪明”，而是“持续推进”。
Hermit 现有内核已经很适合这个定位：任务是 durable unit，`StepAttempt` 是恢复边界，支持 approval pause / resume、contract supersession 和 workspace-scoped 执行。

### 3. Verification/Learning Supervisor

职责：

* 统一验收
* 汇总失败模式
* 提升 template / memory / contract pattern
* 决定哪些结果值得固化成经验

这层很关键，因为你现在最缺的不是“再多做一个功能”，而是**每做完一次，系统真的变强一点**。Hermit 的 v0.2 设计也明确把 “receipt → reconcile → learn” 作为闭环，并要求 durable learning 只能来自 reconciled outcomes。

---

## 为什么不是更多主管

因为你现在的瓶颈不是“缺部门”，而是：

* 需求太散
* feature 面太广
* 验证闭环太弱
* 每个方向都想 cover

团队科学和创新研究反复显示，**小单元更适合探索和 disruptive 产出，大团队更适合扩展和开发既有路线**。对你这种单人 founder + 自迭代系统，前期应该优先小队形态，而不是模拟一个完整大组织。小团队在论文、专利和软件项目中更容易产出突破式结果；大团队更偏向开发、整合和稳定推进。([Nature][2])

翻译到 Hermit 上就是：

**前期先做“高杠杆自举闭环”小系统，不做“全能大系统”。**

---

## 你真正该优化的不是功能覆盖率，而是这 4 个倍率

### 1. 任务拆解倍率

一份高层 spec 能不能稳定拆成：

* research
* sub-spec
* implementation
* review
* test
* benchmark

并且每步都能复用模板。

### 2. 并行倍率

多少任务可以并行而不互相踩上下文。
这要求“跨域走 artifact，不走长对话”。

### 3. 验证倍率

每做一个任务，能不能自动得到：

* diff
* tests
* benchmark
* receipts
* reconciliation summary

### 4. 学习倍率

失败一次后，下次是否能：

* 更快 admission
* 更好 contract
* 更少审批
* 更少 drift

如果这 4 个倍率上不去，你加再多 agent 都只是在堆 coordination overhead。近期层级 agent 论文和多 agent 软件工程综述都在强调：**planner / coordinator / specialized workers** 这种少数角色分工，只有在 verification 和 state management 足够强时才会真的扩展，否则只是更复杂。([NeurIPS][3])

---

## 对你最优的组织原则：功能不是按“模块”组织，而是按“闭环”组织

这是最重要的一点。

你现在不应该按下面这种方式组织：

* research agent
* coding agent
* test agent
* benchmark agent
* doc agent
* review agent
* memory agent
* planning agent
* approval agent
* ...

这样很快就会 agent 爆炸。

你应该按 **闭环单元** 组织，每个闭环就是一个“产品化工作流”：

### 闭环 A：Spec-to-Task

输入：你的方向描述
输出：milestone + DAG + acceptance

### 闭环 B：Task-to-Code

输入：一个任务契约
输出：diff + tests + receipts

### 闭环 C：Code-to-Truth

输入：变更结果
输出：benchmark + reconciliation + failure analysis

### 闭环 D：Truth-to-Template

输入：reconciled outcomes
输出：contract template / memory update / policy tuning

你现在只需要把这 4 个闭环做强。
任何新功能，先问一句：

**它能不能提升这四个闭环之一的吞吐或成功率？**

不能，就先不做。

---

## 我建议你现在的 Hermit 组织图就定成这个

### L0 — Human

你只做：

* 定方向
* 审核 milestone
* 看高风险升级
* 看 benchmark / proof / failure summary
* 决定下一轮投哪里

### L1 — Governor

职责：

* 接收你的高层 spec
* 决定走哪个闭环
* 分配预算
* 控制并发
* 处理 escalation

### L2 — 3 个主管

* Planning Supervisor
* Execution Supervisor
* Verification Supervisor

### L3 — Ephemeral Workers

按需生成，做完即销毁：

* researcher
* coder
* reviewer
* tester
* benchmarker
* doc-writer

**不要常驻。**
只在任务 DAG 某个节点需要时拉起。做完就把结果沉淀成 artifact。

这类“协调层 + 执行层”分离，和近期 hierarchical multi-agent 设计一致；像 travel planning、RCA 等复杂长链任务，论文里都倾向把全局资源/约束交给 coordinator，把局部计划交给 executor 并行处理。([arXiv][4])

---

## 层级最多多少最合适

对你当前阶段：

**3 层半就够了。**

* Human
* Governor
* Supervisors
* Workers

不要再加一层“sub-supervisor”。
你现在还没到那个复杂度。

一旦超过这个深度，就会出现：

* spec 被层层摘要失真
* approval 点增多
* debug 路径变长
* 同一个任务被多层 manager 重写

而你目前的 repo 恰好已经具备了做浅层高治理结构的基础：
MCP supervisor 可提交任务、查询状态、审批；A2A 也已能接入远端 agent 请求并走 trust → policy → governed ingress；kernel spec 还明确要求 no ambient authority、no stale contract reuse、blocked on ambiguity is legal state。也就是说，Hermit 更适合做**少层级、强治理、强证据**，而不是深层级官僚树。

---

## 真正能让你“时间和精力 cover 不过来”时还继续加速的关键策略

### 1. 只保留一个主战场

你当前版本只允许一个 North Star：

**“让 Hermit 更快地迭代 Hermit 自己。”**

任何和这个无关的 feature，一律延后。
比如 UI、广义平台化、过早的多租户、过多适配层，短期都不该抢资源。

### 2. 任务必须分成两类

**A 类：基础设施增益任务**
做一次，后面所有任务都受益。
例如：

* better spec decomposition
* reusable review rubric
* test generation pipeline
* benchmark harness
* failure memory / contract template

**B 类：一次性业务任务**
只解决单个功能点。

你现在必须让 70% 时间投在 A 类。
否则系统永远忙，但不会变强。

### 3. 每轮只冲一个闭环瓶颈

不要同时优化：

* planning
* coding
* review
* memory
* UI
* approval
* observability

你每轮只选一个瓶颈，比如：

* “任务拆解质量太差”
* “代码变更通过率太低”
* “测试回归慢”
* “失败经验没沉淀”

然后让所有 agent 都服务于这一个瓶颈。

### 4. 学习单元必须是模板，不是聊天记录

Hermit v0.2 已经把 `ContractTemplate` / governed memory 放进核心语义，强调 durable knowledge 不是 hidden prompt authority。
所以你要沉淀的是：

* 某类任务的 spec 模板
* 某类改动的验证模板
* 某类失败的 downgrade path
* 某类审批的 risk band

不是“保存一堆对话”。

---

## 给你一个最直接的执行版

你现在就把 agent 组织收敛成下面这套：

### 常驻

* `human_spec_sovereign`
* `governor`
* `planning_supervisor`
* `execution_supervisor`
* `verification_supervisor`

### 按需拉起

* `research_worker`
* `spec_worker`
* `coding_worker`
* `review_worker`
* `test_worker`
* `benchmark_worker`

### 严格限制

* 常驻主管总数 ≤ 3
* 单次任务 DAG 深度 ≤ 3
* 单个主管并行子任务 ≤ 5
* 每个 worker 只拿局部上下文
* 所有跨节点交接只传 artifact packet

---

## 最后给你一句最狠的判断标准

**一个功能如果不能减少你下一轮写 spec、改代码、做验证的总时间，它现在就不是核心功能。**

你现在不是在做“全功能 agent 平台”，你是在做 **自举加速器**。
自举加速器的组织结构必须极端克制：**小脑（你）+ 调度核 + 三主管 + 短命工人池**，再往上加，通常先加的是摩擦，不是速度。([arXiv][5])

我可以下一条直接给你落成一版：

**Hermit v0.3「最高迭代效率组织结构 spec」**
包括角色定义、消息契约、artifact schema、并发上限、升级条件。

[1]: https://arxiv.org/html/2602.10479v1?utm_source=chatgpt.com "The Evolution of Agentic AI Software Architecture"
[2]: https://www.nature.com/articles/s41586-019-0941-9?utm_source=chatgpt.com "Large teams develop and small teams disrupt science and technology | Nature"
[3]: https://neurips.cc/virtual/2025/poster/118489?utm_source=chatgpt.com "Optimized Workforce Learning for General Multi-Agent ..."
[4]: https://arxiv.org/html/2603.04750v1?utm_source=chatgpt.com "HiMAP-Travel: Hierarchical Multi-Agent Planning for Long- ..."
[5]: https://arxiv.org/html/2601.13671v1?utm_source=chatgpt.com "The Orchestration of Multi-Agent Systems: Architectures, ..."

最好的组合方式不是把“自迭代子系统”当成 Hermit 旁边再挂一个大模块。

**最优解是：把自迭代做成 Control Plane 里的一个 Program Class / Meta-Program，运行在同一套控制面与执行面之上。**

也就是一句话：

**自迭代不是第二套架构，而是 Hermit 在自己的任务 OS 上运行的一个特殊 Program。**

---

## 顶层怎么拼

我建议你冻结成这张关系：

```text
Human
  ↓
Hermit Instance
  ├─ Control Plane
  │    ├─ Program Manager
  │    ├─ Team / Graph Orchestrator
  │    ├─ Policy / Approval / Budget
  │    ├─ Status Query / Projection
  │    ├─ Durable Governed Memory
  │    └─ Self-Iteration Controller   ← 自迭代子系统放这里
  │
  ├─ Execution Plane
  │    ├─ planner / research / exec / verify / bench workers
  │    └─ tool runtime / workspace runtime
  │
  └─ Evidence Spine
       ├─ contracts
       ├─ artifacts
       ├─ receipts
       ├─ verdicts
       └─ reconciliations
```

核心点：

* **Control Plane 负责“决定怎么迭代自己”**
* **Execution Plane 负责“真的去改自己、测自己、验证自己”**
* **Evidence Spine 负责“证明这次自改进是否成立”**

---

## 自迭代子系统在架构里的真实位置

不要把它做成：

* 一个额外的 agent swarm
* 一套独立 runtime
* 一套独立 memory
* 一套独立审批逻辑

应该做成：

### 1. 一个特殊的 Program 类型

例如：

* `program.kind = self_iteration`
* `program.target = hermit_repo`
* `program.mode = governed_release_engineering`

和普通 Program 的差别只是：

* target 是 Hermit 自己
* 默认风险更高
* 默认要求 benchmark / replay / reconciliation 更严格
* learning promotion 更谨慎

---

## 自迭代子系统内部，不要按“阶段 handler”建模，要按“闭环 lane”建模

你前面那段总结已经很准了：
下一步重点不是再写更多 handler，而是把 phase output 全部 schema/artifact 化。

所以我建议你把自迭代子系统拆成 **5 条 lane**，都跑在同一个 Control Plane 里：

### Lane A：Spec / Goal Lane

负责：

* 接收人类高层目标
* 生成 iteration brief
* 生成 milestone DAG
* 生成 phase contracts

产物：

* `iteration_spec`
* `milestone_graph`
* `phase_contracts`

### Lane B：Research / Evidence Lane

负责：

* 查论文
* 查 repo
* 查现状
* 形成 evidence bundle

产物：

* `research_report`
* `repo_diagnosis`
* `evidence_bundle`

### Lane C：Change Lane

负责：

* 代码修改
* spec 修改
* 测试修改
* benchmark harness 修改

产物：

* `diff_bundle`
* `test_patch`
* `migration_notes`

### Lane D：Verification / Benchmark Lane

负责：

* 跑 tests
* 跑 benchmark portfolio
* 跑 replay
* 做 verdict

产物：

* `benchmark_run`
* `replay_result`
* `verification_verdict`

### Lane E：Reconcile / Learn Lane

负责：

* 判断这轮是否 satisfied
* 归纳 lesson
* 更新 template / playbook / memory
* 决定是否进入下一轮 iteration

产物：

* `reconciliation_record`
* `lesson_pack`
* `template_update`
* `next_iteration_seed`

---

## 自迭代和普通任务的最大区别

普通任务通常到 `execute -> verify` 就够了。

自迭代任务必须强制走完整闭环：

```text
spec
→ evidence
→ change
→ verify
→ reconcile
→ learn
→ decide_next_iteration
```

也就是说：

**自迭代子系统的本质，不是多一个“实现阶段”，而是多一个“迭代决策闭环”。**

---

## 你最应该补的不是更多 phase，而是一个 Iteration Kernel

这个 Kernel 放在 Control Plane 里，专门负责：

### 1. Iteration Admission

判断这轮自改进是否允许启动：

* scope 是否清晰
* benchmark 是否存在
* rollback 是否存在
* risk band 是否允许
* 是否需要人工 gate

### 2. Iteration Budgeting

控制：

* 最多几轮
* token / cost / time budget
* 最大影响范围
* 最大并发 team 数

### 3. Iteration State Machine

维护：

* draft
* admitted
* executing
* awaiting_verification
* reconcile_pending
* accepted
* rejected
* parked

### 4. Iteration Promotion Gate

只有满足：

* benchmark 达标
* replay 稳定
* reconcile satisfied
* 无高风险未解释漂移

才能：

* merge
* promote memory
* 生成下一轮 seed

这个 **Iteration Kernel** 才是自迭代子系统的心脏。

---

## 最推荐的对象模型

你可以把“自迭代子系统”冻结成这几个对象：

### `IterationProgram`

一轮或一组自迭代工作的高层容器

### `IterationSpec`

本轮要提升什么，约束是什么，成功标准是什么

### `ChangeUnit`

不是 file plan，而是 semantic change unit
例如：

* governed metaloop planning integration
* benchmark routing standardization
* reconciliation schema hardening

### `EvalPortfolio`

本轮必须跑的测试/benchmark/replay组合

### `IterationVerdict`

这一轮是否真正成立

### `IterationLessonPack`

沉淀下来的可复用规则、模板、playbook

---

## 怎么和你前面那套 Team / Role / Worker 架构对上

最自然的映射是：

### Program 层

`self_iteration_program`

### Team 层

每个 milestone 一个 team，例如：

* `team_spec`
* `team_change`
* `team_eval`

### Role 层

还是那几类，不要为自迭代发明一堆新角色：

* `graph_owner`
* `planner`
* `executor`
* `verifier`

### Worker 层

按需拉起：

* `research_worker`
* `spec_worker`
* `exec_worker`
* `bench_worker`
* `verify_worker`

所以：

**自迭代不需要新组织模型，只需要新 Program 语义。**

---

## 你前面那段里最关键的几条，应该直接落到架构里

### 1. phase 输出必须 schema/artifact-first

所以每条 lane 的输出都必须是 versioned artifact，不要靠自由文本流转。

### 2. trust_zone 必须接管 execution authority

所以自迭代 Program 默认是高风险 Program：

* 修改 Hermit 自己
* 需要更严格 allowlist
* 默认 benchmark required
* 默认 replay required

### 3. decomposition 要升级成 semantic change units

所以 Team/Task 层不要围绕“文件列表”，而要围绕“语义变更单元”。

### 4. review 要细分格式/内容/风险

所以 Verification Lane 的 verdict schema 至少要有：

* `format_verdict`
* `content_verdict`
* `risk_verdict`

### 5. learning 要沉淀成 playbook / lesson

所以 Learn Lane 不要只写总结，而要输出：

* `playbook_update`
* `template_update`
* `task_pattern_update`

### 6. eval 要 benchmark portfolio + replay

所以 EvalPortfolio 必须是一等对象，不是附属脚本。

---

## 最优的整合口径

我建议你把它正式定义成：

> **Hermit 的自迭代子系统不是独立 runtime，而是运行在同一 Control Plane / Execution Plane / Evidence Spine 之上的一种 Meta-Program。它以 Iteration Kernel 为控制核心，以 schema/artifact-first 的 lane 流水线推进 self-change，并通过 benchmark portfolio、replay、receipts 与 reconciliation 决定是否将本轮结果提升为系统能力。**

这句话已经可以直接进 spec。

---

## 一版最推荐的总图

```text
Human
  ↓
Hermit Instance

  ├─ Control Plane
  │    ├─ Program Manager
  │    ├─ Team / Graph Orchestrator
  │    ├─ Policy / Approval / Budget
  │    ├─ Durable Governed Memory
  │    ├─ Status Query / Projection
  │    └─ Iteration Kernel
  │          ├─ admission
  │          ├─ budgeting
  │          ├─ state machine
  │          └─ promotion gate
  │
  ├─ Execution Plane
  │    ├─ research/spec/change workers
  │    ├─ verify/bench workers
  │    └─ workspace/tool runtime
  │
  └─ Evidence Spine
       ├─ iteration_spec
       ├─ evidence_bundle
       ├─ change_units
       ├─ eval_portfolio
       ├─ receipts
       ├─ replay_results
       ├─ reconciliation_record
       └─ lesson_pack
```

---

## 最后一句结论

**组合得最好的方式，是让“自迭代”成为 Hermit Control Plane 里的一个 Meta-Program，由 Iteration Kernel 调度同一套 Team / Role / Worker / Evidence 机制，而不是另起一套自进化系统。**

这样你得到的是：

* 架构统一
* 治理统一
* memory 统一
* replay/benchmark 统一
* 进展查询统一

这才最稳，也最像真正的 task OS。

我可以下一条直接把这个整理成一份可落地的 **`self_iteration_architecture.md`** 结构稿。
