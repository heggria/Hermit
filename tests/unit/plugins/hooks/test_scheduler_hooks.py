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


def test_on_serve_start_reload_mode_hot_swaps_runner(monkeypatch) -> None:
    """In reload_mode, an existing engine gets set_runner() called instead of recreating."""
    old_engine = scheduler_hooks._engine
    set_engine_calls: list[object] = []
    monkeypatch.setattr(
        scheduler_hooks, "set_engine", lambda engine: set_engine_calls.append(engine)
    )
    try:
        hot_swap_calls: list[object] = []
        started: list[bool] = []

        class FakeEngine:
            def set_runner(self, runner: object) -> None:
                hot_swap_calls.append(runner)

            def start(self, *, catch_up: bool) -> None:
                started.append(catch_up)

            def stop(self) -> None:
                pass

        existing = FakeEngine()
        scheduler_hooks._engine = existing  # type: ignore[assignment]
        scheduler_hooks._hooks_ref = object()

        scheduler_hooks._on_serve_start(
            settings=SimpleNamespace(scheduler_enabled=True, scheduler_catch_up=True),
            runner="new_runner",
            reload_mode=True,
        )

        assert hot_swap_calls == ["new_runner"], "set_runner should be called with the new runner"
        assert started == [], "start() should NOT be called during a hot reload"
        assert scheduler_hooks._engine is existing, "engine reference should be unchanged"
        assert set_engine_calls == [], "set_engine should NOT be called during a hot reload"
    finally:
        scheduler_hooks._engine = old_engine


def test_on_serve_stop_reload_mode_skips_stop(monkeypatch) -> None:
    """In reload_mode, _on_serve_stop leaves the engine intact."""
    old_engine = scheduler_hooks._engine
    try:
        stopped: list[bool] = []

        class FakeEngine:
            def stop(self) -> None:
                stopped.append(True)

            def set_runner(self, runner: object) -> None:
                pass

            def start(self, *, catch_up: bool) -> None:
                pass

        existing = FakeEngine()
        scheduler_hooks._engine = existing  # type: ignore[assignment]

        scheduler_hooks._on_serve_stop(reload_mode=True)

        assert stopped == [], "stop() should NOT be called during a hot reload"
        assert scheduler_hooks._engine is existing, "engine reference should be unchanged"
    finally:
        scheduler_hooks._engine = old_engine


def test_on_serve_stop_normal_stops_engine(monkeypatch) -> None:
    """Without reload_mode, _on_serve_stop stops and clears the engine."""
    old_engine = scheduler_hooks._engine
    set_engine_calls: list[object] = []
    monkeypatch.setattr(
        scheduler_hooks, "set_engine", lambda engine: set_engine_calls.append(engine)
    )
    try:
        stopped: list[bool] = []

        class FakeEngine:
            def stop(self) -> None:
                stopped.append(True)

        scheduler_hooks._engine = FakeEngine()  # type: ignore[assignment]

        scheduler_hooks._on_serve_stop(reload_mode=False)

        assert stopped == [True]
        assert scheduler_hooks._engine is None
        assert set_engine_calls == [None]
    finally:
        scheduler_hooks._engine = old_engine
