# Hermit 0.3 深度机制：当治理成为代码

> 这是 [Hermit 0.3 愿景文档](./vision-0.3.md) 的姊妹篇。愿景文档讲的是"为什么"，这篇讲的是"怎么做到的"。
>
> 如果你读完愿景文档觉得"听起来不错，但真的有这么深吗？"——这篇文章就是答案。

---

## 一、决策本身也是被治理的：竞争式审议

大多数 agent 系统的决策流程是这样的：模型想了想，做了。

好一点的系统会加一个审批：模型想了想，人类说了"好"，做了。

Hermit 做的事情完全不同。对于高风险决策，Hermit 会启动一轮**正式的竞争式审议**——不是"多想几遍"，而是一套结构化的提案-质疑-仲裁制度。

### 流程

```
决策点触发
    ↓
多个 Candidate 独立提交提案
    ↓
Critic 角色对每个提案发出分级质疑
    ↓
仲裁产出裁决（含置信度 + 是否需要升级到人类）
    ↓
整个辩论打包成 DebateBundle，存入证据链
```

### 提案不是随便写的

每个 `CandidateProposal` 必须包含：

- **target_scope** — 你要动哪里
- **plan_summary** — 你打算怎么做
- **contract_draft** — 你需要什么权限、什么约束、什么验收标准
- **expected_cost / expected_risk / expected_reward** — 量化你的代价和收益

不是"我觉得应该这么做"，而是"这是我的方案、代价、风险、收益，请质疑"。

### 质疑不是随便说的

每条 `CritiqueRecord` 必须包含：

- **target_candidate_id** — 你在质疑哪个提案
- **issue_type** — 什么类型的问题
- **severity** — 严重程度
- **evidence_refs** — 你的质疑基于什么证据
- **suggested_fix** — 你的改进建议

不是"我不同意"，而是"基于这些证据，这个方案在这里有这个级别的问题，建议这样改"。

### 仲裁也不是随便拍的

`ArbitrationDecision` 包含：

- **selected_candidate_id** — 选了谁
- **rejection_reasons** — 为什么没选其他人
- **confidence** — 对这个决定有多大把握
- **escalation_required** — 是否需要升级给人类

如果 confidence 低于 0.3 或者需要升级，决策不会自动执行——它会进入 `awaiting_approval` 状态等人类。

### 触发条件

不是每个动作都要辩论。系统有明确的触发规则：

- 高风险规划
- 高风险代码补丁
- 模糊 spec（多种理解可能）
- benchmark 争议（两种解读冲突）
- 执行后复审（做完了回头看对不对）
- 评审委员会（多角色联合评审）

**底线**：读操作永远不辩论。低风险永远不辩论。只有当后果真正重要的时候，系统才会启动这套制度。

### 为什么这很重要

因为大多数 agent 系统里，"AI 做出了一个错误的决定"和"AI 做出了一个正确的决定"在过程上没有任何区别——都是黑盒推理。你事后只能看结果。

在 Hermit 里，你可以打开 DebateBundle 看到：谁提了什么方案，谁质疑了什么，基于什么证据，最终为什么选了这个方案。这不是"更好的 prompt engineering"。这是**决策过程本身的结构化记录**。

---

## 二、治理会变轻：模式学习与合同模板

"治理意味着更慢"——这是最常见的反对意见。

Hermit 的回答是：**治理在第一次是最重的，之后会越来越轻。**

### TaskPatternLearner：从执行中提取模式

每次 Task 完成后，`TaskPatternLearner` 会：

1. 提取每个 Step 的指纹（SHA-256）
2. 从任务目标中提取关键词（去除停用词）
3. 聚合相似任务的 step 序列
4. 统计成功率

产出一个 `TaskPattern`：

```
pattern_fingerprint: "a3f8c2..."
step_fingerprints: ["b1c2d3...", "e4f5g6...", "h7i8j9..."]
goal_keywords: ["benchmark", "routing", "profile"]
invocation_count: 7
success_count: 6
success_rate: 0.857
```

这不只是"记住了做过什么"。这是系统在理解**什么样的任务结构倾向于成功**。

### ContractTemplateLearner：从成功中学习合同

更有意思的是合同模板学习。当一次执行的 Reconciliation 结果是 `satisfied` 时，系统会：

