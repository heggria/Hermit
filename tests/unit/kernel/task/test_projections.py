"""Tests for ProjectionService — target 80%+ coverage on projections.py."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.projections.projections import (
    ProjectionService,
)


def _setup(tmp_path: Path) -> tuple[KernelStore, ProjectionService]:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    svc = ProjectionService(store)
    return store, svc


def _mk_task(store: KernelStore, **kwargs) -> Any:
    defaults = {
        "conversation_id": "conv-1",
        "title": "Test Task",
        "goal": "Cover gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


# ── rebuild_task ─────────────────────────────────────────────────


def test_rebuild_task_full(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
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


def test_rebuild_task_not_found(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
    with pytest.raises(KeyError, match="Task not found"):
        svc.rebuild_task("nonexistent")


def test_rebuild_task_incremental(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    # First build creates cache
    svc.rebuild_task(task.task_id)
    # Second build should use incremental path (cache exists)
    payload2 = svc.rebuild_task(task.task_id)
    assert "task" in payload2


# ── verify_projection ────────────────────────────────────────────


def test_verify_projection_missing(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
    result = svc.verify_projection(task.task_id)
    assert result["valid"] is False
    assert result["reason"] == "missing"


def test_verify_projection_valid(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    svc.rebuild_task(task.task_id)
    result = svc.verify_projection(task.task_id)
    assert result["valid"] is True
    assert result["reason"] == "ok"


# ── ensure_task_projection ───────────────────────────────────────


def test_ensure_projection_creates_if_missing(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    payload = svc.ensure_task_projection(task.task_id)
    assert "task" in payload


def test_ensure_projection_returns_cached(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
    step = store.create_step(task_id=task.task_id, kind="respond")
    store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running")
    svc.rebuild_task(task.task_id)
    payload = svc.ensure_task_projection(task.task_id)
    assert "task" in payload


# ── rebuild_all ──────────────────────────────────────────────────


def test_rebuild_all(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task1 = _mk_task(store, title="Task 1")
    task2 = _mk_task(store, title="Task 2")
    for t in [task1, task2]:
        step = store.create_step(task_id=t.task_id, kind="respond")
        store.create_step_attempt(task_id=t.task_id, step_id=step.step_id, status="running")
    result = svc.rebuild_all()
    assert result["count"] == 2
    assert task1.task_id in result["rebuilt_tasks"]
    assert task2.task_id in result["rebuilt_tasks"]


def test_rebuild_all_empty(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
    result = svc.rebuild_all()
    assert result["count"] == 0


# ── _tool_history_from_events ────────────────────────────────────


def test_tool_history_from_events(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
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


def test_tool_history_from_events_no_tool_name(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
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


def test_tool_history_skips_non_action(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
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


def test_tool_input_from_event_no_ref(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
    assert svc._tool_input_from_event(None) == {}


def test_tool_input_from_event_missing_artifact(tmp_path: Path) -> None:
    _, svc = _setup(tmp_path)
    assert svc._tool_input_from_event("nonexistent") == {}


def test_tool_input_from_event_with_artifact(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
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


def test_tool_input_from_event_bad_json(tmp_path: Path) -> None:
    store, svc = _setup(tmp_path)
    task = _mk_task(store)
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
