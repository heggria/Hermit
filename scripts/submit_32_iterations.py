#!/usr/bin/env python3
"""Submit 32 kernel evolution iterations to Hermit MCP server via Streamable HTTP."""

import json
import subprocess
import time

MCP_URL = "http://127.0.0.1:8322/mcp"


def curl_mcp(method: str, params: dict, session_id: str = "") -> tuple[str, str]:
    """Send MCP request via curl; return (body, session_id)."""
    payload = {
        "jsonrpc": "2.0",
        "id": f"req-{method}-{int(time.time())}",
        "method": method if method in ("initialize",) else "tools/call",
        "params": params if method == "initialize" else {"name": method, "arguments": params},
    }
    cmd = [
        "curl",
        "-s",
        "-D",
        "-",
        "-X",
        "POST",
        MCP_URL,
        "-H",
        "Content-Type: application/json",
        "-H",
        "Accept: application/json, text/event-stream",
    ]
    if session_id:
        cmd += ["-H", f"Mcp-Session-Id: {session_id}"]
    cmd += ["-d", json.dumps(payload)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stdout

    # Extract session ID from headers
    sid = session_id
    for line in output.split("\n"):
        if line.lower().startswith("mcp-session-id:"):
            sid = line.split(":", 1)[1].strip()
            break

    # Extract body (after blank line in HTTP response)
    parts = output.split("\r\n\r\n", 1)
    body = parts[1] if len(parts) > 1 else output

    return body, sid


def parse_sse(body: str) -> dict:
    """Parse SSE data lines from response body."""
    for line in body.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    # Try plain JSON
    try:
        return json.loads(body.strip())
    except json.JSONDecodeError:
        return {"raw": body[:500]}


def init_session() -> str:
    """Initialize MCP session and return session ID."""
    _body, sid = curl_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "claude-32-submitter", "version": "1.0"},
        },
    )
    print(f"Session initialized: {sid}")
    return sid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 32 Iteration Definitions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ITERATIONS = [
    # Group 1: State Machine Integrity
    {
        "goal": "Register phantom states in formal state machine. Add verification_blocked, budget_exceeded, receipt_pending, needs_attention, reconciling, blocked, and executing to TaskState/StepAttemptState enums in src/hermit/kernel/task/state/enums.py. Define full transition rules for each new state. Update TASK_STATE_TRANSITIONS and STEP_ATTEMPT_STATE_TRANSITIONS dicts. Add tests validating every new state has at least one inbound and one outbound transition. Verify recovery code handles all new states. Run make check.",
        "priority": "high",
        "research_hints": [
            "kernel/task/state/enums.py",
            "kernel/execution/executor/executor.py",
            "kernel/task/services/dag_execution.py",
        ],
    },
    {
        "goal": "Replace character-count budget tracking with token-based estimation. In src/hermit/kernel/execution/executor/executor.py line ~1607, budget uses len(str()) which measures characters not tokens. Implement TokenEstimator in src/hermit/kernel/execution/ using cl100k_base-compatible heuristic (chars/3.5). Replace all character-count budget calculations. Add budget_estimation_method to receipt metadata. Write tests validating estimation accuracy within 20% for English, Chinese, and code. Run make check.",
        "priority": "high",
        "research_hints": [
            "kernel/execution/executor/executor.py lines 1600-1630",
            "kernel/verification/receipts/",
        ],
    },
    {
        "goal": "Replace unsafe eval() in DAG predicate evaluation with AST-whitelisted safe evaluator. In src/hermit/kernel/task/services/dag_builder.py line ~487, eval() with __builtins__={} is escapable. Implement SafePredicateEvaluator in src/hermit/kernel/task/services/predicates.py: parse to AST, whitelist only Compare, BoolOp, UnaryOp, Name, Constant. Pre-compile and cache at materialization. Test: valid predicates pass, injection attempts rejected. Run make check.",
        "priority": "high",
        "research_hints": [
            "kernel/task/services/dag_builder.py",
            "kernel/task/services/dag_execution.py",
        ],
    },
    {
        "goal": "Fix BlackboardService to use public append_event() API instead of private _append_event_tx(). In src/hermit/kernel/artifacts/blackboard.py lines 51, 106, 133, private API is called bypassing hash chain. Replace with self._store.append_event(). Type store as KernelStore. Test event sequence monotonicity after blackboard ops. Verify hash chain integrity. Run make check.",
        "priority": "high",
        "research_hints": ["kernel/artifacts/blackboard.py", "kernel/ledger/journal/store.py"],
    },
    # Group 2: Execution Pipeline Hardening
    {
        "goal": "Persist WorkspaceLeaseQueue to SQLite for crash recovery. In-memory _queue dict in src/hermit/kernel/authority/workspaces/service.py loses requests on restart. Add workspace_lease_queue table (queue_id, workspace_id, holder_principal_id, requested_at, status, priority). Implement persist/recover. On init, recover pending entries. Test: queue 3 leases, restart service, verify recovery. Test concurrent safety. Run make check.",
        "priority": "high",
        "research_hints": [
            "kernel/authority/workspaces/service.py",
            "kernel/ledger/journal/store.py",
        ],
    },
    {
        "goal": "Unify DAG activation by refactoring TaskController.finalize_result() to delegate to DAGExecutionService.advance(). Currently controller.py lines 1004-1032 reimplements progression inline, bypassing verification gates and conditionals. Extract common logic into advance(). Integration test: DAG with verification step → finalize via controller → verification gate checked. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/task/services/controller.py lines 1000-1050",
            "kernel/task/services/dag_execution.py",
        ],
    },
    {
        "goal": "Migrate heartbeat storage from context JSON to dedicated StepAttemptRecord column. In dispatch.py, report_heartbeat() writes to context JSON instead of last_heartbeat_at column. check_heartbeat_timeouts() reads from JSON. Fix both to use column. Add index on last_heartbeat_at. Test: report heartbeat → column updated → timeout detected. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/execution/coordination/dispatch.py",
            "kernel/ledger/journal/store.py",
        ],
    },
    {
        "goal": "Implement CapabilityGrant TTL enforcement at invoke_tool_handler(). Grants have TTL but executor doesn't verify expiry before execution. Add grant_expires_at to execution contract. Verify grant validity before invocation. If expired, suspend attempt for re-authorization. Test: 1s TTL → wait 2s → denied GRANT_EXPIRED. Test: within TTL → succeeds. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/execution/executor/executor.py", "kernel/authority/"],
    },
    # Group 3: Performance Optimization
    {
        "goal": "Optimize verification gate check from O(n_deps*n_receipts) to O(1). _check_verification_gate_blocked() in dag_execution.py calls list_receipts_for_step() per dependency. Add compound index (step_id, reconciliation_required). Replace with targeted query WHERE reconciliation_required=1 LIMIT 1. Early exit on first blocking receipt. Cache result. Benchmark: 50-step DAG with 100 receipts/step < 50ms. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/task/services/dag_execution.py lines 209-259",
            "kernel/ledger/journal/store.py",
        ],
    },
    {
        "goal": "Cache DAG conditional evaluation mapping. _evaluate_conditional_steps() in dag_execution.py rebuilds key_to_step_id per step (40+ DB queries for 20 steps). Pre-build mapping once in advance() before loop. Pass cached mapping. Performance test: 30 conditional steps → total DB queries < 10. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/task/services/dag_execution.py lines 158-207"],
    },
    {
        "goal": "Optimize super-step checkpoint: _maybe_emit_super_step_checkpoint() fetches ALL task steps via list_steps(). For 100-step tasks this is full scan per completion. Implement completion tracking in indexed side-table or memory. Only query when counter matches expected. Add index (task_id, super_step_group). Test: 100-step DAG, 5 groups → checkpoint exactly once per group. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/task/services/dag_execution.py lines 311-362"],
    },
    {
        "goal": "Add missing compound indexes and audit N+1 patterns. Add indexes: (step_id, reconciliation_required) on receipts, (workspace_id, status) on workspace_leases, (task_id, status) on step_attempts. Audit kernel services for N+1 patterns. Refactor top-3 worst offenders to batch queries. Add EXPLAIN QUERY PLAN tests for critical queries. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/ledger/journal/store.py"],
    },
    # Group 4: Security Hardening
    {
        "goal": "Harden dynamic SQL in schema migrations. _ensure_column() in store.py uses f-string interpolation for table/column names. Add _ALLOWED_TABLES frozenset. Validate column names with regex ^[a-z_][a-z0-9_]*$. Reject invalid inputs. Apply to all migration methods. Test: invalid table → SecurityError. Test: injection in column → rejected. Run make check.",
        "priority": "high",
        "research_hints": ["kernel/ledger/journal/store.py"],
    },
    {
        "goal": "Make HMAC proof signing mandatory with startup validation. ReceiptService silently returns None when HERMIT_PROOF_SIGNING_SECRET absent. Change to fail-fast at startup. Add signing_active property. Audit coverage: verify signing includes result_code, action_type, tool_name, timestamp, task_id. Test: missing secret → startup fails. Allow HERMIT_PROOF_SIGNING_SECRET=test-only for tests. Run make check.",
        "priority": "high",
        "research_hints": [
            "kernel/verification/receipts/receipts.py",
            "kernel/verification/proofs/",
        ],
    },
    {
        "goal": "Implement budget TOCTOU prevention. Between BudgetGuard check and tool invocation, budget can change from concurrent steps. Implement task-level budget lock: acquire before check, hold through execution+update, release after receipt. Use BEGIN IMMEDIATE or per-task lock. Test: two concurrent steps exceeding budget → only one succeeds. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/policy/guards/rules_budget.py",
            "kernel/execution/executor/executor.py",
        ],
    },
    {
        "goal": "Add policy guard decision audit trail. Guards return RuleOutcome|None without logging rationale. Add GuardDecision dataclass (guard_name, outcome, reason, evaluated_at, evidence_refs). Add decision_trace to PolicyDecision. Each guard appends to trace. Store in attempt context. Add query list_guard_decisions(task_id, step_id). Test: 3-guard chain → 3 trace entries. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/policy/guards/", "kernel/policy/models/"],
    },
    # Group 5: Team/Worker Evolution
    {
        "goal": "Implement pool-to-team active binding. WorkerPoolManager doesn't enforce team membership on slot claims. Add team_id to WorkerSlot. Verify requesting role matches team role_assembly. Track per-team slot usage against declared limits. Add team utilization to WorkerPoolStatus. Test: team executor:2 → third claim rejected. Test: undeclared role → rejected. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/execution/workers/pool.py", "kernel/task/models/team.py"],
    },
    {
        "goal": "Persist deliberation debates to kernel ledger. DeliberationService is in-memory only. Add tables: deliberation_debates, deliberation_proposals, deliberation_critiques, deliberation_decisions. Store lifecycle events. Add list_debates_by_task(). Test: debate → proposals → arbitrate → restart → decision recoverable. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/execution/competition/", "kernel/ledger/journal/store.py"],
    },
    {
        "goal": "Implement auto-completion cascades: milestone→team→program. On milestone completion, check all team milestones. If complete, auto-transition team to COMPLETED. Check all program teams. If complete, auto-transition program. Handle failure: failed milestone → check deps → possibly fail team/program. Test: 3 milestones complete → team → program auto-complete. Test: milestone fails → cascade. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/task/services/program_manager.py",
            "kernel/task/models/team.py",
            "kernel/task/models/program.py",
        ],
    },
    {
        "goal": "Enforce team context boundary in policy guards. Teams have context_boundary but it's never enforced. Add ContextBoundaryGuard: determine team from task, extract target paths from tool input, check within boundary, DENY for out-of-scope. Register after filesystem guard. Test: boundary ['src/hermit/kernel/'] → 'src/hermit/runtime/' denied. Test: within boundary → allowed. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/policy/guards/", "kernel/task/models/team.py"],
    },
    # Group 6: Frontier Architecture
    {
        "goal": "Implement adaptive topology router for auto DAG structure selection. Add TopologyRouter to kernel/task/services/: analyze task description to select PARALLEL/SEQUENTIAL/HIERARCHICAL/HYBRID topology. Use keyword/intent analysis. Consider priority, budget, historical patterns from TaskPatternLearner. Return TopologyRecommendation with type, confidence, suggested nodes. Test each topology type. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/task/services/dag_builder.py", "kernel/execution/controller/"],
    },
    {
        "goal": "Implement communication density control with token budget guards. Add communication_budget_tokens to TaskRecord. Add DensityGuard: track inter-step message tokens, warn at 80%, deny new steps at 100%, record metrics. Add density_utilization to projection. Config per profile: autonomous=unlimited, default=100k, supervised=50k. Test: exceed budget → denied. Test: autonomous → unlimited. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/task/models/", "kernel/policy/guards/"],
    },
    {
        "goal": "Implement subgraph reopening on verification failure. When verification fails, spawn repair steps targeting gaps while preserving completed branches. Add SubgraphReopener: identify failed verification target, analyze feedback, spawn 'patch' steps, wire as new deps for re-check. Use add_step() for dynamic injection. Test: 5-step DAG → step 3 fails verification → repair → re-verify → passes. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/task/services/dag_execution.py",
            "kernel/task/services/dag_builder.py",
        ],
    },
    {
        "goal": "Implement typed blackboard schema definitions. Add BlackboardSchema with typed sections: findings, contradictions, gaps, synthesis. Add schema_id to BlackboardEntry. Add validate_entry(). Register default schemas for research/code/review tasks. Emit schema_violation events. Test: matching schema → passes. Test: violation → event emitted. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/artifacts/blackboard.py", "kernel/artifacts/models.py"],
    },
    # Group 7: Verification & Proof System
    {
        "goal": "Implement local-log proof anchoring. AnchorMethod base has no implementations. Add LocalLogAnchor: write proof summary + Merkle root to append-only ~/.hermit/kernel/proof-anchors.jsonl. Use atomic_write. Include timestamp, task_id, proof_hash, merkle_root. Implement verify() by log lookup. Add AnchorResult model. Test: anchor → verify → passes. Test: tampered → fails. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/verification/proofs/anchoring.py", "infra/storage/atomic.py"],
    },
    {
        "goal": "Implement git-notes proof anchoring. Add GitNotesAnchor: attach proof hash to HEAD via git notes namespace 'hermit-proofs'. Store JSON {proof_hash, task_id, merkle_root, anchored_at}. Implement verify(). Handle no-git-repo → fall back to local-log. Handle changed commit → warn. Test: anchor to note → verify. Test: no repo → fallback. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/verification/proofs/anchoring.py"],
    },
    {
        "goal": "Add evidence validation at memory promotion boundary. Evidence refs aren't validated against promoted content. Add EvidenceValidator in kernel/context/memory/: validate assertion_hash matches content hash, evidence from same task lineage, evidence not expired (7d TTL). Integrate with promotion service. Test: valid → promotes. Test: hash mismatch → denied. Test: cross-task → denied. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/context/memory/", "kernel/signals/"],
    },
    {
        "goal": "Implement proof chain completeness analysis. Add ChainCompletenessAnalyzer: walk full receipt chain for task_id, detect gaps (missing receipts), unsigned receipts, missing witnesses. Compute completeness_score (0-1). Return CompletenessReport. Integrate with proof export summary tier. Test: complete chain → 1.0. Test: 2 gaps → score reflects. Test: unsigned → warning. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/verification/proofs/", "kernel/verification/receipts/"],
    },
    # Group 8: Observability & Recovery
    {
        "goal": "Push analytics time-window filtering to SQL store layer. AnalyticsEngine has explicit TODO for this. Currently LIMIT applies before time filter, dropping in-window records. Add list_receipts_in_window(start, end, limit), list_events_in_window, list_approvals_in_window to KernelStore. Refactor AnalyticsEngine. Test: 100 receipts, window=last 10 → exactly 10. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/analytics/engine.py", "kernel/ledger/journal/store.py"],
    },
    {
        "goal": "Implement bounded observation ticket recovery. list_active_observation_tickets() has no LIMIT. Add LIMIT+OFFSET. Implement paginated recovery: recover_observation_tickets(batch_size=50). Priority ordering: high-priority tasks first. Max 200 tickets. Test: 500 tickets → batches of 50. Test: priority ordering. Run make check.",
        "priority": "normal",
        "research_hints": [
            "kernel/ledger/journal/store.py",
            "kernel/execution/coordination/observation.py",
        ],
    },
    {
        "goal": "Implement LRU eviction for event hash cache. Per-task hash cache in store.py is unbounded. Add LRUHashCache(max_size=10000) using OrderedDict. Move to end on hit. Evict oldest on full insert. Add cache_stats(). Replace dict in KernelStore. Test: 15000 entries → size=10000. Test: LRU eviction order. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/ledger/journal/store.py"],
    },
    {
        "goal": "Implement schema migration version tracking. Migrations run on every startup relying on idempotency. Add schema_migrations table (version PK, applied_at, description). Check before running. Track in-memory. Add migrate() for unapplied only. Add get_schema_version(). Test: first startup → all tracked. Test: second → none re-run. Test: new migration → only new runs. Run make check.",
        "priority": "normal",
        "research_hints": ["kernel/ledger/journal/store.py"],
    },
]


