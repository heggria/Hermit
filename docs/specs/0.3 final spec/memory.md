对，但要分层。

**memory 不是单独属于某一个面。**
更准确的放法是：

# 结论

**durable memory 属于控制面 / 真相脊柱。**
**working memory 属于执行面。**
**真正安全的结构是“双层 memory 模型”。**

也就是：

* **Control Plane Memory**：可持久、可审计、可失效、可投影
* **Execution Plane Working State**：短期、局部、可丢弃、只服务当前 attempt

这其实和你现在 Hermit v0.2 的语义是吻合的。v0.2 明确写了：上下文是由 artifacts、bounded working state、evidence-backed beliefs 和 governed memory 编译出来的；并且只有 reconciled outcomes 才能变成 durable knowledge。

---

# 一、为什么 memory 不能只放一个面

如果你把全部 memory 都放执行面，会出 3 个问题：

第一，**不可审计**。
worker 内部“记住了什么”会变成黑箱，很容易退化成 hidden prompt accumulation。v0.2 明确反对这种方向，要求推广的是 governed memory 和 contract templates，而不是偷偷堆 prompt。

第二，**不可治理**。
durable knowledge 的升级、失效、降级，必须依赖 reconciliation outcome，而不是某次执行时模型“感觉这个经验有用”。这在 v0.2 是硬规则。

第三，**不可共享**。
如果 memory 只在某个 worker/attempt 里，那 Program status、policy enrichment、template reuse、cross-workspace promotion 都很难做成系统能力。

反过来，如果你把全部 memory 都放控制面，也不对。因为执行时仍然需要大量**短命、局部、可抛弃**的 working state，比如这次 attempt 的局部上下文、临时观察、候选假设、一次性中间产物。v0.2 也明确区分了 bounded working state 和 governed memory。

---

# 二、最稳的划分

## 1）控制面：Durable Memory / Governed Memory

这部分应当属于控制面，或者你前面说的 evidence spine / truth path。

它包括：

* `Belief`
* `MemoryRecord`
* `ContractTemplate`
* `TaskPattern`
* validation / invalidation / promotion 状态
* scope（global / workspace）
* learned_from_reconciliation_ref
* evidence_refs

原因很简单：
这些东西会影响后续 contract synthesis、policy enrichment、risk hint、approval 判断，属于**系统级治理知识**，不能藏在执行器里。v0.2 明确规定 `Belief` revision、`MemoryRecord` promotion、`ContractTemplate` learning 都必须依赖 reconciliation。

你仓库里现在最清楚的例子就是 `ContractTemplateLearner`。它把成功 reconciliation 抽成 `memory_record(memory_kind="contract_template")`，写入 durable store；并且只在 `reconciliation.result_class == "satisfied"` 时学习，否则跳过。

而且这些 template 还会：

* 按 workspace/global 隔离与匹配
* 累积 success/failure/invocation 统计
* 在 violation 后降级或失效
* 满足条件后跨 workspace 提升为 global。

这就是典型的 **控制面 memory**，不是执行面缓存。

## 2）执行面：Working Memory / Ephemeral State

这部分应当留在执行面。

它包括：

* 当前 attempt 的局部上下文
* 当前 context pack
* 当前 witness / observables
* 临时候选 hypothesis
* 一次性草稿、临时 patch 思路、局部检查结果
* 尚未被 reconciliation 确认的中间认知

它们可以参与模型调用，但**不能天然成为 durable truth**。
v0.2 的标准生命周期里，context compiler 会刷新 working state、beliefs、memory，然后给模型 propose-only context；模型产出 candidate contracts，但只有后续 admit/authorize/execute/receipt/reconcile 之后，才有资格进入 durable memory。

---

# 三、所以 memory 应该怎么挂到你的“两面架构”上

我建议你把它画成这样：

```text
Human / IM / CLI
      │
      ▼
┌──────────────────────────────┐
│         Control Plane        │
│ - Program / Team / DAG       │
│ - Policy / Approval          │
│ - Status Projection          │
│ - Durable Memory             │
│   - beliefs                  │
│   - memory_records           │
│   - contract_templates       │
│   - task_patterns            │
└──────────────┬───────────────┘
               │
               │ contract / memory refs / evidence refs
               ▼
┌──────────────────────────────┐
│        Execution Plane       │
│ - planner/exec/verify worker │
│ - context compiler           │
│ - working memory             │
│   - current context pack     │
│   - attempt-local state      │
│   - witness / observables    │
│   - draft hypotheses         │
└──────────────┬───────────────┘
               │
               │ receipts / artifacts / observations
               ▼
┌──────────────────────────────┐
│   Reconcile / Truth Path     │
│ - reconciliation             │
│ - belief revision            │
│ - memory promotion/invalidate│
└──────────────────────────────┘
```

如果你想更精确一点，其实 durable memory 最好说成：

**在控制面可见、由真相层更新。**

因为它不是由普通 Program manager 直接改，而是由 reconciliation gate 驱动更新。v0.2 在职责划分里已经把 “revise beliefs / promote or invalidate memory / learn contract templates only from reconciled outcomes” 放进 receipt, reconciliation, and learning layer。

---

# 四、你现在最该用的 memory 分类

