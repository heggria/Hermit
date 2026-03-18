"""Test webhook policy_profile passthrough to ingress_metadata."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from hermit.plugins.builtin.hooks.webhook.models import WebhookConfig, WebhookRoute
from hermit.plugins.builtin.hooks.webhook.server import WebhookServer
from hermit.runtime.control.runner.runner import AgentRunner


def _make_fake_runner(captured: list[dict[str, Any]]):
    """Create a fake runner that passes the isinstance(AgentRunner) check."""

    class FakeRunner(AgentRunner):
        def __new__(cls):
            return object.__new__(cls)

        def __init__(self):
            pass

        def enqueue_ingress(self, session_id, text, **kwargs):
            captured.append({"session_id": session_id, "text": text, **kwargs})

    return FakeRunner()


def test_webhook_passes_policy_profile_to_ingress_metadata():
    """Verify that payload.policy_profile is forwarded in ingress_metadata."""
    captured: list[dict[str, Any]] = []
    runner = _make_fake_runner(captured)

    route = WebhookRoute(
        name="fix",
        path="/webhook/fix",
        prompt_template="Fix: {description}",
    )
    config = WebhookConfig(routes=[route])
    server = WebhookServer(config, hooks=SimpleNamespace(fire=lambda *a, **kw: None))
    server._runner = runner

    payload = {
        "description": "Fix unsorted imports",
        "file_path": "store.py",
        "policy_profile": "autonomous",
    }
    server._process(route, payload)

    assert len(captured) == 1
    meta = captured[0].get("ingress_metadata", {})
    assert meta.get("policy_profile") == "autonomous", f"Expected 'autonomous', got {meta}"


def test_webhook_default_policy_profile_when_missing():
    """When payload has no policy_profile, ingress_metadata should have empty string."""
    captured: list[dict[str, Any]] = []
    runner = _make_fake_runner(captured)

    route = WebhookRoute(
        name="fix",
        path="/webhook/fix",
        prompt_template="Fix: {description}",
    )
    config = WebhookConfig(routes=[route])
    server = WebhookServer(config, hooks=SimpleNamespace(fire=lambda *a, **kw: None))
    server._runner = runner

    payload = {"description": "Fix something"}
    server._process(route, payload)

    assert len(captured) == 1
    meta = captured[0].get("ingress_metadata", {})
    assert meta.get("policy_profile") == "", f"Expected empty string, got {meta}"
