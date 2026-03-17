from __future__ import annotations

from types import SimpleNamespace

from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


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
