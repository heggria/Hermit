from __future__ import annotations

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

# --- InteractionType enum ---


def test_interaction_type_values() -> None:
    assert InteractionType.handoff == "handoff"
    assert InteractionType.query == "query"
    assert InteractionType.escalation == "escalation"
    assert InteractionType.feedback == "feedback"


def test_interaction_type_all_four_present() -> None:
    """Spec requires exactly: handoff, query, escalation, feedback."""
    expected = {"handoff", "query", "escalation", "feedback"}
    actual = {member.value for member in InteractionType}
    assert actual == expected


# --- TaskContractPacket ---


def test_task_contract_packet_defaults() -> None:
    packet = TaskContractPacket(task_id="task-1", goal="Build feature")
    assert packet.task_id == "task-1"
    assert packet.goal == "Build feature"
    assert packet.scope == {}
    assert packet.inputs == []
    assert packet.constraints == []
    assert packet.acceptance_criteria == []
    assert packet.risk_band == "medium"
    assert packet.suggested_plan == []
    assert packet.dependencies == []
    assert packet.expected_artifacts == []
    assert packet.verification_requirements == {}


def test_task_contract_packet_frozen() -> None:
    packet = TaskContractPacket(task_id="task-1", goal="Build feature")
    with pytest.raises(AttributeError):
        packet.goal = "Changed"  # type: ignore[misc]


def test_task_contract_packet_scope_subfields() -> None:
    """Spec requires scope to support allowed_paths and forbidden_paths."""
    scope = {
        "allowed_paths": ["src/hermit/metaloop/", "tests/metaloop/"],
        "forbidden_paths": ["src/hermit/kernel/"],
    }
    packet = TaskContractPacket(task_id="task-1", goal="x", scope=scope)
    assert packet.scope["allowed_paths"] == ["src/hermit/metaloop/", "tests/metaloop/"]
    assert packet.scope["forbidden_paths"] == ["src/hermit/kernel/"]


def test_create_task_contract_full() -> None:
    packet = create_task_contract(
        task_id="task-1",
        goal="Implement auth",
        scope={"allowed_paths": ["src/auth/"], "forbidden_paths": ["src/kernel/"]},
        inputs=["research_note_001", "spec_fragment_014"],
        constraints=["no_external_deps"],
        acceptance_criteria=["tests_pass", "coverage_80"],
        risk_band="high",
        suggested_plan=["step1: scaffold", "step2: implement", "step3: test"],
        dependencies=["task-0"],
        expected_artifacts=["code_diff", "test_result", "execution_receipts"],
        verification_requirements={"min_coverage": 80},
    )
    assert packet.task_id == "task-1"
    assert packet.goal == "Implement auth"
    assert packet.scope == {"allowed_paths": ["src/auth/"], "forbidden_paths": ["src/kernel/"]}
    assert packet.inputs == ["research_note_001", "spec_fragment_014"]
    assert packet.constraints == ["no_external_deps"]
    assert packet.acceptance_criteria == ["tests_pass", "coverage_80"]
    assert packet.risk_band == "high"
    assert packet.suggested_plan == ["step1: scaffold", "step2: implement", "step3: test"]
    assert packet.dependencies == ["task-0"]
    assert packet.expected_artifacts == ["code_diff", "test_result", "execution_receipts"]
    assert packet.verification_requirements == {"min_coverage": 80}


def test_create_task_contract_invalid_risk_band() -> None:
    with pytest.raises(ValueError, match="Invalid risk_band"):
        create_task_contract(task_id="task-1", goal="x", risk_band="extreme")


def test_create_task_contract_is_scoped_not_full_context() -> None:
    """Spec principle: packets are scoped, not full context.

    A contract should carry only the fields needed by the execution supervisor,
    not raw planning context or other supervisor internals.
    """
    packet = create_task_contract(
        task_id="task-1",
        goal="Refactor memory module",
        scope={"allowed_paths": ["src/hermit/kernel/context/memory/"]},
        constraints=["no breaking changes"],
        acceptance_criteria=["tests pass"],
    )
    # Should not have any planning-internal fields beyond what the spec defines.
    field_names = {f.name for f in packet.__dataclass_fields__.values()}
    spec_fields = {
        "task_id",
        "goal",
        "scope",
        "inputs",
        "constraints",
        "acceptance_criteria",
        "risk_band",
        "suggested_plan",
        "dependencies",
        "expected_artifacts",
        "verification_requirements",
    }
    assert field_names == spec_fields


# --- CompletionPacket ---


