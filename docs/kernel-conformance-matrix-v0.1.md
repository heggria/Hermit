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
| Effectful execution uses scoped authority and approval packets | `implemented` | `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/policy/approvals/approvals.py`, `src/hermit/kernel/execution/controller/contracts.py`, `src/hermit/kernel/policy/guards/rules.py`, `src/hermit/kernel/authority/grants/service.py`, `src/hermit/kernel/authority/grants/models.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/plugins/feishu/test_feishu_dispatcher_adapter_messages.py`, `tests/unit/kernel/test_kernel_permits.py` |
| Principal identity is resolved and recorded for every actor | `implemented` | `src/hermit/kernel/authority/identity/service.py`, `src/hermit/kernel/authority/identity/models.py` | `tests/unit/kernel/authority/test_identity_service.py` |
| Workspace leases enforce exclusive execution boundaries | `implemented` | `src/hermit/kernel/authority/workspaces/service.py`, `src/hermit/kernel/authority/workspaces/models.py` | `tests/unit/kernel/authority/test_workspace_service.py`, `tests/unit/kernel/authority/test_workspace_lifecycle.py`, `tests/e2e/test_workspace_lease_e2e.py` |
| Capability grants are scoped, time-bounded, and traceable | `implemented` | `src/hermit/kernel/authority/grants/service.py`, `src/hermit/kernel/authority/grants/models.py` | `tests/unit/kernel/test_kernel_permits.py`, `tests/unit/kernel/execution/test_dispatch_handler.py` |
| Sequential action types produce durable reconciliation records | `implemented` | `src/hermit/kernel/execution/recovery/reconcile.py`, `src/hermit/kernel/execution/recovery/reconciliations.py` | `tests/unit/kernel/test_reconcile_service.py` |
| Proof export reconstructs full contract/evidence/authority/receipt/reconciliation chains | `implemented` | `src/hermit/kernel/verification/proofs/proofs.py` | `tests/unit/kernel/test_proof_chain_completeness.py` |
| Contract-sensitive retries invalidate stale contract, approval, evidence, and witness state | `implemented` | `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/artifacts/lineage/evidence_cases.py` | `tests/unit/kernel/test_contract_expiry_and_policy_revalidation.py` |

## Current Hard-Cut Boundaries

Implemented:

- tool governance metadata is mandatory for builtin, plugin, delegation, and MCP tools
- approval grant and deny transitions are ledger-backed decision + receipt events
- capability grants are scoped to task/step/attempt and carry decision + approval provenance (`src/hermit/kernel/authority/grants/`)
- principal identity resolution is mandatory for every actor before ledger writes (`src/hermit/kernel/authority/identity/`)
- workspace leases enforce exclusive execution boundaries with queue-based arbitration (`src/hermit/kernel/authority/workspaces/`)
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
