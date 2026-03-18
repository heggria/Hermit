---
id: demo-add-iteration-summary
title: "添加 iteration_summary 核心工具，用于输出迭代结果摘要"
priority: normal
trust_zone: low
---

## Goal

在 Hermit 的核心工具集中新增一个 `iteration_summary` 只读工具，供 agent 在 `hermit run` 完成后输出结构化的迭代结果摘要（JSON 格式，包含 task_id、status、changed_files、acceptance_results 字段）。

这个工具将被 hermit-iterate 工作流在 Phase 4（PR 闭环）中使用，让 agent 在迭代完成时生成可嵌入 PR body 的结构化摘要。

## Constraints

- 工具为只读（action_class = "read_local"），不修改任何文件或状态
- 注册在核心工具集中（src/hermit/runtime/capability/registry/tools.py 的 register_core_tools 函数中）
- 工具 schema 和描述需要通过 i18n（提供 en-US 和 zh-CN）
- 不修改 kernel 核心合约
- 输出 JSON 格式

## Acceptance Criteria

- [ ] `make check` 通过
- [ ] 文件 `src/hermit/runtime/capability/registry/tools.py` 中包含 `iteration_summary` 工具注册
- [ ] `uv run pytest tests/ -q -k "iteration_summary"` 有对应测试通过
- [ ] i18n locale 文件中包含工具描述的中英文翻译

## Context

- 核心工具注册位置：`src/hermit/runtime/capability/registry/tools.py`
- i18n locale 文件：`src/hermit/infra/system/locales/en-US/` 和 `src/hermit/infra/system/locales/zh-CN/`
- 现有核心工具参考：`read_file`、`write_file`、`bash`、`read_hermit_file` 等
