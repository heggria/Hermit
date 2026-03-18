from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.execution.competition.evaluator import CompetitionEvaluator
from hermit.kernel.execution.competition.models import CandidateScore
from hermit.kernel.execution.competition.service import CompetitionService
from hermit.kernel.ledger.journal.store import KernelStore


class AlwaysPassEvaluator(CompetitionEvaluator):
    """Evaluator that always gives a perfect score."""

    def evaluate(self, competition: Any, candidates: Any) -> list[CandidateScore]:
        scores = []
        for i, c in enumerate(candidates):
            if c.status != "completed":
                continue
            scores.append(
                CandidateScore(
                    candidate_id=c.candidate_id,
                    task_id=c.task_id,
                    total=1.0 - i * 0.1,
                    breakdown={"quality": 1.0 - i * 0.1},
                    passed=True,
                )
            )
        return sorted(scores, key=lambda s: s.total, reverse=True)


class NonePassEvaluator(CompetitionEvaluator):
    """Evaluator where no candidate passes."""

    def evaluate(self, competition: Any, candidates: Any) -> list[CandidateScore]:
        scores = []
        for c in candidates:
            if c.status != "completed":
                continue
            scores.append(
                CandidateScore(
                    candidate_id=c.candidate_id,
                    task_id=c.task_id,
                    total=0.3,
                    breakdown={"quality": 0.3},
                    passed=False,
                )
            )
        return scores


def _make_service(
    tmp_path: Path,
    evaluator: CompetitionEvaluator | None = None,
) -> tuple[CompetitionService, KernelStore]:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        evaluator=evaluator or AlwaysPassEvaluator(),
    )
    return svc, store


def test_create_competition(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="Build feature X",
        candidate_count=3,
        evaluation_criteria={"tests_pass": True},
    )
    assert comp.status == "draft"
    assert comp.candidate_count == 3

    # Verify parent task was created
    parent = store.get_task(comp.parent_task_id)
    assert parent is not None
    assert "Competition:" in parent.title

    # Verify event was logged
    events = store.list_events(task_id=comp.parent_task_id)
    assert any(e["event_type"] == "competition.created" for e in events)


def test_spawn_candidates(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    candidate_ids = svc.spawn_candidates(comp.competition_id)
    assert len(candidate_ids) == 2

    updated = store.get_competition(comp.competition_id)
    assert updated is not None
    assert updated.status == "running"

    candidates = store.list_candidates(comp.competition_id)
    assert len(candidates) == 2
    assert all(c.status == "running" for c in candidates)


def test_candidate_completion_triggers_evaluation(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)

    # Mark child tasks as completed and trigger hook
    for cand in candidates:
        store.update_task_status(cand.task_id, "completed")
        svc.on_candidate_task_completed(cand.task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "decided"
    assert final.winner_task_id is not None
    assert final.winner_score is not None


def test_all_candidates_fail_cancels_competition(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    for cand in candidates:
        store.update_task_status(cand.task_id, "failed")
        svc.on_candidate_task_completed(cand.task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "cancelled"


def test_cancel_competition(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    svc.spawn_candidates(comp.competition_id)
    svc.cancel_competition(comp.competition_id, reason="user_request")

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "cancelled"

    candidates = store.list_candidates(comp.competition_id)
    assert all(c.status == "disqualified" for c in candidates)


def test_no_candidates_pass_cancels(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path, evaluator=NonePassEvaluator())
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    for cand in candidates:
        store.update_task_status(cand.task_id, "completed")
        svc.on_candidate_task_completed(cand.task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "cancelled"


def test_dispatch_result_hook_ignores_non_competition_tasks(tmp_path: Path) -> None:
    svc, _store = _make_service(tmp_path)
    # Should not raise for unknown task
    svc.on_dispatch_result("unknown_task_id")


def test_cancel_already_decided_is_noop(tmp_path: Path) -> None:
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="goal",
        candidate_count=2,
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    for cand in candidates:
        store.update_task_status(cand.task_id, "completed")
        svc.on_candidate_task_completed(cand.task_id)

    # Already decided; cancel should be a no-op
    svc.cancel_competition(comp.competition_id, reason="late_cancel")
    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "decided"
