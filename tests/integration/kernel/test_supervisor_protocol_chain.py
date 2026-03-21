"""Integration test: Supervisor Protocol packet chain — create -> validate -> serialize -> flow."""

from __future__ import annotations

import dataclasses

import pytest

from hermit.kernel.execution.controller.supervisor_protocol import (
    CompletionPacket,
    InteractionType,
    SupervisorEscalation,
    SupervisorQuery,
    TaskContractPacket,
    VerdictPacket,
    create_completion,
    create_escalation,
    create_query,
    create_task_contract,
    create_verdict,
)

# ---------------------------------------------------------------------------
# 1. TaskContractPacket creation
# ---------------------------------------------------------------------------


class TestTaskContractPacketCreation:
    def test_create_with_all_fields(self) -> None:
        scope = {
            "allowed_paths": ["/src/hermit/kernel/"],
            "forbidden_paths": ["/etc/", "/var/"],
        }
        contract = create_task_contract(
            task_id="task-001",
            goal="Refactor memory module",
            scope=scope,
            inputs=["memory.py", "governance.py"],
            constraints=["no-breaking-changes", "keep-tests-green"],
            acceptance_criteria=["all tests pass", "coverage >= 80%"],
            risk_band="high",
            suggested_plan=["step-1: analyze", "step-2: refactor", "step-3: test"],
            dependencies=["task-000"],
            expected_artifacts=["diff", "test_report"],
            verification_requirements={"lint": True, "typecheck": True},
        )

        assert isinstance(contract, TaskContractPacket)
        assert contract.task_id == "task-001"
        assert contract.goal == "Refactor memory module"
        assert contract.scope["allowed_paths"] == ["/src/hermit/kernel/"]
        assert contract.scope["forbidden_paths"] == ["/etc/", "/var/"]
        assert contract.inputs == ["memory.py", "governance.py"]
        assert contract.constraints == ["no-breaking-changes", "keep-tests-green"]
        assert contract.acceptance_criteria == ["all tests pass", "coverage >= 80%"]
        assert contract.risk_band == "high"
        assert len(contract.suggested_plan) == 3
        assert contract.dependencies == ["task-000"]
        assert contract.expected_artifacts == ["diff", "test_report"]
        assert contract.verification_requirements == {"lint": True, "typecheck": True}

    def test_frozen_immutability(self) -> None:
        contract = create_task_contract(task_id="task-002", goal="Test immutability")
        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.goal = "mutated"  # type: ignore[misc]

    def test_defaults_populated(self) -> None:
        contract = create_task_contract(task_id="task-003", goal="Minimal contract")
        assert contract.scope == {}
        assert contract.inputs == []
        assert contract.constraints == []
        assert contract.acceptance_criteria == []
        assert contract.risk_band == "medium"
        assert contract.suggested_plan == []
        assert contract.dependencies == []
        assert contract.expected_artifacts == []
        assert contract.verification_requirements == {}


# ---------------------------------------------------------------------------
# 2. CompletionPacket creation
# ---------------------------------------------------------------------------


class TestCompletionPacketCreation:
    def test_create_with_artifact_refs(self) -> None:
        completion = create_completion(
            task_id="task-001",
            status="completed",
            changed_files=["src/hermit/kernel/context/memory/governance.py"],
            artifacts={
                "diff_ref": "artifact:diff:abc123",
                "test_report_ref": "artifact:report:def456",
                "receipts_ref": "artifact:receipts:ghi789",
            },
            known_risks=["possible regression in memory retrieval"],
            needs_review_focus=["governance.py:L42-L60"],
        )

        assert isinstance(completion, CompletionPacket)
        assert completion.task_id == "task-001"
        assert completion.status == "completed"
        assert len(completion.changed_files) == 1
        assert completion.artifacts["diff_ref"] == "artifact:diff:abc123"
        assert completion.artifacts["test_report_ref"] == "artifact:report:def456"
        assert completion.artifacts["receipts_ref"] == "artifact:receipts:ghi789"
        assert len(completion.known_risks) == 1
        assert len(completion.needs_review_focus) == 1

    def test_frozen_immutability(self) -> None:
        completion = create_completion(task_id="task-004", status="completed")
        with pytest.raises(dataclasses.FrozenInstanceError):
            completion.status = "failed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        completion = create_completion(task_id="task-005", status="completed")
        assert completion.changed_files == []
        assert completion.artifacts == {}
        assert completion.known_risks == []
        assert completion.needs_review_focus == []


