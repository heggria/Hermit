from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


def test_spawn_candidates_not_found(tmp_path: Path) -> None:
    """spawn_candidates raises ValueError for unknown competition_id (line 94)."""
    svc, _store = _make_service(tmp_path)
    with pytest.raises(ValueError, match="Competition not found"):
        svc.spawn_candidates("nonexistent_comp")


def test_spawn_candidates_with_workspace_manager(tmp_path: Path) -> None:
    """spawn_candidates calls workspace.create_workspace for each candidate (line 103)."""
    from types import SimpleNamespace

    created: list[tuple[str, str]] = []

    def fake_create(competition_id: str, label: str) -> str:
        ref = f"/ws/{competition_id}/{label}"
        created.append((competition_id, label))
        return ref

    ws = SimpleNamespace(create_workspace=fake_create)
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=2)
    svc.spawn_candidates(comp.competition_id)

    assert len(created) == 2
    candidates = store.list_candidates(comp.competition_id)
    assert all(c.workspace_ref is not None for c in candidates)


def test_spawn_candidates_with_task_controller(tmp_path: Path) -> None:
    """spawn_candidates uses TaskController.enqueue_task when available (line 106-115)."""
    from types import SimpleNamespace

    enqueued: list[dict[str, Any]] = []

    def fake_enqueue(**kwargs: Any) -> SimpleNamespace:
        tid = f"tc_task_{len(enqueued)}"
        enqueued.append(kwargs)
        return SimpleNamespace(task_id=tid)

    tc = SimpleNamespace(enqueue_task=fake_enqueue)
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        task_controller=tc,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=2)
    candidate_ids = svc.spawn_candidates(comp.competition_id)

    assert len(candidate_ids) == 2
    assert len(enqueued) == 2
    assert enqueued[0]["kind"] == "competition_candidate"


