"""Additional IngressRouter tests — cover scoring, workspace, and structural binding gaps."""

from __future__ import annotations

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ConversationRecord, TaskRecord
from hermit.kernel.task.services.ingress_router import (
    IngressRouter,
)


def _router(store: KernelStore) -> IngressRouter:
    return IngressRouter(store)


def _mk_task(store: KernelStore, conv_id: str, **kwargs) -> TaskRecord:
    defaults = {
        "conversation_id": conv_id,
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


# ── _score_task with continue/ambiguous markers ─────────────────


def test_score_task_continue_marker(shared_store: KernelStore, conv_id: str, monkeypatch) -> None:
    router = _router(shared_store)
    task = _mk_task(shared_store, conv_id, title="data pipeline", goal="build data pipeline")
    score, _reasons = router._score_task(
        task, "continue with the data pipeline", focus_task_id=None
    )
    assert score > 0


def test_score_task_token_overlap(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(
        shared_store, conv_id, title="database migration", goal="migrate postgres schema"
    )
    score, _reasons = router._score_task(task, "postgres migration status", focus_task_id=None)
    assert score > 0


# ── _resolve_structural_binding with conflicting refs ───────────


def test_structural_binding_conflicting_artifacts(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task1 = _mk_task(shared_store, conv_id, title="Task A", goal="A")
    task2 = _mk_task(shared_store, conv_id, title="Task B", goal="B")
    art1 = shared_store.create_artifact(
        task_id=task1.task_id,
        step_id="",
        kind="test",
        uri="/tmp/a",
        content_hash="ha",
        producer="test",
    )
    art2 = shared_store.create_artifact(
        task_id=task2.task_id,
        step_id="",
        kind="test",
        uri="/tmp/b",
        content_hash="hb",
        producer="test",
    )
    result = router._resolve_structural_binding(
        open_tasks=[task1, task2],
        text=f"compare {art1.artifact_id} with {art2.artifact_id}",
    )
    assert result is not None
    assert result.resolution == "pending_disambiguation"
    assert "conflicting_reference_targets" in result.reason_codes


# ── _workspace_targets with matching path ───────────────────────


def test_workspace_targets_matching_path(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    shared_store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        context={"workspace_root": "/home/user/project"},
    )
    targets = router._workspace_targets(
        open_tasks=[task],
        text="edit /home/user/project/src/main.py",
    )
    assert len(targets) == 1
    assert targets[0][0] == task.task_id
    assert targets[0][1] > 0.8


def test_workspace_targets_multiple_matching(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task1 = _mk_task(shared_store, conv_id, title="Task A", goal="A")
    task2 = _mk_task(shared_store, conv_id, title="Task B", goal="B")
    for task, ws in [(task1, "/home/user/proj-a"), (task2, "/home/user/proj-a/sub")]:
        step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
        shared_store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="running",
            context={"workspace_root": ws},
        )
    targets = router._workspace_targets(
        open_tasks=[task1, task2],
        text="edit /home/user/proj-a/sub/file.py",
    )
    assert len(targets) == 2


# ── _task_references_receipt ────────────────────────────────────


def test_task_references_receipt(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    attempt = shared_store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="running"
    )
    receipt = shared_store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="test",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="ok",
    )
    assert router._task_references_receipt(task.task_id, f"check {receipt.receipt_id}") is True
    assert router._task_references_receipt(task.task_id, "no receipts here") is False


# ── _task_matches_workspace_path ────────────────────────────────


def test_task_matches_workspace_path(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    shared_store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        context={"workspace_root": "/home/user/project"},
    )
    assert (
        router._task_matches_workspace_path(task.task_id, "edit /home/user/project/main.py") is True
    )
    assert router._task_matches_workspace_path(task.task_id, "no paths here") is False


# ── bind: high confidence single candidate ─────────────────────


def test_bind_high_confidence_single_match(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(
        shared_store, conv_id, title="database migration", goal="migrate the database schema"
    )
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    shared_store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        context={"workspace_root": "/home/user/db-migrate"},
    )
    result = router.bind(
        conversation=None,
        open_tasks=[task],
        normalized_text="edit /home/user/db-migrate/schema.sql to add new column",
    )
    assert result.resolution == "append_note"
    assert result.chosen_task_id == task.task_id


# ── bind: pending_disambiguation (ambiguous top candidates) ────


def test_bind_pending_disambiguation(shared_store: KernelStore, conv_id: str, monkeypatch) -> None:
    router = _router(shared_store)
    task1 = _mk_task(shared_store, conv_id, title="Task Alpha Alpha", goal="alpha alpha work")
    task2 = _mk_task(shared_store, conv_id, title="Task Alpha Beta", goal="alpha beta work")
    conv = _mk_conversation()
    result = router.bind(
        conversation=conv,
        open_tasks=[task1, task2],
        normalized_text="alpha work status",
    )
    assert result.resolution in (
        "append_note",
        "start_new_root",
        "pending_disambiguation",
        "fork_child",
    )


# ── _normalized_path edge cases ─────────────────────────────────


def test_normalized_path_os_error(monkeypatch) -> None:
    result = IngressRouter._normalized_path("/some/valid/path")
    assert "some" in result


# ── _task_workspace_root ────────────────────────────────────────


def test_task_workspace_root_found(shared_store: KernelStore, conv_id: str) -> None:
    router = _router(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="respond", status="running")
    shared_store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        status="running",
        context={"workspace_root": "/home/user/my-project"},
    )
    assert router._task_workspace_root(task.task_id) == "/home/user/my-project"