# ---------------------------------------------------------------------------
# 3. VerdictPacket creation — all 4 verdict types
# ---------------------------------------------------------------------------


class TestVerdictPacketCreation:
    @pytest.mark.parametrize(
        "verdict_str",
        ["accepted", "accepted_with_followups", "rejected", "blocked"],
    )
    def test_all_valid_verdict_types(self, verdict_str: str) -> None:
        verdict = create_verdict(
            task_id="task-001",
            verdict=verdict_str,
            acceptance_check={"tests_pass": True, "lint_clean": True},
            issues=[{"severity": "low", "description": "minor style issue"}],
            recommended_next_action="merge" if verdict_str == "accepted" else "revise",
        )

        assert isinstance(verdict, VerdictPacket)
        assert verdict.verdict == verdict_str
        assert verdict.task_id == "task-001"
        assert verdict.acceptance_check["tests_pass"] is True

    def test_invalid_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Invalid verdict.*'invalid_verdict'"):
            create_verdict(task_id="task-001", verdict="invalid_verdict")

    def test_frozen_immutability(self) -> None:
        verdict = create_verdict(task_id="task-006", verdict="accepted")
        with pytest.raises(dataclasses.FrozenInstanceError):
            verdict.verdict = "rejected"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. SupervisorQuery creation
# ---------------------------------------------------------------------------


class TestSupervisorQueryCreation:
    def test_blocking_query(self) -> None:
        query = create_query(
            task_id="task-001",
            question="Should we proceed with the risky refactor?",
            options=["yes", "no", "defer"],
            blocking=True,
            from_role="executor",
        )

        assert isinstance(query, SupervisorQuery)
        assert query.type == "query"
        assert query.blocking is True
        assert query.task_id == "task-001"
        assert query.question == "Should we proceed with the risky refactor?"
        assert query.options == ["yes", "no", "defer"]
        assert query.from_role == "executor"
        assert query.query_id.startswith("query_")

    def test_non_blocking_query(self) -> None:
        query = create_query(
            task_id="task-002",
            question="FYI: found deprecated API usage",
            blocking=False,
            from_role="verifier",
        )

        assert query.type == "query"
        assert query.blocking is False
        assert query.options == []

    def test_query_id_uniqueness(self) -> None:
        q1 = create_query(task_id="t", question="q1")
        q2 = create_query(task_id="t", question="q2")
        assert q1.query_id != q2.query_id


# ---------------------------------------------------------------------------
# 5. SupervisorEscalation creation
# ---------------------------------------------------------------------------


class TestSupervisorEscalationCreation:
    @pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
    def test_all_severity_levels(self, severity: str) -> None:
        escalation = create_escalation(
            task_id="task-001",
            reason="Policy violation detected",
            severity=severity,
            from_role="policy_engine",
            context={"violation": "write to forbidden path"},
        )

        assert isinstance(escalation, SupervisorEscalation)
        assert escalation.task_id == "task-001"
        assert escalation.severity == severity
        assert escalation.reason == "Policy violation detected"
        assert escalation.from_role == "policy_engine"
        assert escalation.context["violation"] == "write to forbidden path"
        assert escalation.escalation_id.startswith("esc_")

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Invalid severity.*'catastrophic'"):
            create_escalation(
                task_id="task-001",
                reason="test",
                severity="catastrophic",
            )

    def test_escalation_id_uniqueness(self) -> None:
        e1 = create_escalation(task_id="t", reason="r1")
        e2 = create_escalation(task_id="t", reason="r2")
        assert e1.escalation_id != e2.escalation_id


# ---------------------------------------------------------------------------
# 6. Full flow simulation
# ---------------------------------------------------------------------------


