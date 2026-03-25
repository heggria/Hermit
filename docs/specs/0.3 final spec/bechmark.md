benchmark 不该单独再拉一个常驻 supervisor。

**到 1.0 前的最优放法是：把 benchmark 当成 `Verification / Reconciliation Supervisor` 下面的一个标准验收分支。**

也就是：

```text
Governor
  ├─ Planning / Contract Supervisor
  ├─ Execution Supervisor
  └─ Verification / Reconciliation Supervisor
       ├─ verify_worker
       ├─ benchmark_worker
       └─ reconcile_worker
```

原因很直接：Hermit 当前内核闭环不是“执行完就算结束”，而是
**observe → contract → authorize → execute → receipt → reconcile → learn**。
在这个模型里，`Receipt` 只关闭 side effects，`ReconciliationRecord` 才关闭 contract validity，而且 durable learning 只能来自 reconciled outcomes。换句话说，benchmark 本质上属于“结果是否成立”的证据链，而不是独立组织部门。

更重要的是，你仓库里其实已经有两类 benchmark/评估：

第一类是 **治理正确性 benchmark**。
`roadmap.md` 里已经把 **TrustLoop-Bench** 定义成 v0.2 的正式 benchmark，覆盖 5 个 task family、15 个测试和 7 个治理指标，比如 Contract Satisfaction Rate、Unauthorized Effect Rate、Rollback Success Rate、Mean Recovery Depth、Operator Burden 等。这个 benchmark 明显不是“独立产品线”，而是对执行结果做 formal validation 的一部分。

第二类是 **性能 benchmark**。
仓库已经有 `tests/benchmark/`，并且 `pyproject.toml` 里也显式启用了 `pytest-benchmark` 和 `benchmark` marker，所以性能基准目前是作为测试/验证体系的一部分存在，而不是一个单独控制面。

所以最合适的组织方式不是：

* 单独一个 `Benchmark Supervisor`

而是：

## 1. Planning 负责“定义 benchmark requirement”

Planning / Contract Supervisor 在 task contract 里声明：

* 这次改动需不需要 benchmark
* 跑哪类 benchmark
* pass threshold 是什么
* 和什么 baseline 比较

也就是 benchmark requirement 是 contract 的一部分，不是执行时临时想到的。

## 2. Execution 只负责“触发并产出 benchmark artifact”

Execution Supervisor 不负责解释 benchmark 结果，只负责：

* 跑指定基准
* 收集结果
* 输出 artifact
  例如：
* benchmark report
* raw metrics
* baseline diff

## 3. Verification / Reconciliation 负责“判定 benchmark 是否过关”

这层才做：

* threshold check
* regression classification
* 是否 accepted / accepted_with_followups / rejected
* 是否把结果写入 reconciliation summary
* 是否允许 learning/template promotion

这个分工最贴合你现有内核，因为 v0.2 已经把 learning gate 明确绑定到 reconciliation，而不是绑定到“代码跑通了”。

你可以把 benchmark 再细分成 3 类，但都挂在 Verification 下面：

### A. Governance benchmark

验证：

* unauthorized effect 是否为 0
* stale authorization execution 是否为 0
* rollback/recovery 指标是否达标
  这类你现在已有 TrustLoop-Bench。

### B. Performance benchmark

验证：

* CLI 启动
* kernel store append/list
* runtime I/O
* 未来的 metaloop latency / task throughput
  这类你现在已有 `tests/benchmark/` 基础。

### C. Exit-criteria benchmark

验证某个 milestone 是否真的完成，而不是“感觉差不多”。
这类其实就是把 roadmap 里的 exit criteria 机械化。当前 v0.2 已经这么做了。

所以到 1.0 前，我建议你冻结成下面这个结构：

```text
Human
  ↓
Governor

  ├─ Planning / Contract Supervisor
  │    └─ planner workers

  ├─ Execution Supervisor
  │    └─ exec workers

  └─ Verification / Reconciliation Supervisor
       ├─ verification workers
       ├─ benchmark workers
       └─ reconciliation / learning workers
```

然后定一条规则：

**benchmark 是一种 verdict evidence，不是独立权力中心。**

只有到后面你真的出现下面这些情况，才值得考虑单独拆出 Benchmark Supervisor：

* benchmark 类型暴涨，覆盖多个独立产品面
* benchmark 调度本身很复杂
* 需要长期维护大量 baseline / 环境 / dataset
* benchmark 已经成为独立瓶颈，而不是 verification 的子能力

