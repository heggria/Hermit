"""Tests for IngressRouter — target 80%+ coverage on ingress_router.py."""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ConversationRecord, TaskRecord
from hermit.kernel.task.services.ingress_router import (
    BindingDecision,
    CandidateScore,
    IngressRouter,
)


def _setup(tmp_path: Path) -> tuple[KernelStore, IngressRouter]:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    router = IngressRouter(store)
    return store, router


def _mk_task(store: KernelStore, **kwargs) -> TaskRecord:
    defaults = {
        "conversation_id": "conv-1",
        "title": "Test Task",
        "goal": "Cover gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


def _mk_conversation(
    focus_task_id: str | None = None,
    focus_reason: str | None = None,
) -> ConversationRecord:
    return ConversationRecord(
        conversation_id="conv-1",
        source_channel="chat",
        focus_task_id=focus_task_id,
        focus_reason=focus_reason,
    )


# ── BindingDecision / CandidateScore ─────────────────────────────


def test_binding_decision_defaults() -> None:
    d = BindingDecision(resolution="start_new_root")
    assert d.chosen_task_id is None
    assert d.parent_task_id is None
    assert d.confidence == 0.0
    assert d.margin == 0.0
    assert d.candidates == []
    assert d.reason_codes == []


def test_candidate_score_defaults() -> None:
    c = CandidateScore(task_id="t1", score=0.5)
    assert c.reason_codes == []


# ── _normalize ───────────────────────────────────────────────────


def test_normalize() -> None:
    assert IngressRouter._normalize("  hello   world  ") == "hello world"
    assert IngressRouter._normalize("") == ""
    assert IngressRouter._normalize(None) == ""


# ── bind: explicit_task_ref ──────────────────────────────────────


def test_bind_explicit_task_ref(tmp_path: Path) -> None:
    _, router = _setup(tmp_path)
    result = router.bind(
        conversation=None,
        open_tasks=[],
        normalized_text="anything",
        explicit_task_ref="task-123",
    )
    assert result.resolution == "append_note"
    assert result.chosen_task_id == "task-123"
    assert result.confidence == 1.0
    assert "explicit_task_ref" in result.reason_codes


# ── bind: reply_to_task_id ───────────────────────────────────────


def test_bind_reply_to_task_id(tmp_path: Path) -> None:
    _, router = _setup(tmp_path)
    result = router.bind(
        conversation=None,
        open_tasks=[],
        normalized_text="test",
        reply_to_task_id="task-456",
    )
    assert result.resolution == "append_note"
    assert result.chosen_task_id == "task-456"
    assert "reply_target" in result.reason_codes


# ── bind: no open tasks ─────────────────────────────────────────


def test_bind_no_open_tasks(tmp_path: Path) -> None:
    _, router = _setup(tmp_path)
    result = router.bind(
        conversation=None,
        open_tasks=[],
        normalized_text="do something new",
    )
    assert result.resolution == "start_new_root"
    assert "no_open_tasks" in result.reason_codes


# ── bind: no candidate match ────────────────────────────────────


def test_bind_no_candidate_match(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store, title="specific topic A", goal="specific topic A")
    result = router.bind(
        conversation=None,
        open_tasks=[task],
        normalized_text="completely unrelated topic xyz zzz",
    )
    # Should either be start_new_root or a weak match
    assert result.resolution in ("start_new_root", "append_note")


# ── _artifact_refs / _receipt_refs / _path_refs ──────────────────


def test_artifact_refs() -> None:
    refs = IngressRouter._artifact_refs("Check artifact_abc123 and artifact_def456")
    assert "artifact_abc123" in refs
    assert "artifact_def456" in refs


def test_artifact_refs_dedup() -> None:
    refs = IngressRouter._artifact_refs("artifact_abc123 artifact_abc123")
    assert len(refs) == 1


def test_receipt_refs() -> None:
    refs = IngressRouter._receipt_refs("See receipt_abc123 receipt_xyz789")
    assert "receipt_abc123" in refs
    assert "receipt_xyz789" in refs


def test_path_refs() -> None:
    refs = IngressRouter._path_refs("File at /home/user/project/main.py")
    assert any("/home/user/project/main.py" in r for r in refs)


def test_path_refs_with_tilde() -> None:
    refs = IngressRouter._path_refs("Edit ~/Documents/file.txt")
    assert any("Documents/file.txt" in r for r in refs)


def test_path_refs_empty() -> None:
    refs = IngressRouter._path_refs("no paths here")
    assert refs == []


# ── _normalized_path ─────────────────────────────────────────────


def test_normalized_path_empty() -> None:
    assert IngressRouter._normalized_path("") == ""


def test_normalized_path_normal() -> None:
    result = IngressRouter._normalized_path("/tmp/test")
    assert "tmp" in result
    assert "test" in result


# ── bind: branch marker ─────────────────────────────────────────


def test_bind_branch_marker_with_focus(tmp_path: Path, monkeypatch) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store, title="Main task")
    conv = _mk_conversation(focus_task_id=task.task_id)
    # Monkeypatch to detect branch marker
    monkeypatch.setattr(IngressRouter, "_has_branch_marker", staticmethod(lambda text: True))
    result = router.bind(
        conversation=conv,
        open_tasks=[task],
        normalized_text="branch this into subtask",
    )
    assert result.resolution == "fork_child"
    assert result.parent_task_id == task.task_id
    assert "branch_marker" in result.reason_codes