1. 提取这次执行的合同（action_class、tool、约束、效果）
2. 对路径做归一化（`/Users/beta/work/Hermit/src/foo.py` → `path:*/foo.py`）——这样学到的模板跨工作区也能用
3. 和已有模板计算 Jaccard 相似度
4. 如果相似度 > 0.8，合并到已有模板；否则创建新模板
5. 当一个模板被成功使用 ≥ 3 次，它获得"提升"资格

### 被提升的模板可以简化治理

当一个未来的动作匹配到一个高置信度模板（成功率 > 85%，使用 ≥ 3 次），系统会在 policy 评估中附加一条建议：

```
policy_suggestion: {
    skip_approval_eligible: true,
    suggested_risk_level: "medium",   // 从 high 降到 medium
    template_confidence: 0.91,
    template_invocation_count: 5
}
```

注意：这只是**建议**。最终 verdict 仍然由完整的守卫链决定。而且，**危险动作类永远不适用模板降级**——write_local、patch_file、network_write 等动作即使匹配到完美模板也不会自动跳过审批。

### 为什么这很重要

因为这意味着 Hermit 的治理开销不是一个常数。它是一个**递减函数**。

第一次写某类文件需要审批。第三次写同类文件，系统已经学会了这种模式，治理变轻。第十次，它几乎是透明的。

但如果你突然写了一个新类型的文件——治理又回到全强度。这才是真正的治理：**对已知的宽容，对未知的严格**。

---

## 三、信任是一个数字，不是一个开关

大多数系统的信任模型是二元的：信任，或者不信任。Hermit 的信任是一个**持续计算的分数**。

### 公式

```
composite = 0.5 × success_rate
          + 0.3 × (1 - rollback_rate)
          + 0.2 × avg_reconciliation_confidence
```

三个维度：

- **success_rate** — 历史执行成功率（权重最高，0.5）
- **rollback_rate** — 多少次执行需要回滚（越少越好）
- **reconciliation_confidence** — 和解过程的平均置信度（执行结果和预期匹配的程度）

### 分数如何影响风险

| 分数 | 风险等级 |
|------|---------|
| ≥ 0.85 | low |
| ≥ 0.65 | medium |
| ≥ 0.40 | high |
| < 0.40 | critical |

### 五条安全线

1. **最少 5 次执行**才启用评分——不允许靠运气拿高分
2. **只对安全动作类生效**——read_local、delegate_reasoning、execute_command_readonly 等
3. **危险动作类永不降级**——write_local、patch_file、network_write、vcs_mutation、publication 不管分数多高都保持原始风险
4. **critical 风险永不降级**——即使分数满分
5. **分数是建议，不是命令**——最终决定权在守卫链

### 为什么不自动生效？

因为信任评分是**归纳推理**：过去 100 次这样做都没问题，所以第 101 次可能也没问题。但归纳推理有一个根本缺陷——它对黑天鹅事件无能为力。

所以 Hermit 把信任评分当作 advisory signal，不是 policy override。操作者可以看到"系统认为这个动作风险较低，基于 N 次成功历史"，但最终的 allow/deny 仍然由完整的守卫链产出。

---

## 四、14 步守卫链：Policy 不是一个 if/else

当一个工具调用到达 Policy Engine 时，它不是走一个简单的检查。它走的是一条 **14 步顺序守卫链**，每一步检查不同的维度，任何一步都可以升级 verdict 的严格程度。

```
① 委派范围限制
    ↓ 如果在子 agent 的委派范围之外 → deny
② Profile 级守卫
    ↓ readonly profile → 只允许 read_local
    ↓ autonomous profile → 跳过审批，保留 receipt，硬拦危险操作
③ 只读放行
    ↓ read_local / network_read / delegate_reasoning → allow
④ 治理放行
    ↓ delegate_execution / approval_resolution / scheduler_mutation → allow_with_receipt
⑤ 附件规则
    ↓ 只有特定 adapter 可以接收附件
⑥ 规划守卫
    ↓ 高风险变更没有 plan → approval_required
⑦ 文件系统规则
    ↓ 敏感路径 + 工作区外 → deny
    ↓ 敏感路径 + 工作区内 → approval_required (critical)
    ↓ 内核自修改 → approval_required (critical, 即使 autonomous)
    ↓ 工作区外 → approval_required (high)
    ↓ 工作区内 → preview_required
⑧ Shell 命令规则
    ↓ sudo / curl|sh → deny
    ↓ git push → approval_required (critical)
    ↓ 写磁盘 / 删文件 / 网络访问 → approval_required (critical)
    ↓ 只读 shell → allow_with_receipt
⑨ 外部变更规则
    ↓ network_write / credentialed_api_call / publication → approval_required
⑩ 记忆写入规则
    ↓ 内核记忆写入（有证据） → allow_with_receipt
    ↓ agent 记忆写入 → approval_required
⑪ 信号风险升级
    ↓ 检测到 critical 信号 → 强制升级到 approval_required
⑫ 信任评分降级
    ↓ 安全动作类 + 高信任分 → 建议降级（仅建议）
⑬ 模板置信度建议
    ↓ 匹配高置信度模板 → 建议跳过审批（仅建议）
⑭ 兜底
    ↓ 没有任何规则匹配 → approval_required（保守默认）
```

