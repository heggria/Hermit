# Metaloop 全面升级到生产级 LLM-Governed 自迭代的完整实现规格

## 背景与现状诊断

在 entity["organization","Hermit","open-source agent system"] 的 release/0.3 分支里，Metaloop 的“状态机骨架”已经存在：它能把一个 iteration spec 从 `pending` 推进到 `researching → generating_spec → spec_approval → decomposing → implementing → reviewing → benchmarking → learning → completed/failed`。这一点可以从 Metaloop orchestrator 的 phase handlers（`_handle_*`）以及其推进逻辑（`advance()` 根据 phase 调用 handler，否则直接 advance 到下一阶段）直接看出来。citeturn55view0turn56view0

但你在上下文里指出的关键事实同样成立：**除 implementing 之外，当前大部分阶段仍然是“非 LLM、低智能、可运行但产出质量受限”的占位式实现**，并没有把阶段当作独立的 governed LLM task 来调度。证据主要体现在三处：

第一，Research 阶段当前是“本地 research pipeline + 写 metadata”的同步式 handler，而不是一个异步 governed task。Metaloop 在 `_handle_researching` 中直接构建 research hints、调用 `ResearchPipeline`（并在代码里显式 import `ResearchPipeline` 和 research strategies），得到 report 后把内容序列化进 metadata，然后立刻推进到 `generating_spec`。citeturn55view1turn44view0

第二，Spec 生成阶段当前明确使用了 `SpecGenerator`（文件注释写得很直白：template-based spec generation），内部通过正则/heuristic 从 goal text 抽取 file plan（例如匹配 “create|modify|delete + *.py” 之类模式），并不是 LLM 驱动的规划与约束生成。Metaloop 的 `_handle_generating_spec` 直接调用 `SpecGenerator().generate(...)` 并把结果写入 metadata。citeturn56view0turn38view0

第三，Decompose 阶段同样是 deterministic：`TaskDecomposer` 的文件注释和规则明确声明 “deterministic decomposition（no LLM）”，把 `file_plan` 映射为 code steps，再把 `acceptance_criteria` 映射为 review steps，最后追加 “Run make check”。Metaloop 的 `_handle_decomposing` 也确实是 `TaskDecomposer().decompose(spec)` 然后写 metadata。citeturn56view0turn39view2turn39view3

此外，现有的 `spec_approval` 也不是“审查 spec 的质量与可行性”，而只是做了一个存在性检查：metadata 里有 `generated_spec` 就直接推进到 `decomposing`；没有就 fail。这里没有任何 semantic review、issue 列表、revised spec 等机制。citeturn56view0

需要强调的是：**Implementing 阶段已经“接入 Kernel 的 governed 执行能力”**。Metaloop 在 `_handle_implementing` 中把 decomposition steps 转换成 `StepNode`，然后调用 `task_controller.start_dag_task(...)`，并且显式使用 `policy_profile="autonomous"`。这说明 Kernel 侧的“policy/receipt/ledger”的治理链路是可用的，而 Metaloop 确实只把它用于 implementing。citeturn56view0turn34view0

同样，Metaloop poller 的设计也显示出“只针对 implementing 做了治理式轮询与超时补偿”，并没有抽象成对所有 governed phases 的统一机制：`SpecBacklogPoller._tick()` 会“优先扫描 implementing specs 并检查 timeout（Fix 1）”。超时逻辑集中在 `_check_implementing_timeout()`。citeturn15view4turn15view3

这就是你总结的现状本质：**Metaloop 有状态机骨架、有 Kernel 的 governed task 基础设施，但除了 implementing，阶段本身没被建模为 governed tasks；因此系统是“能跑的玩具闭环”，不是“可扩展的生产级自迭代操作系统”。**citeturn56view0turn15view4turn34view0

## 生产级自迭代的定义与验收标准

要“完全达到自迭代预期”，需要把目标从“把 LLM 接进去”提升为“形成稳健的三环闭环（task / evaluation / goal）”。你在上下文里已经给出了非常准确的分层，我在这里把它固化成可验收的 spec（验收标准被写成可以验证的系统性质，而不是抽象口号）：

Task loop（阶段执行闭环）必须满足：