def test_completion_packet_defaults() -> None:
    packet = CompletionPacket(task_id="task-1", status="completed")
    assert packet.changed_files == []
    assert packet.artifacts == {}
    assert packet.known_risks == []
    assert packet.needs_review_focus == []


def test_completion_packet_artifacts_contain_refs() -> None:
    """Spec requires artifacts dict with diff_ref, test_report_ref, receipts_ref."""
    packet = CompletionPacket(
        task_id="task-1",
        status="completed",
        artifacts={
            "diff_ref": "artifact://diff/task-1",
            "test_report_ref": "artifact://tests/task-1",
            "receipts_ref": "artifact://receipts/task-1",
        },
    )
    assert packet.artifacts["diff_ref"] == "artifact://diff/task-1"
    assert packet.artifacts["test_report_ref"] == "artifact://tests/task-1"
    assert packet.artifacts["receipts_ref"] == "artifact://receipts/task-1"


def test_create_completion_full() -> None:
    packet = create_completion(
        task_id="task-1",
        status="completed",
        changed_files=["src/auth.py"],
        artifacts={
            "diff_ref": "artifact://diff/task-1",
            "test_report_ref": "artifact://tests/task-1",
            "receipts_ref": "artifact://receipts/task-1",
        },
        known_risks=["untested edge case"],
        needs_review_focus=["governed task enqueue path", "retry behavior"],
    )
    assert packet.task_id == "task-1"
    assert packet.status == "completed"
    assert packet.changed_files == ["src/auth.py"]
    assert packet.artifacts["diff_ref"] == "artifact://diff/task-1"
    assert packet.known_risks == ["untested edge case"]
    assert packet.needs_review_focus == ["governed task enqueue path", "retry behavior"]


def test_completion_packet_frozen() -> None:
    packet = CompletionPacket(task_id="task-1", status="done")
    with pytest.raises(AttributeError):
        packet.status = "failed"  # type: ignore[misc]


def test_completion_packet_is_scoped() -> None:
    """Spec: completion carries evidence, not full execution context."""
    field_names = {f.name for f in CompletionPacket.__dataclass_fields__.values()}
    spec_fields = {
        "task_id",
        "status",
        "changed_files",
        "artifacts",
        "known_risks",
        "needs_review_focus",
    }
    assert field_names == spec_fields


# --- VerdictPacket ---


def test_verdict_packet_defaults() -> None:
    packet = VerdictPacket(task_id="task-1", verdict="accepted")
    assert packet.acceptance_check == {}
    assert packet.issues == []
    assert packet.recommended_next_action == ""


def test_create_verdict_accepted() -> None:
    packet = create_verdict(
        task_id="task-1",
        verdict="accepted",
        acceptance_check={"tests_pass": True, "coverage_ok": True},
    )
    assert packet.verdict == "accepted"
    assert packet.acceptance_check == {"tests_pass": True, "coverage_ok": True}


def test_create_verdict_rejected_with_issues() -> None:
    packet = create_verdict(
        task_id="task-1",
        verdict="rejected",
        issues=[{"type": "test_failure", "detail": "3 tests failed"}],
        recommended_next_action="fix_tests",
    )
    assert packet.verdict == "rejected"
    assert len(packet.issues) == 1
    assert packet.recommended_next_action == "fix_tests"


def test_create_verdict_accepted_with_followups() -> None:
    packet = create_verdict(
        task_id="task-1",
        verdict="accepted_with_followups",
        issues=[{"type": "cleanup", "detail": "remove dead code"}],
    )
    assert packet.verdict == "accepted_with_followups"


def test_create_verdict_blocked() -> None:
    """Spec requires blocked as a valid verdict."""
    packet = create_verdict(
        task_id="task-1",
        verdict="blocked",
        issues=[{"type": "dependency", "detail": "upstream task incomplete"}],
        recommended_next_action="wait_for_dependency",
    )
    assert packet.verdict == "blocked"
    assert packet.recommended_next_action == "wait_for_dependency"


def test_create_verdict_invalid_verdict() -> None:
    with pytest.raises(ValueError, match="Invalid verdict"):
        create_verdict(task_id="task-1", verdict="maybe")


def test_verdict_packet_frozen() -> None:
    packet = VerdictPacket(task_id="task-1", verdict="accepted")
    with pytest.raises(AttributeError):
        packet.verdict = "rejected"  # type: ignore[misc]


def test_verdict_all_valid_values() -> None:
    """Spec requires exactly: accepted, accepted_with_followups, rejected, blocked."""
    for v in ("accepted", "accepted_with_followups", "rejected", "blocked"):
        packet = create_verdict(task_id="task-1", verdict=v)
        assert packet.verdict == v