class TestFullFlowSimulation:
    """Simulates the complete supervisor interaction chain:
    Planning -> Execution -> Verification -> (Escalation on reject | Complete on accept).
    """

    def test_accepted_flow(self) -> None:
        # Phase 1: Planning creates a TaskContractPacket
        contract = create_task_contract(
            task_id="flow-task-001",
            goal="Implement feature X",
            scope={"allowed_paths": ["/src/"]},
            acceptance_criteria=["tests pass", "no regressions"],
            risk_band="medium",
            expected_artifacts=["diff", "test_report", "receipts"],
        )
        assert isinstance(contract, TaskContractPacket)

        # Phase 2: Execution receives contract, produces CompletionPacket
        completion = create_completion(
            task_id=contract.task_id,
            status="completed",
            changed_files=["src/feature_x.py", "tests/test_feature_x.py"],
            artifacts={
                "diff_ref": "artifact:diff:flow001",
                "test_report_ref": "artifact:report:flow001",
                "receipts_ref": "artifact:receipts:flow001",
            },
            known_risks=[],
        )
        assert completion.task_id == contract.task_id
        assert completion.status == "completed"

        # Phase 3: Verification receives completion, produces VerdictPacket
        verdict = create_verdict(
            task_id=completion.task_id,
            verdict="accepted",
            acceptance_check={"tests pass": True, "no regressions": True},
            recommended_next_action="merge",
        )
        assert verdict.task_id == contract.task_id
        assert verdict.verdict == "accepted"
        assert all(verdict.acceptance_check.values())

    def test_rejected_then_escalation_flow(self) -> None:
        # Phase 1: Planning
        contract = create_task_contract(
            task_id="flow-task-002",
            goal="Fix critical bug",
            risk_band="critical",
            acceptance_criteria=["bug is fixed", "no side effects"],
        )

        # Phase 2: Execution completes but with issues
        completion = create_completion(
            task_id=contract.task_id,
            status="completed",
            changed_files=["src/bugfix.py"],
            artifacts={"diff_ref": "artifact:diff:flow002"},
            known_risks=["potential side effect in auth module"],
            needs_review_focus=["bugfix.py:L15-L30"],
        )

        # Phase 3: Verification rejects
        verdict = create_verdict(
            task_id=completion.task_id,
            verdict="rejected",
            acceptance_check={"bug is fixed": True, "no side effects": False},
            issues=[
                {
                    "severity": "high",
                    "description": "Auth module regression detected",
                    "file": "src/bugfix.py",
                    "line": 22,
                }
            ],
            recommended_next_action="fix auth regression and resubmit",
        )
        assert verdict.verdict == "rejected"
        assert not all(verdict.acceptance_check.values())

        # Phase 4: Escalation back to Planning
        escalation = create_escalation(
            task_id=verdict.task_id,
            reason="Verification rejected: auth module regression",
            severity="high",
            from_role="verifier",
            context={
                "verdict": verdict.verdict,
                "issues": verdict.issues,
                "recommended_action": verdict.recommended_next_action,
            },
        )
        assert escalation.task_id == contract.task_id
        assert escalation.severity == "high"
        assert escalation.context["verdict"] == "rejected"

    def test_accepted_with_followups_flow(self) -> None:
        contract = create_task_contract(
            task_id="flow-task-003",
            goal="Add logging to kernel",
            risk_band="low",
        )

        completion = create_completion(
            task_id=contract.task_id,
            status="completed",
            changed_files=["src/hermit/kernel/execution/executor/executor.py"],
            artifacts={"diff_ref": "artifact:diff:flow003"},
        )

        verdict = create_verdict(
            task_id=completion.task_id,
            verdict="accepted_with_followups",
            acceptance_check={"logging added": True},
            issues=[
                {
                    "severity": "low",
                    "description": "Consider adding structured log fields",
                    "followup": True,
                }
            ],
            recommended_next_action="create followup task for structured logging",
        )
        assert verdict.verdict == "accepted_with_followups"
        assert len(verdict.issues) == 1
        assert verdict.issues[0]["followup"] is True

    def test_blocked_flow_with_query(self) -> None:
        contract = create_task_contract(
            task_id="flow-task-004",
            goal="Delete deprecated API",
            risk_band="high",
        )

        # Executor raises a blocking query before completion
        query = create_query(
            task_id=contract.task_id,
            question="Deprecated API still has 3 consumers. Proceed with deletion?",
            options=["delete anyway", "deprecation warning only", "abort"],
            blocking=True,
            from_role="executor",
        )
        assert query.blocking is True
        assert query.task_id == contract.task_id

        # After query is answered, execution continues to completion
        completion = create_completion(
            task_id=contract.task_id,
            status="completed",
            changed_files=["src/api.py"],
            artifacts={"diff_ref": "artifact:diff:flow004"},
        )

        verdict = create_verdict(
            task_id=completion.task_id,
            verdict="blocked",
            acceptance_check={"api deleted": True, "consumers migrated": False},
            issues=[
                {
                    "severity": "critical",
                    "description": "2 consumers not migrated",
                }
            ],
            recommended_next_action="migrate remaining consumers first",
        )
        assert verdict.verdict == "blocked"

    def test_task_id_consistency_across_chain(self) -> None:
        """Ensure task_id threads through the entire packet chain."""
        tid = "chain-consistency-001"

        contract = create_task_contract(task_id=tid, goal="consistency check")
        completion = create_completion(task_id=tid, status="completed")
        verdict = create_verdict(task_id=tid, verdict="accepted")
        query = create_query(task_id=tid, question="check?")
        escalation = create_escalation(task_id=tid, reason="check")

        assert contract.task_id == tid
        assert completion.task_id == tid
        assert verdict.task_id == tid
        assert query.task_id == tid
        assert escalation.task_id == tid