1) **每个阶段是独立 governed task**：阶段 handler 不做重计算，只负责（a）提交，（b）轮询，（c）结构化提取，（d）推进或纠偏。现有的 implementing 已经是 DAG governed task（`start_dag_task` + `policy_profile="autonomous"`），但 research/spec/decompose/review/learning 仍是同步 inline。citeturn56view0turn55view1
2) **可重入 / 幂等**：重复进入 phase handler 不会重复提交任务；任务状态必须由持久化 state（metadata 或专用表）驱动。Metaloop 已经有 `_update_metadata` 并通过 store 持久化 metadata（`update_spec_status(..., metadata=...)`），这为幂等提供了落点。citeturn55view0
3) **输出契约是强约束**：阶段输出必须是 schema-validated 的结构化对象；字段缺失不能“默认填充继续跑”，必须触发 repair 或 rollback。当前 spec_approval 的“仅检查 generated_spec 是否存在”就是典型的弱契约。citeturn56view0

Evaluation loop（质量评估与纠偏闭环）必须满足：

1) **失败有分型（format / content / risk）**：不同失败类型走不同纠偏路径（同阶段 repair、回流上一阶段 revise、升级 supervision、终止并沉淀）。当前 review 阶段用的 `GovernedReviewer` 实际是本地 lint/todo + AST 小检查，并不具备“语义级 spec compliance”能力，且失败策略主要是 `mark_failed`。citeturn42view0turn16view1
2) **评估指标不仅是 make check**：BenchmarkRunner 目前会记录 `check_passed/test_total/test_passed/coverage/lint_violations/duration_seconds/regression_detected/compared_to_baseline` 等信息，说明系统里已经存在可用于“学习与回归度量”的信号面。生产级自迭代应该把这些指标纳入 replay/eval。citeturn16view1

Goal loop（目标选择与停止条件闭环）必须满足：

1) **followup 有 lineage、dedupe、budget、depth cap、novelty gate**：系统要能生成 followup，但必须可控。Metaloop 目前已经有 `MAX_FOLLOWUP_DEPTH/MAX_FOLLOWUP_FANOUT/MAX_QUEUE_DEPTH` 常量及 learning 阶段的 depth/fanout 限制（Fix 2/3/4），但 “lineage” 仍主要停留在 metadata 的 `followup_depth` 层面，缺乏语义去重与“新信息门槛”。citeturn15view3turn16view2turn15view4
2) **trust_zone 必须成为控制平面**：spec 里已经写入 `trust_zone` 字段，但 implementing 阶段仍然硬编码 `policy_profile="autonomous"`，这意味着 trust_zone 还没真正影响审批/工具预算/自动化级别。citeturn56view0

以上定义直接导出“完全达到预期”的验收口径：当你把 Metaloop 的每个阶段都变为 governed task，并补齐可纠偏的评估闭环与可控的 goal loop，Metaloop 才从“toy pipeline”跃迁为“生产级自迭代系统”。citeturn34view0turn52view1turn50view1

## 总体架构设计

这一版规格把“After 架构”进一步升级为 **Production-grade After++**：不仅每阶段 async governed task，而且明确引入“输出 artifact 化 + 纠偏回路 + goal director”。

### 阶段被建模为 governed phase task

统一模式为：

当 Poller tick 进入某个阶段 handler 时：

- 若该 phase 尚未提交 task：构建 prompt（包含 schema、上下文、工具集、边界），提交一个 governed task，并将 `phase_task_state` 持久化。
- 若已提交 task：轮询 task 状态并根据结果推进/纠偏/失败。

你已有的 Kernel/TaskController 能力足以支撑这种调度模型：TaskController 支持 `start_task(...)`、`enqueue_task(...)`、`start_dag_task(...)`，并为任务执行构建 `TaskExecutionContext`（包含 `conversation_id/task_id/step_id/step_attempt_id/policy_profile/workspace_root/ingress_metadata` 等），这正是“阶段任务把 prompt/metadata 作为任务输入并进入 governed 执行”的最小接口面。citeturn54view2turn54view4turn56view0

同时，GOVERNED_ITERATION 文档明确说明 governed pipeline 的核心保证：授权（policy engine）、workspace lease、receipt、ledger 等，因此 Metaloop 把阶段纳入 governed task 后，可以天然获得“可审计、可验证”的执行记录（这对生产级非常关键）。citeturn34view0turn52view1turn52view3

### 生产级 After++ 的阶段图

建议的阶段序列如下（保持你现有 phase 命名，新增两个纠偏/目标控制阶段）：

- researching（governed）
- generating_spec（governed）
- spec_approval（governed review gate + revise loop）
- decomposing（governed）
- implementing（保留现有 DAG governed，增强 failure 分型与 trust_zone 映射）
- reviewing（governed semantic review + patch suggestion）
- benchmarking（保留现有同步 make check / BenchmarkRunner）
- learning（governed，总结 lessons + followup 提议）
- goal_directing（governed，做去重/预算/停止条件/队列策略）
- completed / failed