到 1.0 前，建议只保留 3 类 durable memory，别扩太多：

## A. Belief Memory

系统对外部状态/任务状态的可修正判断。
必须可 revision，不可 silent overwrite。v0.2 对 revisable knowledge 有硬约束。

## B. Contract Template Memory

这是你现在 repo 里最成熟的一类。
从 satisfied reconciliation 提取模板，用于后续 contract synthesis、policy suggestion、approval skip hint 等。

## C. Task Pattern Memory

任务级执行模式。
你现在也已经有 `task_pattern` 的痕迹，并且 policy evidence enrichment 会把 task pattern 写进 context。

其他像“长聊天摘要记忆”“人格经验记忆”这类，先别做成核心 durable memory，容易把系统搞脏。

---

# 五、一个非常重要的边界

**memory 不应该直接授权执行。**

它最多能做：

* enrich context
* suggest policy
* bias contract synthesis
* recommend lower risk / approval skip eligibility

但不能因为“memory 里以前成功过”就直接越权执行。
你 repo 里这条边界已经很清晰：template match 只是写 `policy_suggestion` 到 context，最后仍然要走 policy / approval / contract path。

---

# 六、落到你的两面模型里，一句话怎么讲

最适合你的口径是：

> **Hermit 的 memory 分为两层：执行面持有 attempt-local working memory；控制面通过 reconciliation gate 管理 durable governed memory。前者服务当前执行，后者服务后续治理、合约合成与系统学习。**

---

# 七、最终结论

所以，“memory 应该在哪个面”的最准确答案不是二选一，而是：

* **短期工作记忆在执行面**
* **持久治理记忆在控制面 / 真相脊柱**
* **只有经过 reconciliation 的结果，才允许从执行面上升为 durable memory**。

这套放法和你现在 Hermit 的 kernel v0.2 方向是完全一致的。

下一步最值得做的是把 memory 明确冻结成：
**`working_memory` / `belief_memory` / `template_memory` / `task_pattern_memory`** 四类模型。


对，**很像“短期记忆 / 长期记忆”**，这个类比是成立的。
但我建议你**不要直接用人类认知里的“长短期记忆”当正式架构名词**，因为它会把 Hermit 的关键治理约束讲弱。

更准确的说法是：

* `attempt-local working memory` ≈ **短期工作记忆**
* `durable governed memory` ≈ **长期可治理记忆**

## 为什么说“像”，但不能完全等同

因为你这里不是普通 agent memory，而是 **governed task OS memory**。

普通“长短期记忆”类比容易让人误以为：

* 短期记忆 = 聊天上下文
* 长期记忆 = 自动存起来以后随便拿来用

但在 Hermit 里不是这样。

### 1. working memory 不是随便的上下文缓存

它是：

* 当前 attempt 局部可见
* 可丢弃
* 不可信到不能直接升级为系统真相
* 主要服务当前执行

所以它更像：
**scratchpad / execution-local state / working set**

### 2. durable memory 也不是“永久记住”

它不是“只要出现过就长期保存”，而是：

* 必须经过 reconciliation
* 必须可审计
* 必须可失效 / 降级 / 修订
* 不能直接替代 policy / approval / contract

所以它更像：
**validated long-term memory with governance**

---

# 我建议你的正式术语

如果你只是为了自己快速理解，可以脑内映射成：

* working memory = 短期记忆
* durable governed memory = 长期记忆

但如果你要写进 Hermit spec，我更建议用这组：

## 1. Execution-local Working Memory

强调：

* 作用域在 execution/attempt
* 短命
* 局部
* 不自动升级为真相

## 2. Governed Durable Memory

强调：

* 持久
* 可治理
* 经验证
* 可修订

这比“短期记忆 / 长期记忆”更贴合你的系统目标。

---

# 最好再补一个中间层意识

其实你这里不只是二分法，严格讲更像三层：

## A. Context / Working State

当前 attempt 的局部工作记忆

## B. Beliefs / Structured Intermediate Knowledge

已经被系统表达出来，但仍可能修订的知识
比如：

* 当前判断的 blocker
* 某个模块风险判断
* 某个 task family 匹配结果

## C. Durable Governed Memory

经过 reconciliation 后，允许长期影响系统行为的知识
比如：

* contract template
* task pattern
* validated memory record

所以如果要更完整，可以理解成：

```text id="7n6ju1"
短期执行态
  -> working memory

中期可修订知识
  -> beliefs / provisional structured knowledge

长期可治理知识
  -> durable governed memory
```

这样比简单“短期/长期”更准。

---

# 为什么这个区分对 Hermit 很重要

因为你真正要防的是这件事：

> 某次执行里模型临时想到一个东西，系统就把它当长期真相记住了

这会非常危险。

Hermit 的价值恰恰在于：
**不是“记住更多”，而是“只把被验证过的东西持久化”。**

所以你的长期记忆不是“记忆容量”问题，
而是 **truth promotion** 问题。

---

# 一句话结论

**对，概念上很像长短期记忆。**
但在 Hermit 里，最好正式表述为：

* **attempt-local working memory**：执行期短命工作记忆
* **durable governed memory**：经过 reconciliation 后才允许持久化的治理记忆

如果你愿意，我下一条可以直接把这两类 memory 的边界画成一张对照表。
