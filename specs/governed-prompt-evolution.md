---
id: governed-prompt-evolution
title: "Self-improving prompts through governed operator approval workflow"
priority: high
trust_zone: low
---

## Goal

Enable governed prompt self-improvement: the kernel analyzes traces to identify prompt patterns correlated with success/failure, proposes prompt modifications, and routes proposals through operator approval. Makes Hermit self-improving while maintaining operator control.

## Steps

1. Create `src/hermit/kernel/context/evolution/analyzer.py`:
   - `PromptEvolutionAnalyzer` class:
     - `analyze_traces(store, window_days=7)` → list[EvolutionInsight]
     - `propose_change(insight)` → PromptChangeProposal
     - `evaluate_proposal(proposal, store)` → ProposalEvaluation

2. Create `src/hermit/kernel/context/evolution/models.py`:
   - `EvolutionInsight`, `PromptChangeProposal`, `ProposalEvaluation`

3. Create `src/hermit/kernel/context/evolution/governor.py`:
   - `PromptEvolutionGovernor` class:
     - `submit_proposal(proposal)` → approval_id
     - `activate(proposal_id)` → bool
     - `revert(proposal_id)` → bool
     - `history()` → list[PromptChangeProposal]

4. Proposals stored as memory_records with memory_kind="prompt_evolution"
5. Each activation produces a receipt

6. Write tests in `tests/unit/kernel/test_prompt_evolution.py` (>= 8 tests)

## Constraints

- ALL changes MUST go through operator approval
- Proposals must include evidence_refs
- Changes are VERSIONED and reversible
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/context/evolution/analyzer.py` exists
- [ ] `src/hermit/kernel/context/evolution/models.py` exists
- [ ] `src/hermit/kernel/context/evolution/governor.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_prompt_evolution.py -q` passes with >= 8 tests

## Context

- ContextCompiler: `src/hermit/kernel/context/compiler/compiler.py`
- ApprovalService: `src/hermit/kernel/policy/approvals/approvals.py`
