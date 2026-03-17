from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.infra.storage.atomic import atomic_write
from hermit.plugins.builtin.bundles.usage import commands as usage_commands
from hermit.plugins.builtin.hooks.scheduler import hooks as scheduler_hooks
from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks
from hermit.plugins.builtin.subagents.orchestrator import state as builtin_orchestrator_state
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.control.dispatch import orchestrator as core_orchestrator
from hermit.runtime.provider_host.shared import messages


@pytest.mark.asyncio
async def test_orchestrators_route_work_to_expected_worker() -> None:
    async def researcher(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, "route": "direct", "research": "notes"}

    async def coder(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, "route": "direct", "code": "patch"}

    core_state = core_orchestrator.SharedState(
        messages=[{"role": "user", "content": "hi"}], route="research"
    )
    builtin_state = builtin_orchestrator_state.SharedState(route="code")

    core_result = await core_orchestrator.SimpleOrchestrator(researcher, coder).run(core_state)
    builtin_result = await builtin_orchestrator_state.SimpleOrchestrator(researcher, coder).run(
        builtin_state
    )

    assert core_result.research == "notes"
    assert core_result.route == "direct"
    assert builtin_result.code == "patch"
    assert builtin_result.to_dict()["route"] == "direct"


def test_usage_command_registers_and_formats_session_totals() -> None:
    session = SimpleNamespace(
        messages=[{"role": "user"}, {"role": "assistant"}, {"role": "user"}],
        total_input_tokens=1200,
        total_output_tokens=340,
        total_cache_read_tokens=10,
        total_cache_creation_tokens=20,
    )
    runner = SimpleNamespace(
        session_manager=SimpleNamespace(get_or_create=lambda session_id: session)
    )
    ctx = PluginContext(HooksEngine())

    usage_commands.register(ctx)
    result = ctx.commands[0].handler(runner, "session-1", "/usage")

    assert ctx.commands[0].name == "/usage"
    assert result.is_command is True
    assert "Input: 1,200" in result.text
    assert "User turns: 2" in result.text


def test_webhook_hooks_register_and_manage_server_lifecycle(monkeypatch) -> None:
    ctx = PluginContext(HooksEngine())
    started: list[object] = []
    stopped: list[bool] = []

    class FakeServer:
        def __init__(self, config, hooks_ref) -> None:
            self.config = config
            self.hooks_ref = hooks_ref

        def start(self, runner) -> None:
            started.append(runner)

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.webhook.models.load_config",
        lambda settings: SimpleNamespace(routes=[], control_secret=None),
    )
    webhook_hooks._server = None
    webhook_hooks._hooks_ref = None
    webhook_hooks.register(ctx)
    ctx._hooks.fire(
        HookEvent.SERVE_START, settings=SimpleNamespace(webhook_enabled=False), runner="runner"
    )
    ctx._hooks.fire(
        HookEvent.SERVE_START, settings=SimpleNamespace(webhook_enabled=True), runner="runner"
    )

    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.webhook.models.load_config",
        lambda settings: SimpleNamespace(routes=["/hook"], control_secret=None),
    )
    monkeypatch.setattr("hermit.plugins.builtin.hooks.webhook.server.WebhookServer", FakeServer)
    ctx._hooks.fire(
        HookEvent.SERVE_START, settings=SimpleNamespace(webhook_enabled=True), runner="runner"
    )
    ctx._hooks.fire(HookEvent.SERVE_STOP)

    assert started == ["runner"]
    assert stopped == [True]
    assert webhook_hooks._server is None


def test_scheduler_hooks_register_and_manage_engine(monkeypatch) -> None:
    ctx = PluginContext(HooksEngine())
    created: list[tuple[object, object]] = []
    started: list[bool] = []
    stopped: list[bool] = []
    set_engine_calls: list[object] = []

    class FakeSchedulerEngine:
        def __init__(self, settings, hooks_ref) -> None:
            created.append((settings, hooks_ref))
            self.runner = None

        def set_runner(self, runner) -> None:
            self.runner = runner

        def start(self, *, catch_up: bool) -> None:
            started.append(catch_up)

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(scheduler_hooks, "SchedulerEngine", FakeSchedulerEngine)
    monkeypatch.setattr(
        scheduler_hooks, "set_engine", lambda engine: set_engine_calls.append(engine)
    )
    scheduler_hooks._engine = None
    scheduler_hooks._hooks_ref = None
    scheduler_hooks.register(ctx)
    ctx._hooks.fire(
        HookEvent.SERVE_START, settings=SimpleNamespace(scheduler_enabled=False), runner="runner"
    )
    ctx._hooks.fire(
        HookEvent.SERVE_START,
        settings=SimpleNamespace(scheduler_enabled=True, scheduler_catch_up=False),
        runner="runner",
    )
    ctx._hooks.fire(HookEvent.SERVE_STOP)

    assert created and created[0][1] is not None
    assert started == [False]
    assert stopped == [True]
    assert set_engine_calls[0].runner == "runner"
    assert set_engine_calls[-1] is None
    assert scheduler_hooks._engine is None


def test_provider_messages_normalize_blocks_and_extract_text() -> None:
    class ModelDumpBlock:
        def model_dump(self):
            return {"type": "text", "text": "hello", "ignored": True}

    class ToDictBlock:
        def to_dict(self):
            return {"type": "thinking", "thinking": "plan", "signature": "sig", "ignored": True}

    class FallbackBlock:
        type = "tool_use"
        id = "tool-1"
        name = "echo"
        input = {"value": "hi"}
        ignored = True

    dict_block = {
        "type": "tool_result",
        "tool_use_id": "1",
        "content": "ok",
        "is_error": False,
        "ignored": True,
    }

    assert messages.normalize_block(dict_block) == {
        "type": "tool_result",
        "tool_use_id": "1",
        "content": "ok",
        "is_error": False,
    }
    assert messages.normalize_block(ModelDumpBlock()) == {"type": "text", "text": "hello"}
    assert messages.normalize_block(ToDictBlock()) == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig",
    }
    assert messages.normalize_block(FallbackBlock()) == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "echo",
        "input": {"value": "hi"},
    }

    normalized = messages.normalize_messages(
        [
            {"role": "assistant", "content": [ModelDumpBlock(), ToDictBlock()]},
            {"role": "user", "content": "hello"},
        ]
    )

    assert normalized[0]["role"] == "assistant"
    assert (
        messages.extract_text(
            [{"type": "text", "text": "line-1"}, {"type": "text", "text": "line-2"}]
        )
        == "line-1\nline-2"
    )
    assert messages.extract_thinking([{"type": "thinking", "thinking": "step-1"}]) == "step-1"


def test_atomic_write_writes_and_cleans_up_temp_files_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "nested" / "value.txt"
    atomic_write(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"

    original_replace = os.replace

    def broken_replace(src, dst) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(RuntimeError, match="boom"):
        atomic_write(tmp_path / "nested" / "broken.txt", "broken")

    assert list((tmp_path / "nested").glob("broken.txt.*.tmp")) == []
    monkeypatch.setattr(os, "replace", original_replace)
