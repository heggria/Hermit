"""Shared fixtures for assurance tests."""

from __future__ import annotations

import time
import uuid

import pytest

from hermit.kernel.verification.assurance.models import (
    EvidenceRetention,
    FaultSpec,
    InvariantSpec,
    OracleSpec,
    ScenarioMetadata,
    ScenarioSpec,
    TraceContractSpec,
    TraceEnvelope,
)


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_envelope(
    *,
    run_id: str = "run-test",
    task_id: str = "task-test",
    event_type: str = "generic",
    event_seq: int = 0,
    **overrides: object,
) -> TraceEnvelope:
    """Factory for TraceEnvelope with sensible defaults."""
    defaults: dict[str, object] = {
        "trace_id": _uid("trace"),
        "run_id": run_id,
        "task_id": task_id,
        "event_type": event_type,
        "event_seq": event_seq,
        "wallclock_at": time.time(),
        "logical_clock": event_seq,
    }
    defaults.update(overrides)
    return TraceEnvelope(**defaults)  # type: ignore[arg-type]


def make_governed_trace(
    num_steps: int = 3,
    *,
    run_id: str = "run-test",
    task_id: str = "task-test",
) -> list[TraceEnvelope]:
    """Create a realistic governed-execution trace.

    Generates: task_created, then for each step:
      approval_requested → approval_granted → tool_call_start → receipt_issued
    """
    envelopes: list[TraceEnvelope] = []
    seq = 0
    now = time.time()

    envelopes.append(
        make_envelope(
            run_id=run_id,
            task_id=task_id,
            event_type="task.created",
            event_seq=seq,
            wallclock_at=now,
        )
    )
    seq += 1

    for step_idx in range(num_steps):
        step_id = f"step-{step_idx}"
        attempt_id = f"attempt-{step_idx}"
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        receipt_id = _uid("receipt")
        decision_id = _uid("decision")

        for evt_type, extra in [
            ("approval.requested", {"approval_ref": approval_id}),
            ("approval.granted", {"approval_ref": approval_id, "decision_ref": decision_id}),
            (
                "tool_call.start",
                {
                    "grant_ref": grant_id,
                    "lease_ref": lease_id,
                    "decision_ref": decision_id,
                    "approval_ref": approval_id,
                },
            ),
            (
                "receipt.issued",
                {
                    "receipt_ref": receipt_id,
                    "grant_ref": grant_id,
                    "lease_ref": lease_id,
                    "decision_ref": decision_id,
                },
            ),
        ]:
            envelopes.append(
                make_envelope(
                    run_id=run_id,
                    task_id=task_id,
                    event_type=evt_type,
                    event_seq=seq,
                    wallclock_at=now + seq * 0.001,
                    step_id=step_id,
                    step_attempt_id=attempt_id,
                    **extra,
                )
            )
            seq += 1

    envelopes.append(
        make_envelope(
            run_id=run_id,
            task_id=task_id,
            event_type="task.completed",
            event_seq=seq,
            wallclock_at=now + seq * 0.001,
        )
    )
    return envelopes


@pytest.fixture()
def sample_envelopes() -> list[TraceEnvelope]:
    """A simple governed execution trace with 3 steps."""
    return make_governed_trace(num_steps=3)


@pytest.fixture()
def sample_scenario() -> ScenarioSpec:
    """The gov-chaos-restart-v1 scenario from the spec."""
    return ScenarioSpec(
        scenario_id="gov-chaos-restart-v1",
        schema_version=1,
        contract_pack_version=3,
        trace_schema_version=2,
        metadata=ScenarioMetadata(
            name="governed_write_under_restart",
            owner="assurance-lab",
            risk_band="high",
            tags=["governance", "restart", "duplicate-delivery"],
        ),
        fault_injection_plan=[
            FaultSpec(
                injection_point="queue_dispatch",
                trigger_condition={"event": "tool_call.start"},
                fault_mode="duplicate_delivery",
                scope="step_attempt",
                cardinality="repeated",
                timing="post",
                delivery="async",
            ),
        ],
        trace_contracts_enabled=[
            "task.lifecycle",
            "approval.gating",
            "side_effect.authorization",
            "receipt.linkage",
            "no_duplicate_execution",
        ],
        attribution_mode="post_run",
        oracle=OracleSpec(
            final_state="completed",
            must_pass_contracts=[
                "task.lifecycle",
                "approval.gating",
                "side_effect.authorization",
            ],
        ),
        evidence_retention=EvidenceRetention(
            raw_ttl_days=30,
            sanitized_ttl_days=365,
            proof_ttl_days=3650,
            redact_fields=["prompt_text", "secret_values"],
        ),
    )


@pytest.fixture()
def sample_fault_spec() -> FaultSpec:
    return FaultSpec(
        injection_point="queue_dispatch",
        trigger_condition={"event": "tool_call.start"},
        fault_mode="duplicate_delivery",
        scope="step_attempt",
        cardinality="once",
    )


@pytest.fixture()
def sample_contract() -> TraceContractSpec:
    return TraceContractSpec(
        contract_id="approval.gating.v1",
        scope={"action_class": ["write_local", "vcs_mutation"]},
        severity="blocker",
        mode="runtime",
        assert_expr={
            "all": [
                {"exists": "approval.granted"},
                {"before": {"event1": "approval.granted", "event2": "tool_call.start"}},
            ]
        },
        remediation_hint="Ensure approval is granted before tool execution",
    )


@pytest.fixture()
def sample_invariant() -> InvariantSpec:
    return InvariantSpec(
        invariant_id="state.task_transition_legality",
        scope="task_state_machine",
        detection_method="state_projection",
        severity="blocker",
        evidence_fields=["task_id", "old_state", "new_state"],
        remediation_hint="Check task controller and state validator",
    )
