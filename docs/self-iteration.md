# Self-Iteration (Meta-Loop)

Hermit 的自迭代系统让内核能够自主发现问题、生成改进方案、执行代码修改、验证结果并从中学习。整个过程在一个 daemon thread 中自动驱动，无需人工介入。

## 快速开始

### 1. 启用自迭代

设置环境变量或在配置中启用：

```bash
export HERMIT_METALOOP_ENABLED=true
hermit serve
```

或在 `hermit.toml` 中：

```toml
metaloop_enabled = true
metaloop_poll_interval = 5        # 秒，backlog 轮询间隔
metaloop_max_retries = 2          # 每个阶段最大重试次数
metaloop_signal_poll_interval = 30 # 秒，信号消费轮询间隔
```

### 2. 提交迭代目标

通过 MCP tool 提交：

```json
// hermit_self_iterate
{
  "iterations": [
    {
      "goal": "Improve error handling in the store module",
      "priority": "high",
      "research_hints": ["focus on exception propagation patterns"]
    }
  ]
}
```

或通过 `hermit_spec_queue` 管理队列：

```json
// hermit_spec_queue — 添加
{
  "action": "add",
  "entries": [
    {"goal": "Add retry logic to HTTP client", "priority": "normal"}
  ]
}

// hermit_spec_queue — 查看
{
  "action": "list",
  "filters": {"status": "pending", "limit": 10}
}
```

### 3. 观察进度

```json
// hermit_spec_queue — 查看所有状态
{
  "action": "list",
  "filters": {"limit": 50}
}
```

日志中搜索 `metaloop_` 前缀可追踪每个阶段的执行详情。

## 迭代生命周期

每个迭代经过 10 个阶段，由 Poller 自动驱动：

```
PENDING → RESEARCHING → GENERATING_SPEC → SPEC_APPROVAL → DECOMPOSING
→ IMPLEMENTING → REVIEWING → BENCHMARKING → LEARNING → COMPLETED
```

### 阶段详解

| 阶段 | 模块 | 行为 |
|------|------|------|
| **PENDING** | — | 初始状态，等待 Poller claim |
| **RESEARCHING** | D1 ResearchPipeline | 运行 CodebaseStrategy + GitHistoryStrategy 分析代码库，注入历史 lessons 作为 hints |
| **GENERATING_SPEC** | D2a SpecGenerator | 从 goal + research findings 生成确定性 spec（标题、约束、验收标准、文件计划） |
| **SPEC_APPROVAL** | Trust zone 检查 | v0.3 自动批准所有 spec，记录 trust_zone |
| **DECOMPOSING** | D2b TaskDecomposer | 将 spec 拆解为 DAG steps（代码步骤 → review 步骤 → final check） |
| **IMPLEMENTING** | SelfModifyWorkspace + DAG | 创建 git worktree，提交 DAG task，等待异步执行完成 |
| **REVIEWING** | D3 GovernedReviewer | 对计划中涉及的文件执行代码审查 |
| **BENCHMARKING** | D4a BenchmarkRunner | 在 workspace 中运行 `make check`，解析 pytest/coverage/ruff 输出 |
| **LEARNING** | D4b IterationLearner | 从 benchmark 结果提取 lessons，自动生成 follow-up spec |
| **COMPLETED** | — | 终态 |

### 失败处理

