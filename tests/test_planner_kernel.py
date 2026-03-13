from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.builtin.planner.commands import (
    _cmd_plan,
    _planner_state,
    _post_run_hook,
    _pre_run_hook,
)
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.store import KernelStore


@pytest.fixture(autouse=True)
def _force_planner_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


def _make_runner(tmp_path):
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifact_store = ArtifactStore(tmp_path / "kernel" / "artifacts")
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=SimpleNamespace(artifact_store=artifact_store),
    )
    return runner, store


def test_plan_mode_state_lives_in_kernel_metadata(tmp_path) -> None:
    runner, store = _make_runner(tmp_path)

    result = _cmd_plan(runner, "chat-1", "/plan")

    assert result.is_command is True
    assert "Entered plan mode" in result.text
    state = _planner_state(store, "chat-1")
    assert state["mode"] is True
    assert state["plan_artifact_id"] is None


def test_plan_result_is_saved_as_artifact_and_can_execute_by_intent(tmp_path) -> None:
    runner, store = _make_runner(tmp_path)
    _cmd_plan(runner, "chat-1", "/plan")

    _post_run_hook(
        SimpleNamespace(text="## Plan\n\n1. Read\n2. Execute", task_id=None, step_id=None),
        session_id="chat-1",
        runner=runner,
    )

    state = _planner_state(store, "chat-1")
    assert state["plan_artifact_id"] is not None

    prompt = _pre_run_hook("开始执行", session_id="chat-1", runner=runner)

    assert isinstance(prompt, str)
    assert "<execution_plan>" in prompt
    assert "1. Read" in prompt
    assert _planner_state(store, "chat-1")["mode"] is False


def test_plan_confirm_replays_saved_plan(tmp_path) -> None:
    runner, store = _make_runner(tmp_path)
    captured: dict[str, str] = {}

    def fake_handle(session_id: str, prompt: str):
        captured["session_id"] = session_id
        captured["prompt"] = prompt
        return SimpleNamespace(text="executed")

    runner.handle = fake_handle

    _cmd_plan(runner, "chat-2", "/plan")
    _post_run_hook(
        SimpleNamespace(text="## Plan\n\nShip it", task_id=None, step_id=None),
        session_id="chat-2",
        runner=runner,
    )

    result = _cmd_plan(runner, "chat-2", "/plan confirm")

    assert result.is_command is False
    assert result.text == "executed"
    assert captured["session_id"] == "chat-2"
    assert "<execution_plan>" in captured["prompt"]
    assert "Ship it" in captured["prompt"]
    assert _planner_state(store, "chat-2")["plan_artifact_id"] is None


def test_plan_messages_can_render_zh_cn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    runner, _store = _make_runner(tmp_path)

    result = _cmd_plan(runner, "chat-zh", "/plan")

    assert "已进入规划模式" in result.text
