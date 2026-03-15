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
| Every ingress is task-first and durable | `implemented` | `hermit/kernel/controller.py`, `hermit/kernel/ingress_router.py`, `hermit/kernel/store_tasks.py` | `tests/test_kernel_dispatch_and_controller_extra.py`, `tests/test_runner_extra.py`, CLI `task case` |
| Durable truth is event-backed and append-only | `implemented` | `hermit/kernel/store.py`, `hermit/kernel/store_tasks.py`, `hermit/kernel/store_ledger.py` | `tests/test_task_kernel.py`, `tests/test_kernel_topics_and_projections_extra.py` |
| No direct model-to-tool execution bypass | `implemented` | `hermit/core/tools.py`, `hermit/plugin/manager.py`, `hermit/plugin/mcp_client.py`, `hermit/builtin/github/mcp.py` | `tests/test_plugin_manager_extra.py`, `tests/test_mcp.py`, `tests/test_main_mcp_helpers.py` |
| Effectful execution uses scoped authority and approval packets | `implemented` | `hermit/kernel/executor.py`, `hermit/kernel/approvals.py`, `hermit/kernel/contracts.py`, `hermit/kernel/policy/rules.py` | `tests/test_task_kernel.py`, `tests/test_feishu_dispatcher.py` |
| Important actions emit receipts | `implemented` | `hermit/kernel/receipts.py`, `hermit/kernel/approvals.py`, `hermit/kernel/proofs.py` | `tests/test_task_kernel.py`, CLI `task proof-export` |
| Uncertain outcomes re-enter via observation or reconciliation | `implemented` | `hermit/kernel/executor.py`, `hermit/kernel/observation.py`, `hermit/kernel/dispatch.py` | `tests/test_observation_and_client_extra.py`, `tests/test_tools.py`, CLI `task case` |
| Input drift / witness drift / approval drift use durable re-entry | `implemented` | `hermit/kernel/controller.py`, `hermit/kernel/executor.py`, `hermit/kernel/dispatch.py` | `tests/test_task_kernel.py`, `tests/test_kernel_dispatch_and_controller_extra.py`, CLI `task show` |
| Artifact-native context is the default runtime path | `implemented` | `hermit/kernel/context_compiler.py`, `hermit/kernel/provider_input.py`, `hermit/kernel/artifacts.py` | `tests/test_context_compiler.py`, `tests/test_kernel_coverage_boost.py` |
| Memory writes are evidence-bound and kernel-backed | `implemented` | `hermit/kernel/knowledge.py`, `hermit/kernel/memory_governance.py`, `hermit/builtin/memory/hooks.py` | `tests/test_memory_governance.py`, `tests/test_memory_hooks.py`, CLI `memory export` |
| Verifiable profile exposes proof coverage and exportable bundles | `implemented` | `hermit/kernel/proofs.py`, `hermit/kernel/store_ledger.py`, `hermit/main.py` | `tests/test_task_kernel.py`, CLI `task proof-export` |
| Strong signed proofs and inclusion proofs are available when signing is configured | `conditional` | `hermit/kernel/proofs.py`, `hermit/kernel/store_ledger.py` | `tests/test_kernel_store_tasks_support.py`, CLI `task claim-status` |

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
