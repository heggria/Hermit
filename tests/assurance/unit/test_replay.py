"""Unit tests for ReplayService."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from hermit.kernel.verification.assurance.models import (
    ContractViolation,
    CounterfactualMutation,
    EvidenceRetention,
    InvariantViolation,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.replay import ReplayService
from tests.assurance.conftest import make_governed_trace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc() -> ReplayService:
    return ReplayService()


@pytest.fixture()
def trace() -> list[TraceEnvelope]:
    return make_governed_trace(num_steps=3)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class TestIngest:
    def test_ingest_creates_entry_with_hash(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace, scenario_id="scen-1")

        expected_hash = hashlib.sha256(trace[-1].trace_id.encode()).hexdigest()
        assert entry.event_head_hash == expected_hash
        assert entry.run_id == "run-1"
        assert entry.scenario_id == "scen-1"
        assert entry.entry_id.startswith("replay-")
        assert entry.sanitized is False

    def test_ingest_with_sanitize(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        retention = EvidenceRetention(redact_fields=["secret_values"])
        entry = svc.ingest(
            "run-2", trace, sanitize=True, retention=retention
        )

        assert entry.sanitized is True
        assert entry.event_head_hash != ""

    def test_ingest_empty_trace_raises(self, svc: ReplayService) -> None:
        with pytest.raises(ValueError, match="empty trace"):
            svc.ingest("run-x", [])


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class TestReplay:
    def test_replay_identical_trace_all_same(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        result = svc.replay(entry, trace)

        diff = result.diff_summary
        assert diff["same"] == len(trace)
        assert diff["diverged"] == 0
        assert diff["missing"] == []
        assert diff["extra"] == []
        assert diff["head_hash_match"] is True

    def test_replay_empty_trace_raises(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        with pytest.raises(ValueError, match="empty trace"):
            svc.replay(entry, [])

    def test_replay_trace_path_contains_all_ids(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        result = svc.replay(entry, trace)

        expected_ids = [env.trace_id for env in trace]
        assert result.trace_path == expected_ids


# ---------------------------------------------------------------------------
# Counterfactual - drop_event
# ---------------------------------------------------------------------------


class TestCounterfactualDrop:
    def test_drop_event_creates_missing(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        target_id = trace[2].trace_id  # an approval.requested event

        mutations = [
            CounterfactualMutation(
                mutation_id="mut-1",
                mutation_type="drop_event",
                target_ref=target_id,
                description="Drop an approval event",
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        diff = result.diff_summary

        assert target_id in diff["missing"]
        assert len(result.trace_path) == len(trace) - 1
        assert result.mutations == mutations

    def test_drop_nonexistent_target_no_change(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        mutations = [
            CounterfactualMutation(
                mutation_id="mut-x",
                mutation_type="drop_event",
                target_ref="nonexistent-id",
            )
        ]
        result = svc.counterfactual(entry, trace, mutations)

        assert result.diff_summary["same"] == len(trace)
        assert result.diff_summary["missing"] == []


# ---------------------------------------------------------------------------
# Counterfactual - toggle_approval
# ---------------------------------------------------------------------------


class TestCounterfactualToggleApproval:
    def test_toggle_approval_changes_trace(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)

        # Find an approval.granted envelope
        granted_env = next(
            env for env in trace if env.event_type == "approval.granted"
        )

        mutations = [
            CounterfactualMutation(
                mutation_id="mut-toggle",
                mutation_type="toggle_approval",
                target_ref=granted_env.trace_id,
                description="Flip grant to deny",
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        diff = result.diff_summary

        assert diff["diverged"] >= 1
        # The toggled event should still be present (same trace_id, different content)
        assert granted_env.trace_id not in diff["missing"]

    def test_toggle_non_approval_event_no_change(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)

        # task.created is not an approval event
        task_env = trace[0]
        mutations = [
            CounterfactualMutation(
                mutation_id="mut-noop",
                mutation_type="toggle_approval",
                target_ref=task_env.trace_id,
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        assert result.diff_summary["diverged"] == 0


# ---------------------------------------------------------------------------
# Counterfactual - replace_event
# ---------------------------------------------------------------------------


class TestCounterfactualReplace:
    def test_replace_event_diverges(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        target = trace[1]

        mutations = [
            CounterfactualMutation(
                mutation_id="mut-replace",
                mutation_type="replace_event",
                target_ref=target.trace_id,
                replacement={"event_type": "approval.denied"},
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        assert result.diff_summary["diverged"] >= 1


# ---------------------------------------------------------------------------
# Counterfactual - rewrite_artifact
# ---------------------------------------------------------------------------


class TestCounterfactualRewriteArtifact:
    def test_rewrite_artifact_changes_refs(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        target = trace[3]

        mutations = [
            CounterfactualMutation(
                mutation_id="mut-art",
                mutation_type="rewrite_artifact",
                target_ref=target.trace_id,
                replacement={"artifact_refs": ["art-new-1", "art-new-2"]},
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        assert result.diff_summary["diverged"] >= 1


# ---------------------------------------------------------------------------
# Counterfactual - advance_restart_epoch
# ---------------------------------------------------------------------------


class TestCounterfactualAdvanceRestartEpoch:
    def test_advance_restart_epoch_increments(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        entry = svc.ingest("run-1", trace)
        target_idx = 2
        target = trace[target_idx]

        mutations = [
            CounterfactualMutation(
                mutation_id="mut-epoch",
                mutation_type="advance_restart_epoch",
                target_ref=target.trace_id,
            )
        ]

        result = svc.counterfactual(entry, trace, mutations)
        # All envelopes from target_idx onward diverge (restart_epoch changed)
        assert result.diff_summary["diverged"] >= len(trace) - target_idx


# ---------------------------------------------------------------------------
# diff_traces
# ---------------------------------------------------------------------------


class TestDiffTraces:
    def test_identical_traces_all_same(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        diff = svc.diff_traces(trace, trace)

        assert diff["same"] == len(trace)
        assert diff["diverged"] == 0
        assert diff["missing"] == []
        assert diff["extra"] == []
        assert diff["reordered"] == 0

    def test_missing_detection(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        shorter = trace[:-1]
        diff = svc.diff_traces(trace, shorter)

        assert trace[-1].trace_id in diff["missing"]
        assert diff["extra"] == []

    def test_extra_detection(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        from tests.assurance.conftest import make_envelope

        extended = list(trace) + [
            make_envelope(event_type="extra.event", event_seq=999)
        ]
        diff = svc.diff_traces(trace, extended)

        assert len(diff["extra"]) == 1
        assert diff["missing"] == []

    def test_reordered_detection(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        reordered = list(trace)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        diff = svc.diff_traces(trace, reordered)

        assert diff["reordered"] >= 2
        assert diff["same"] == len(trace)  # content is the same, just reordered

    def test_recovered_counts_recovery_events(
        self, svc: ReplayService
    ) -> None:
        from tests.assurance.conftest import make_envelope

        original = [make_envelope(event_type="task.created", event_seq=0)]
        replayed = [
            make_envelope(event_type="task.created", event_seq=0),
            make_envelope(event_type="recovery.started", event_seq=1),
            make_envelope(event_type="recovery.completed", event_seq=2),
        ]
        diff = svc.diff_traces(original, replayed)

        assert diff["recovered"] == 2


# ---------------------------------------------------------------------------
# sanitize_trace
# ---------------------------------------------------------------------------


class TestSanitizeTrace:
    def test_removes_redacted_fields(
        self, svc: ReplayService
    ) -> None:
        from tests.assurance.conftest import make_envelope

        envelopes = [
            make_envelope(
                event_seq=0,
                payload={"prompt_text": "secret", "safe_data": "ok"},
            ),
            make_envelope(
                event_seq=1,
                payload={"prompt_text": "also_secret", "metric": 42},
            ),
        ]

        retention = EvidenceRetention(redact_fields=["prompt_text"])
        sanitized = svc.sanitize_trace(envelopes, retention)

        assert len(sanitized) == 2
        for env in sanitized:
            assert "prompt_text" not in env.payload

        assert sanitized[0].payload == {"safe_data": "ok"}
        assert sanitized[1].payload == {"metric": 42}

    def test_sanitize_does_not_mutate_originals(
        self, svc: ReplayService
    ) -> None:
        from tests.assurance.conftest import make_envelope

        original_payload = {"secret": "value", "keep": "data"}
        env = make_envelope(event_seq=0, payload=dict(original_payload))
        envelopes = [env]

        retention = EvidenceRetention(redact_fields=["secret"])
        sanitized = svc.sanitize_trace(envelopes, retention)

        # Original must be unchanged
        assert env.payload == original_payload
        # Sanitized must be a different object
        assert sanitized[0] is not env
        assert "secret" not in sanitized[0].payload

    def test_sanitize_no_redact_fields_returns_copies(
        self, svc: ReplayService
    ) -> None:
        from tests.assurance.conftest import make_envelope

        envelopes = [make_envelope(event_seq=0, payload={"data": 1})]
        retention = EvidenceRetention(redact_fields=[])
        sanitized = svc.sanitize_trace(envelopes, retention)

        assert len(sanitized) == 1
        assert sanitized[0] is not envelopes[0]
        assert sanitized[0].payload == {"data": 1}


# ---------------------------------------------------------------------------
# replay_with_assurance
# ---------------------------------------------------------------------------


class TestReplayWithAssurance:
    def test_replay_with_no_engines(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Without engines, result has no violations."""
        entry = svc.ingest("run-1", trace)
        result = svc.replay_with_assurance(entry, trace)

        assert result.contract_violations == []
        assert result.diff_summary["same"] == len(trace)

    def test_replay_with_invariant_engine(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Invariant violations are converted to contract violations in result."""
        entry = svc.ingest("run-1", trace)

        inv_engine = MagicMock()
        inv_violation = InvariantViolation(
            violation_id="iv-1",
            invariant_id="test.invariant",
            severity="blocker",
            event_id="evt-0",
            task_id="task-test",
            evidence={"detail": "bad"},
        )
        inv_engine.check.return_value = [inv_violation]

        result = svc.replay_with_assurance(
            entry, trace, invariant_engine=inv_engine
        )

        assert len(result.contract_violations) == 1
        cv = result.contract_violations[0]
        assert cv.contract_id == "test.invariant"
        assert cv.severity == "blocker"
        assert cv.mode == "invariant"
        inv_engine.check.assert_called_once_with(trace)

    def test_replay_with_contract_engine(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Contract violations from evaluate_post_run are included."""
        entry = svc.ingest("run-1", trace)

        contract_engine = MagicMock()
        cv = ContractViolation(
            violation_id="cv-1",
            contract_id="task.lifecycle",
            severity="high",
            mode="post_run",
            task_id="task-test",
        )
        contract_engine.evaluate_post_run.return_value = [cv]

        result = svc.replay_with_assurance(
            entry, trace, contract_engine=contract_engine
        )

        assert len(result.contract_violations) == 1
        assert result.contract_violations[0] is cv
        contract_engine.evaluate_post_run.assert_called_once_with(trace)

    def test_replay_with_both_engines(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Both invariant and contract violations are combined."""
        entry = svc.ingest("run-1", trace)

        inv_engine = MagicMock()
        inv_engine.check.return_value = [
            InvariantViolation(
                violation_id="iv-1",
                invariant_id="inv.check",
                severity="blocker",
                event_id="evt-0",
                task_id="task-test",
            )
        ]

        contract_engine = MagicMock()
        contract_engine.evaluate_post_run.return_value = [
            ContractViolation(
                violation_id="cv-1",
                contract_id="contract.check",
                severity="high",
                mode="post_run",
                task_id="task-test",
            )
        ]

        result = svc.replay_with_assurance(
            entry, trace,
            invariant_engine=inv_engine,
            contract_engine=contract_engine,
        )

        assert len(result.contract_violations) == 2


# ---------------------------------------------------------------------------
# counterfactual_with_assurance
# ---------------------------------------------------------------------------


class TestCounterfactualWithAssurance:
    def test_counterfactual_with_no_engines(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Without engines, result has no violations."""
        entry = svc.ingest("run-1", trace)
        mutations = [
            CounterfactualMutation(
                mutation_id="mut-1",
                mutation_type="drop_event",
                target_ref=trace[2].trace_id,
            )
        ]
        result = svc.counterfactual_with_assurance(entry, trace, mutations)

        assert result.contract_violations == []
        assert len(result.mutations) == 1

    def test_counterfactual_with_contract_engine(
        self, svc: ReplayService, trace: list[TraceEnvelope]
    ) -> None:
        """Contract engine receives the mutated trace."""
        entry = svc.ingest("run-1", trace)
        mutations = [
            CounterfactualMutation(
                mutation_id="mut-1",
                mutation_type="drop_event",
                target_ref=trace[2].trace_id,
            )
        ]

        contract_engine = MagicMock()
        cv = ContractViolation(
            violation_id="cv-1",
            contract_id="task.lifecycle",
            severity="blocker",
            mode="post_run",
            task_id="task-test",
        )
        contract_engine.evaluate_post_run.return_value = [cv]

        result = svc.counterfactual_with_assurance(
            entry, trace, mutations, contract_engine=contract_engine
        )

        assert len(result.contract_violations) == 1
        # The contract engine should receive the mutated (shorter) trace
        call_args = contract_engine.evaluate_post_run.call_args[0][0]
        assert len(call_args) == len(trace) - 1
