# Self-Iteration Pipeline: Consolidated Audit Findings

## Phase 1 Results: 20 exploration agents, ~300 raw findings

### TIER 1: CRITICAL — Must fix for pipeline to work at all

| ID | Issue | Files | Confidence |
|----|-------|-------|-----------|
| C1 | **Dispatch SQL excludes reconciling/blocked tasks** | store_tasks.py:850 | FIXED ✅ |
| C2 | **Poller IMPLEMENTING in _tick loop** | orchestrator.py:1918 | FIXED ✅ |
| C3 | **Council empty perspectives** | orchestrator.py:1088 | FIXED ✅ |
| C4 | **Observation stale timeout missing** | observation.py | FIXED ✅ |
| C5 | **changed_files from step metadata instead of file_plan** | orchestrator.py:1005,1112 | FIXED ✅ |
| C6 | **Benchmark exception doesn't block acceptance** — If BenchmarkRunner raises, benchmark_passed stays True, spec auto-accepts | orchestrator.py:1183-1196 | HIGH (3 agents confirmed) |
| C7 | **Council timeout auto-accepts** — If ALL reviewers timeout, findings empty → verdict=accept | council_service.py:343-376, council_arbiter.py | HIGH (3 agents) |
| C8 | **_run_quick always returns rc=0** — Benchmark quick mode ignores lint/test failures | runner.py:305-306 | HIGH (1 agent, code-confirmed) |
| C9 | **Ruff regex misses plural "errors"** — `Found\s+(\d+)\s+error` doesn't match "errors" | runner.py:37 | HIGH (1 agent, code-confirmed) |
| C10 | **Observation tickets never resolved** — finalize_observation() doesn't call resolve_ticket() | observation_handler.py:239-354 | HIGH (2 agents) |
| C11 | **No cleanup of observations on task cancel/timeout** | controller.py:1150-1237, staleness_guard.py | HIGH (2 agents) |
| C12 | **Contradictory lesson injection** — AVOID vs PREFER same pattern, no conflict detection | orchestrator.py:597-627 | HIGH (1 agent, design-confirmed) |
| C13 | **Shell injection in benchmark** — File paths passed to shell=True without quoting | runner.py:236,248,292,299 | HIGH (1 agent, code-confirmed) |
| C14 | **Stuck PLANNING approval** — No approval polling, spec hangs forever waiting | orchestrator.py:418-427 | HIGH (2 agents) |
| C15 | **No PLANNING/REVIEWING phase timeouts** — Only IMPLEMENTING has 900s timeout | orchestrator.py:36 | HIGH (2 agents) |
| C16 | **needs_attention not counted as failed in join barrier** — DAG steps block forever | store_tasks.py:900-902 | HIGH (2 agents) |
| C17 | **Metadata race condition** — Non-atomic read-modify-write in _update_metadata() | orchestrator.py:200-211 | HIGH (4 agents) |
| C18 | **SUBTASK_COMPLETE emission race** — emitted_complete set not locked | dispatch.py:323-341 | MEDIUM (2 agents) |

### TIER 2: HIGH — Correctness issues that cause silent failures

| ID | Issue | Files | Confidence |
|----|-------|-------|-----------|
| H1 | **Revision cycle not reset on retry** — revision_cycle preserved after mark_failed() | backlog.py:294 | HIGH (2 agents) |
| H2 | **Prompt placeholders never substituted** — Council reviewer system prompts have {placeholders} that are sent as-is | council_prompts.py, council_service.py | HIGH (1 agent, code-confirmed) |
| H3 | **Consensus boost double-counts findings** — Base score + consensus boost applied to same finding | council_arbiter.py:103-133 | MEDIUM (1 agent) |
| H4 | **Provider clone failures silently return None** — _get_provider() swallows all exceptions | orchestrator.py:105-118 | HIGH (2 agents) |
| H5 | **No rate-limit (429) retry logic** — API rate limits instantly fail iteration | claude.py:224-242 | HIGH (1 agent) |
| H6 | **Semaphore starvation** — Metaloop LLM calls share 8-slot semaphore with user interactions | claude.py:24-25 | HIGH (2 agents) |
| H7 | **dag_task_id not cleared in revision path** | orchestrator.py:1492-1530 | MEDIUM (2 agents) |
| H8 | **Revision directive not passed to implementing workers** | orchestrator.py:1509-1515 | MEDIUM (1 agent) |
| H9 | **Retry backoff not enforced** — next_retry_at stored but not checked before claiming | backlog.py:250-255, backlog.py:89 | MEDIUM (2 agents) |
| H10 | **Fallback deterministic synthesis differs from arbiter** — Different verdict rules | council_service.py:543-588 | MEDIUM (1 agent) |
| H11 | **Attempt counter race** — increment_spec_attempt decoupled from state update | backlog.py:248-282 | MEDIUM (2 agents) |
| H12 | **Subprocess timeout masked as pass in delta comparison** — Empty output → all metrics 0 | runner.py:324-328 | MEDIUM (1 agent) |
| H13 | **Observing recovery broken** — "observing" attempts recovered without restoring tickets | dispatch.py:431-587 | HIGH (1 agent) |
| H14 | **Task-level blocking for DAG** — One observing step blocks entire task, not just that step | observation_handler.py:148 | MEDIUM (1 agent) |
| H15 | **Trust zone inference fails on empty file_plan** — Returns "normal" for critical goals | llm_spec_generator.py:202-217 | MEDIUM (1 agent) |
| H16 | **fire_first() no exception handling** — Unlike fire(), crashes kill entire hook chain | hooks.py:72-78 | MEDIUM (1 agent) |
| H17 | **Learning only on success** — Failed iterations create no lessons | orchestrator.py:1018-1227 | MEDIUM (1 agent) |
| H18 | **Poller timeout oscillation** — mark_failed clears implementing_started_at → immediate re-entry | orchestrator.py, backlog.py:265 | MEDIUM (1 agent) |
| H19 | **_check_dag_task_terminal returns None treated as "still running"** | orchestrator.py:1753-1790 | LOW (1 agent) |
| H20 | **Missing file existence check for benchmark changed_files** | runner.py:230-232 | MEDIUM (1 agent) |

