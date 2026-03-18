---
id: governed-subagent-identity
title: "Governed Subagent Identity: 给 subagent 安装 kernel 身份和治理基线"
priority: normal
trust_zone: low
---

## Goal

让 subagent 成为 kernel 可见的一等公民：每个 subagent 拥有独立的 PrincipalRecord，其生命周期（创建、完成、失败）被 ledger 记录，delegation 行为产生 receipt。

这是 agent team 能力的基础层。没有身份，就没有按 agent 授权、审计、协调的可能。

## Steps

### 1. 扩展 SubagentSpec

在 `src/hermit/runtime/capability/contracts/base.py` 的 `SubagentSpec` 中增加：

```python
@dataclass
class SubagentSpec:
    name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list[str])
    model: str = ""
    policy_profile: str = "readonly"       # 新增：治理级别
    governed: bool = False                  # 新增：是否走 kernel 治理
```

### 2. Subagent Principal 注册

在 `src/hermit/runtime/capability/registry/manager.py` 的 `_run_subagent()` 中：

- 调用 kernel store 的 `ensure_principal()` 为 subagent 注册 PrincipalRecord
  - `principal_id`: `f"principal_subagent_{spec.name}"`
  - `principal_type`: `"subagent"`
  - `display_name`: `spec.name`
  - `metadata`: `{"parent_principal": "principal_user", "tools": spec.tools}`
- 在 subagent 开始前，记录 ledger 事件 `subagent_spawned`
- 在 subagent 完成后，记录 ledger 事件 `subagent_completed`（包含 turns、tool_calls）
- 在 subagent 失败时，记录 ledger 事件 `subagent_failed`（包含 error）

### 3. Delegation Receipt

修改 `_build_delegation_tool()` 中的 ToolSpec：

- 当 `spec.governed` 为 True 时：
  - `action_class` 改为 `"delegate_execution"`（非 `delegate_reasoning`）
  - `requires_receipt` 改为 `True`
  - `readonly` 改为 `False`
  - `risk_hint` 改为 `"medium"`
- Receipt 的 metadata 中包含 `subagent_principal_id` 和 `subagent_name`

### 4. Policy 支持

在 `src/hermit/kernel/policy/guards/rules.py` 中：

- 为 `delegate_execution` 添加规则：需要 decision + receipt（但不需要 approval）
- 在 autonomous 模式下，`delegate_execution` 自动 allow + require_receipt

### 5. 测试

新增 `tests/unit/runtime/test_subagent_identity.py`：

1. `test_subagent_spec_defaults` — 验证 SubagentSpec 新字段默认值
2. `test_subagent_spec_governed_mode` — 验证 governed=True 的 SubagentSpec
3. `test_delegation_tool_readonly_mode` — 验证 governed=False 时保持原有行为
4. `test_delegation_tool_governed_mode` — 验证 governed=True 时 action_class 和 receipt 设置

新增 `tests/unit/kernel/test_delegation_policy.py`：

1. `test_delegate_execution_requires_decision` — 验证 delegate_execution 需要 decision
2. `test_delegate_execution_requires_receipt` — 验证 delegate_execution 需要 receipt
3. `test_delegate_execution_no_approval_needed` — 验证 delegate_execution 不需要 approval
4. `test_delegate_reasoning_unchanged` — 验证 delegate_reasoning 行为不变

## Constraints

- 不修改现有 subagent 的行为——所有现有 subagent 默认 `governed=False`，保持原样
- 不修改 AgentRuntime.clone() 路径——身份注册和 ledger 事件在 PluginManager 层完成
- 新增的 ledger 事件使用现有的 `_append_event_tx` 机制
- 使用 `write_file` 进行所有文件写入
- 遵循现有代码风格（Ruff、dataclass、structlog）

## Acceptance Criteria

- [ ] `make check` 通过
- [ ] `uv run pytest tests/unit/runtime/test_subagent_identity.py -q` 通过
- [ ] `uv run pytest tests/unit/kernel/test_delegation_policy.py -q` 通过
- [ ] SubagentSpec 支持 `governed` 和 `policy_profile` 字段
- [ ] governed=True 的 delegation 工具 action_class 为 `delegate_execution`

## Context

**后续迭代路径（不在本 spec 范围内）：**

- Phase 2: Governed subagent execution — subagent 的工具调用走 kernel policy → grant → receipt
- Phase 3: Task delegation protocol — 任务委派记录、owner 转移、子任务创建
- Phase 4: Agent team spec — TeamSpec 配置、角色定义、协调模式

**关键代码路径：**
- `src/hermit/runtime/capability/contracts/base.py` — SubagentSpec 定义
- `src/hermit/runtime/capability/registry/manager.py:270-372` — delegation 工具构建和 subagent 执行
- `src/hermit/kernel/authority/identity/models.py` — PrincipalRecord
- `src/hermit/kernel/policy/guards/rules.py` — policy 规则
- `src/hermit/kernel/ledger/journal/store.py` — ledger 事件记录
- `src/hermit/plugins/builtin/subagents/orchestrator/subagents.py` — 现有 subagent 注册
