---
id: governed-self-evolution
title: "Governed Self-Evolution: Hermit 端到端治理自身代码迭代"
priority: high
trust_zone: normal
---

## Goal

让 hermit-iterate 工作流端到端跑通，并成为 Hermit 的**标志性 demo**：Hermit 读取一个 spec，自主规划实现、修改自身代码、每一步经过 kernel 治理（policy → approval → receipt），执行完成后导出完整 proof chain，测试失败时自动 rollback 受影响步骤，最终生成带 proof summary 的 PR。

这不是功能堆砌 — 这是 **"agents need a kernel"** 论点的完整实证：一个 agent 在修改自己的代码，而你能看到每一个决策、每一份证据、每一条审批链、以及一键回滚任何步骤。

## Non-Goals

- 不做 Web Dashboard（那是后续 spec）
- 不做 agent-agnostic 治理接口（那是长期方向）
- 不重构 kernel 核心 — 只打通已有链路

## Breakdown

### Phase 1: 链路打通 — hermit-iterate 端到端可运行

**目标**: `scripts/hermit-iterate.sh specs/governed-self-evolution.md` 能完整执行，任务记录可查询。

- [ ] 确认 hermit-dev 环境可用（claude-code profile 生效）
- [ ] 确认 `hermit run` 能成功发起任务、执行 tool calls、记录到 ledger
- [ ] 确认 `hermit task list` / `hermit task show <id>` 能查到执行结果
- [ ] 修复链路中遇到的任何阻塞问题

### Phase 2: 治理可见性 — 每步都有 receipt 和 proof

**目标**: 迭代执行的每个 consequential action 都有 receipt，完成后能导出结构化 proof bundle。

- [ ] 确认 tool execution 经过 PolicyEngine → ApprovalService → CapabilityGrant
- [ ] 确认 write_file / bash 等 mutable tool 调用产生 ReceiptRecord
- [ ] `hermit task proof-export <id> --output .hermit-proof/governed-self-evolution.json` 输出完整 proof
- [ ] proof bundle 包含：task → steps → receipts → evidence → authority → reconciliation 全链

### Phase 3: 失败回滚 — rollback 可用

**目标**: 当测试失败时，能通过 `hermit task rollback` 回滚受影响的 receipt。

- [ ] 确认 write_file receipt 包含 prestate snapshot
- [ ] `hermit task rollback <receipt-id>` 能恢复文件到变更前状态
- [ ] rollback 本身产生 RollbackRecord 并记入 ledger

### Phase 4: PR 闭环 — 自动创建带 proof 的 PR

**目标**: 迭代完成后自动创建 PR，PR body 包含 acceptance results + proof summary。

- [ ] hermit-iterate skill 的 step 7 完整执行
- [ ] PR body 包含：
  - 变更摘要
  - acceptance criteria 逐项 pass/fail
  - proof summary（task ID、status、receipt count、proof hash）
  - 可下载的 proof bundle 链接（或嵌入摘要）

### Phase 5: Demo 体验优化

**目标**: 让整个流程从执行到展示都足够惊艳。

- [ ] 执行过程有清晰的 structured logging（每步的 policy result、approval status、receipt ID）
- [ ] proof export 结果人类可读（不只是 JSON dump）
- [ ] 编写一个简单的 demo spec（如"给某模块加一个 utility function"）作为演示用例
- [ ] 录制一次完整执行的 terminal 录屏或 log trace 作为 showcase material

## Constraints

- 不修改 kernel 核心合约（task、ledger、policy、receipt、proof 的 public API）
- 不引入新的外部依赖
- 所有改动必须通过 `make check`
- hermit-iterate.sh 保持向后兼容

## Acceptance Criteria

- [ ] `make check` 通过
- [ ] `scripts/hermit-iterate.sh specs/<demo-spec>.md` 端到端成功执行
- [ ] `hermit task show <task-id>` 显示 completed 状态
- [ ] `hermit task proof-export <task-id>` 输出有效 proof bundle
- [ ] proof bundle 至少包含 1 个 receipt、关联的 evidence case、authority chain
- [ ] `hermit task rollback <receipt-id>` 对 write_file receipt 可执行（如果有）
- [ ] PR 自动创建，body 包含 acceptance results 和 proof summary

## Context

### 为什么这是爆点

1. **Meta 效果** — "agent 治理自身进化"本身就是最好的 storytelling
2. **可验证** — 不是 PPT 演示，是真实的 kernel 执行记录
3. **可回滚** — 不是"agent 做了就做了"，是"做了但你能撤"
4. **独特性** — 其他 agent framework 没有这个能力：governed execution + proof chain + rollback
5. **通向未来** — 跑通后可以加 Proof Explorer Dashboard 可视化

### 已有基础

- `hermit-iterate` skill + `scripts/hermit-iterate.sh` — 执行骨架
- `specs/TEMPLATE.md` — spec 格式定义
- Kernel 全链路（Task → Step → StepAttempt → Policy → Approval → Grant → Execution → Receipt → Proof → Rollback）
- `hermit task` CLI surface（list/show/events/receipts/proof/proof-export/rollback）
- TrustLoop-Bench 验证了 kernel 治理正确性

### 关键文件路径

- `.agents/skills/hermit-iterate/SKILL.md` — iteration skill 定义
- `scripts/hermit-iterate.sh` — 执行脚本
- `src/hermit/surfaces/cli/_commands_task.py` — task CLI
- `src/hermit/kernel/execution/executor/executor.py` — 治理执行器
- `src/hermit/kernel/verification/` — receipt/proof/rollback
- `src/hermit/runtime/control/runner/runner.py` — AgentRunner
