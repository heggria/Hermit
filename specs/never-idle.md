---
id: never-idle
title: "AI 永不空转: 审批感知的自治任务队列"
priority: high
trust_zone: normal
---

## Goal

当任务因审批阻塞时，Hermit 不再全线停工。系统自动 checkpoint 并 park 当前任务，转去执行下一个高价值、低风险任务；从执行结果中自动发现并生成后续任务；主动巡逻代码健康状态；夜间活动聚合为晨报。所有行为通过 Evidence Signal Protocol 统一记录，可审计、可追溯。

**核心叙事**: 审批阻塞 ≠ 全线停工。Hermit 总在做最有价值的事。

## Non-Goals

- 不修改 kernel 核心合约（task、ledger、policy、receipt、proof 的 public API）
- 不做跨机器分布式调度（scope 限定在单实例多任务）
- 不做 Web Dashboard 前端（仅提供 JSON API + CLI 输出）
- 不做自然语言理解式的触发规则（仅 regex 模式匹配）

## Architecture Overview

5 个并行开发维度，通过 Evidence Signal Protocol (D5) 统一数据层：

```
                    ┌──────────────────────────────────────────────┐
                    │              Evidence Signal Protocol (D5)    │
                    │   EvidenceSignal · SteeringDirective · Store  │
                    └────┬──────────────┬──────────────┬───────────┘
                         │              │              │
              ┌──────────▼──┐   ┌───────▼───────┐  ┌──▼────────────┐
              │ Trigger (D2) │   │ Patrol (D3)   │  │ Overnight (D4)│
              │ POST_RUN hook│   │ daemon thread  │  │ 晨报 + API    │
              │ → signals    │   │ → signals      │  │ ← signals     │
              └──────────────┘   └───────────────┘  └───────────────┘
                                                            ▲
              ┌──────────────────────────────────────────────┘
              │
     ┌────────▼────────┐
     │ Smart Dispatch   │
     │ & Auto-Park (D1) │
     │ park → re-focus   │
     └──────────────────┘
```

**零文件冲突**：5 个维度可并行开发，各维度拥有独立目录。

---

## D1: Smart Dispatch & Auto-Park

### 目标

任务 park 在审批时，自动切换 focus 到下一个最高价值任务；审批回来后重新评估。

### 设计

#### 优先级评分公式

```
final_score = raw_score - risk_penalty + age_bonus + blocked_bonus
```

| 分量 | 计算方式 | 说明 |
|------|---------|------|
| `raw_score` | 最高 `queue_priority`（来自 active step_attempts） | 基础优先级 |
| `risk_penalty` | `default=5`, `elevated=10`, `critical=20`, 其他=0 | 高风险任务扣分 |
| `age_bonus` | `min(hours_since_creation, 10)` | 防饥饿：老任务加分，上限 10 |
| `blocked_bonus` | 曾 blocked 现恢复 → +10，否则 0 | 奖励等待后重新就绪的任务 |

#### 核心流程

**Park 时 (on_task_parked)**:
1. `TaskController.mark_suspended()` 将任务置为 `blocked`
2. `AutoParkService.on_task_parked(conversation_id, parked_task_id)` 被调用
3. `TaskPrioritizer.best_candidate_after_park()` 在同一 conversation 的 `queued`/`running` 任务中选最高分（排除 parked 任务）
4. 找到候选 → `store.set_conversation_focus(conversation_id, task_id, reason="auto_park")`
5. 无候选 → focus 不变

**Unpark 时 (on_task_unparked)**:
1. 审批通过，任务恢复
2. `AutoParkService.on_task_unparked()` 对 conversation 全量重算优先级
3. 如果恢复的任务得分最高 → focus 切回（reason="auto_unpark"）
4. 否则 → focus 留在当前任务

