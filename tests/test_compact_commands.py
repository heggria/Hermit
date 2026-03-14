from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.builtin.compact import commands as compact
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.store import KernelStore
from hermit.plugin.base import PluginContext
from hermit.plugin.hooks import HooksEngine


@pytest.fixture(autouse=True)
def _force_compact_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


def _runner(tmp_path):
    store = KernelStore(tmp_path / "kernel" / "state.db")
    store.ensure_conversation("session-1", source_channel="chat")
    return SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        agent=SimpleNamespace(artifact_store=ArtifactStore(tmp_path / "artifacts")),
    )


def test_do_compact_refreshes_conversation_projection(tmp_path) -> None:
    runner = _runner(tmp_path)

    success, message = compact._do_compact(runner, "session-1")

    assert success is True
    assert "Compacted" in message
    cached = runner.task_controller.store.get_conversation_projection_cache("session-1")
    assert cached is not None
    assert cached["payload"]["conversation_id"] == "session-1"
    assert cached["payload"]["artifact_ref"]


def test_do_compact_without_store_returns_empty_message() -> None:
    success, message = compact._do_compact(SimpleNamespace(), "missing")

    assert success is False
    assert message == "Nothing to compact."


def test_compact_command_and_registration(tmp_path) -> None:
    runner = _runner(tmp_path)
    ctx = PluginContext(HooksEngine())

    result = compact._cmd_compact(runner, "session-1", "/compact")
    assert result.is_command is True
    assert "Compacted" in result.text

    compact.register(ctx)
    assert ctx.commands[0].name == "/compact"
    assert ctx._hooks.has_handlers("pre_run") is False
    assert ctx._hooks.has_handlers("post_run") is False


def test_compact_messages_can_render_zh_cn(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    runner = _runner(tmp_path)

    success, message = compact._do_compact(runner, "session-1")

    assert success is True
    assert "已压缩" in message