def main():
    assert len(ITERATIONS) == 32, f"Expected 32, got {len(ITERATIONS)}"

    # Initialize session
    print("Initializing MCP session...")
    body, session_id = curl_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "claude-32-submitter", "version": "1.0"},
        },
    )
    print(f"Session: {session_id}\n")

    # Submit in 4 batches of 8
    batch_size = 8
    all_iteration_ids = []
    all_results = []

    for batch_num in range(1, 5):
        start = (batch_num - 1) * batch_size
        end = start + batch_size
        batch = ITERATIONS[start:end]

        print(f"{'=' * 60}")
        print(f"Batch {batch_num}/4: Submitting iterations {start + 1}-{end}")
        print(f"{'=' * 60}")
        for i, it in enumerate(batch):
            print(f"  [{start + i + 1:02d}] {it['goal'][:75]}...")

        body, session_id = curl_mcp(
            "hermit_submit_iteration",
            {
                "iterations": batch,
                "policy_profile": "autonomous",
            },
            session_id,
        )

        result = parse_sse(body)
        all_results.append(result)

        # Extract iteration IDs
        try:
            # Navigate MCP response structure
            content = result.get("result", {}).get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    data = json.loads(item["text"])
                    if "results" in data:
                        for res in data["results"]:
                            if "iteration_id" in res:
                                all_iteration_ids.append(res["iteration_id"])
                                print(f"    → {res['iteration_id']} ({res.get('status', '?')})")
        except Exception as e:
            print(f"  Parse error: {e}")
            print(f"  Raw: {json.dumps(result, indent=2, ensure_ascii=False)[:300]}")

        print()
        time.sleep(1)  # Brief pause between batches

    # Summary
    print(f"\n{'=' * 60}")
    print("SUBMISSION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total submitted: {len(all_iteration_ids)}/{len(ITERATIONS)}")

    if all_iteration_ids:
        print("\nIteration IDs:")
        for iid in all_iteration_ids:
            print(f"  {iid}")

    # Save for monitoring
    output = {
        "session_id": session_id,
        "iteration_ids": all_iteration_ids,
        "count": len(all_iteration_ids),
        "submitted_at": time.time(),
        "raw_results": all_results,
    }
    with open("/tmp/hermit_32_iterations.json", "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print("\nSaved to /tmp/hermit_32_iterations.json")

    return output


if __name__ == "__main__":
    main()