**Dispatch 收割时**:
- `KernelDispatchService._reap_futures()` 在 futures 完成后调用 `prioritizer.recalculate_priorities()`

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/hermit/kernel/execution/coordination/prioritizer.py` | 新建 | `PriorityScore` (frozen dataclass) + `TaskPrioritizer` |
| `src/hermit/kernel/execution/coordination/auto_park.py` | 新建 | `AutoParkService` |
| `src/hermit/kernel/task/services/controller.py` | 修改 | `mark_suspended()` 末尾调用 `auto_park.on_task_parked()` + `set_auto_park_service()` |
| `src/hermit/kernel/execution/coordination/dispatch.py` | 修改 | `_reap_futures()` 末尾调用 `prioritizer.recalculate_priorities()` + `set_prioritizer()` |
| `src/hermit/runtime/control/runner/runner.py` | 修改 | `start_background_services()` 创建并注入 Prioritizer + AutoPark |
| `tests/unit/kernel/test_prioritizer.py` | 新建 | 13 tests |
| `tests/unit/kernel/test_auto_park.py` | 新建 | 5 tests |

### 边界情况

- 无候选任务：focus 不变，返回 None
- 同一任务：parked_task_id 被排除出候选池
- 任务已删除：`score_task()` 返回 None，过滤掉
- 全部 blocked：无 `queued`/`running` 候选，focus 不变
- 分数并列：取排序后第一个（稳定顺序）

---

## D2: Trigger Engine

### 目标

POST_RUN hook 分析执行结果文本，通过 regex 规则匹配异常模式，自动生成后续任务并发出 EvidenceSignal。

### 设计

#### 内置规则

| 规则名 | source_kind | 匹配模式 | risk | policy | cooldown_key |
|--------|------------|---------|------|--------|-------------|
| `test_failure` | `test_failure` | `FAILED\|ERROR\|AssertionError` | medium | default | `test_failure:{match}` |
| `lint_violation` | `lint_violation` | `ruff\|flake8\|pylint` + error code | low | autonomous | `lint:{match}` |
| `todo_found` | `todo_scan` | `TODO\|FIXME\|HACK\|XXX` | low | autonomous | `todo:{match}` |
| `security_vuln` | `security_vuln` | `CVE-\d{4}-\d+\|vulnerability` | critical | default | `security:{match}` |

#### 文本提取优先级

`_extract_text(result)` 按以下顺序提取可搜索文本：
1. 直接字符串 → 原样返回
2. `.result_text` 属性
3. `.messages` 列表 → 提取每个 message 的 `.content` 或 `.get("content")`
4. `.tool_outputs` 列表
5. 兜底：`str(obj.__dict__)`

#### 流控机制

- **冷却**: 同一 `cooldown_key` 在 `trigger_cooldown_seconds`（默认 86400s）内不重复触发
- **单次上限**: 每次 POST_RUN 最多生成 `trigger_max_tasks_per_run`（默认 3）个 match
- **匹配截断**: matched_text 上限 200 字符，cooldown_key 中的 `{match}` 上限 80 字符
- **安全等级映射**: `critical` → `policy_profile=default`（需审批）；`low` → `autonomous`

#### 任务创建流程

```
POST_RUN hook 触发
  → _extract_text(result)
  → 逐规则 re.finditer() 匹配
  → 截断到 max_tasks_per_run
  → 逐 match:
      → check_cooldown() → 活跃则跳过
      → store.create_signal(EvidenceSignal(...))
      → 日志记录
```

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/hermit/plugins/builtin/hooks/trigger/plugin.toml` | 新建 | 插件清单 |
| `src/hermit/plugins/builtin/hooks/trigger/models.py` | 新建 | `TriggerRule` + `TriggerMatch` |
| `src/hermit/plugins/builtin/hooks/trigger/rules.py` | 新建 | 4 条内置规则 |
| `src/hermit/plugins/builtin/hooks/trigger/engine.py` | 新建 | `TriggerEngine` 核心逻辑 |
| `src/hermit/plugins/builtin/hooks/trigger/hooks.py` | 新建 | SERVE_START(priority=25) + POST_RUN(priority=30) |
| `tests/unit/plugins/hooks/test_trigger_engine.py` | 新建 | 21 tests |

### 插件配置

```toml
[variables.trigger_enabled]
default = true
[variables.trigger_cooldown_seconds]
default = 86400
[variables.trigger_max_tasks_per_run]
default = 3
```

---

## D3: Patrol Engine

### 目标

定时巡逻代码健康状态，发现问题自动生成 EvidenceSignal。

### 设计

#### 5 个内置 Check

