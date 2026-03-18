---
id: contract-template-learning
title: "Contract template learning from successful reconciliations"
priority: high
trust_zone: low
---

## Goal

Enable the kernel to learn from successfully reconciled execution contracts and automatically suggest/reuse contract templates for similar future actions. Schema fields `memory_kind=contract_template` and `learned_from_reconciliation_ref` are already reserved in the store — this spec activates them.

## Steps

1. Create `src/hermit/kernel/execution/controller/template_learner.py`:
   - `ContractTemplateLearner` class with methods:
     - `extract_template(contract, reconciliation)` — distill a reusable template from a completed contract+reconciliation pair
     - `match_template(action_request)` — find best matching template for a new action
     - `apply_template(template, action_request)` — generate a pre-filled contract from template
   - Template matching uses action_class + resource_scope similarity (Jaccard on path prefixes + exact command match)
   - Minimum 3 successful reconciliations of the same pattern before a template is promoted
   - Store templates as memory_records with `memory_kind="contract_template"` and `learned_from_reconciliation_ref`

2. Create `src/hermit/kernel/execution/controller/template_models.py`:
   - `ContractTemplate` dataclass: action_class, resource_scope_pattern, constraint_defaults, evidence_requirements, success_count, last_used_at
   - `TemplateMatch` dataclass: template_ref, confidence, match_reasons

3. Integrate into `ExecutionContractService.synthesize()`:
   - Before synthesizing from scratch, check `ContractTemplateLearner.match_template()`
   - If a high-confidence match exists (>0.8), use it as the base contract
   - Log template reuse as a decision event

4. Hook into reconciliation close:
   - When `ReconcileService` closes a reconciliation with `result_class=authorized` or `reconciled_applied`, call `extract_template()`
   - Only extract if the contract had >= 2 obligations fulfilled

5. Write tests in `tests/unit/kernel/test_contract_template_learner.py`:
   - Test template extraction from a reconciled contract
   - Test template matching by action_class similarity
   - Test promotion threshold (need 3 successes)
   - Test template application to new action request
   - Test that low-confidence matches are skipped

## Constraints

- Do NOT modify the KernelStore schema — use existing memory_records table with memory_kind="contract_template"
- Do NOT auto-apply templates without logging a decision event
- Template matching must be deterministic (no LLM calls)
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/execution/controller/template_learner.py` exists
- [ ] `src/hermit/kernel/execution/controller/template_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_contract_template_learner.py -q` passes with >= 5 tests
- [ ] Templates are stored as memory_records with correct memory_kind

## Context

- Existing contract synthesis: `src/hermit/kernel/execution/controller/execution_contracts.py`
- Reconciliation service: `src/hermit/kernel/execution/recovery/reconcile.py`
- Memory records schema: `src/hermit/kernel/ledger/journal/store.py` (memory_records table)
- Reserved fields already in schema: memory_kind, learned_from_reconciliation_ref