# ---------------------------------------------------------------------------
# 7. Packet scoping invariant
# ---------------------------------------------------------------------------


class TestPacketScopingInvariant:
    """Verify no packet contains unbounded data fields like full_context or raw_messages."""

    _FORBIDDEN_FIELDS = frozenset({"full_context", "raw_messages", "conversation_history"})

    @pytest.mark.parametrize(
        "packet_cls",
        [
            TaskContractPacket,
            CompletionPacket,
            VerdictPacket,
            SupervisorQuery,
            SupervisorEscalation,
        ],
    )
    def test_no_unbounded_fields(self, packet_cls: type) -> None:
        field_names = {f.name for f in dataclasses.fields(packet_cls)}
        violations = field_names & self._FORBIDDEN_FIELDS
        assert not violations, (
            f"{packet_cls.__name__} contains forbidden unbounded fields: {violations}"
        )


# ---------------------------------------------------------------------------
# 8. Risk band validation
# ---------------------------------------------------------------------------


class TestRiskBandValidation:
    @pytest.mark.parametrize("band", ["low", "medium", "high", "critical"])
    def test_valid_risk_bands(self, band: str) -> None:
        contract = create_task_contract(task_id="rb-test", goal="test", risk_band=band)
        assert contract.risk_band == band

    def test_invalid_risk_band_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Invalid risk_band.*'extreme'"):
            create_task_contract(task_id="rb-test", goal="test", risk_band="extreme")

    def test_empty_risk_band_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid risk_band"):
            create_task_contract(task_id="rb-test", goal="test", risk_band="")


# ---------------------------------------------------------------------------
# 9. InteractionType coverage
# ---------------------------------------------------------------------------


class TestInteractionTypeCoverage:
    def test_all_four_types_exist(self) -> None:
        expected = {"handoff", "query", "escalation", "feedback"}
        actual = {member.value for member in InteractionType}
        assert actual == expected

    def test_str_enum_values(self) -> None:
        assert InteractionType.handoff == "handoff"
        assert InteractionType.query == "query"
        assert InteractionType.escalation == "escalation"
        assert InteractionType.feedback == "feedback"

    def test_is_str_subclass(self) -> None:
        for member in InteractionType:
            assert isinstance(member, str)
