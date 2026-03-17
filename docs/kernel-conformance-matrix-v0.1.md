# Kernel Conformance Matrix v0.1

This document tracks how the current repository maps to the `v0.1` kernel spec. It is intentionally stricter than the README: a row is only marked `implemented` when the repo has both a concrete code path and a regression test or operator surface that exercises it.

Status legend:

- `implemented`: shipped in code and covered by tests or operator output
- `conditional`: available when a local configuration or task-specific capability is present, but not a repository-level blocker
- `partial`: kernel primitive exists, but not yet fully closed across every surface
- `planned`: named in the spec or roadmap, but not yet claimable

## Exit Criteria

| Spec exit criterion | Status | Primary implementation | Regression coverage / operator surface |
| --- | --- | --- | --- |
| Every ingress is task-first and durable | `implemented` | `src/hermit/kernel/task/services/controller.py`, `src/hermit/kernel/task/services/ingress_router.py`, `src/hermit/kernel/ledger/journal/store_tasks.py` | `tests/integration/kernel/test_task_kernel_controller.py`, `tests/integration/runtime/test_runner_dispatch.py`, CLI `task case` |
| Durable truth is event-backed and append-only | `implemented` | `src/hermit/kernel/ledger/journal/store.py`, `src/hermit/kernel/ledger/journal/store_tasks.py`, `src/hermit/kernel/ledger/events/store_ledger.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/kernel/test_kernel_projections_and_topics.py` |
| No direct model-to-tool execution bypass | `implemented` | `src/hermit/runtime/capability/registry/tools.py`, `src/hermit/runtime/capability/registry/manager.py`, `src/hermit/runtime/capability/resolver/mcp_client.py`, `src/hermit/plugins/builtin/mcp/github/mcp.py` | `tests/unit/runtime/test_plugin_manager_governance.py`, `tests/integration/plugins/mcp/test_mcp.py`, `tests/integration/plugins/mcp/test_main_mcp_helpers.py` |
| Effectful execution uses scoped authority and approval packets | `implemented` | `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/policy/approvals/approvals.py`, `src/hermit/kernel/execution/controller/contracts.py`, `src/hermit/kernel/policy/guards/rules.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/plugins/feishu/test_feishu_dispatcher_adapter_messages.py` |
| Important actions emit receipts | `implemented` | `src/hermit/kernel/verification/receipts/receipts.py`, `src/hermit/kernel/policy/approvals/approvals.py`, `src/hermit/kernel/verification/proofs/proofs.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, CLI `task proof-export` |
| Uncertain outcomes re-enter via observation or reconciliation | `implemented` | `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/execution/coordination/observation.py`, `src/hermit/kernel/execution/coordination/dispatch.py` | `tests/unit/plugins/feishu/test_observation_and_feishu_client.py`, `tests/unit/runtime/test_tools.py`, CLI `task case` |
| Input drift / witness drift / approval drift use durable re-entry | `implemented` | `src/hermit/kernel/task/services/controller.py`, `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/execution/coordination/dispatch.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/kernel/test_task_kernel_controller.py`, CLI `task show` |
| Artifact-native context is the default runtime path | `implemented` | `src/hermit/kernel/context/compiler/compiler.py`, `src/hermit/kernel/context/injection/provider_input.py`, `src/hermit/kernel/artifacts/models/artifacts.py` | `tests/unit/kernel/test_context_compiler.py`, `tests/integration/kernel/test_kernel_coverage_boost.py` |
| Memory writes are evidence-bound and kernel-backed | `implemented` | `src/hermit/kernel/context/memory/knowledge.py`, `src/hermit/kernel/context/memory/governance.py`, `src/hermit/plugins/builtin/hooks/memory/hooks.py` | `tests/unit/kernel/test_memory_governance.py`, `tests/unit/plugins/memory/test_memory_hooks.py`, CLI `memory export` |
| Verifiable profile exposes proof coverage and exportable bundles | `implemented` | `src/hermit/kernel/verification/proofs/proofs.py`, `src/hermit/kernel/ledger/events/store_ledger.py`, `src/hermit/surfaces/cli/main.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, CLI `task proof-export` |
| Strong signed proofs and inclusion proofs are available when signing is configured | `conditional` | `src/hermit/kernel/verification/proofs/proofs.py`, `src/hermit/kernel/ledger/events/store_ledger.py` | `tests/unit/kernel/test_kernel_store_tasks_support.py`, CLI `task claim-status` |

## Current Hard-Cut Boundaries

Implemented:

- tool governance metadata is mandatory for builtin, plugin, delegation, and MCP tools
- approval grant and deny transitions are ledger-backed decision + receipt events
- worker interruption no longer fabricates terminal failure for in-flight governed attempts
- memory injection and retrieval fail closed without kernel state
- proof export reports missing proof coverage instead of implying signed completeness

Current transition-era surfaces that remain intentionally compatible:

- markdown memory mirror still exists, but only as an export surface around kernel truth
- runtime/operator views still expose compatibility-friendly summaries in addition to strict ledger objects

## Claim Boundary

The repo can now gate and surface claims through code:

- `Core`: claimable through the conformance matrix and `task claim-status`
- `Governed`: claimable through the same gate once task/operator surfaces are green
- `Verifiable`: claimable as a baseline profile, with stronger task-level readiness depending on exported proof coverage and local signing configuration

The repo still keeps compatibility surfaces, so these claims apply to the kernel contract rather than every legacy runtime affordance.