**多条规则的裁决取最严。** 如果第七步说 `preview_required`，但第十一步因为信号升级到 `approval_required`，最终 verdict 是 `approval_required`。所有理由合并保留。

这里有一个关键的设计决定：**默认是拒绝，不是允许。** 如果 14 步走完没有任何规则明确放行，兜底 verdict 是 `approval_required`。这和操作系统的 capability-based security 是同一个理念——默认无权限，必须显式授予。

---

## 五、CapabilityGrant：不是"给你权限"，是"给你一张精确的许可证"

操作系统里的文件权限是粗粒度的：读、写、执行。Hermit 的 CapabilityGrant 是**精确到每个字段的许可证**。

### 一个 Grant 长什么样

```python
CapabilityGrantRecord(
    grant_id="g-a3f8c2",
    task_id="t-001",
    step_attempt_id="sa-042",
    decision_ref="d-017",             # 关联到哪个 policy 决定
    approval_ref="ap-009",            # 关联到哪个审批（如有）
    issued_to_principal_id="agent-hermit",
    action_class="write_local",       # 只允许写本地文件
    resource_scope=["task_workspace"], # 只在任务工作区内
    constraints={
        "target_paths": ["/Users/beta/work/Hermit/src/foo.py"],
        "lease_root_path": "/Users/beta/work/Hermit/src/"
    },
    status="issued",
    expires_at=1711180200.0,          # 300 秒后过期
)
```

### 执行前的 6 项校验

当工具真正要执行时，`CapabilityGrantService.enforce()` 会做 6 项精确校验：

1. **存在性 + 归属**：Grant 存在且属于当前 task
2. **状态**：必须是 `issued`（不是 consumed / revoked / uncertain / invalid）
3. **过期检查**：默认 TTL 300 秒——超时自动失效
4. **action_class 精确匹配**：请求的动作类必须和 grant 的动作类完全一致
5. **resource_scope 超集检查**：grant 的范围必须**包含**请求的范围
6. **约束逐项校验**：
   - `target_paths`：必须匹配已批准的路径列表
   - `network_hosts`：必须是已批准主机的**子集**
   - `command_preview`：Shell 命令必须**完全匹配**
   - `lease_root_path`：必须**包围**所有目标路径
   - 引用的 `workspace_lease_ref` 必须仍然活跃

任何一项不通过，Grant 状态变为 `invalid`，执行被拒绝。

### 为什么 TTL 是 300 秒？

因为权限不应该是永久的。一个 5 分钟前批准的操作，5 分钟后可能已经不合适了——文件可能被别的任务改过，环境可能发生了变化。300 秒强制 grant 在短时间内消费或过期。如果过期了，需要重新走一遍 policy 评估。

这和操作系统的 token-based auth（如 Kerberos ticket）是同一个思路：权限有时效，必须定期更新。

---

## 六、WorkspaceLease：同一时刻只有一个人能改同一个地方

这是 Hermit 中最接近操作系统互斥锁的机制——但比互斥锁更精细。

### 核心规则

同一个工作区（workspace）同一时刻只允许**一个可变租约（mutable lease）**。多个只读租约可以共存。

### 竞争处理

如果 Task A 持有 `/src/` 的可变租约，Task B 也需要修改 `/src/`：

1. Task B 的请求不会被拒绝——它进入 **FIFO 队列**
2. 当 Task A 释放租约时，系统自动出队 Task B 的请求
3. Task B 被通知可以继续

这意味着审批阻塞和资源竞争都不会让系统停下来。Task B 在等待的同时，系统可以把它泊车（park），去执行其他不冲突的任务。

### TOCTOU 防护

租约获取使用**两遍过期清理**：

1. 第一遍：过期所有陈旧租约
2. 第二遍：重新读取活跃租约列表

这消除了"检查时没冲突，获取时冲突了"的竞态条件。

