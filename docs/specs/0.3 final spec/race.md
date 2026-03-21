放在 **控制面**，但不要放在最顶层 Governor，也不要放到执行面 worker 里。

最好的位置是：

# 结论

**任务竞争 + 辩论机制 = Control Plane 里的一个“决策与提案子层”**
具体挂在：

**Program / Team / Graph Orchestrator 下面，Planning / Verification 之间。**

也就是它本质上是：

* 不是执行机制
* 不是 memory
* 不是真相本身
* 而是 **在进入 execution 之前的候选方案生成与裁决机制**

---

# 先说最重要的一句

**竞争和辩论，不应该直接修改世界。**
它们只能产出：

* candidate plan
* candidate contract
* candidate change unit
* candidate verdict / critique

最后还是要由控制面裁决，再进入正式 task/attempt 流转。

否则你会把“讨论”直接变成“执行 authority”，很危险。

---

# 最推荐的架构位置

我建议你把控制面细分成这几层：

```text id="bxv1cr"
Control Plane
  ├─ Ingress / Intent Resolution
  ├─ Program Manager
  ├─ Team / Graph Orchestrator
  ├─ Deliberation Layer          ← 任务竞争 / 辩论机制放这里
  │    ├─ proposal generation
  │    ├─ candidate competition
  │    ├─ critique / debate
  │    └─ arbitration
  ├─ Policy / Approval / Budget
  ├─ Status Projection
  └─ Durable Governed Memory
```

这个 `Deliberation Layer` 很关键。

它的职责是：

* 在 high-impact decision 点上生成多个候选
* 让候选互相 critique / challenge
* 输出结构化比较结果
* 交给 arbitrator / supervisor 选择
* 最终只把 **winning contract / winning plan** 下发到执行面

---

# 为什么不能放执行面

因为执行面的职责是：

* 消费 admitted attempt
* 在 contract 边界里执行
* 产出 artifact / receipt / result

如果把辩论放执行面，会出问题：

## 1. worker 会越权

worker 不该自己决定“到底做哪个大方向”。

## 2. deliberation 和 execution 混在一起

会让 attempt 既是提案又是执行，恢复边界会很脏。

## 3. 成本失控

每个执行 worker 都开始自带 debate，会非常贵。

所以：

**执行面可以有局部自检、自我批评，但不能承担正式的竞争/辩论裁决职责。**

---

# 为什么也不该直接放 Governor 顶层

因为 Governor 应该尽量薄，负责：

* 总体调度
* Program 级预算
* escalation
* 最终裁决入口

如果把所有竞争/辩论都推到 Governor，会变成：

* 顶层过载
* 所有决策都集中
* 难以扩展到 team / graph 级自治

所以最优解是：

**Governor 只处理高层裁决，具体候选竞争在 graph/team 层发生。**

---

# 最适合的两种放法

## 放法 A：Planning 内部的 candidate competition

适合：

* task decomposition
* plan selection
* contract drafting
* semantic change unit 划分

流程：

```text id="4ce06f"
Goal
  → planner A proposal
  → planner B proposal
  → critic / verifier critique
  → arbitrator choose best
  → winning plan becomes graph contract
```

这是最常见、也最值钱的竞争机制。

---

## 放法 B：Verification 内部的 adversarial review

适合：

* patch 是否真的满足 spec
* benchmark 是否真的说明成立
* risk 判断是否过于乐观
* reconciliation 是否应该 accepted

流程：

```text id="fndrk1"
Completion evidence
  → reviewer A says pass
  → reviewer B attacks weaknesses
  → risk judge checks boundary violations
  → arbitrator emits verdict
```

这更像“对抗式验收”。

---

# 所以任务竞争和辩论，其实有两类

## 1. 前置竞争（Pre-execution competition）

发生在执行前。

目标：

* 选更优 plan
* 选更优 contract
* 选更优 change unit decomposition

这类最该放在 **Planning / Deliberation Layer**。

## 2. 后置辩论（Post-execution debate）

