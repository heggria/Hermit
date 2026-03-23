"""Core data models for the Trace-Contract-Driven Assurance System.

All models are immutable-first dataclasses following kernel conventions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _id(prefix: str = "assurance") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Scenario metadata & retention
# ---------------------------------------------------------------------------


@dataclass
class ScenarioMetadata:
    """Scenario metadata block."""

    name: str = ""
    description: str = ""
    owner: str = ""
    tags: list[str] = field(default_factory=list)
    risk_band: str = "medium"
    source_ref: str = ""


@dataclass
class EvidenceRetention:
    """Evidence retention policy."""

    raw_ttl_days: int = 30
    sanitized_ttl_days: int = 365
    proof_ttl_days: int = 3650
    redact_fields: list[str] = field(default_factory=list)


@dataclass
class OracleSpec:
    """Acceptance criteria for a scenario run."""

    final_state: str = "completed"
    must_pass_contracts: list[str] = field(default_factory=list)
    allowed_failures: list[str] = field(default_factory=list)
    max_duplicate_side_effects: int = 0
    max_unresolved_violations: int = 0


# ---------------------------------------------------------------------------
# Trace envelope
# ---------------------------------------------------------------------------


@dataclass
class TraceEnvelope:
    """Append-only trace record for a single runtime event."""

    trace_id: str
    run_id: str
    task_id: str
    event_type: str
    event_seq: int
    wallclock_at: float
    logical_clock: int

    scenario_id: str | None = None
    step_id: str | None = None
    step_attempt_id: str | None = None
    phase: str | None = None
    actor_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None

    artifact_refs: list[str] = field(default_factory=list)
    approval_ref: str | None = None
    decision_ref: str | None = None
    grant_ref: str | None = None
    lease_ref: str | None = None
    receipt_ref: str | None = None

    restart_epoch: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------


@dataclass
class FaultSpec:
    """Fault injection specification."""

    injection_point: str
    trigger_condition: dict[str, Any] = field(default_factory=dict)
    fault_mode: str = ""
    scope: str = "step_attempt"
    cardinality: str = "once"  # once | repeated | probabilistic
    timing: str = "pre"  # pre | mid | post
    delivery: str = "sync"  # sync | async
    replayable: bool = True
    attributable: bool = True
    severity: str = "high"
    expected_observables: list[str] = field(default_factory=list)


@dataclass
class FaultHandle:
    """Reference to an armed fault."""

    handle_id: str
    fault_spec: FaultSpec
    armed_at: float = field(default_factory=time.time)
    triggered: bool = False
    trigger_count: int = 0


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@dataclass
class TraceContractSpec:
    """Trace contract definition with predicate algebra assertions."""

    contract_id: str
    scope: dict[str, Any] = field(default_factory=dict)
    severity: str = "high"
    mode: str = "runtime"  # runtime | post_run | both
    assert_expr: dict[str, Any] = field(default_factory=dict)
    evidence_requirements: list[str] = field(default_factory=list)
    remediation_hint: str = ""
    fail_open: bool = False


@dataclass
class ContractViolation:
    """Record of a contract violation."""

    violation_id: str
    contract_id: str
    severity: str
    mode: str
    task_id: str
    evidence: dict[str, Any] = field(default_factory=dict)
    event_id: str | None = None
    remediation_hint: str = ""
    detected_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@dataclass
class InvariantSpec:
    """Invariant definition."""

    invariant_id: str
    scope: str = ""
    detection_method: str = ""
    severity: str = "blocker"
    evidence_fields: list[str] = field(default_factory=list)
    remediation_hint: str = ""


@dataclass
class InvariantViolation:
    """Record of an invariant violation."""

    violation_id: str
    invariant_id: str
    severity: str
    event_id: str
    task_id: str
    evidence: dict[str, Any] = field(default_factory=dict)
    step_attempt_id: str | None = None
    detected_at: float = field(default_factory=time.time)
    trace_slice_start: int = 0
    trace_slice_end: int = 0


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class ReplayEntry:
    """Replay corpus entry."""

    entry_id: str
    scenario_id: str
    run_id: str
    trace_schema_version: int = 1
    contract_pack_version: int = 1
    event_head_hash: str = ""
    snapshot_ref: str | None = None
    source: str = "live"
    sanitized: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass
class CounterfactualMutation:
    """Specification for a counterfactual trace mutation."""

    mutation_id: str
    mutation_type: str  # replace_event | drop_event | rewrite_artifact | toggle_approval | advance_restart_epoch
    target_ref: str
    replacement: dict[str, Any] | None = None
    description: str = ""


@dataclass
class ReplayResult:
    """Output of a replay or counterfactual replay."""

    replay_id: str
    entry_id: str
    mutations: list[CounterfactualMutation] = field(default_factory=list)
    trace_path: list[str] = field(default_factory=list)
    contract_violations: list[ContractViolation] = field(default_factory=list)
    state_transitions: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    recovery_depth: int = 0
    timing_profile: dict[str, Any] = field(default_factory=dict)
    diff_summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@dataclass
class AttributionNode:
    """Node in a causal attribution graph."""

    node_id: str
    node_type: str  # task | step | step_attempt | phase | tool_call | approval | decision | grant | lease | message | artifact | event | fault | contract_violation | recovery_action
    ref: str
    role: str = "unknown"  # root_cause | enabler | propagator | victim | mitigator


@dataclass
class AttributionEdge:
    """Edge in a causal attribution graph."""

    source: str
    target: str
    edge_type: str  # caused_by | propagates_to | guards | mitigates | invalidates | replays_as


@dataclass
class AttributionCase:
    """Complete attribution result for a failure."""

    case_id: str
    failure_signature: str = ""
    first_divergence: str = ""
    root_cause_candidates: list[str] = field(default_factory=list)
    selected_root_cause: str = ""
    propagation_chain: list[str] = field(default_factory=list)
    counterfactuals: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    fix_hints: list[str] = field(default_factory=list)
    nodes: list[AttributionNode] = field(default_factory=list)
    edges: list[AttributionEdge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSpec:
    """Complete scenario definition."""

    scenario_id: str
    schema_version: int = 1
    contract_pack_version: int = 1
    trace_schema_version: int = 1
    determinism_budget: dict[str, Any] = field(default_factory=dict)

    metadata: ScenarioMetadata = field(default_factory=ScenarioMetadata)
    workload: dict[str, Any] = field(default_factory=dict)
    phase_distribution: list[dict[str, Any]] = field(default_factory=list)
    concurrency_topology: dict[str, Any] = field(default_factory=dict)
    approval_policy: dict[str, Any] = field(default_factory=dict)
    restart_plan: dict[str, Any] = field(default_factory=dict)
    fault_injection_plan: list[FaultSpec] = field(default_factory=list)
    adversarial_perturbation_plan: list[dict[str, Any]] = field(default_factory=list)

    trace_contracts_enabled: list[str] = field(default_factory=list)
    attribution_mode: str = "off"  # off | post_run | streaming
    replay: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence_retention: EvidenceRetention = field(default_factory=EvidenceRetention)
    oracle: OracleSpec = field(default_factory=OracleSpec)


# ---------------------------------------------------------------------------
# Assurance report
# ---------------------------------------------------------------------------


@dataclass
class AssuranceReport:
    """Unified assurance report — source of truth for both JSON and markdown."""

    report_id: str
    scenario_id: str
    run_id: str
    status: str  # pass | fail
    verdict: str = ""

    first_violation: ContractViolation | InvariantViolation | None = None

    timelines: dict[str, Any] = field(default_factory=dict)
    violations: list[ContractViolation | InvariantViolation] = field(default_factory=list)
    attribution: AttributionCase | None = None

    fault_impact_graph: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)
    duplicates: dict[str, Any] = field(default_factory=dict)
    stuck_orphans: dict[str, Any] = field(default_factory=dict)
    side_effect_audit: dict[str, Any] = field(default_factory=dict)
    approval_bottlenecks: dict[str, Any] = field(default_factory=dict)
    adversarial: dict[str, Any] = field(default_factory=dict)
    regression_comparison: dict[str, Any] = field(default_factory=dict)
    replay_diff: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Cause taxonomy constants
# ---------------------------------------------------------------------------

CAUSE_INPUT_FAULT = "input_fault"
CAUSE_MESSAGE_DISTORTION = "message_distortion"
CAUSE_TOOL_FAILURE = "tool_failure"
CAUSE_STATE_CORRUPTION = "state_corruption"
CAUSE_COORDINATION_COLLAPSE = "coordination_collapse"
CAUSE_APPROVAL_DEADLOCK = "approval_deadlock"
CAUSE_RECOVERY_BUG = "recovery_bug"
CAUSE_ADVERSARIAL_INJECTION = "adversarial_injection"

# ---------------------------------------------------------------------------
# Injection point constants
# ---------------------------------------------------------------------------

INJ_INGRESS = "ingress"
INJ_PHASE_HANDLER = "phase_handler"
INJ_QUEUE_DISPATCH = "queue_dispatch"
INJ_TOOL_PRE_CALL = "tool_pre_call"
INJ_TOOL_MID_CALL = "tool_mid_call"
INJ_TOOL_POST_CALL = "tool_post_call"
INJ_LEDGER_WRITE = "ledger_write"
INJ_ARTIFACT_WRITE = "artifact_write"
INJ_APPROVAL_QUEUE = "approval_queue"
INJ_RESTART_BOUNDARY = "restart_boundary"
INJ_RECOVERY_BOUNDARY = "recovery_boundary"
INJ_MEMORY_WRITE = "memory_write"
INJ_MESSAGE_TRANSIT = "message_transit"
INJ_WORKSPACE_WRITE = "workspace_write"
INJ_BRANCH_MUTATION = "branch_mutation"