以你现在的 Hermit 阶段，还远没到这一步。现在拆出去，只会多一个组织层和消息面。

一句话总结：

**benchmark 现在最优归属：Verification / Reconciliation Supervisor 下的专职 worker，而不是第四个常驻 supervisor。**

我可以下一条直接给你写一版：
**Hermit 1.0 前 benchmark / verify / reconcile 三者的职责边界表。**

可以，而且这里的关键不是“让 benchmark 自己很聪明”，而是把它变成 **contract-driven 的标准化验证能力**。

也就是：

**任务先被分类并写入 contract，benchmark 再按 contract 的验证声明自动选型、执行、判定。**

不是每次临时想“这次该跑什么 benchmark”。

---

## 先说任务怎么流转

最稳的流转不是：

`task -> 写代码 -> 跑一堆测试 -> 看看结果`

而是 Hermit 现在这条主闭环：

`observe -> contract -> authorize -> execute -> receipt -> reconcile -> learn`

并且 v0.2 已经把这些对象关系写得很明确了：

* `Task` 拥有 `Step`
* `Step` 拥有多个 `StepAttempt`
* 每个 attempt 绑定一个 `ExecutionContract`
* 执行之后产生 `Receipt`
* 最后由 `ReconciliationRecord` 关闭 contract validity，而不是 receipt 自己宣布任务成功

所以对你这套 supervisor 组织，推荐的任务流转应该是：

### 1. Planning / Contract Supervisor

把一个高层目标拆成 `Step`，并给每个 step 生成一个 **task contract packet**。
这里要写清：

* 任务类型
* 改动范围
* 风险等级
* 验收标准
* 需要哪些 verification lanes
* 是否需要 benchmark
* 需要哪类 benchmark

这一步在 Hermit 里本来就该发生，因为 v0.2 要求 consequential execution 必须先有 `ExecutionContract`，并且 contract admission 前必须有 `EvidenceCase` 和 `AuthorizationPlan`。

### 2. Execution Supervisor

消费 contract，拉起 worker 执行，产出：

* code diff
* test report
* benchmark input context
* receipts
* observed artifacts

Execution 不负责解释 benchmark 是否达标，只负责把“可验证结果”产出来。
因为 receipt 在 v0.2 里只是 side effects 的闭合对象，不是 cognition 的闭合对象。

### 3. Verification / Reconciliation Supervisor

按 contract 中声明的验证要求，动态选择：

* 只跑功能验证
* 功能验证 + performance benchmark
* 功能验证 + governance benchmark
* 功能验证 + benchmark + rollback check

然后产出：

* verdict
* benchmark summary
* reconciliation summary
* 是否允许 learning / template promotion

Hermit 的规范非常明确：**没有 reconciliation，就不能 durable learning；没有 reconciliation，也不能把 consequential attempt 直接判成 terminal success。**

---

# 如何让 benchmark 适配不同 task

核心答案：

**不要把 benchmark 绑定到“某个 agent”，要绑定到“某类 contract”。**

也就是先建立一个 **Benchmark Profile Registry**。

---

## 一、先做任务分类，而不是 benchmark 分类

Planning 在合成 contract 时，先把任务归到少量稳定的 task family。
你仓库里其实已经有这种思路了：TrustLoop-Bench 就是按 **task families** 来做验证，而不是按“随便一个脚本”来做。当前正式 benchmark 覆盖 5 个 task families、15 个测试、7 个治理指标。

你可以把 1.0 前的任务先收敛成这几类：

### A. Kernel governance mutation

例如：

* approval drift
* contract supersession
* reconciliation correctness
* memory invalidation
* rollback flow

这类走 **governance benchmark profile**。
TrustLoop-Bench 已经证明这是 Hermit 的强项，而且当前门槛已经有正式阈值。

### B. Runtime / performance mutation

例如：

* store/query 优化
* CLI latency
* runtime throughput
* executor overhead

这类走 **performance benchmark profile**。
仓库已经启用了 `pytest-benchmark` 和 `benchmark` marker，说明性能验证本来就应该作为标准测试通道的一部分。

### C. Surface / UX / integration mutation

例如：

* CLI 输出
* proof-export surface
* iteration script end-to-end
* MCP / A2A flow

这类主要走：

* e2e / integration
* 必要时再叠加轻量 benchmark