- 任何阶段失败时，spec 重置为 `PENDING` 并递增 attempt 计数
- 超过 `max_retries` 后标记为 `FAILED`（终态）
- DAG task 执行失败时，worktree 自动清理（不 merge）

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                     Hermit Serve                         │
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐ │
│  │SpecBacklog   │   │MetaLoop      │   │SignalToSpec   │ │
│  │Poller        │──▶│Orchestrator  │◀──│Consumer       │ │
│  │(5s daemon)   │   │              │   │(30s daemon)   │ │
│  └──────────────┘   └──────┬───────┘   └──────────────┘ │
│                            │                             │
│         ┌──────────────────┼──────────────────┐          │
│         ▼                  ▼                  ▼          │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   │
│  │D1 Research  │   │D2 Decompose │   │D3/D4 Quality│   │
│  │Pipeline     │   │Spec+Task    │   │Review+Bench │   │
│  └─────────────┘   └─────────────┘   └─────────────┘   │
│                            │                             │
│                            ▼                             │
│                    ┌──────────────┐                      │
│                    │SelfModify    │                      │
│                    │Workspace     │                      │
│                    │(git worktree)│                      │
│                    └──────────────┘                      │
│                            │                             │
│                            ▼                             │
│                    ┌──────────────┐                      │
│                    │TaskController│                      │
│                    │.start_dag    │                      │
│                    └──────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

### 关键组件

- **SpecBacklogPoller**: 每 5 秒扫描 `spec_backlog` 表，atomic claim 一个 `pending` spec 并推进
- **MetaLoopOrchestrator**: 无状态状态机，根据当前 phase 调用对应 handler，所有状态存 DB
- **SignalToSpecConsumer**: 每 30 秒消费 PatrolEngine 产生的 `EvidenceSignal`，自动创建 spec
- **SelfModifyWorkspace**: 在 `.hermit/self-modify/{iteration_id}` 创建隔离 git worktree
- **SpecBacklog**: DB-backed 优先队列，支持原子 claim、phase 推进、失败重试

## 三个闭环

### 闭环 1: 手动触发

```
用户提交 goal → Poller claim → 10 阶段全自动 → COMPLETED
```

通过 `hermit_self_iterate` 或 `hermit_spec_queue` MCP tool 手动提交。

### 闭环 2: Lessons 反馈

```
迭代 A 的 LEARNING 阶段发现问题 → 自动创建 follow-up spec → Poller 拾取 → 迭代 B 自动修复
```

触发条件：lesson 的 category 为 `mistake`、`rollback_pattern`、`regression` 或 `test_failure`。

follow-up spec 自动设为 `high` 优先级，metadata 中包含 `produced_from` 链接。

### 闭环 3: 信号驱动

```
PatrolEngine 发现代码问题 → 产生 EvidenceSignal → SignalToSpecConsumer 创建 spec → 自动迭代修复
```

eligible 信号源：`patrol`、`benchmark`、`review`、`test_failure`。

信号消费后标记为 `acted`，避免重复处理。

## IMPLEMENTING 阶段详解

这是整个链路最复杂的阶段，分两种模式：

### DAG 模式（有 runner 时）

1. 从 metadata 读取 `decomposition_plan.steps`
2. 构建 `StepNode` 列表（key, kind, title, depends_on, metadata）
3. 调用 `SelfModifyWorkspace.create()` 创建隔离 worktree
4. 调用 `TaskController.start_dag_task()` 提交 DAG
5. **不自动推进** — 等待 `SUBTASK_COMPLETE` hook 回调
6. DAG 成功 → `merge_to_main()` 合并 worktree → 推进到 REVIEWING
7. DAG 失败 → 清理 worktree → 重试或标记 FAILED

### Dry-run 模式（无 runner 时）

当 `task_controller` 不可用时，记录计划但不执行代码修改，直接推进到 REVIEWING。适用于验证前序阶段的输出。

## 数据存储

所有迭代状态存储在 `spec_backlog` 表中：

| 字段 | 说明 |
|------|------|
| `spec_id` | 迭代唯一标识（如 `iter-a1b2c3d4e5f6`） |
| `goal` | 迭代目标 |
| `status` | 当前阶段（对应 `IterationPhase` 枚举值） |
| `priority` | 优先级（`low` / `normal` / `high`） |
| `attempt` | 当前重试次数 |
| `dag_task_id` | 关联的 DAG task ID（IMPLEMENTING 阶段设置） |
| `error` | 最近一次错误信息 |
| `metadata` | JSON — 存储每阶段产出（findings, spec, plan, review, benchmark, lessons） |
| `trust_zone` | 信任区域 |
| `research_hints` | JSON 数组 — 研究阶段的额外提示 |