### TIER 3: MEDIUM — Quality and robustness improvements

| ID | Issue | Files | Confidence |
|----|-------|-------|-----------|
| M1 | **No global iteration timeout** — Only IMPLEMENTING has 15min limit | orchestrator.py | HIGH |
| M2 | **Verification plan measurement_command shell=True** — Unvalidated commands from LLM | orchestrator.py:308-335 | HIGH |
| M3 | **Baseline capture latency** — pyright takes ~60s during PLANNING | orchestrator.py:238-278 | MEDIUM |
| M4 | **Delta comparison only total counts** — New+old errors cancel out | orchestrator.py, runner.py | MEDIUM |
| M5 | **Council global timeout vs per-reviewer mismatch** — 120s global < sum of individual timeouts | council_service.py:27,343 | HIGH |
| M6 | **Module detection fragile** — Hard-coded list in _derive_constraints_from_research | spec_generator.py:99-105 | MEDIUM |
| M7 | **No step count limit in LLM decomposer** | llm_task_decomposer.py:125 | MEDIUM |
| M8 | **final_check dependencies unchecked** — LLM can create final_check with no deps | llm_task_decomposer.py:154-155 | MEDIUM |
| M9 | **Token budget not tracked for metaloop** — Unlimited LLM spend | orchestrator.py | MEDIUM |
| M10 | **Research path exclusions incomplete** — Missing .mypy_cache, .pytest_cache | strategies.py:347-355 | LOW |
| M11 | **No research caching across iterations** — Full pipeline rerun each time | tools.py | LOW |
| M12 | **Incomplete ALLOWED_TRANSITIONS** — No entries for ACCEPTED/REJECTED phases | models.py:38-62 | LOW |
| M13 | **Empty changed_files validated but not warned in council** | council_service.py:150-307 | MEDIUM |
| M14 | **JSON parsing silent data loss in council** — Malformed LLM response → empty findings | council_service.py:68-118 | MEDIUM |
| M15 | **Lint future timeout doesn't cancel** — Resource leak in council | council_service.py:275-284 | LOW |
| M16 | **Git diff unbounded in council** — Can exceed LLM context window | council_service.py:47-65 | MEDIUM |
| M17 | **can_revise boundary ambiguity** — revision_cycle < 3 allows cycles 0,1,2 | models.py:86-87 | LOW |
| M18 | **max_retries default mismatch** — MAX_ATTEMPTS=3 but mark_failed defaults to 2 | models.py:66, backlog.py:248 | LOW |
| M19 | **No validation of empty perspectives tuple** | council_service.py:142 | LOW |
| M20 | **Signal consumer can flood backlog** — No atomic slot claim before spec creation | orchestrator.py:2040-2050 | LOW |

### TIER 4: LOW — Code quality, logging, documentation

~60 additional issues across all agents (naming inconsistencies, missing logs, doc gaps, minor edge cases)

## Cross-Reference Matrix: Issues confirmed by multiple agents

| Issue | Agent 1 | Agent 2 | Agent 3 | Agent 4 |
|-------|---------|---------|---------|---------|
| Metadata race (C17) | Error+Timeout | PLANNING | REVIEWING | Backlog |
| No phase timeouts (C15) | Error+Timeout | PLANNING | — | — |
| Semaphore starvation (H6) | Provider | Council | — | — |
| Provider None (H4) | PLANNING | Provider | — | — |
| SUBTASK_COMPLETE race (C18) | Dispatch | Hooks | — | — |
| needs_attention join (C16) | Dispatch | Error+Timeout | — | — |
| Approval stuck (C14) | Error+Timeout | PLANNING | — | — |
| Revision cycle reset (H1) | Backlog | Poller | — | — |