### D. Template / learning mutation

例如：

* contract template matching
* policy suggestion
* template degradation / invalidation

这类主要看：

* correctness
* promotion threshold
* false positive / false promotion 风险

仓库里 template learner 已经是“从 satisfied reconciliation 学习模板，并对 violation 做降级/失效”的模式，所以这类任务不一定总要性能 benchmark，但一定要有 reconciliation-aware verification。

---

## 二、每个 contract 里写入 verification lanes

不要只写 `acceptance_criteria`，还要写：

```yaml
verification_requirements:
  functional: required
  governance_bench: optional|required|forbidden
  performance_bench: optional|required|forbidden
  rollback_check: optional|required|forbidden
  reconciliation_mode: strict|standard|light
  benchmark_profile: trustloop|cli_perf|store_perf|template_quality|none
  benchmark_baseline_ref: ...
  thresholds_ref: ...
```

这样 Verification Supervisor 就不是“猜这次要不要跑 benchmark”，而是根据 contract 直接 dispatch。

这和 Hermit 的 v0.2 思路是一致的：approval、evidence、reconciliation packet 都应该尽量能从 artifact reconstruct，而不是依赖 transcript replay。

---

## 三、让 benchmark profile 成为可复用模板

你现在最需要的不是 100 个 benchmark，而是 **少量高复用 profile**。

比如先固定 4 个：

### 1. `trustloop_governance`

输入：

* governed mutation task
* affected action classes
* receipts/reconciliation expectations

执行：

* TrustLoop-Bench 子集或全集
* 验 7 个核心治理指标

当前正式指标包括：

* Contract Satisfaction Rate
* Unauthorized Effect Rate
* Stale Authorization Execution Rate
* Belief Calibration Under Contradiction
* Rollback Success Rate
* Mean Recovery Depth
* Operator Burden Per Successful Task

### 2. `runtime_perf`

输入：

* performance-sensitive module change
* baseline commit / last-known-good result

执行：

* `pytest -m benchmark`
* 指定 benchmark case
* 与 baseline 做 diff

### 3. `integration_regression`

输入：

* CLI / script / surface 任务

执行：

* integration / e2e 测试
* 可选附带耗时阈值

### 4. `template_quality`

输入：

* contract template / learning path change

执行：

* learner unit tests
* promotion / degradation correctness
* match confidence sanity checks

template learner 现在已经有：

* promotion threshold
* similarity-based match
* workspace/global scope matching
* violation degradation
* policy suggestion 逻辑，完全可以沉淀成单独 profile。

---

## 四、Benchmark 要“声明式选择”，不要“自由发挥”

我建议 Verification Supervisor 内部只做两步：

### step 1: classify

根据 contract 决定用哪个 benchmark profile

### step 2: execute profile

按 profile 的固定 runner、固定指标、固定阈值执行

也就是说 benchmark 不是让 agent 自由思考：
“这次我觉得应该测这个那个。”

而是：
“这个 task_family + mutation_scope + risk_band -> 命中 profile X -> 跑 profile X。”

这样才能保证可重复、可比较、可自动化。

---

## 五、如何保证“准确执行”

你真正要防的是 3 类错误：

### 1. 跑错 benchmark

解决方式：
**contract 里显式写 `benchmark_profile` 和 `thresholds_ref`**。

### 2. 跑了 benchmark 但结果没法比较

解决方式：
每次 benchmark 都绑定：

* baseline ref
* environment tag
* commit/task ref
* raw metrics artifact

### 3. benchmark 结果影响 learning 时不够可靠

解决方式：
benchmark verdict 不能直接进 memory。
必须先进 `ReconciliationRecord`，只有 reconciled outcome 才能推动 template promotion / policy relaxation / durable memory。
这正是 v0.2 的硬约束。

---

# 一套你可以直接落地的流转

## 任务进入

Human 给目标

## Planning / Contract

生成：

```yaml
task_family: runtime_perf
mutation_scope:
  - src/hermit/kernel/execution/
risk_band: medium
acceptance:
  - tests pass
  - no unauthorized effects
  - p95 latency regression <= 10%
verification_requirements:
  functional: required
  performance_bench: required
  governance_bench: forbidden
  rollback_check: optional
  reconciliation_mode: strict
benchmark_profile: runtime_perf
thresholds_ref: thresholds/runtime_perf/default.yaml
```

## Execution

执行代码改动，产出：