其中，`goal_directing` 的目的，是把你指出的第三环（goal loop）从“学习后直接创建 followup spec”升级为“有可控 portfolio 策略的 director”。现有代码里已经存在 SignalToSpec 消费线程，并且实现了 queue depth check，这说明系统已有“从 signals 创建 specs”的入口；但是它目前更多是规则化消费，而不是 LLM-governed 的目标选择。After++ 把这部分正式纳入 governed。citeturn15view4turn15view3

## 关键机制设计

### 阶段输出必须 artifact 化而非扒 result_text

你指出 `_extract_task_output()` “扒 result_text”不够稳，这是生产级目标里最关键的 P0 之一。我建议把“结构化输出”从“文本中 JSON”升级为“artifact / blackboard 级一等对象”，原因与落点如下：

Kernel 的数据模型已经把 “artifact” 与 “receipt” 当作一等公民：`ArtifactRecord` 有 `artifact_id/task_id/step_id/kind/uri/content_hash/trust_tier/metadata` 等字段；`ReceiptRecord` 则记录每次 action 的 `input_refs/output_refs/policy_result/...`。这意味着**系统已经具备把阶段输出变成可追溯 artifact 的数据层承载**。citeturn52view1turn52view3

同时，Kernel 还提供了 task-scoped typed blackboard：`BlackboardService.post(...)` 会把结构化 `content` 以 `BlackboardEntryType`（claim/evidence/patch/risk/todo/decision 等）写入 KernelStore，并 append event 到 ledger。对于“阶段输出”这种结构化对象，blackboard 是天然通道。citeturn50view1turn52view4

因此，这一版 spec 的强制要求是：

- 每个 phase task 完成时，必须产出一个 `metaloop.phase_output` artifact（JSON），或写入 `BlackboardEntryType.decision`（content 为 phase 输出 JSON）。
- orchestrator 的 extractor 只从 artifact/blackboard 读取，不再从 model 输出文本中做脆弱解析。
- 若 phase task 未产出 artifact/blackboard entry，视为 **format failure**，进入 phase-repair（见下一节）。

这并不要求你立刻大改 Kernel：在最小实现路径里，你可以先让 phase task 把 JSON 写入一个受控路径（例如 workspace 的 `.hermit/metaloop_artifacts/{spec_id}/{phase}.json`），并由一个轻量工具负责把该文件登记成 ArtifactRecord（producer=metaloop，trust_tier=normal/supervised）。ArtifactRecord 的字段设计已经在 Kernel 中固定，可直接对齐。citeturn52view1turn54view4

### 失败必须变成 bounded revise loop

当前代码里，spec_approval/review 失败基本走 `mark_failed`，这会导致系统“能重跑但不会自纠偏”。例如 spec_approval 现在仅检查 `generated_spec` 是否存在，不存在就 fail；即使存在，也不会审查质量。citeturn56view0turn16view1

After++ 的规则化纠偏策略是：

- Format failure（缺字段/无 artifact/JSON schema 不通过）：
  在同 phase 内触发 `phase_repair` governed task，输入为“原 prompt + 当前输出（或缺失信息）+ schema + 错误信息”，目标是“仅修复输出契约”，repair 成功后继续推进；repair 超过 N 次（建议 2）则升级风险或终止。

- Content failure（spec review 不通过 / semantic review blocking）：
  走“回流 revise loop”：把 issues 作为输入，回流到上一阶段（spec_generation 或 implementing），由 LLM 生成“带针对性修订”的新 artifact（revised_spec 或 patch_plan），然后再次进入 review gate。
  关键点是：**不是全量重跑，而是带 issues 的定向修订**。

- Risk failure（触发高风险动作、删除关键文件、广泛重构）：
  直接升级到 supervised 信任域（见 trust_zone），进入 approval gate（需要人工确认或审计通过后才进入 implementing DAG）。

这一套 bounded revise loop 的实现依赖两个基础设施：
（a）phase task state 持久化（便于计数与幂等）；（b）review issues 的结构化输出（便于精确回流）。当前 Metaloop 已有 metadata 持久化通道 `_update_metadata`，并且 learning 中已经体现了“从 lessons 创建 followup specs”的机制雏形，因此把 “issues → revise”固化为状态机分支是可行的。citeturn55view0turn16view2turn15view3

### trust_zone 必须驱动 policy_profile、工具集与审批门

目前 spec 里确实保存了 `trust_zone`（在 `_handle_generating_spec` 写入 generated_spec 时保存），但 implementing 阶段无条件 `policy_profile="autonomous"`。这会让 trust_zone 变成无效字段。citeturn56view0