| Check | 命令 | 超时 | 输出解析 | 状态判定 |
|-------|------|------|---------|---------|
| `LintCheck` | `ruff check <workspace> --output-format=json` | 120s | JSON 数组 | 有 issue → `issues_found` |
| `TestCheck` | `python -m pytest --tb=no -q <workspace>` | 300s | regex 解析 passed/failed | returncode≠0 → `issues_found` |
| `TodoScanCheck` | `os.walk()` + regex `TODO\|FIXME\|HACK\|XXX` | — | 直接扫描 .py 文件 | 有匹配 → `issues_found` |
| `CoverageCheck` | `python -m pytest --cov --cov-report=term-missing` | 300s | regex 匹配 `TOTAL ... N%` | <80% → `issues_found` |
| `SecurityCheck` | `pip-audit --format=json` | 120s | JSON `dependencies[].vulns[]` | 有漏洞 → `issues_found` |

**文件扫描排除**: `.`开头目录、`__pycache__`、`node_modules`、`.git`

#### 信号发射逻辑

```
run_patrol()
  → 执行所有 enabled checks
  → _emit_signals(report):
      → 仅处理 status="issues_found" 且 issue_count > 0 的 check
      → source_kind 映射: lint→lint_violation, test→test_failure, ...
      → cooldown_key: "patrol:{check_name}"
      → check_cooldown(key, 3600) → 1 小时内不重复
      → _emit_single():
          → 创建 EvidenceSignal
          → risk: security_vuln=critical, test_failure=medium, 其他=low
          → policy: low risk → autonomous, 其他 → default
          → confidence: 0.8
          → store.create_signal(signal)
```

#### Daemon 线程生命周期

- **启动**: `SERVE_START` hook (priority=15)，仅当 `patrol_enabled=true` 时
- **循环**: `_loop()` → `run_patrol()` → `_stop.wait(interval)` → repeat
- **停止**: `SERVE_STOP` hook → `_stop.set()` → `thread.join(timeout=5)`
- **默认关闭**: `patrol_enabled` 默认为 `false`

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/hermit/plugins/builtin/hooks/patrol/plugin.toml` | 新建 | 插件清单（默认关闭） |
| `src/hermit/plugins/builtin/hooks/patrol/models.py` | 新建 | `PatrolCheckResult` + `PatrolReport` |
| `src/hermit/plugins/builtin/hooks/patrol/checks.py` | 新建 | 5 个 Check 实现 + `BUILTIN_CHECKS` 注册表 |
| `src/hermit/plugins/builtin/hooks/patrol/engine.py` | 新建 | `PatrolEngine` daemon + 信号发射 |
| `src/hermit/plugins/builtin/hooks/patrol/hooks.py` | 新建 | SERVE_START/SERVE_STOP 生命周期 |
| `src/hermit/plugins/builtin/hooks/patrol/tools.py` | 新建 | `patrol_run` + `patrol_status` 工具 |
| `tests/unit/plugins/hooks/test_patrol_engine.py` | 新建 | 26 tests（含信号发射） |

### 插件配置

```toml
[variables.patrol_enabled]
default = false
[variables.patrol_interval_minutes]
default = 60
[variables.patrol_checks]
default = "lint,test,todo_scan"
```

---

## D4: Overnight Dashboard & Morning Report

### 目标

聚合夜间活动生成治理报告，通过 CLI、JSON API、Markdown 三种形式输出。

### 设计

#### OvernightSummary 数据模型

```python
@dataclass
class OvernightSummary:
    tasks_completed: list[dict]        # 回看窗口内完成的任务
    tasks_failed: list[dict]           # 回看窗口内失败的任务
    tasks_blocked: list[dict]          # 仍在等审批的任务
    tasks_auto_generated: list[dict]   # trigger/patrol 生成的任务
    total_governed_actions: int        # 回看窗口内的 receipt 数
    boundary_violations_prevented: int # 被拒绝的 approval 数
    approvals_pending: list[dict]      # 当前待审批列表（无时间窗口限制）
    signals_emitted: int               # 信号总数
    signals_acted: int                 # 已处理信号数
    lookback_hours: int
    generated_at: float