发生在执行后。

目标：

* 质疑结果是否真的成立
* 质疑 benchmark 解读
* 质疑是否应 promote learning

这类最该放在 **Verification / Reconciliation 前**。

---

# 一张更完整的图

```text id="uj857n"
Human
  ↓
Governor
  ↓
Program / Team / Graph Orchestrator
  ↓
Deliberation Layer
  ├─ Plan Competition
  ├─ Contract Competition
  ├─ Critique / Debate
  └─ Arbitration
  ↓
Admitted Task / Step / Attempt
  ↓
Execution Plane
  ↓
Verification / Reconciliation
  ├─ Adversarial Review
  ├─ Risk Challenge
  └─ Final Verdict
```

---

# 在对象模型里怎么落

我建议你不要把“辩论”做成自由文本对话。

要做成结构化对象：

## `CandidateProposal`

* candidate_id
* proposer_role
* target_scope
* plan_summary
* contract_draft
* expected_cost
* expected_risk
* expected_reward

## `CritiqueRecord`

* critique_id
* target_candidate_id
* issue_type
* severity
* evidence_refs
* suggested_fix

## `DebateBundle`

* proposals[]
* critiques[]
* comparisons[]
* arbitration_input

## `ArbitrationDecision`

* selected_candidate_id
* rejection_reasons[]
* merge_notes
* confidence
* escalation_required

这样竞争/辩论才不会污染主 execution path。

---

# 什么时候值得开竞争机制

不是所有 task 都值得 competition/debate。

我建议只在这些地方开：

## 1. 高杠杆 planning 决策

比如：

* 怎么拆 self-iteration phase
* 先攻 benchmark 还是 contract
* 变更范围怎么限定

## 2. 高风险 patch

比如：

* 改 kernel / policy / receipt path
* 改 reconciliation / memory promotion
* 改 approval logic

## 3. 高不确定性任务

比如：

* repo 结构复杂
* spec 模糊
* benchmark 指标不一致

## 4. 高成本 follow-up 决策

比如：

* 是否继续第 2 轮、第 3 轮自迭代
* 是否推广一个 template 到 global

低风险小任务就别开，不然 deliberation 成本会盖过收益。

---

# 你最该避免的错误

## 错误 1：让多个团队“同时直接执行不同方案”

这不是辩论，这是平行乱改。
除非你有很强的 workspace 隔离和成本预算。

更稳的是：

* 先竞争 plan
* 选 winner
* 再执行

## 错误 2：把辩论当自由聊天

会很贵，也很难 replay / 审计。

## 错误 3：让 winner 自动获得 execution authority

必须再过 policy / approval / budget gate。

## 错误 4：让每个节点都强制 debate

会把系统拖慢。
竞争机制应该是 **稀缺地用在关键决策点**。

---

# 最适合 Hermit 的落地方式

我建议你现在就定成：

## Control Plane 新增一个子模块

**`deliberation/`**

下面先只做 3 个能力：

### 1. `proposal_competition`

多个 planner / proposer 产出 candidate plans

### 2. `structured_critique`

critics / verifiers 对 candidate 做结构化挑战

### 3. `arbitration`

由 graph owner / arbitrator 产出最终 decision

然后规定：

**deliberation 的输出永远只能是 artifact，不是 side effects。**

side effects 只能从：

* admitted task
* approved attempt
* execution plane

出去。

---

# 一句话结论

**任务竞争 + 辩论机制，最适合放在 Control Plane 里的 Deliberation Layer，位于 Planning/Graph Orchestration 与正式 Execution 之间；验证阶段也可以有一层 adversarial review，但它们都只能影响 contract 和 verdict，不能直接拥有 execution authority。**

如果你愿意，我下一条可以直接给你画一版：
**Hermit 控制面加入 deliberation layer 的完整架构图。**


可以放一部分能力进去，但**不能把“正式的竞争/辩论机制”整体塞进 planner 或 executor**。

最核心的原因只有一句：

