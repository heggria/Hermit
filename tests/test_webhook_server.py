"""Tests for the webhook plugin — routes, signature verification, dispatch."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermit.builtin.webhook.models import WebhookConfig, WebhookRoute, load_config
from hermit.builtin.webhook.server import WebhookServer
from hermit.plugin.base import HookEvent
from hermit.plugin.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_config() -> WebhookConfig:
    return WebhookConfig(
        host="127.0.0.1",
        port=8321,
        routes=[
            WebhookRoute(
                name="test",
                path="/webhook/test",
                prompt_template="Event: {action} on {repository.full_name}",
                notify={"feishu_chat_id": "oc_abc"},
            )
        ],
    )


@pytest.fixture
def signed_config() -> WebhookConfig:
    return WebhookConfig(
        host="127.0.0.1",
        port=8321,
        routes=[
            WebhookRoute(
                name="github",
                path="/webhook/github",
                prompt_template="PR: {pull_request.title}",
                secret="mysecret",
                signature_header="X-Hub-Signature-256",
                notify={"feishu_chat_id": "oc_gh"},
            )
        ],
    )


@pytest.fixture
def hooks() -> HooksEngine:
    return HooksEngine()


def _make_server(config: WebhookConfig, hooks: HooksEngine) -> WebhookServer:
    server = WebhookServer(config, hooks)
    mock_runner = MagicMock()
    mock_result = MagicMock()
    mock_result.text = "agent output"
    mock_runner.dispatch.return_value = mock_result
    server._runner = mock_runner
    return server


# ---------------------------------------------------------------------------
# Route registration and health endpoints
# ---------------------------------------------------------------------------

class TestWebhookServerEndpoints:
    def test_health_endpoint(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        client = TestClient(server._app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_routes_endpoint(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        client = TestClient(server._app)
        resp = client.get("/routes")
        assert resp.status_code == 200
        routes = resp.json()["routes"]
        assert len(routes) == 1
        assert routes[0]["name"] == "test"
        assert routes[0]["path"] == "/webhook/test"
        assert routes[0]["has_secret"] is False

    def test_post_returns_202(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        client = TestClient(server._app)
        payload = {"action": "opened", "repository": {"full_name": "org/repo"}}
        resp = client.post("/webhook/test", json=payload)
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

class TestSignatureVerification:
    def _sign(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid_signature_passes(self, signed_config, hooks) -> None:
        server = _make_server(signed_config, hooks)
        client = TestClient(server._app)
        body = json.dumps({"pull_request": {"title": "Fix"}}).encode()
        sig = self._sign(body, "mysecret")
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 202

    def test_invalid_signature_returns_401(self, signed_config, hooks) -> None:
        server = _make_server(signed_config, hooks)
        client = TestClient(server._app, raise_server_exceptions=False)
        body = json.dumps({"pull_request": {"title": "Fix"}}).encode()
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=badhash",
            },
        )
        assert resp.status_code == 401

    def test_missing_signature_returns_401(self, signed_config, hooks) -> None:
        server = _make_server(signed_config, hooks)
        client = TestClient(server._app, raise_server_exceptions=False)
        resp = client.post("/webhook/github", json={"pull_request": {"title": "Fix"}})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Prompt template rendering
# ---------------------------------------------------------------------------

class TestPromptRendering:
    def test_prompt_built_from_payload(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        dispatched: list[str] = []

        def capture_dispatch(session_id, prompt, **kw):
            dispatched.append(prompt)
            r = MagicMock()
            r.text = "done"
            return r

        server._runner.dispatch.side_effect = capture_dispatch  # type: ignore[union-attr]
        route = simple_config.routes[0]
        server._process(route, {"action": "opened", "repository": {"full_name": "org/repo"}})

        assert len(dispatched) == 1
        assert dispatched[0] == "Event: opened on org/repo"

    def test_missing_keys_render_as_placeholder(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        dispatched: list[str] = []

        def capture_dispatch(session_id, prompt, **kw):
            dispatched.append(prompt)
            r = MagicMock()
            r.text = "done"
            return r

        server._runner.dispatch.side_effect = capture_dispatch  # type: ignore[union-attr]
        route = simple_config.routes[0]
        server._process(route, {})

        assert len(dispatched) == 1
        # Missing keys rendered as placeholders, both should appear
        assert "{action}" in dispatched[0] and "{repository.full_name}" in dispatched[0]


# ---------------------------------------------------------------------------
# DISPATCH_RESULT event fired after processing
# ---------------------------------------------------------------------------

class TestDispatchResultFired:
    def test_fires_dispatch_result_on_success(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        events: list[dict[str, Any]] = []

        hooks.register(
            str(HookEvent.DISPATCH_RESULT),
            lambda **kw: events.append(kw),
        )

        route = simple_config.routes[0]
        server._process(route, {"action": "opened", "repository": {"full_name": "org/repo"}})

        assert len(events) == 1
        ev = events[0]
        assert ev["source"] == "webhook/test"
        assert ev["success"] is True
        assert ev["notify"] == {"feishu_chat_id": "oc_abc"}
        assert ev["result_text"] == "agent output"

    def test_fires_dispatch_result_on_agent_error(self, simple_config, hooks) -> None:
        server = _make_server(simple_config, hooks)
        events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: events.append(kw))

        server._runner.dispatch.side_effect = RuntimeError("agent crashed")  # type: ignore[union-attr]
        route = simple_config.routes[0]
        server._process(route, {})

        assert len(events) == 1
        assert events[0]["success"] is False
        assert "agent crashed" in (events[0]["error"] or "")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_empty_config_returns_defaults(self, tmp_path: Path) -> None:
        settings = MagicMock()
        settings.base_dir = tmp_path
        settings.webhook_host = None
        settings.webhook_port = None
        config = load_config(settings)
        assert config.routes == []
        assert config.port == 8321

    def test_loads_routes_from_json(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "webhooks.json"
        cfg_file.write_text(json.dumps({
            "host": "127.0.0.1",
            "port": 9000,
            "routes": {
                "github": {
                    "path": "/webhook/github",
                    "secret": "abc",
                    "prompt_template": "PR: {pull_request.title}",
                    "notify": {"feishu_chat_id": "oc_x"},
                }
            },
        }))
        settings = MagicMock()
        settings.base_dir = tmp_path
        settings.webhook_host = None
        settings.webhook_port = None
        config = load_config(settings)
        assert config.port == 9000
        assert config.host == "127.0.0.1"
        assert len(config.routes) == 1
        route = config.routes[0]
        assert route.name == "github"
        assert route.secret == "abc"
        assert route.notify == {"feishu_chat_id": "oc_x"}

    def test_missing_file_returns_empty_config(self, tmp_path: Path) -> None:
        settings = MagicMock()
        settings.base_dir = tmp_path / "nonexistent"
        settings.webhook_host = None
        settings.webhook_port = None
        config = load_config(settings)
        assert config.routes == []

    def test_settings_override_host_and_port(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "webhooks.json"
        cfg_file.write_text(json.dumps({"host": "127.0.0.1", "port": 9000, "routes": {}}))
        settings = MagicMock()
        settings.base_dir = tmp_path
        settings.webhook_host = "0.0.0.0"
        settings.webhook_port = 8321

        config = load_config(settings)

        assert config.host == "0.0.0.0"
        assert config.port == 8321