After++ 把 trust_zone 升级为控制平面：

- `trust_zone="normal"`：允许自动进入 implementing，policy_profile 允许 autonomous，但仍受 Kernel policy engine 约束。citeturn34view0turn56view0
- `trust_zone="supervised"`：
  - implementing 必须使用更保守的 policy_profile（例如 `"supervised"` 或 `"default"`，按你们 policy 配置命名），并强制开启 approval gate（关键动作需批准）。
  - 禁止自动创建 followup specs，必须先进入 goal_directing 并通过 dedupe/budget/novelty gate；必要时需要人工确认。
- 工具预算：phase tasks 的 tools 白名单随 trust_zone 收紧。例如 research/spec 阶段只允许 `read_file/bash/web_tools`，禁止任何写入；review 阶段允许读取 + 运行 lint/test；只有 implementing DAG 允许 self_modify 类写入（现有工作树 merge/cleanup 逻辑已存在）。citeturn56view0turn55view1turn34view0

### Decomposition 的基本单位必须从 file entry 升级为 semantic change unit

当前 `TaskDecomposer` 的规则是 “每个 file_plan entry 生成 code step；每个 acceptance criterion 生成 review step；最后 make check”。这一设计在代码中被明确写死。citeturn39view2turn39view3turn56view0

它的工程后果（你在上下文里已指出）是：step 粒度错位、依赖失真、跨文件变更被硬拆碎。After++ 的 spec 要求：

- Decomposition 输出的 steps 是 semantic units（能力变更/接口迁移/重构 slice），允许一个 step 涉及多个文件。
- 每个 semantic code step 后应有对应的 test/verification step，但这种映射应由 LLM 根据变更性质生成，而不是机械地“每文件一个 test step”。
- 依赖图 depends_on 必须基于 import/interface 依赖推断，必要时允许 `read_file` + `bash grep` 来理解依赖。

实现上，你现有 DAG 执行通道不需要变：仍然产出符合 StepNode 的 `{key, kind, title, depends_on, metadata}` 列表即可（Metaloop 现在就是把 steps 转成 StepNode 并交给 `start_dag_task`）。变化只在于“谁生成 steps”：从 deterministic TaskDecomposer 变成 governed decomposition task。citeturn56view0turn54view4

## 测试与评估体系

### Metaloop 的测试必须覆盖“异步提交—轮询—提取—纠偏—推进”

当前 release/0.3 的 Metaloop 主要行为集中在 orchestrator/poller/thread 里：poller 会周期性 `_tick()`，优先检查 implementing timeout，再 claim pending spec 并推进。citeturn15view4turn55view0

After++ 的测试矩阵必须覆盖：

- submit：phase task 第一次进入时只提交，不推进；metadata 写入 phase_task_state（task_id/submitted_at/timeout/retry_count 等）。
- poll running：仍在 running → 不推进（下一 tick 再查）。
- poll completed：从 artifact/blackboard 读取结构化输出 → extractor 校验 → 写 metadata → 推进到 next phase。
- poll failed：按失败类型进入 repair/revise/terminate 分支（而不是单一 mark_failed）。
- timeout：统一超时逻辑覆盖所有 active governed phases（不再只覆盖 implementing）。
- idempotency：重复进入 handler 不重复提交任务；repair/revise 次数受限。
- trust_zone：覆盖 normal/supervised 两种模式下 policy_profile 与审批门行为差异。

### 需要一个 replay/eval suite，让 learning 不再“只学到过没过”

BenchmarkRunner 目前已经能产出多维指标（是否通过、耗时、覆盖率、回归检测、对 baseline 的比较等），并被写入 metaloop metadata，这说明你们的数据面已经具备“训练/回放”的最小要素。citeturn16view1

After++ 的新增验收标准是：

- 新增 `metaloop_replay` 测试/脚本：选取一组历史 spec（或合成 spec），重复运行 metaloop，并记录：
  - 成功率（completed / failed）
  - 平均 iteration wall time（按 benchmark 的 duration 近似）
  - semantic review blocking finding 率
  - 重试次数分布（phase repair/revise 次数）
- learning 阶段输出必须引用这些指标（至少包括是否成功、失败根因、可复用经验、是否应重试/升级 supervision），否则 learning 输出不合格。

这会把系统从“make check 全绿就算成功”提升到“系统在历史任务上的表现是否变好”，从而让自迭代真正可度量。citeturn16view1turn34view0

## 文件变更与落地路线图

这一节给出一份 **“完全达到自迭代预期”的落地级 spec**。它兼容你给出的 After 架构方向，但把你点名的 5 个 P0 缺口固化为必须实现的验收项；同时对齐 release/0.3 的实际路径（文件都在 `src/hermit/plugins/builtin/...` 下）。