### 孤儿收割

后台运行的孤儿收割器做三件事：

1. **终止任务扫描**：释放已完成/失败/取消的任务持有的租约
2. **TTL 扫描**：强制过期超过 24 小时的租约（硬限制）
3. **队列清理**：移除已终止任务的排队请求

---

## 七、19 态状态机：治理的每个阶段都有名字

大多数 agent 框架里，一个工具调用的状态是：pending → running → done/error。三个状态。

Hermit 的 StepAttempt 有 **19 个显式状态**：

```
ready → running → dispatching → contracting → preflighting
→ policy_pending → awaiting_approval → awaiting_plan_confirmation
→ observing → reconciling → verification_blocked → receipt_pending
→ succeeded / completed / skipped / failed / superseded
```

每个状态代表治理流水线中的一个**确定阶段**：

| 状态 | 含义 |
|------|------|
| dispatching | 正在分配到 worker pool |
| contracting | 正在合成执行合同 |
| preflighting | 正在做授权预检 |
| policy_pending | 等待 policy engine 评估 |
| awaiting_approval | 阻塞等待人类审批 |
| observing | 执行完毕但结果不确定，等待外部观察 |
| reconciling | 正在比对授权效果 vs 实际效果 |
| verification_blocked | 等待 benchmark 验证通过 |
| receipt_pending | 执行完成，等待 receipt 签发 |
| superseded | 被更新的 attempt 取代（drift detection） |

### 为什么 19 个状态这么重要？

因为**状态是持久化的**。如果系统在 `contracting` 阶段崩溃了，重启后它知道自己在 contracting 阶段——不需要从头开始，不需要猜"做到哪了"。

更重要的是，每个状态转换都会产出一个 kernel event。这意味着你可以在事后完整重放一个 Step Attempt 的生命周期：它什么时候开始分配 worker，什么时候进入 policy 评估，什么时候阻塞在审批上，等了多久，审批通过后什么时候开始执行，执行完什么时候进入和解，和解结论是什么。

**19 态状态机不是过度设计。它是治理可观测性的最低要求。**

---

## 八、自动泊车 + 优先级调度：阻塞不等于停机

当一个任务卡在审批或者资源竞争上时，大多数 agent 系统的反应是：等。

Hermit 的反应是：**换一个任务做。**

### AutoParkService

当 Task A 进入 `awaiting_approval` 状态时：

1. `AutoParkService` 被触发
2. 它调用 `TaskPrioritizer` 扫描所有 ready 状态的任务
3. 选出分数最高的 Task B
4. 把对话焦点切换到 Task B
5. Task A 保持泊车状态，不占用任何 worker slot

### 优先级评分

```
score = raw_score - risk_penalty + age_bonus + blocked_bonus
```

- **risk_penalty**：default=5, elevated=10, critical=20（高风险任务不会轻易被选为替代）
- **age_bonus**：从创建到现在的小时数，封顶 10（越老的任务越优先，防止饥饿）
- **blocked_bonus**：之前被阻塞过的任务 +10（补偿等待时间）

### 解除泊车

当审批通过时：

1. Task A 的 StepAttempt 从 `awaiting_approval` 转为 `ready`
2. `AutoParkService` 重新评估：Task A 现在可能不是最高优先级了（可能在等待期间有更紧急的任务进来了）
3. 系统选择真正的最优任务继续

**这就是"睡后干活"能成立的底层机制。** 不是"一个任务一直跑到完"，而是系统在任何时刻都在推进**当前能推进的最高优先级工作**。

---

## 九、Fork-Join：不是所有汇合都需要"全部成功"

任务可以 fork 出子任务。但子任务汇合时，不是只有"全部完成"一种策略。

Hermit 提供 4 种汇合策略：

| 策略 | 含义 | 适用场景 |
|------|------|---------|
| ALL_REQUIRED | 所有依赖都成功 | 串行流水线：每一步都必须成功 |
| ANY_SUFFICIENT | 任意一个成功即可 | 冗余执行：多个方案试一个能用的 |
| MAJORITY | >50% 成功 | 投票/共识：多数通过即可 |
| BEST_EFFORT | 所有依赖终止即可（含失败） | 信息收集：能拿多少拿多少 |

这和竞争式审议是配合使用的。比如：fork 出 3 个 Candidate 方案并行执行，用 `ANY_SUFFICIENT` 汇合——第一个通过 benchmark 的方案被采纳。