def test_verdict_packet_is_scoped() -> None:
    """Spec: verdict carries structured judgment, not full verification context."""
    field_names = {f.name for f in VerdictPacket.__dataclass_fields__.values()}
    spec_fields = {
        "task_id",
        "verdict",
        "acceptance_check",
        "issues",
        "recommended_next_action",
    }
    assert field_names == spec_fields


# --- SupervisorQuery ---


def test_supervisor_query_defaults() -> None:
    query = SupervisorQuery(
        query_id="q-1",
        task_id="task-1",
        question="Which approach?",
    )
    assert query.type == "query"
    assert query.options == []
    assert query.blocking is False
    assert query.from_role == ""


def test_supervisor_query_type_field() -> None:
    """Spec requires type='query' on query packets."""
    query = SupervisorQuery(query_id="q-1", task_id="task-1", question="x?")
    assert query.type == "query"


def test_create_query_generates_id() -> None:
    query = create_query(
        task_id="task-1",
        question="Should we proceed?",
        options=["yes", "no"],
        blocking=True,
        from_role="worker",
    )
    assert query.query_id.startswith("query_")
    assert len(query.query_id) > len("query_")
    assert query.type == "query"
    assert query.task_id == "task-1"
    assert query.question == "Should we proceed?"
    assert query.options == ["yes", "no"]
    assert query.blocking is True
    assert query.from_role == "worker"


def test_create_query_unique_ids() -> None:
    q1 = create_query(task_id="task-1", question="A?")
    q2 = create_query(task_id="task-1", question="B?")
    assert q1.query_id != q2.query_id


def test_supervisor_query_frozen() -> None:
    query = SupervisorQuery(query_id="q-1", task_id="task-1", question="?")
    with pytest.raises(AttributeError):
        query.question = "new?"  # type: ignore[misc]


def test_supervisor_query_blocking_gate() -> None:
    """Spec: blocking=True means synchronous gate; default is async (False)."""
    async_query = create_query(task_id="task-1", question="FYI?")
    assert async_query.blocking is False

    blocking_query = create_query(task_id="task-1", question="Must know?", blocking=True)
    assert blocking_query.blocking is True


# --- SupervisorEscalation ---


def test_supervisor_escalation_defaults() -> None:
    esc = SupervisorEscalation(
        escalation_id="esc-1",
        task_id="task-1",
        reason="Out of budget",
    )
    assert esc.severity == "medium"
    assert esc.from_role == ""
    assert esc.context == {}


def test_create_escalation_full() -> None:
    esc = create_escalation(
        task_id="task-1",
        reason="Security vulnerability detected",
        severity="critical",
        from_role="scanner",
        context={"cve": "CVE-2024-1234"},
    )
    assert esc.escalation_id.startswith("esc_")
    assert esc.task_id == "task-1"
    assert esc.reason == "Security vulnerability detected"
    assert esc.severity == "critical"
    assert esc.from_role == "scanner"
    assert esc.context == {"cve": "CVE-2024-1234"}


def test_create_escalation_invalid_severity() -> None:
    with pytest.raises(ValueError, match="Invalid severity"):
        create_escalation(task_id="task-1", reason="x", severity="extreme")


def test_create_escalation_unique_ids() -> None:
    e1 = create_escalation(task_id="task-1", reason="A")
    e2 = create_escalation(task_id="task-1", reason="B")
    assert e1.escalation_id != e2.escalation_id


def test_supervisor_escalation_frozen() -> None:
    esc = SupervisorEscalation(escalation_id="e-1", task_id="task-1", reason="x")
    with pytest.raises(AttributeError):
        esc.reason = "y"  # type: ignore[misc]


# --- Cross-cutting spec compliance ---


def test_packets_are_scoped_not_full_context() -> None:
    """Spec principle: 跨层只传任务契约，不传全部原始上下文.

    Each packet type should carry only its designated fields, not arbitrary
    context blobs. We verify that no packet type has a generic 'context'
    or 'full_context' field (SupervisorEscalation's 'context' is allowed
    since it's a scoped escalation context, not full planning context).
    """
    for cls in (TaskContractPacket, CompletionPacket, VerdictPacket, SupervisorQuery):
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        assert "full_context" not in field_names, f"{cls.__name__} should not have full_context"
        assert "raw_messages" not in field_names, f"{cls.__name__} should not have raw_messages"


def test_async_by_default() -> None:
    """Spec principle: 不同步阻塞等待 — async by default, only gate on explicit blocks."""
    query = create_query(task_id="task-1", question="Info request")
    assert query.blocking is False, "Queries should be non-blocking by default"