def test_on_candidate_task_completed_task_not_found(tmp_path: Path) -> None:
    """on_candidate_task_completed returns early when task is None (line 173)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    cand = candidates[0]
    # Delete the task so get_task returns None
    store._get_conn().execute("DELETE FROM tasks WHERE task_id = ?", (cand.task_id,))
    store._get_conn().commit()

    # Should return early without error
    svc.on_candidate_task_completed(cand.task_id)
    # Competition should still be running
    updated = store.get_competition(comp.competition_id)
    assert updated is not None
    assert updated.status == "running"


def test_on_candidate_task_completed_timeout_evaluate(tmp_path: Path) -> None:
    """Timeout with evaluate_completed policy triggers evaluation (lines 214-215)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="g",
        candidate_count=3,
        min_candidates=1,
        timeout_seconds=0.0,  # already timed out
        timeout_policy="evaluate_completed",
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    # Complete only one candidate (min_candidates=1 met, but not all terminal)
    store.update_task_status(candidates[0].task_id, "completed")
    svc.on_candidate_task_completed(candidates[0].task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "decided"


def test_on_candidate_task_completed_timeout_cancel(tmp_path: Path) -> None:
    """Timeout with non-evaluate policy cancels competition (line 217)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(
        conversation_id="conv-1",
        goal="g",
        candidate_count=3,
        min_candidates=1,
        timeout_seconds=0.0,
        timeout_policy="cancel",  # not evaluate_completed
    )
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    store.update_task_status(candidates[0].task_id, "completed")
    svc.on_candidate_task_completed(candidates[0].task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "cancelled"


def test_select_winner_competition_not_found(tmp_path: Path) -> None:
    """select_winner returns early if competition is None (line 284)."""
    svc, _store = _make_service(tmp_path)
    winner = CandidateScore(candidate_id="cand_1", task_id="task_1", total=0.9, passed=True)
    # Should not raise
    svc.select_winner("nonexistent", winner)


def test_promote_winner_not_decided(tmp_path: Path) -> None:
    """promote_winner returns early if competition is not decided (line 331)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    # Competition is still in "draft" status
    svc.promote_winner(comp.competition_id)
    # Parent task should not be completed
    parent = store.get_task(comp.parent_task_id)
    assert parent is not None
    assert parent.status != "completed"


def test_promote_winner_no_winner_task_id(tmp_path: Path) -> None:
    """promote_winner returns early if winner_task_id is None (line 333)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    # Move to decided without setting winner_task_id
    store.update_competition_status(comp.competition_id, "spawning")
    store.update_competition_status(comp.competition_id, "running")
    store.update_competition_status(comp.competition_id, "evaluating")
    store.update_competition_status(comp.competition_id, "decided")
    # winner_task_id is None
    svc.promote_winner(comp.competition_id)
    parent = store.get_task(comp.parent_task_id)
    assert parent is not None
    assert parent.status != "completed"


def test_promote_winner_nonexistent(tmp_path: Path) -> None:
    """promote_winner returns early if competition not found (line 330)."""
    svc, _store = _make_service(tmp_path)
    svc.promote_winner("nonexistent")


def test_promote_winner_with_workspace_merge(tmp_path: Path) -> None:
    """promote_winner merges winner worktree and cleans up (lines 341-352)."""
    from types import SimpleNamespace

    merged: list[tuple[str, str]] = []
    cleaned: list[str] = []

    def fake_merge(competition_id: str, workspace_ref: str) -> None:
        merged.append((competition_id, workspace_ref))

    def fake_cleanup(competition_id: str) -> None:
        cleaned.append(competition_id)

    def fake_create(competition_id: str, label: str) -> str:
        return f"/ws/{competition_id}/{label}"

    ws = SimpleNamespace(
        create_workspace=fake_create,
        merge_winner=fake_merge,
        cleanup_all=fake_cleanup,
    )
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=2)
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    for cand in candidates:
        store.update_task_status(cand.task_id, "completed")
        svc.on_candidate_task_completed(cand.task_id)

    assert len(merged) == 1
    assert len(cleaned) == 1


def test_promote_winner_merge_failure_still_cleans_up(tmp_path: Path) -> None:
    """When merge fails, cleanup_all is still called (lines 341-344, 352)."""
    from types import SimpleNamespace

    cleaned: list[str] = []

    def fake_merge(competition_id: str, workspace_ref: str) -> None:
        raise RuntimeError("merge failed")

    def fake_cleanup(competition_id: str) -> None:
        cleaned.append(competition_id)

    def fake_create(competition_id: str, label: str) -> str:
        return f"/ws/{competition_id}/{label}"

    ws = SimpleNamespace(
        create_workspace=fake_create,
        merge_winner=fake_merge,
        cleanup_all=fake_cleanup,
    )
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    store.update_task_status(candidates[0].task_id, "completed")
    svc.on_candidate_task_completed(candidates[0].task_id)

    # cleanup_all called even though merge raised
    assert len(cleaned) == 1
    # Parent task should still be completed
    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "decided"


def test_cancel_competition_nonexistent(tmp_path: Path) -> None:
    """cancel_competition returns early for unknown competition (line 362)."""
    svc, _store = _make_service(tmp_path)
    svc.cancel_competition("nonexistent")


def test_cancel_competition_disqualify_raises_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_competition catches ValueError when disqualifying (lines 385-386)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=2)
    svc.spawn_candidates(comp.competition_id)

    # Patch update_candidate_status to raise ValueError for one candidate
    original = store.update_candidate_status
    call_count = 0

    def failing_update(candidate_id: str, new_status: str, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1 and new_status == "disqualified":
            raise ValueError("simulated invalid transition")
        return original(candidate_id, new_status, **kwargs)

    monkeypatch.setattr(store, "update_candidate_status", failing_update)

    svc.cancel_competition(comp.competition_id, reason="test")
    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "cancelled"


def test_cancel_competition_with_workspace_cleanup(tmp_path: Path) -> None:
    """cancel_competition calls workspace.cleanup_all (line 388-389)."""
    from types import SimpleNamespace

    cleaned: list[str] = []

    def fake_cleanup(competition_id: str) -> None:
        cleaned.append(competition_id)

    def fake_create(competition_id: str, label: str) -> str:
        return f"/ws/{competition_id}/{label}"

    ws = SimpleNamespace(create_workspace=fake_create, cleanup_all=fake_cleanup)
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    svc.spawn_candidates(comp.competition_id)
    svc.cancel_competition(comp.competition_id)
    assert len(cleaned) == 1


def test_cleanup_orphan_worktrees_no_workspace(tmp_path: Path) -> None:
    """cleanup_orphan_worktrees returns early when workspace is None (line 399-400)."""
    svc, _store = _make_service(tmp_path)
    # No workspace_manager set, should not raise
    svc.cleanup_orphan_worktrees()


def test_cleanup_orphan_worktrees(tmp_path: Path) -> None:
    """cleanup_orphan_worktrees cleans worktrees with no competition record (lines 399-411)."""
    from types import SimpleNamespace

    cleaned: list[str] = []

    def fake_list_orphans() -> list[str]:
        return ["/base/comp_orphan1", "/base/comp_orphan2", "/base/comp_existing"]

    def fake_cleanup(competition_id: str) -> None:
        cleaned.append(competition_id)

    ws = SimpleNamespace(list_orphans=fake_list_orphans, cleanup_all=fake_cleanup)
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
        evaluator=AlwaysPassEvaluator(),
    )

    # All are "orphans" since no competitions exist in store
    svc.cleanup_orphan_worktrees()
    assert len(cleaned) == 3


def test_cleanup_orphan_worktrees_empty_path_segment(tmp_path: Path) -> None:
    """cleanup_orphan_worktrees skips entries with empty competition_id (lines 404-407)."""
    from types import SimpleNamespace

    cleaned: list[str] = []

    def fake_list_orphans() -> list[str]:
        return [""]  # empty string, parts[-1] will be ""

    def fake_cleanup(competition_id: str) -> None:
        cleaned.append(competition_id)

    ws = SimpleNamespace(list_orphans=fake_list_orphans, cleanup_all=fake_cleanup)
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = CompetitionService(
        store=store,
        workspace_manager=ws,  # type: ignore[arg-type]
    )
    svc.cleanup_orphan_worktrees()
    assert len(cleaned) == 0


def test_on_dispatch_result_routes_to_candidate_handler(tmp_path: Path) -> None:
    """on_dispatch_result calls on_candidate_task_completed for known candidates (line 420)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)
    svc.spawn_candidates(comp.competition_id)

    candidates = store.list_candidates(comp.competition_id)
    store.update_task_status(candidates[0].task_id, "completed")
    svc.on_dispatch_result(candidates[0].task_id)

    final = store.get_competition(comp.competition_id)
    assert final is not None
    assert final.status == "decided"


def test_get_conversation_id_parent_task_not_found(tmp_path: Path) -> None:
    """_get_conversation_id returns '' when parent task is missing (line 428)."""
    svc, store = _make_service(tmp_path)
    comp = svc.create_competition(conversation_id="conv-1", goal="g", candidate_count=1)

    # Delete the parent task
    store._get_conn().execute("DELETE FROM tasks WHERE task_id = ?", (comp.parent_task_id,))
    store._get_conn().commit()

    result = svc._get_conversation_id(comp)
    assert result == ""