```

#### 报告生成逻辑

`OvernightReportService.generate(lookback_hours=12)`:
1. 计算 `since = now - lookback_hours * 3600`
2. 查询 `tasks` 表: `WHERE updated_at >= since` 按 status 分类
3. 统计 `receipts` 表: `WHERE created_at >= since` → `total_governed_actions`
4. 查询 `approvals` 表: `WHERE status = 'pending'`（全量，不限时间窗口）
5. 如果 store 支持 `signal_stats()`:
   - `signals_emitted = sum(stats.values())`
   - `signals_acted = stats.get("acted", 0)`

#### API 路由

| 路由 | 方法 | 参数 | 响应 |
|------|------|------|------|
| `/overnight/latest` | GET | `lookback: int = 12` | Dashboard JSON |
| `/overnight/history` | GET | — | `{"status": "not_implemented"}` |
| `/overnight/signals` | GET | `limit: int = 50` | 最近信号列表 |

#### CLI 命令

```bash
hermit overnight                      # Markdown 格式输出
hermit overnight --lookback 24        # 回看 24 小时
hermit overnight --json               # JSON 格式输出
```

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/hermit/plugins/builtin/hooks/overnight/plugin.toml` | 新建 | 插件清单 |
| `src/hermit/plugins/builtin/hooks/overnight/report.py` | 新建 | `OvernightSummary` + `OvernightReportService` |
| `src/hermit/plugins/builtin/hooks/overnight/dashboard.py` | 新建 | FastAPI router（3 routes） |
| `src/hermit/plugins/builtin/hooks/overnight/hooks.py` | 新建 | SERVE_START hook (priority=20) |
| `src/hermit/surfaces/cli/_commands_overnight.py` | 新建 | `hermit overnight` CLI |
| `src/hermit/surfaces/cli/main.py` | 修改 | 添加 `_commands_overnight` import |
| `src/hermit/plugins/builtin/hooks/webhook/server.py` | 修改 | 挂载 overnight router（try/except 保护） |
| `tests/unit/plugins/hooks/test_overnight_report.py` | 新建 | 9 tests |

### 插件配置

```toml
[variables.overnight_enabled]
default = true
[variables.overnight_report_hour]
default = 8
[variables.overnight_lookback_hours]
default = 12
```

---

## D5: Evidence Signal Protocol

### 目标

所有维度共享的信号数据层。Trigger/Patrol 产出信号 → Store 持久化 → Dashboard 消费 → Protocol 协调生命周期。

### 设计

#### EvidenceSignal 数据模型

| 字段 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `signal_id` | `str` | `sig_{uuid12}` | 唯一标识（自动生成） |
| `source_kind` | `str` | `""` | 类别: `test_failure`, `lint_violation`, `coverage_drop`, `todo_scan`, `security_vuln`, `perf_regression`, `task_blocked` |
| `source_ref` | `str` | `""` | 来源证据 URI |
| `conversation_id` | `str \| None` | `None` | 关联 conversation |
| `task_id` | `str \| None` | `None` | 关联 task |
| `summary` | `str` | `""` | 人类可读摘要 |
| `confidence` | `float` | `0.5` | 置信度 (0.0-1.0) |
| `evidence_refs` | `list[str]` | `[]` | artifact URI 列表 |
| `suggested_goal` | `str` | `""` | 建议的行动目标 |
| `suggested_policy_profile` | `str` | `"default"` | 建议的治理策略 |
| `risk_level` | `str` | `"low"` | `low\|medium\|high\|critical` |
| `disposition` | `str` | `"pending"` | `pending\|accepted\|suppressed\|expired\|acted` |
| `cooldown_key` | `str` | `""` | 去重键 |
| `cooldown_seconds` | `int` | `86400` | 冷却时长 |
| `produced_task_id` | `str \| None` | `None` | 消费此信号产生的 task_id |
| `metadata` | `dict[str, Any]` | `{}` | 扩展元数据 |
| `created_at` | `float` | `time.time()` | 创建时间 |
| `expires_at` | `float \| None` | `None` | 过期时间 |
| `acted_at` | `float \| None` | `None` | 处理时间 |

#### SteeringDirective 数据模型