### 变更总览

核心变更会落在 4 个区域：

- Metaloop：重写 orchestrator，使 researching/spec/decompose/review/learning/goal_directing 都变成 governed phase tasks，并引入统一 phase timeout 与纠偏回路。citeturn56view0turn15view4
- Decompose：删除 deterministic `SpecGenerator` 与 `TaskDecomposer`（它们目前分别是 template-based + deterministic no LLM）。citeturn38view0turn39view2
- Kernel output channel：引入 phase 输出 artifact/blackboard 作为强契约（基于现有 ArtifactRecord/BlackboardService 的数据模型）。citeturn52view1turn50view1
- Tests + Replay：新增 metaloop_governed 测试文件与 replay suite（以 BenchmarkRunner 指标为核心）。citeturn16view1

### 最小可行实现方案

把你的 Step 0–6 重排成一个更“可收敛”的生产级版本：

- Foundation：新增 `PhaseTaskState`（或等价的 metadata schema），并在 orchestrator 实现统一的：
  - `submit_phase_task(...)`：用 TaskController.enqueue_task/start_task 创建单 step governed task（conversation_id 建议 `metaloop-{spec_id}-{phase}`）；metadata 写入 `metaloop_spec_id/metaloop_phase/trust_zone/attempt`；tools 白名单按 phase 指定。citeturn54view4turn52view1
  - `poll_phase_task(...)`：读取 task 状态；completed 时从 artifact/blackboard 读取输出（禁止从纯文本 JSON parse）；根据 extractor 校验结果推进或触发 repair。citeturn52view1turn50view1
  - `check_phase_task_timeout(...)`：对所有 active governed phases 统一使用。

- Prompts：为每个 phase 定义 prompt builder，统一要求“输出写入 artifact/blackboard，而不是直接在回复里输出 JSON”。（这点是对你原 prompts spec 的关键升级。）

- Extractors：严格 schema 校验（缺字段直接触发 repair，不做 silent fallback）。

- Handlers：逐个 phase 改写为 “submit/poll/repair/revise/advance” 模式；尤其：
  - `spec_approval`：从“存在性检查”升级为 “LLM-governed spec review gate”，输出 issues + revised_spec（可选），并驱动 revise loop。citeturn56view0
  - `reviewing`：从本地 `GovernedReviewer` 升级为 “lint（sync）+ semantic review（governed）”，语义审查输出必须包含 `spec_compliance.met/unmet`。citeturn42view0turn16view0
  - `implementing`：保留 DAG governed，但 policy_profile 必须由 trust_zone 决定，而不是固定 autonomous。citeturn56view0
  - `learning`：输出 lessons + followup_goals，但不直接创建 followup specs；改为写入候选队列，交给 `goal_directing` 做 dedupe/budget/novelty gate。citeturn16view2turn15view3

- Poller：把当前 “只检查 implementing timeout” 改成 “所有 active governed phases 都检查 timeout + 驱动推进”，避免某些 phase 永久卡住。citeturn15view4turn15view3

- Tests：新增覆盖异步 phase task 的全量测试矩阵 + replay/eval suite。

### 生产级验收清单

最终验收标准（全部必须满足）：

1) orchestrator 中 researching/generating_spec/spec_approval/decomposing/reviewing/learning/goal_directing 均不再直接调用 `SpecGenerator/TaskDecomposer/GovernedReviewer` 这类本地占位逻辑；而是改为 governed phase tasks（submit+poll 模式）。citeturn56view0turn38view0turn39view2turn42view0
2) phase 输出只允许从 artifact/blackboard 读取，提取器不允许从“LLM 纯文本输出”扒 JSON。citeturn52view1turn50view1
3) spec review 与 semantic review 的失败会触发 bounded revise loop，而不是直接 mark_failed；并且 repair/revise 有最大次数与升级策略。citeturn55view0turn34view0
4) trust_zone 真实驱动 policy_profile、工具白名单与审批门；implementing 不再固定 `policy_profile="autonomous"`。citeturn56view0turn54view4
5) replay/eval suite 存在且在 CI 中可运行，learning 输出引用 benchmark 指标并能据此给出“是否应重试/是否升级 supervision”。citeturn16view1turn16view2

在 release/0.3 的现状基础上（implementing 已经走 `start_dag_task` 并具备 governed 执行与 receipt/ledger 能力），完成上述改造后，Metaloop 才能从“阶段占位状态机”升格为“生产级可持续自迭代系统”。citeturn56view0turn34view0turn15view4
