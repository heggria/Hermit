---
id: kernel-self-mod-guard
title: "Governed Self-Surgery: Hermit 给自己的 kernel 加装自我修改守卫"
priority: high
trust_zone: low
---

## Goal

在 Hermit 的 policy 层新增 **Kernel Self-Modification Guard**：当 agent 尝试写入 `src/hermit/kernel/` 下的文件时，自动升级为 `approval_required`（risk_level: critical），并在 PolicyReason 中标记 `kernel_self_modification`。

这个 guard 的存在本身就是 governed self-evolution 的终极证明：Hermit 在治理链监管下给自己安装了一道新的治理防线，而安装这道防线的行为本身也被现有治理链记录和审计。安装完成后，任何未来对 kernel 代码的修改（包括修改这个 guard 本身）都需要 approval。

## Constraints

- 只修改 `src/hermit/kernel/policy/guards/rules.py` 和 `src/hermit/kernel/policy/evaluators/derivation.py`
- 新增测试文件 `tests/unit/kernel/test_kernel_self_mod_guard.py`
- 不修改任何其他现有文件
- guard 仅对 `write_local` 和 `patch_file` action_class 生效
- guard 检查 `derived["target_paths"]` 中是否包含 `src/hermit/kernel/` 路径
- 当 profile 为 `autonomous` 时，guard 同样生效（autonomous 模式下 kernel 修改也需要 receipt + 明确标记）
- 遵循现有 guard 代码风格和 RuleOutcome 模式

## Implementation Details

### 1. derivation.py 增强

在 `derive_request()` 中，对已解析的 `target_paths` 增加 kernel 路径检测：

```python
# After existing target_paths derivation
kernel_paths = [p for p in derived.get("target_paths", []) if _is_kernel_path(p, workspace_root)]
if kernel_paths:
    derived["kernel_paths"] = kernel_paths
```

新增辅助函数：

```python
def _is_kernel_path(path: str, workspace_root: str) -> bool:
    """Check if path falls within the kernel source tree."""
    if not workspace_root:
        return False
    kernel_prefix = str(Path(workspace_root) / "src" / "hermit" / "kernel")
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return path
    return resolved.startswith(kernel_prefix)
```

### 2. rules.py 新增 guard

在 `evaluate_rules()` 中，在现有的 `sensitive_paths` 检查之后、`write_local` 普通处理之前，插入 kernel self-modification guard：

```python
# Kernel self-modification guard
kernel_paths = list(request.derived.get("kernel_paths", []))
if request.action_class in {"write_local", "patch_file"} and kernel_paths:
    outcomes.append(
        RuleOutcome(
            verdict="approval_required",
            reasons=[
                PolicyReason(
                    "kernel_self_modification",
                    "Modifying kernel source requires elevated approval. "
                    "This action targets governed execution internals.",
                    "warning",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_preview=True,
                require_approval=True,
                require_evidence=True,
                approval_risk_level="critical",
            ),
            normalized_constraints={"kernel_paths": kernel_paths},
            approval_packet={
                "title": "Approve kernel self-modification",
                "summary": (
                    f"Agent requests to modify kernel source: "
                    f"{', '.join(Path(p).name for p in kernel_paths)}. "
                    f"This changes governed execution internals."
                ),
                "risk_level": "critical",
            },
            risk_level="critical",
        )
    )
    return outcomes
```

同时在 `_evaluate_autonomous()` 中，在 sensitive_paths 检查之后，加入同样的 kernel 保护：

```python
# Kernel self-modification guard (applies even in autonomous mode)
kernel_paths = list(request.derived.get("kernel_paths", []))
if request.action_class in {"write_local", "patch_file"} and kernel_paths:
    return [
        RuleOutcome(
            verdict="approval_required",
            reasons=[
                PolicyReason(
                    "kernel_self_modification",
                    "Kernel modification requires approval even in autonomous mode.",
                    "warning",
                )
            ],
            obligations=PolicyObligations(
                require_receipt=True,
                require_approval=True,
                require_evidence=True,
                approval_risk_level="critical",
            ),
            normalized_constraints={"kernel_paths": kernel_paths},
            approval_packet={
                "title": "Approve kernel self-modification (autonomous)",
                "summary": (
                    f"Even in autonomous mode, kernel changes require approval: "
                    f"{', '.join(Path(p).name for p in kernel_paths)}"
                ),
                "risk_level": "critical",
            },
            risk_level="critical",
        )
    ]
```

### 3. 测试用例

在 `tests/unit/kernel/test_kernel_self_mod_guard.py` 中编写以下测试：

1. `test_kernel_path_detected_in_derivation` — 验证 derivation 正确识别 kernel 路径
2. `test_non_kernel_path_not_flagged` — 验证普通路径不触发 kernel_paths
3. `test_kernel_write_requires_approval` — 验证 write_local 到 kernel 路径产生 approval_required
4. `test_kernel_patch_requires_approval` — 验证 patch_file 到 kernel 路径产生 approval_required
5. `test_kernel_read_not_affected` — 验证 read_local 到 kernel 路径不受影响（仍然 allow）
6. `test_kernel_guard_in_autonomous_mode` — 验证 autonomous 模式下 kernel 修改也需要 approval
7. `test_kernel_guard_reason_code` — 验证 reason code 为 `kernel_self_modification`
8. `test_kernel_guard_risk_level_critical` — 验证 risk_level 为 critical

## Acceptance Criteria

- [ ] `make check` 通过
- [ ] `uv run pytest tests/unit/kernel/test_kernel_self_mod_guard.py -q` 通过
- [ ] `uv run pytest tests/unit/kernel/test_policy_properties.py -q` 通过（现有测试不回归）
- [ ] `uv run pytest tests/unit/kernel/test_policy_derivation.py -q` 通过（现有测试不回归）

## Context

**叙事价值**：这是 "Governed Self-Surgery" 的核心 demo case。Hermit 在自身治理链的监管下，给自己安装了一道永久性的治理防线。proof bundle 将完整记录这个过程：

1. Hermit 读取了自己的 policy 代码（read_local → allow）
2. Hermit 请求写入 `rules.py`（write_local → 当时还没有 kernel guard，走普通 workspace_mutation）
3. 写入完成后，guard 生效
4. 从此刻起，任何对 kernel 代码的修改都需要 approval

这个时间线本身就是最强的叙事：治理能力的安装行为被治理，安装完成后治理能力生效。递归闭合。

**相关代码路径**：
- `src/hermit/kernel/policy/guards/rules.py` — guard 评估主逻辑
- `src/hermit/kernel/policy/evaluators/derivation.py` — 请求派生事实
- `src/hermit/kernel/policy/models/models.py` — 数据模型（不修改）
- `tests/unit/kernel/test_policy_properties.py` — 现有 property-based 测试