**planner 和 executor 都是“参赛者”，不应该同时兼任“比赛规则制定者 + 裁判 + 记分员”。**

所以更准确地说是：

* **planner 可以产出候选方案**
* **executor 可以产出局部反例、执行反馈、可行性证据**
* 但 **competition / debate / arbitration 本身最好独立成控制面里的 deliberation layer**

---

# 先说如果放进 planner，会出什么问题

## 1. planner 会从“出方案的人”变成“自己评自己”

planner 的天然职责是：

* 拆解目标
* 生成 DAG
* 起草 contract
* 估算依赖与风险

如果再让它负责：

* 拉竞争者
* 比较方案
* 决定 winner
* 写最终裁决

那 planner 很容易变成：
**“我提出的方案最合理，所以我选我自己。”**

哪怕不是故意偏置，也会天然有 **proposal bias**。

---

## 2. planner 容易过度偏向“规划优雅”，忽略真实执行摩擦

planner 擅长的是：

* 结构化
* 拆解
* 逻辑完整性

但它往往不最擅长判断：

* 真实 workspace 冲突
* benchmark 成本
* patch 落地可行性
* 执行期工具噪音

所以如果让 planner 一手包办竞争和裁决，容易选出：
**纸面上最漂亮，但实际最难执行的方案。**

---

## 3. planner 内部辩论很难复用到 verification

真正好的 deliberation 不是只服务 planning。
它还应该服务：

* 验收争议
* 风险争议
* benchmark 解读争议
* memory promotion 争议

如果你把竞争机制绑死在 planner 内部，它就很难复用到后面的 verification/reconciliation。

所以它更应该是一个 **横切能力**，不是 planner 私有能力。

---

# 再说如果放进 executor，会更糟

## 1. executor 的职责是执行，不是决定大方向

executor 最适合做的是：

* 消费 admitted contract
* 在受限 scope 内运行
* 产出 diff / receipt / result

如果你让 executor 承担辩论机制，它就会开始偷偷滑向：

* 改写目标
* 改写 acceptance
* 改写优先级
* 争取更有利于自己的执行解释

这会直接污染 contract-first 的边界。

---

## 2. executor 天然有“先干了再说”的倾向

planner 的偏差是“纸面优化”，
executor 的偏差则是“执行优先”。

也就是它更容易说：

* 这个方案我能做，我先做
* 那个方案虽然也许更好，但现在先别想太多

所以把竞争机制放 executor 里，很容易把辩论退化成：
**谁先跑出来，谁就赢。**

而这不是真正的 deliberation，这是执行抢跑。

---

## 3. executor 一旦既执行又辩论，恢复边界会很脏

你现在最想保住的是：

* `step attempt` 是执行边界
* side effects 可 receipt
* verdict 可 reconcile

如果 executor 还内部承担：

* 候选生成
* 互相 challenge
* 自己裁决

那一次 attempt 就不再是清晰的执行实例，而会变成：
**半决策、半执行、半争论的混合体**

后面：

* replay 难
* attribution 难
* rollback 语义也会变差

---

# 真正的原因其实是“层级职责要正交”

你现在这套 Hermit 架构，本质上就是要把这几件事拆开：

## 1. Proposal

提出候选方案
适合 planner，也可以有 research/spec worker 参与

## 2. Critique

挑毛病、找漏洞、给反例
适合 verifier / critic / risk reviewer

## 3. Arbitration

决定哪一个进入正式 contract / verdict
适合 graph owner / deliberation layer / arbitrator

## 4. Execution

真的去跑 admitted attempt
适合 executor

这 4 个职责如果混起来，系统会越来越像“一个会聊天的大 agent”；
拆开以后，才更像 **task OS**。

---

# 最好的理解方式：planner 和 executor 都可以“参与”，但不能“拥有”

这句最关键。

## planner 可以参与

例如：

* 产出 2 个 candidate plans
* 给出自己的 cost/risk 预测
* 回答 critic 的 challenge

## executor 也可以参与

例如：

