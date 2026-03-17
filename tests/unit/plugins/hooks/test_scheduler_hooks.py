from __future__ import annotations

from types import SimpleNamespace

from hermit.plugins.builtin.hooks.scheduler import hooks as scheduler_hooks
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


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