# ── bind: focus followup ────────────────────────────────────────


def test_bind_focus_followup(tmp_path: Path, monkeypatch) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store, title="Focused task")
    conv = _mk_conversation(focus_task_id=task.task_id)
    monkeypatch.setattr(
        IngressRouter, "_looks_like_focus_followup", staticmethod(lambda text: True)
    )
    result = router.bind(
        conversation=conv,
        open_tasks=[task],
        normalized_text="continue with this",
    )
    assert result.resolution == "append_note"
    assert result.chosen_task_id == task.task_id
    assert "focus_followup_marker" in result.reason_codes


# ── _resolve_structural_binding ──────────────────────────────────


def test_structural_binding_single_artifact(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store)
    # Create an artifact linked to the task
    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id="",
        kind="test",
        uri="/tmp/test",
        content_hash="hash_test",
        producer="test",
    )
    result = router._resolve_structural_binding(
        open_tasks=[task],
        text=f"check {artifact.artifact_id}",
    )
    assert result is not None
    assert result.resolution == "append_note"
    assert result.chosen_task_id == task.task_id


def test_structural_binding_no_refs(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store)
    result = router._resolve_structural_binding(
        open_tasks=[task],
        text="no references here",
    )
    assert result is None


# ── _score_task ──────────────────────────────────────────────────


def test_score_task_focus_boost(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store, title="Focused task", goal="test goal")
    score, reasons = router._score_task(task, "test goal topic", focus_task_id=task.task_id)
    assert score > 0
    assert "focus_task" in reasons


def test_score_task_no_focus(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store, title="Some task", goal="topic XYZ")
    score, _reasons = router._score_task(task, "topic XYZ", focus_task_id=None)
    assert score >= 0


# ── _workspace_targets ───────────────────────────────────────────


def test_workspace_targets_empty_paths(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store)
    targets = router._workspace_targets(open_tasks=[task], text="no paths here")
    assert targets == []


# ── _task_workspace_root ─────────────────────────────────────────


def test_task_workspace_root_empty(tmp_path: Path) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store)
    assert router._task_workspace_root(task.task_id) == ""


# ── bind: pending_approval correlation ───────────────────────────


def test_bind_pending_approval_correlation(tmp_path: Path, monkeypatch) -> None:
    store, router = _setup(tmp_path)
    task = _mk_task(store)
    monkeypatch.setattr(
        IngressRouter,
        "_looks_like_approval_followup",
        staticmethod(lambda text: True),
    )
    result = router.bind(
        conversation=None,
        open_tasks=[task],
        normalized_text="yes approve it",
        pending_approval_task_id=task.task_id,
    )
    assert result.resolution == "append_note"
    assert result.chosen_task_id == task.task_id
    assert "pending_approval_correlation" in result.reason_codes


# ── _looks_like_approval_followup ────────────────────────────────


def test_looks_like_approval_followup_negative() -> None:
    # Normal text should not look like approval
    assert IngressRouter._looks_like_approval_followup("build the feature") is False