操作者发出的结构化中途转向指令，映射为 EvidenceSignal 存储。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `directive_id` | `str` | `sig_steer_{uuid12}` | 唯一标识 |
| `task_id` | `str` | `""` | 目标任务 |
| `steering_type` | `str` | `""` | `scope\|constraint\|priority\|strategy\|policy` |
| `directive` | `str` | `""` | 指令内容 |
| `evidence_refs` | `list[str]` | `[]` | 支持证据 |
| `issued_by` | `str` | `"operator"` | 发出者 |
| `disposition` | `str` | `"pending"` | `pending\|acknowledged\|applied\|rejected\|superseded` |
| `supersedes_id` | `str \| None` | `None` | 被取代的指令 ID |
| `metadata` | `dict[str, Any]` | `{}` | 扩展元数据 |
| `created_at` | `float` | `time.time()` | 发出时间 |
| `applied_at` | `float \| None` | `None` | 应用时间 |

**转换方法**: `to_signal()` / `from_signal()` 实现 SteeringDirective ↔ EvidenceSignal 双向映射。

#### SignalProtocol 接口

| 方法 | 行为 |
|------|------|
| `emit(signal) → EvidenceSignal \| None` | 发出信号；被冷却拦截时返回 None |
| `consume(signal_id, produced_task_id)` | 标记为 `acted`，关联产生的 task_id |
| `suppress(signal_id, reason)` | 标记为 `suppressed` |
| `actionable(limit=50)` | 返回 pending 且未过期的信号列表 |
| `stats(since=None)` | 返回 disposition 分布统计 |

#### SteeringProtocol 接口

| 方法 | 行为 |
|------|------|
| `issue(directive)` | 持久化 + 追加 `steering.issued` event + 标记 step_attempt `input_dirty` |
| `acknowledge(directive_id)` | 设为 `acknowledged` + event |
| `apply(directive_id)` | 设为 `applied` + 记录 `applied_at` + event |
| `reject(directive_id, reason)` | 设为 `rejected` + event (含 reason) |
| `supersede(old_id, new)` | 旧指令设为 `superseded`，新指令设置 `supersedes_id` 后发出 |
| `active_for_task(task_id)` | 返回 `pending\|acknowledged\|applied` 状态的指令列表 |

#### 数据库 Schema

```sql
CREATE TABLE IF NOT EXISTS evidence_signals (
    signal_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    conversation_id TEXT,
    task_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    suggested_goal TEXT NOT NULL DEFAULT '',
    suggested_policy_profile TEXT NOT NULL DEFAULT 'default',
    risk_level TEXT NOT NULL DEFAULT 'low',
    disposition TEXT NOT NULL DEFAULT 'pending',
    cooldown_key TEXT NOT NULL DEFAULT '',
    cooldown_seconds INTEGER NOT NULL DEFAULT 86400,
    produced_task_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL,
    acted_at REAL
);

CREATE INDEX idx_evidence_signals_disposition ON evidence_signals(disposition, created_at);
CREATE INDEX idx_evidence_signals_cooldown ON evidence_signals(cooldown_key, created_at DESC);
CREATE INDEX idx_evidence_signals_source ON evidence_signals(source_kind, created_at DESC);
```

Schema version: `"10"` (from `"8"`, additive migration)

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/hermit/kernel/signals/__init__.py` | 新建 | 导出 `EvidenceSignal`, `SignalProtocol`, `SteeringDirective`, `SteeringProtocol` |
| `src/hermit/kernel/signals/models.py` | 新建 | 数据模型 |
| `src/hermit/kernel/signals/store.py` | 新建 | `SignalStoreMixin`（19 个方法） |
| `src/hermit/kernel/signals/protocol.py` | 新建 | `SignalProtocol` |
| `src/hermit/kernel/signals/steering.py` | 新建 | `SteeringProtocol` |
| `src/hermit/kernel/ledger/journal/store.py` | 修改 | schema v10 + `evidence_signals` DDL + mixin 继承 |
| `tests/unit/kernel/test_evidence_signals.py` | 新建 | 19 tests |

---

## Integration Wiring

各维度通过以下注入点连接：

### 注入链路图

```
AgentRunner.start_background_services()
├─ KernelDispatchService(worker_count)
├─ TaskPrioritizer(store)                    ← D1
├─ AutoParkService(store, prioritizer)       ← D1
├─ task_controller.set_auto_park_service()   ← D1 → controller
└─ dispatch_service.set_prioritizer()        ← D1 → dispatch

