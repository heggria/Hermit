from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


def test_competition_crud(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")

    parent = store.create_task(
        conversation_id="conv-1",
        title="Parent",
        goal="goal",
        source_channel="chat",
    )

    comp = store.create_competition(
        parent_task_id=parent.task_id,
        goal="Build feature",
        candidate_count=3,
        min_candidates=2,
        evaluation_criteria={"tests_pass": True},
        scoring_weights={"tests_pass": 1.0},
        timeout_seconds=600.0,
    )
    assert comp.competition_id.startswith("comp_")
    assert comp.status == "draft"
    assert comp.candidate_count == 3
    assert comp.min_candidates == 2
    assert comp.evaluation_criteria == {"tests_pass": True}
    assert comp.timeout_seconds == 600.0

    fetched = store.get_competition(comp.competition_id)
    assert fetched is not None
    assert fetched.goal == "Build feature"

    assert store.get_competition("nonexistent") is None


def test_competition_find_by_parent_task(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)
    found = store.find_competition_by_parent_task(parent.task_id)
    assert found is not None
    assert found.parent_task_id == parent.task_id

    assert store.find_competition_by_parent_task("no_such_task") is None


def test_competition_status_transitions(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)

    store.update_competition_status(comp.competition_id, "spawning")
    store.update_competition_status(comp.competition_id, "running")
    store.update_competition_status(comp.competition_id, "evaluating")
    store.update_competition_status(
        comp.competition_id,
        "decided",
        winner_task_id="task_123",
        winner_score=0.95,
    )

    updated = store.get_competition(comp.competition_id)
    assert updated is not None
    assert updated.status == "decided"
    assert updated.winner_task_id == "task_123"
    assert updated.winner_score == 0.95


def test_competition_invalid_transition(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)

    with pytest.raises(ValueError, match="Invalid competition transition"):
        store.update_competition_status(comp.competition_id, "decided")


def test_candidate_crud(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")

    cand = store.create_candidate(
        competition_id=comp.competition_id,
        task_id=child.task_id,
        label="candidate_1",
        workspace_ref="/tmp/ws1",
    )
    assert cand.candidate_id.startswith("cand_")
    assert cand.status == "pending"
    assert cand.workspace_ref == "/tmp/ws1"

    candidates = store.list_candidates(comp.competition_id)
    assert len(candidates) == 1

    candidates_filtered = store.list_candidates(comp.competition_id, status="running")
    assert len(candidates_filtered) == 0


def test_candidate_status_transitions(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")
    cand = store.create_candidate(
        competition_id=comp.competition_id, task_id=child.task_id, label="c1"
    )

    store.update_candidate_status(cand.candidate_id, "running")
    store.update_candidate_status(
        cand.candidate_id,
        "completed",
        score=0.85,
        score_breakdown={"tests_pass": 0.9, "lint_clean": 0.8},
    )

    updated = store.find_candidate_by_task(child.task_id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.score == 0.85
    assert updated.score_breakdown == {"tests_pass": 0.9, "lint_clean": 0.8}
    assert updated.finished_at is not None


def test_candidate_invalid_transition(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")
    cand = store.create_candidate(
        competition_id=comp.competition_id, task_id=child.task_id, label="c1"
    )

    with pytest.raises(ValueError, match="Invalid candidate transition"):
        store.update_candidate_status(cand.candidate_id, "completed")


def test_find_competition_by_candidate_task(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")
    store.create_candidate(competition_id=comp.competition_id, task_id=child.task_id, label="c1")

    found = store.find_competition_by_candidate_task(child.task_id)
    assert found is not None
    assert found.competition_id == comp.competition_id

    assert store.find_competition_by_candidate_task("unknown") is None


def test_schema_version_bumped_to_10(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "state.db")
    assert store.schema_version() == "10"


def test_update_competition_status_not_found(tmp_path: Path) -> None:
    """update_competition_status raises ValueError for unknown id (line 165)."""
    store = KernelStore(tmp_path / "state.db")
    with pytest.raises(ValueError, match="Competition not found"):
        store.update_competition_status("nonexistent", "running")


def test_update_competition_status_with_decision_ref(tmp_path: Path) -> None:
    """update_competition_status persists decision_ref (lines 177-178)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)

    store.update_competition_status(comp.competition_id, "spawning")
    store.update_competition_status(comp.competition_id, "running")
    store.update_competition_status(comp.competition_id, "evaluating")
    store.update_competition_status(
        comp.competition_id,
        "decided",
        decision_ref="decision_abc",
    )

    updated = store.get_competition(comp.competition_id)
    assert updated is not None
    assert updated.decision_ref == "decision_abc"


def test_update_competition_status_with_evaluation_artifact_ref(tmp_path: Path) -> None:
    """update_competition_status persists evaluation_artifact_ref (lines 180-181)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=2)

    store.update_competition_status(comp.competition_id, "spawning")
    store.update_competition_status(comp.competition_id, "running")
    store.update_competition_status(comp.competition_id, "evaluating")
    store.update_competition_status(
        comp.competition_id,
        "decided",
        evaluation_artifact_ref="artifact_xyz",
    )

    updated = store.get_competition(comp.competition_id)
    assert updated is not None
    assert updated.evaluation_artifact_ref == "artifact_xyz"


def test_update_candidate_status_not_found(tmp_path: Path) -> None:
    """update_candidate_status raises ValueError for unknown id (line 260)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    # Need to init schema
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=1)

    with pytest.raises(ValueError, match="Candidate not found"):
        store.update_candidate_status("nonexistent_cand", "running")


def test_update_candidate_status_with_receipt_and_promoted(tmp_path: Path) -> None:
    """update_candidate_status persists evaluation_receipt_ref and promoted (lines 275-279)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=1)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")
    cand = store.create_candidate(
        competition_id=comp.competition_id, task_id=child.task_id, label="c1"
    )

    store.update_candidate_status(
        cand.candidate_id,
        "running",
        evaluation_receipt_ref="receipt_abc",
        promoted=True,
    )

    updated = store.find_candidate_by_task(child.task_id)
    assert updated is not None
    assert updated.evaluation_receipt_ref == "receipt_abc"
    assert updated.promoted is True


def test_update_candidate_score_not_found(tmp_path: Path) -> None:
    """update_candidate_score raises ValueError for unknown id (line 305)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=1)

    with pytest.raises(ValueError, match="Candidate not found"):
        store.update_candidate_score("nonexistent_cand", score=0.9)


def test_update_candidate_score_with_receipt_ref(tmp_path: Path) -> None:
    """update_candidate_score persists evaluation_receipt_ref (lines 312-313)."""
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    parent = store.create_task(conversation_id="conv-1", title="P", goal="g", source_channel="chat")
    comp = store.create_competition(parent_task_id=parent.task_id, goal="g", candidate_count=1)
    child = store.create_task(conversation_id="conv-1", title="C1", goal="g", source_channel="chat")
    cand = store.create_candidate(
        competition_id=comp.competition_id, task_id=child.task_id, label="c1"
    )

    store.update_candidate_score(
        cand.candidate_id,
        score=0.85,
        score_breakdown={"quality": 0.9},
        evaluation_receipt_ref="receipt_xyz",
        promoted=True,
    )

    updated = store.find_candidate_by_task(child.task_id)
    assert updated is not None
    assert updated.score == 0.85
    assert updated.score_breakdown == {"quality": 0.9}
    assert updated.evaluation_receipt_ref == "receipt_xyz"
    assert updated.promoted is True
