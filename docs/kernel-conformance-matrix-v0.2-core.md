# Hermit Kernel v0.2 Core Conformance Matrix

This matrix tracks the repository-level `v0.2 Core` execution-loop hardening that sits on top of the existing `v0.1` claim surface.

| v0.2 Core Exit Criterion | Status | Primary Implementation | Verification |
| --- | --- | --- | --- |
| Consequential execution synthesizes an `ExecutionContractRecord` before dispatch | `implemented` | `src/hermit/kernel/execution/controller/execution_contracts.py`, `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/ledger/journal/store_v2.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py` |
| Contract admission records an `EvidenceCaseRecord` and `AuthorizationPlanRecord` | `implemented` | `src/hermit/kernel/artifacts/lineage/evidence_cases.py`, `src/hermit/kernel/policy/permits/authorization_plans.py`, `src/hermit/kernel/execution/executor/executor.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py` |
| Receipt issuance carries contract / authorization linkage and requires reconciliation | `implemented` | `src/hermit/kernel/verification/receipts/receipts.py`, `src/hermit/kernel/ledger/events/store_ledger.py`, `src/hermit/kernel/execution/executor/executor.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/surfaces/test_cli.py::test_task_proof_commands_report_and_export_proof_bundle` |
| Reconciliation writes a durable `ReconciliationRecord` and closes the contract loop | `implemented` | `src/hermit/kernel/execution/recovery/reconciliations.py`, `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/verification/proofs/proofs.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `tests/integration/surfaces/test_cli.py::test_task_claim_status_command_reports_repo_and_task_gates` |
| Witness drift supersedes prior attempts instead of silently reusing stale approval state | `implemented` | `src/hermit/kernel/execution/executor/executor.py`, `src/hermit/kernel/ledger/journal/store_tasks.py`, `src/hermit/kernel/ledger/journal/store_v2.py` | `tests/integration/kernel/test_task_kernel_policy_executor.py`, `src/hermit/kernel/artifacts/lineage/claims.py::_probe_durable_reentry` |
| Durable memory promotion is reconciliation-gated | `implemented` | `src/hermit/kernel/context/memory/knowledge.py`, `src/hermit/plugins/builtin/hooks/memory/hooks.py` | `tests/integration/kernel/test_kernel_context_and_memory_services.py`, `tests/unit/kernel/test_memory_governance.py` |
| Projection / proof surfaces expose contract-loop entities | `implemented` | `src/hermit/kernel/task/projections/projections.py`, `src/hermit/kernel/verification/proofs/proofs.py`, `src/hermit/kernel/task/services/topics.py` | `tests/integration/surfaces/test_cli.py::test_task_proof_commands_report_and_export_proof_bundle`, `tests/unit/test_docs_alignment.py::test_conformance_matrix_rows_match_claim_manifest` |

Notes:

- The repository still preserves the existing `v0.1` claim labels and operator-facing `task claim-status` output.
- This matrix is the implementation map for the new `v0.2 Core` loop: `contracting -> preflighting -> executing -> reconciling`.
- Automatic contract-template reuse is intentionally deferred; the schema now reserves `MemoryRecord.memory_kind = contract_template` and `learned_from_reconciliation_ref` so the learning path can be enabled later without another ledger shape break.