Plugin System (builtin discovery)
├─ trigger/hooks.py:register()
│   ├─ SERVE_START(priority=25) → engine.set_runner(runner)    ← D2
│   └─ POST_RUN(priority=30) → engine.analyze_and_dispatch()  ← D2
├─ patrol/hooks.py:register()
│   ├─ SERVE_START(priority=15) → engine.start()    ← D3
│   └─ SERVE_STOP(priority=15) → engine.stop()      ← D3
└─ overnight/hooks.py:register()
    └─ SERVE_START(priority=20) → log ready          ← D4

WebhookServer.start()
└─ app.include_router(create_overnight_router(store))  ← D4
```

### 信号数据流

```
D2 Trigger: POST_RUN 结果 → regex 匹配 → store.create_signal()  ─┐
D3 Patrol:  定时巡逻 → check 发现问题 → store.create_signal()   ─┤
                                                                   ↓
                                                    evidence_signals 表 (D5)
                                                                   ↓
D4 Overnight: signal_stats() → 聚合到 OvernightSummary ──────────┘
D4 API:       /overnight/signals → list_signals() ────────────────┘
```

### 松耦合保护

- 所有注入使用 `getattr(obj, attr, None)` 保护，缺少任何组件不会崩溃
- D4 webhook 挂载使用 `try/except ImportError` 保护
- D3 信号发射检查 `hasattr(store, "create_signal")` 后才调用
- D2 task 创建检查 `hasattr(store, "check_cooldown")` 后才使用冷却

---

## Constraints

- 不修改 kernel 核心合约（task、ledger、policy、receipt、proof 的 public API）
- 不引入新的外部依赖（所有命令行工具如 ruff、pytest、pip-audit 为可选）
- 所有改动必须通过 `make check`（lint + typecheck + test）
- Pyright strict mode 兼容（Python 3.13）
- daemon 线程使用 `daemon=True`，不阻塞进程退出
- 每个 check/trigger 的异常被 catch 并 log，不影响其他 check 执行
- SteeringDirective 映射为 EvidenceSignal 存储，复用同一张表

## Acceptance Criteria

- [x] `make check` 通过（lint + typecheck + test）
- [x] 93 个新增测试全部通过:
  - [x] `uv run pytest tests/unit/kernel/test_evidence_signals.py -q` — 19 tests
  - [x] `uv run pytest tests/unit/kernel/test_prioritizer.py -q` — 13 tests
  - [x] `uv run pytest tests/unit/kernel/test_auto_park.py -q` — 5 tests
  - [x] `uv run pytest tests/unit/plugins/hooks/test_trigger_engine.py -q` — 21 tests
  - [x] `uv run pytest tests/unit/plugins/hooks/test_patrol_engine.py -q` — 26 tests
  - [x] `uv run pytest tests/unit/plugins/hooks/test_overnight_report.py -q` — 9 tests
- [x] Schema 从 v8 升级到 v10，向后兼容 v5-v9
- [x] D1: Task park → focus 自动切到同 conversation 的下一个最优任务
- [x] D1: Task unpark → focus 重新评估
- [x] D2: POST_RUN 结果包含 test failure → 自动产生 EvidenceSignal
- [x] D2: 冷却和单次上限生效
- [x] D3: patrol 发现 lint warning → 产生 EvidenceSignal（含 cooldown 保护）
- [x] D3: security check 产生 critical risk 信号
- [x] D4: `hermit overnight` 输出 Markdown 报告
- [x] D4: `/overnight/latest` API 返回 JSON dashboard
- [x] D5: EvidenceSignal CRUD 完整（create/get/list/update/cooldown/stats）
- [x] D5: SteeringDirective 双向映射 EvidenceSignal 正确
- [x] 所有维度通过松耦合注入连接，缺少任何组件不崩溃

## Context

- 分支: `iterate/adversarial-governance`
- 前置工作: Hermit 治理内核（Policy → Grant → Execute → Receipt → Proof）和 4-worker KernelDispatchService 已就绪
- 关联 spec: `specs/governed-self-evolution.md`（Phase 5 已完成）
- 设计灵感: 所有主流 coding agent（Claude Code、Cursor、Codex）审批阻塞 = 全线停工的痛点