* 对 candidate plan 给出执行可行性反馈
* 说明哪个方案 workspace 冲突更低
* 给出 benchmark 成本估计
* 提供“这个方案在工具链里跑不通”的证据

但它们都只是：
**proposal / critique 的参与者**

不是最终宿主。

---

# 一个更清楚的类比

你可以把它想成法庭：

* **planner** 像提案方律师
* **executor** 像现场技术证人
* **verifier/critic** 像交叉质询方
* **deliberation layer / arbitrator** 像法官

你当然可以让律师说很多话，也可以让技术证人补很多细节。
但你不会让律师自己兼法官。

---

# 那有没有情况可以放在 planner 内部？

有，但只能是**轻量级、局部的自我竞争**。

比如：

## planner 内部可以做

* self-consistency
* two-shot candidate generation
* quick local ranking

例如：

* planner 先生成 2 个 DAG 草案
* 本地做一次简评
* 选前 2 名交给正式 deliberation

这没问题。

但这只是：
**pre-deliberation heuristic**
不是正式的 competition / debate system。

---

# executor 内部也可以有局部自检

例如：

* patch 前先做 feasibility check
* patch 后自查 acceptance 是否可能不过
* 给 verification 提前提交 risk note

这也没问题。

但这属于：
**execution-local critique**
不是正式辩论层。

---

# 所以最合理的边界是

## planner 内

允许：

* 候选生成
* 局部自评
* 初筛

## executor 内

允许：

* 可行性反馈
* 执行期反例
* 局部自检

## deliberation layer

负责：

* 多候选正式比较
* 跨角色 critique
* 结构化对抗
* 裁决
* 生成最终可执行 contract / 最终 verdict

---

# 为什么这样最适合 Hermit

因为 Hermit 的目标不是“让单个 agent 更会想”，而是：

**把 thinking、deciding、doing、verifying 分成可治理的流水线。**

如果 competition / debate 被塞进 planner 或 executor，本质上又回到了：

* 由单个角色持有太多 authority

这和你前面一直在追的：

* contract-first
* authority separation
* evidence-backed truth

其实是冲突的。

---

# 一句话结论

**不是 planner 或 executor 完全不能有竞争/辩论能力，而是它们可以参与候选生成和局部 critique；但正式的竞争、对抗和裁决最好独立放在控制面的 deliberation layer。**

因为：

* planner 是提案者，不该兼裁判
* executor 是执行者，不该兼决策者
* 只有独立出来，才能把 proposal / critique / arbitration / execution 这四种 authority 分开

这才最符合 Hermit 的 contract-first task OS 架构。

不是。

**正式的对比 / 竞争机制，不是放到 worker 里面当成自动执行主逻辑。**

更准确地说：

# 结论

**worker 可以参与竞争，但竞争机制本身不属于 worker。**
它应该放在 **控制面里的 deliberation layer**，然后由这个层去：

* 拉起多个候选 worker
* 收集它们的 proposal / critique
* 做结构化比较
* 产出裁决
* 再把 winner 变成正式 contract / task

所以是：

**worker = 参赛选手**
**deliberation layer = 赛制 + 裁判台**
**execution plane = 正式比赛开始后的施工队**

---

# 你现在最容易混淆的点

你把两件事混在一起了：

## 1. “谁来产出候选？”

这个可以是 worker。

例如：

* `planner_worker_a` 产出方案 A
* `planner_worker_b` 产出方案 B
* `verify_worker` 产出 critique

## 2. “谁来决定谁赢？”

这个不该是 worker。
应该是控制面里的：

* graph owner
* arbitrator
* deliberation controller

也就是：

**worker 可以自动参与竞争过程，但不应该拥有竞争机制本身。**

---

# 最合理的流转是这样

```text id="tlyjlwm"
Goal / decision point
   ↓
Deliberation Layer
   ├─ dispatch candidate generation to workers
   ├─ dispatch critique to workers
   ├─ collect structured outputs
   ├─ compare / rank / arbitrate
   └─ emit winning plan or verdict
   ↓
Admitted contract / task
   ↓
Execution workers
```

