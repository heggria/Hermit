# Hermit Kernel v0.2 Core Conformance Matrix

This matrix tracks the repository-level `v0.2 Core` execution-loop hardening that sits on top of the existing `v0.1` claim surface.

| v0.2 Core Exit Criterion | Status | Primary Implementation | Verification |
| --- | --- | --- | --- |
| Consequential execution synthesizes an `ExecutionContractRecord` before dispatch | `implemented` | `hermit/kernel/execution_contracts.py`, `hermit/kernel/executor.py`, `hermit/kernel/store_v2.py` | `tests/test_task_kernel_policy_executor.py` |
| Contract admission records an `EvidenceCaseRecord` and `AuthorizationPlanRecord` | `implemented` | `hermit/kernel/evidence_cases.py`, `hermit/kernel/authorization_plans.py`, `hermit/kernel/executor.py` | `tests/test_task_kernel_policy_executor.py` |
| Receipt issuance carries contract / authorization linkage and requires reconciliation | `implemented` | `hermit/kernel/receipts.py`, `hermit/kernel/store_ledger.py`, `hermit/kernel/executor.py` | `tests/test_task_kernel_policy_executor.py`, `tests/test_cli.py::test_task_proof_commands_report_and_export_proof_bundle` |
| Reconciliation writes a durable `ReconciliationRecord` and closes the contract loop | `implemented` | `hermit/kernel/reconciliations.py`, `hermit/kernel/executor.py`, `hermit/kernel/proofs.py` | `tests/test_task_kernel_policy_executor.py`, `tests/test_cli.py::test_task_claim_status_command_reports_repo_and_task_gates` |
| Witness drift supersedes prior attempts instead of silently reusing stale approval state | `implemented` | `hermit/kernel/executor.py`, `hermit/kernel/store_tasks.py`, `hermit/kernel/store_v2.py` | `tests/test_task_kernel_policy_executor.py`, `hermit/kernel/claims.py::_probe_durable_reentry` |
| Durable memory promotion is reconciliation-gated | `implemented` | `hermit/kernel/knowledge.py`, `hermit/builtin/memory/hooks.py` | `tests/test_kernel_context_and_memory_services.py`, `tests/test_memory_governance.py` |
| Projection / proof surfaces expose contract-loop entities | `implemented` | `hermit/kernel/projections.py`, `hermit/kernel/proofs.py`, `hermit/kernel/topics.py` | `tests/test_cli.py::test_task_proof_commands_report_and_export_proof_bundle`, `tests/test_docs_alignment.py::test_conformance_matrix_rows_match_claim_manifest` |

Notes:

- The repository still preserves the existing `v0.1` claim labels and operator-facing `task claim-status` output.
- This matrix is the implementation map for the new `v0.2 Core` loop: `contracting -> preflighting -> executing -> reconciling`.
- Automatic contract-template reuse is intentionally deferred; the schema now reserves `MemoryRecord.memory_kind = contract_template` and `learned_from_reconciliation_ref` so the learning path can be enabled later without another ledger shape break.