`JoinBarrierService` 还负责**失败级联检测**：如果一个关键依赖失败了，它会自动标记所有下游 step 为 fail，避免无意义的执行。

---

## 十、证据充分性：不是 pass/fail，是一个分数

在执行前，系统会评估当前的证据链是否"足够"。这不是一个布尔判断，而是一个**加权评分**。

### 权重

```
witness_ref:        0.35   — 执行前状态快照（最重要）
policy_result_ref:  0.25   — Policy 评估结果
context_pack_ref:   0.20   — 上下文编译产出
action_request_ref: 0.20   — 动作请求记录
```

### 计算

```
weighted_sum = Σ(ref_weight × (1 if ref exists else 0))
baseline = 0.25 × total_refs
raw = max(weighted_sum, baseline) - 0.2 × unresolved_gaps
sufficiency = clamp(raw, 0.0, 1.0)
```

- ≥ 0.5 且无未解决缺口 → `sufficient`
- 输出**置信区间**（±0.15），表达不确定性

### 漂移敏感度

评分还会输出一个 drift_sensitivity 字段：

- 有 witness → `high`（witness 变化意味着证据可能失效）
- 只有 context pack → `medium`（上下文可能衰减）

**这意味着系统知道自己的证据链有多脆弱。** 不是"有证据就行"，而是"这些证据有多容易过时"。

---

## 十一、混合上下文检索：4 条路径 × 融合排名

给模型的上下文不是简单拼接——它是一个**多路径检索 + 融合排名**的产物。

### 4 条检索路径

1. **Token 重叠**：基于倒排索引的精确匹配（最快）
2. **语义嵌入**：向量相似度搜索（最深）
3. **图谱遍历**：沿知识图谱的关联链查找（最广）
4. **时间衰减**：考虑记忆新旧程度的衰减评分（最新）

### 融合

4 条路径各自产出一个排名列表，用 **Reciprocal Rank Fusion (K=60)** 合并：

```
RRF_score(d) = Σ 1 / (K + rank_i(d))
```

这是信息检索领域的标准做法——不依赖任何单一信号，而是让多个弱信号互相补强。

### 快/慢路径

- **query < 50 字符**（简单问题）：只走 token 匹配，~5ms 级响应
- **query ≥ 50 字符或有风险信号**（复杂问题）：全部 4 路 + 可选 cross-encoder 重排

### ContextPack 的最终结构

```
static_memory      — 永远注入（治理策略、用户偏好）
retrieval_memory    — 检索排名结果
selected_beliefs    — 范围匹配的信念（top 10）
working_state       — 当前执行状态
episodic_context    — 时间序列记忆
procedural_context  — 操作型知识
task_summary        — 任务元数据
policy_summary      — 策略摘要
pack_hash           — SHA-256（防篡改）
```

每条被选中的记忆都标记了**选择原因**（`"static_policy"` / `"hybrid:semantic,token"` / `"retrieval_rank"`），每条被排除的记忆都标记了**排除原因**。

**你可以事后审查：模型看到了什么，没看到什么，为什么。**

---

## 十二、中途转向：不需要取消重来

操作者发现方向不对时，不需要取消任务重新提交。Hermit 支持**执行中发出转向指令**。

### 5 种转向类型

| 类型 | 含义 | 风险 |
|------|------|------|
| scope | 调整目标范围 | medium |
| strategy | 改变执行策略 | medium |
| constraint | 增加/放松约束 | low |
| priority | 调整优先级 | low |
| policy | 改变治理策略 | high |

### 生命周期

```
pending → acknowledged → applied / rejected
```

- 系统不会静默忽略你的指令——它必须明确 acknowledge
- 应用还是拒绝也是显式记录的
- 如果新指令和旧指令冲突，旧指令被标记为 `superseded`，链接到新指令

### 脏标记机制

当 steering directive 到达时，系统会 `mark_attempt_input_dirty()`——这意味着当前 attempt 的输入已经变化，下一轮推理会把新指令纳入上下文。

---

## 十三、哈希链事件日志：46 张表的不可篡改账本

Hermit 的事件日志不是"写到 SQLite 就算了"。它是一条**密码学链接的不可篡改事件流**。

### 链接机制

```
event_hash = SHA-256(canonical_json(payload))
prev_event_hash = 上一个事件的 event_hash
```

`canonical_json` 是确定性序列化：key 排序 + 紧凑分隔符 + 无 ensure_ascii。同样的 payload 永远产出同样的 JSON，永远产出同样的 hash。

这意味着：