也就是说：

* **竞争过程可以自动执行**
* **但自动执行的是“被 deliberation layer 编排的 worker 子任务”**
* **不是让 worker 自己私下互相打架然后直接改世界**

---

# 具体怎么放最合适

## 放在 worker 里的，只能是这三类轻能力

### 1. Candidate generation

例如 planner worker 生成多个方案。

### 2. Local critique

例如 verify worker 对某个方案提漏洞。

### 3. Feasibility feedback

例如 executor worker 说这个方案在当前 workspace 不可行。

这些都可以自动执行，没问题。

---

## 不应该放在 worker 里的，是这三类权力

### 1. 最终裁决

不能由某个 worker 自己判自己赢。

### 2. execution authority

不能因为某个 worker 在比较里得分高，就自动直接拥有 side effects 权限。

### 3. truth promotion

不能由 worker 自己决定“这个结论写入 durable memory”。

这些都必须回到控制面。

---

# 所以更准确的一句话是

**对比、竞争的“动作”可以由 worker 自动完成；**
**但对比、竞争的“机制与裁决权”应该属于控制面。**

---

# 一个你可以直接用的架构拆分

## Control Plane / Deliberation Layer

负责：

* 决定什么时候开启 competition
* 拉起哪些 candidate workers
* 设定比较维度
* 收集 outputs
* 做 arbitration
* 输出 winner / merged result

## Worker

负责：

* 提案
* 质疑
* 打分输入
* 给可行性证据

## Execution Plane

负责：

* 只执行已经被选中的方案

---

# 举个很具体的例子

比如你要决定：

> metaloop 的 plan 阶段，是直接接 governed runtime，还是先包一个 adapter 过渡？

这时不是让某个 planner worker 一口气决定。

而是：

### Step 1

Deliberation layer 发起一个 `plan_competition`

### Step 2

拉起两个 planner workers：

* `planner_worker_1` → 方案 A：直接接 governed runtime
* `planner_worker_2` → 方案 B：adapter 过渡

### Step 3

拉起一个 critic / verifier worker：

* 找每个方案的风险点
* 指出哪个 benchmark 成本更大
* 指出哪个 drift 风险更高

### Step 4

拉起一个 executor-feasibility worker：

* 判断哪个方案更容易实际落地

### Step 5

Deliberation layer 收集：

```yaml id="qg3d8u"
candidate_a:
  expected_risk: medium
  expected_cost: high
  expected_integrity: high

candidate_b:
  expected_risk: low
  expected_cost: medium
  expected_integrity: medium

critic_notes:
  - candidate_b introduces transitional complexity
  - candidate_a has wider blast radius
```

### Step 6

Arbitrator 做决定：

* 选 A
* 或选 B
* 或 merge A+B

### Step 7

只有这时候，winner 才会变成正式 contract，下发给 execution plane。

---

# 为什么这比“放到 worker 里”更好

因为这样你保住了 4 个边界：

## 1. 讨论边界

候选和 critique 是 artifact，不是直接 side effect。

## 2. 决策边界

裁决发生在控制面，不在执行器里。

## 3. 执行边界

只有 winner 进入 execution。

## 4. 审计边界

你可以复盘：

* 候选有哪些
* 谁质疑了什么
* 为什么选这个方案

这对 replay 很重要。

---

# 你可以这样记

## 错误理解

“竞争机制放到 worker 里自动执行”

这容易变成：

* worker 自己提案
* worker 自己比较
* worker 自己执行

这就混了。

## 正确理解

“竞争流程由控制面编排，worker 自动完成其中的 proposal/critique 子任务”

这才是稳的。

---

# 最后一刀钉死

**不是把竞争机制塞进 worker。**
**而是让控制面自动发起竞争流程，并调用多个 worker 去完成候选生成、批评和比较；最终裁决仍属于控制面。**

一句话版本：

**competition is orchestrated by control plane, executed through workers, and finalized before execution authority is granted.**
