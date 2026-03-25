"""Tests for ProjectionService — target 80%+ coverage on projections.py."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.projections.projections import (
    ProjectionService,
)

# ---------------------------------------------------------------------------
# Module-scoped store: schema init runs once for all tests in this file.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shared_store(tmp_path_factory: pytest.TempPathFactory) -> KernelStore:
    """Create a single KernelStore for the entire module to avoid repeated schema init."""
    db_path = tmp_path_factory.mktemp("proj") / "state.db"
    return KernelStore(db_path)


# Stub for task_claim_status — avoids expensive semantic probe scans.
def _stub_task_claim_status(
    store: Any, task_id: str, *, proof_summary: dict[str, Any]
) -> dict[str, Any]:
    return {"status": "stub", "verifiable_ready": False, "strongest_ready": False}


def _setup(store: KernelStore) -> tuple[KernelStore, ProjectionService]:
    conv_id = f"conv-{uuid.uuid4().hex[:8]}"
    store.ensure_conversation(conv_id, source_channel="chat")
    svc = ProjectionService(store)
    return store, svc, conv_id  # type: ignore[return-value]


def _mk_task(store: KernelStore, conv_id: str, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "conversation_id": conv_id,
        "title": "Test Task",
        "goal": "Cover gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


# ── rebuild_task ─────────────────────────────────────────────────


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_rebuild_task_full(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    payload = svc.rebuild_task(task.task_id)
    assert "task" in payload
    assert "projection" in payload
    assert "proof" in payload
    assert "topic" in payload
    assert "beliefs" in payload
    assert "knowledge" in payload
    assert "tool_history" in payload
    assert "rollbacks" in payload
    assert "contract_loop" in payload


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_rebuild_task_not_found(shared_store: KernelStore) -> None:
    _, svc, _ = _setup(shared_store)  # type: ignore[misc]
    with pytest.raises(KeyError, match="Task not found"):
        svc.rebuild_task("nonexistent")


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_rebuild_task_incremental(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    # First build creates cache
    svc.rebuild_task(task.task_id)
    # Second build should use incremental path (cache exists)
    payload2 = svc.rebuild_task(task.task_id)
    assert "task" in payload2


# ── verify_projection ────────────────────────────────────────────


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_verify_projection_missing(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    result = svc.verify_projection(task.task_id)
    assert result["valid"] is False
    assert result["reason"] == "missing"


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_verify_projection_valid(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    svc.rebuild_task(task.task_id)
    result = svc.verify_projection(task.task_id)
    assert result["valid"] is True
    assert result["reason"] == "ok"


# ── ensure_task_projection ───────────────────────────────────────


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_ensure_projection_creates_if_missing(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    payload = svc.ensure_task_projection(task.task_id)
    assert "task" in payload


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_ensure_projection_returns_cached(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    svc.rebuild_task(task.task_id)
    payload = svc.ensure_task_projection(task.task_id)
    assert "task" in payload


# ── rebuild_all ──────────────────────────────────────────────────


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_rebuild_all(tmp_path: Path) -> None:
    # rebuild_all iterates *all* tasks in the store; use an isolated store
    # so we control exactly which tasks exist.
    store = KernelStore(tmp_path / "ra.db")
    store.ensure_conversation("conv-ra", source_channel="chat")
    svc = ProjectionService(store)
    task1 = store.create_task(
        conversation_id="conv-ra", title="Task 1", goal="g", source_channel="chat"
    )
    task2 = store.create_task(
        conversation_id="conv-ra", title="Task 2", goal="g", source_channel="chat"
    )
    for t in [task1, task2]:
        step = store.create_step(task_id=t.task_id, kind="respond")
        store.create_step_attempt(task_id=t.task_id, step_id=step.step_id, status="running")
    result = svc.rebuild_all()
    assert result["count"] == 2
    assert task1.task_id in result["rebuilt_tasks"]
    assert task2.task_id in result["rebuilt_tasks"]


@patch(
    "hermit.kernel.task.projections.projections.task_claim_status",
    _stub_task_claim_status,
)
def test_rebuild_all_empty(tmp_path: Path) -> None:
    # Use an isolated store so list_tasks returns nothing.
    store = KernelStore(tmp_path / "empty.db")
    svc = ProjectionService(store)
    result = svc.rebuild_all()
    assert result["count"] == 0


# ── _tool_history_from_events ────────────────────────────────────


def test_tool_history_from_events(shared_store: KernelStore) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    # Create an artifact for tool input
    store.create_artifact(
        task_id=task.task_id,
        step_id="",
        kind="action.request",
        uri="",
        content_hash="hash_test",
        producer="test",
    )
    events = [
        {
            "event_type": "action.requested",
            "payload": {"tool_name": "bash", "artifact_ref": None},
            "event_seq": 1,
            "occurred_at": time.time(),
        }
    ]
    history = svc._tool_history_from_events(events)
    assert len(history) == 1
    assert history[0]["tool_name"] == "bash"


def test_tool_history_from_events_no_tool_name(shared_store: KernelStore) -> None:
    _, svc, _ = _setup(shared_store)  # type: ignore[misc]
    events = [
        {
            "event_type": "action.requested",
            "payload": {"tool_name": ""},
            "event_seq": 1,
            "occurred_at": time.time(),
        }
    ]
    history = svc._tool_history_from_events(events)
    assert len(history) == 0


def test_tool_history_skips_non_action(shared_store: KernelStore) -> None:
    _, svc, _ = _setup(shared_store)  # type: ignore[misc]
    events = [
        {
            "event_type": "task.created",
            "payload": {"title": "test"},
            "event_seq": 1,
            "occurred_at": time.time(),
        }
    ]
    history = svc._tool_history_from_events(events)
    assert len(history) == 0


# ── _key_input ───────────────────────────────────────────────────


def test_key_input_empty() -> None:
    assert ProjectionService._key_input({}) == ""


def test_key_input_returns_first_value() -> None:
    result = ProjectionService._key_input({"command": "ls -la", "cwd": "/tmp"})
    assert result == "ls -la"


# ── _tool_input_from_event ───────────────────────────────────────


def test_tool_input_from_event_no_ref(shared_store: KernelStore) -> None:
    _, svc, _ = _setup(shared_store)  # type: ignore[misc]
    assert svc._tool_input_from_event(None) == {}


def test_tool_input_from_event_missing_artifact(shared_store: KernelStore) -> None:
    _, svc, _ = _setup(shared_store)  # type: ignore[misc]
    assert svc._tool_input_from_event("nonexistent") == {}


def test_tool_input_from_event_with_artifact(shared_store: KernelStore, tmp_path: Path) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    tool_data = {"tool_input": {"command": "echo hello"}}
    data_path = tmp_path / "tool_input.json"
    data_path.write_text(json.dumps(tool_data))
    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id="",
        kind="action.request",
        uri=str(data_path),
        content_hash="hash_test",
        producer="test",
    )
    result = svc._tool_input_from_event(artifact.artifact_id)
    assert result == {"command": "echo hello"}


def test_tool_input_from_event_bad_json(shared_store: KernelStore, tmp_path: Path) -> None:
    store, svc, conv_id = _setup(shared_store)  # type: ignore[misc]
    task = _mk_task(store, conv_id)
    data_path = tmp_path / "bad.json"
    data_path.write_text("not json")
    artifact = store.create_artifact(
        task_id=task.task_id,
        step_id="",
        kind="action.request",
        uri=str(data_path),
        content_hash="hash_test",
        producer="test",
    )
    assert svc._tool_input_from_event(artifact.artifact_id) == {}