- 篡改任何一个事件 → hash 不匹配 → 链断裂
- 插入一个伪造事件 → prev_event_hash 指向错误的前驱 → 链断裂
- 删除一个事件 → 后继事件的 prev_event_hash 指向不存在的前驱 → 链断裂

### Schema v18 的 46 张表

12 个零耦合的 Store Mixin，各管各的表：

| Mixin | 职责 | 代表性表 |
|-------|------|---------|
| TaskStore | 任务生命周期 | tasks, steps, step_attempts |
| LedgerStore | 治理实体 | approvals, decisions, receipts, capability_grants, workspace_leases, artifacts, beliefs, memory_records, rollbacks |
| ProjectionStore | 事件回放投影 | conversation_projection_cache |
| SchedulerStore | 调度 | schedule_specs, schedule_history |
| RecordStore | 通用记录 | (generic CRUD) |
| V2Store | 执行合同 | execution_contracts, evidence_cases, authorization_plans, reconciliations |
| SignalStore | 证据信号 | evidence_signals |
| CompetitionStore | 竞争评估 | competitions, competition_candidates |
| AssuranceStore | 保证报告 | assurance_trace_envelopes, assurance_scenarios |
| DelegationStore | 委派追踪 | delegations |
| SelfIterateStore | 自迭代 | spec_backlog, iteration_lessons |
| ProgramStore | 程序/团队 | programs, teams, milestones |

### 投影重建

所有状态都可以从事件流**完整重建**：`build_task_projection(task_id)` 按事件序号顺序重放所有事件，合并到 14 种实体类型桶中。缓存是纯加速层——不是真相来源。

---

## 十四、自迭代的 5 通道 + 3 阶段验证门

Hermit 修改自己不是"改了提交"。它是一个 5 通道制品追踪 + 3 阶段 fail-fast 验证的正式流程。

### 5 通道

| 通道 | 阶段 | 产出 |
|------|------|------|
| A | Spec/Goal | iteration_spec, milestone_graph, phase_contracts |
| B | Research | research_report, repo_diagnosis, evidence_bundle |
| C | Change | diff_bundle, test_patch, migration_notes |
| D | Verification | benchmark_run, replay_result, verification_verdict |
| E | Reconcile | reconciliation_record, lesson_pack, template_update, next_iteration_seed |

**关键规则：只有 E 通道（和解后的结论）才能驱动模板晋升和系统学习。** 通道 C 的代码变更本身不能直接变成系统能力——必须经过 D 的验证和 E 的和解。

### 3 阶段验证门

自修改的代码必须过三道门，fail-fast：

| 门 | 命令 | 超时 | 范围 |
|----|------|------|------|
| test-quick | `make test` | 30s | 核心冒烟测试 |
| test-changed | pytest 受影响文件 | 180s | 被修改影响的测试 |
| check | `make check` | 600s | 完整 lint + 类型检查 + 全量测试 |

第一道门失败 → 直接停，不进第二道。这避免了"浪费 10 分钟跑完整测试只为发现编译都不过"。

### 工作区隔离

所有自修改发生在 **git worktree** 中——不是直接改主分支。只有全部验证通过 + reconciliation 结论是 `accepted`，才会 merge 回主分支。

如果验证失败或被拒绝，worktree 被丢弃。主分支没有任何影响。

---

## 结语：这不是过度设计

读到这里你可能会想：19 态状态机、14 步守卫链、4 路检索融合、6 项 grant 校验、5 通道制品追踪——这是不是过度设计？

答案是：**对于一个帮你修 typo 的聊天机器人，是的。**

但 Hermit 不是聊天机器人。它是一个你**睡着之后还在替你干活**的系统。它修改你的代码、跑你的测试、操作你的文件系统、连接你的 API。当你第二天醒来，你需要知道发生了什么、为什么被允许发生、以及能不能撤回。

这些机制不是功能列表。它们是 Hermit 能够在你不盯着的时候安全运行的**结构性前提**。

19 个状态意味着任何位置崩溃都能恢复。
14 步守卫链意味着没有任何动作能绕过治理。
CapabilityGrant 的 300 秒 TTL 意味着权限不会悄悄累积。
WorkspaceLease 的互斥意味着两个任务不会同时改同一个文件。
模式学习意味着治理开销随时间递减。
信任评分意味着系统对自己有量化的自知之明。

这些加在一起，构成了一个你可以信赖到**关上电脑去睡觉**的系统。

这不是过度设计。这是必要设计的最低下限。