## metadata 结构

每个阶段的产出追加存入 metadata JSON：

```json
{
  "findings": {
    "goal": "...",
    "count": 5,
    "sources": ["codebase", "git_history"],
    "top_findings": [...]
  },
  "generated_spec": {
    "spec_id": "...",
    "title": "...",
    "constraints": [...],
    "acceptance_criteria": [...],
    "file_plan": [...]
  },
  "decomposition_plan": {
    "steps": [
      {"key": "create_util", "kind": "code", "title": "...", "depends_on": []},
      {"key": "review_0", "kind": "review", "title": "...", "depends_on": ["create_util"]}
    ]
  },
  "implementation": {
    "mode": "dag",
    "dag_task_id": "...",
    "worktree_path": "...",
    "merge_sha": "abc123"
  },
  "review": {
    "passed": true,
    "finding_count": 2,
    "findings": [...]
  },
  "benchmark": {
    "check_passed": true,
    "test_total": 6130,
    "test_passed": 6130,
    "coverage": 0.93,
    "regression_detected": false
  },
  "lessons": [
    {"lesson_id": "...", "category": "optimization", "summary": "..."}
  ],
  "followup_specs": ["followup-abc123"]
}
```

## 配置参考

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| `metaloop_enabled` | `HERMIT_METALOOP_ENABLED` | `false` | 启用自迭代 |
| `metaloop_poll_interval` | — | `5` | Poller 轮询间隔（秒） |
| `metaloop_max_retries` | — | `2` | 每个 spec 最大重试次数 |
| `metaloop_signal_poll_interval` | — | `30` | 信号消费轮询间隔（秒） |

## 日志关键字

| 日志事件 | 含义 |
|----------|------|
| `metaloop_started` | Meta-loop 系统启动 |
| `metaloop_poller_claimed` | Poller 拾取了一个 pending spec |
| `metaloop_research_complete` | D1 研究阶段完成 |
| `metaloop_spec_generated` | D2a spec 生成完成 |
| `metaloop_decomposed` | D2b 任务分解完成 |
| `metaloop_worktree_created` | Git worktree 创建成功 |
| `metaloop_dag_submitted` | DAG task 提交成功 |
| `metaloop_worktree_merged` | Worktree 合并回主分支 |
| `metaloop_benchmarked` | D4a benchmark 完成 |
| `metaloop_learned` | D4b 学习阶段完成 |
| `metaloop_followup_spawned` | 自动创建了 follow-up spec |
| `metaloop_lessons_injected` | 历史 lessons 注入研究阶段 |
| `signal_to_spec_created` | 信号转化为 spec |

## 安全机制

- **Worktree 隔离**: 所有代码修改在独立 git worktree 中进行，主分支不受影响
- **FileGuard 锁**: worktree 创建和 merge 操作使用跨进程文件锁，防止并发冲突
- **Supervised policy**: DAG task 默认使用 `supervised` policy profile
- **Merge conflict 处理**: 合并冲突时自动 `git merge --abort` 恢复清洁状态
- **Atomic claim**: 使用数据库级原子操作防止多个 poller 重复处理同一个 spec
- **Retry 上限**: 超过 `max_retries` 后不再重试，标记为 FAILED
- **Signal 去重**: 信号消费后标记为 `acted`，spec_id 从 signal_id 确定性派生

## 限制（v0.3）

- **SPEC_APPROVAL** 阶段自动批准所有 spec，未接入人工审批流
- DAG step 执行依赖 runner 的 agent 执行管线，step 的 prompt 构建需要感知 metaloop context
- `make check` 是硬编码的 benchmark 命令，不同项目可能需要自定义
- 研究阶段只使用本地策略（CodebaseStrategy + GitHistoryStrategy），不联网