* diff artifact
* test artifact
* benchmark input artifact
* receipts

## Verification

读取 `benchmark_profile=runtime_perf`，自动调用对应 runner：

* 跑指定 benchmark suite
* 产出 raw metrics
* 对比 threshold 和 baseline
* 形成 benchmark verdict

## Reconciliation

综合：

* intended effects
* authorized effects
* observed effects
* receipted effects
* benchmark verdict
* test verdict

写入 `ReconciliationRecord`

## Learning

只有 `ReconciliationRecord.result_class == satisfied` 时，才允许：

* template reinforce
* policy suggestion
* durable memory promotion

template learner 当前就是这么设计的：从 satisfied reconciliation 学习模板，对 violated outcome 做 degradation / invalidation。

---

# 最后一层关键建议

你现在不要追求“benchmark 能适配无限多 task”。

最优策略是：

**先把 task family 压到很少，给每个 family 配一个稳定 benchmark profile。**

先做到这 4 个映射就够了：

* `governance_mutation -> trustloop_governance`
* `runtime_perf_mutation -> runtime_perf`
* `surface_integration_mutation -> integration_regression`
* `learning_template_mutation -> template_quality`

这样你得到的是：

* 任务流转清晰
* benchmark 自动选型
* threshold 有依据
* 结果能进入 reconciliation
* learning 不会失真

一句话总结：

**任务通过 contract 流转，benchmark 通过 profile 适配，准确性通过“contract 声明 + 固定 runner + reconciliation gate”来保证。**

我可以下一条直接给你写成一份 **`benchmark-routing-spec.md`**，把 `task_family -> benchmark_profile -> runner -> thresholds -> reconciliation action` 全部列出来。

对，**你这个理解更接近正确抽象了**。

**worker 不应该等同于 task。**
更准确地说：

**worker 是“有固定职责边界的执行身份 / 执行器类型”，task/step/attempt 才是“这一次具体工作”的 durable 对象。**

在 Hermit 现在的对象模型里，真正的一次工作单元是：

* `Task`：有意义工作的总边界
* `Step`：最小逻辑可恢复单元
* `StepAttempt`：某个 step 的一次具体执行实例，也是主要 recovery boundary。

所以如果你问：

**“worker 到底是什么？”**

我会给你一句最稳的定义：

> **worker 是可复用的 role-bound executor；它消费 step/attempt 级别的 contract packet，并产出特定类型的 artifact / evidence / result。**

不是“worker 自己就是一个 task”。

---

## 一、最容易做偏的错误模型

错误模型是：

* 一个 worker = 一个完整 task agent
* worker 自己拿到目标后自由拆解、自由执行、自由验收
* 最后回一个自然语言总结

这会直接冲掉 Hermit 现在最重要的东西：
**task-first、event-backed、contract-first、attempt-granular recovery。**
仓库文档已经反复强调，Hermit 的 governed path 不是“模型直接干活”，而是：

`task -> step -> step attempt -> policy -> approval -> scoped authority -> execution -> receipt -> proof / rollback`。

所以 worker 不能成为一个“自由漂浮的大 agent”，否则你会把 kernel 语义绕开。

---

## 二、正确抽象：worker 是“固定身份 + 固定输入输出协议”

更适合 Hermit 的建模是：

### worker 负责：

* 在某个固定 role 下工作
* 接受某类 `contract packet`
* 只处理与自己职责匹配的 step/attempt
* 产出固定 schema 的 artifact

### task / step / attempt 负责：

* 持久化工作边界
* 生命周期状态
* 重试 / supersede / resume
* policy / approval / grant / receipt / reconciliation 挂载点。

也就是说，worker 更像：

* `planner_worker`
* `exec_worker`
* `verify_worker`
* `benchmark_worker`

它们不是“任务对象”，而是**对任务对象执行某种操作的 principal / executor 角色**。
v0.2 里 `Principal` 的 kinds 也明确包含 `agent`、`executor`、`supervisor`、`system` 等身份，说明“谁在执行”本来就应当和“执行了哪次任务尝试”分开建模。

---

## 三、你可以这样理解层次

### 1. Task

“这件事整体要完成什么”

比如：

* 为 metaloop 的 plan 阶段接入 governed execution

### 2. Step

“这件事拆出来的最小逻辑工作单元”

比如：

* inspect current metaloop plan path
* synthesize execution contract
* implement code patch
* run benchmark
* reconcile outcome。

### 3. StepAttempt

“某个 step 的一次具体执行实例”

比如：

* 第 1 次 patch 失败
* 第 2 次在新 contract 下重试
* 第 3 次因为 approval drift 被 supersede。

### 4. Worker

“谁来执行这个 attempt 对应的 role-bound work”

比如：

* `exec_worker_2` 执行 patch
* `verify_worker_1` 执行验证
* `planner_worker_1` 执行 contract synthesis

所以：

**worker 是 actor / executor identity**
**attempt 才是 durable execution instance**

---

## 四、对你现在最重要的一点：worker 不该自由生成 task，而应该消费 DAG 节点

你刚刚那句其实非常关键：

> “按照输入，输出特定的 dag task 执行，并获取到结果”

我会稍微修正成更严谨的一版：

**worker 不应该“生成 DAG task 并执行它”；而应该由 supervisor / orchestrator 先生成 DAG 上的 step，再把 ready step 的 attempt 分配给匹配 worker 去执行。**

因为在 Hermit 的 layered architecture 里：

* control plane 负责 ingress、task 管理、事件发布
* task and step orchestrator 负责创建 step、维护依赖顺序、分配 attempts
* execution layer 才负责在 grant/lease 范围内执行。

这说明：

**DAG 的创建权不该在普通 worker 手里。**
worker 更像是：

* 被调度
* 消费 ready work
* 产出 artifacts
* 回传结果

而不是自己无约束地产生新的任务图。

---

## 五、一个很适合 Hermit 的 worker 抽象

我建议你把 worker 明确定义成：

```text
Worker = Role-bound stateless-or-light-state executor
```

具备这几个属性：

* `worker_id`
* `worker_role`
* `accepted_step_kinds`
* `accepted_contract_profiles`
* `max_concurrency`
* `required_capabilities`
* `output_artifact_kinds`

例如：

### planner_worker

输入：

* task goal
* context pack
* evidence summary
* upstream constraints

输出：

* `contract.packet`
* `admission.report`
* `step proposals`

### exec_worker

输入：

* admitted execution contract
* workspace lease
* capability grant
* context pack

输出：

* code diff artifact
* command outputs
* receipts
* observation artifacts

### verify_worker

输入：

* completion packet
* acceptance criteria
* benchmark profile

输出：

* verification report
* benchmark report
* verdict packet
* reconciliation input

你看，这里 worker 的本质是 **typed transformer**，不是“完整 autonomous task organism”。

---

## 六、为什么这样更对

因为这和 Hermit 现在的 kernel law 完全一致：

### 1. work is task-first

Hermit 明确强调 work 是 task-first，不是 session-first，也不是 agent-first。

### 2. step/attempt 才是 recovery boundary

v0.1/v0.2 都明确说了：
`Step` 是最小逻辑 recoverable unit，`StepAttempt` 是 concrete execution instance，也是 primary recovery boundary。

### 3. attempt 接收 authority / approval / receipt

policy、approval、grant、receipt、reconciliation 都是挂在 attempt 上的，不是挂在“worker 心智”上的。

### 4. control plane 不应藏执行权

文档明确写了 control plane 不能藏 hidden execution authority；同理，普通 worker 也不该偷偷跨过 orchestrator 和 contract boundary 自由扩张任务。

---

## 七、所以你最终应该怎么设计

我建议你把关系定成：

```text
Supervisor
  -> creates / selects DAG steps
  -> allocates StepAttempt
  -> dispatches attempt to matching Worker
  -> Worker executes under contract
  -> Worker emits artifacts/results
  -> Kernel records receipts/reconciliation
```

也就是：

**supervisor 管工作图**
**worker 管具体变换**
**kernel 管真相与治理**

这个分工最稳。

---

## 八、一句话钉死这个概念

**worker 不是 task 级 agent。**
**worker 是固定角色的执行单元；真正的工作单元是 task → step → stepAttempt。**

再说得更工程一点：

* `Task/Step/Attempt` = durable work records
* `Worker` = reusable execution role
* `Supervisor` = DAG / allocation / escalation authority
* `Kernel` = policy / grant / receipt / reconciliation truth path

这个抽象，我认为是你现在最不容易做偏的一版。

如果你愿意，我下一条可以直接给你写成一份：
**`worker-vs-task-vs-attempt.md` 的对象关系图 + TypeScript/Python 风格接口定义。**
