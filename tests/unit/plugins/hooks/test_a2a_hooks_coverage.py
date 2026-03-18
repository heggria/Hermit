"""Tests for a2a_hooks.py — covers missing lines for CI coverage."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from hermit.plugins.builtin.hooks.webhook.a2a import A2AHandler

# ---------------------------------------------------------------------------
# _on_serve_start
# ---------------------------------------------------------------------------


def test_on_serve_start_server_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When webhook server is None, _on_serve_start should return early."""
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks
    from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks

    old_server = webhook_hooks._server
    old_handler = a2a_hooks._handler
    try:
        webhook_hooks._server = None
        a2a_hooks._on_serve_start(settings=SimpleNamespace())
        # handler should not be set when server is None
        assert a2a_hooks._handler is old_handler
    finally:
        webhook_hooks._server = old_server
        a2a_hooks._handler = old_handler


def test_on_serve_start_registers_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """When webhook server is available, _on_serve_start registers A2A routes."""
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks
    from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks

    app = FastAPI()
    fake_server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=None,
        _runner_lock=threading.Lock(),
    )
    old_server = webhook_hooks._server
    old_handler = a2a_hooks._handler
    try:
        webhook_hooks._server = fake_server
        settings = SimpleNamespace(agent_id="a1", agent_name="TestAgent")
        a2a_hooks._on_serve_start(settings=settings)
        assert a2a_hooks._handler is not None
        assert a2a_hooks._handler.agent_id == "a1"
    finally:
        webhook_hooks._server = old_server
        a2a_hooks._handler = old_handler


def test_on_serve_start_defaults_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent_name defaults to 'Hermit' when not set."""
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks
    from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks

    app = FastAPI()
    fake_server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=None,
        _runner_lock=threading.Lock(),
    )
    old_server = webhook_hooks._server
    old_handler = a2a_hooks._handler
    try:
        webhook_hooks._server = fake_server
        settings = SimpleNamespace()  # no agent_id or agent_name
        a2a_hooks._on_serve_start(settings=settings)
        assert a2a_hooks._handler is not None
        assert a2a_hooks._handler.agent_name == "Hermit"
    finally:
        webhook_hooks._server = old_server
        a2a_hooks._handler = old_handler


# ---------------------------------------------------------------------------
# register function
# ---------------------------------------------------------------------------


def test_register_adds_hooks() -> None:
    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import register
    from hermit.runtime.capability.contracts.base import PluginContext
    from hermit.runtime.capability.contracts.hooks import HooksEngine

    engine = HooksEngine()
    ctx = PluginContext(engine)
    register(ctx)
    # SERVE_START and DISPATCH_RESULT hooks should be registered
    from hermit.runtime.capability.contracts.base import HookEvent

    assert len(engine._handlers.get(HookEvent.SERVE_START, [])) >= 1
    assert len(engine._handlers.get(HookEvent.DISPATCH_RESULT, [])) >= 1


# ---------------------------------------------------------------------------
# submit_task endpoint — error paths
# ---------------------------------------------------------------------------


@pytest.fixture()
def a2a_server() -> tuple[TestClient, Any]:
    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="test-cov")
    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=None,
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(side_effect=HTTPException(status_code=503)),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app, raise_server_exceptions=False)
    return client, server


def test_submit_task_invalid_json(a2a_server: tuple[TestClient, Any]) -> None:
    """Invalid JSON body should return 400."""
    client, _ = a2a_server
    resp = client.post(
        "/a2a/tasks", content=b"not json{{{", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400


def test_submit_task_missing_sender_agent_id(a2a_server: tuple[TestClient, Any]) -> None:
    """Missing sender_agent_id should return 400."""
    client, server = a2a_server
    server._runner = SimpleNamespace()
    resp = client.post(
        "/a2a/tasks",
        content=json.dumps({"task_description": "do something"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "sender_agent_id" in resp.json()["detail"]


def test_submit_task_missing_task_description(a2a_server: tuple[TestClient, Any]) -> None:
    """Missing task_description should return 400."""
    client, server = a2a_server
    server._runner = SimpleNamespace()
    resp = client.post(
        "/a2a/tasks",
        content=json.dumps({"sender_agent_id": "agent-1"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "task_description" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# task_status endpoint
# ---------------------------------------------------------------------------


def test_task_status_with_control_secret() -> None:
    """task_status verifies signature when control_secret is set."""
    import hashlib
    import hmac as hmac_mod

    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="sig-status")
    fake_task = SimpleNamespace(task_id="t1", status="completed")
    fake_store = MagicMock()
    fake_store.get_task.return_value = fake_task

    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret="mysecret"),
        _runner=SimpleNamespace(),
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(return_value=fake_store),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app, raise_server_exceptions=False)

    # Without signature → 401
    resp = client.get("/a2a/tasks/t1/status")
    assert resp.status_code == 401

    # With valid signature → 200
    body = b""
    sig = hmac_mod.new(b"mysecret", body, hashlib.sha256).hexdigest()
    with patch("hermit.kernel.verification.proofs.proofs.ProofService") as mock_ps:
        mock_ps.return_value.build_proof_summary.return_value = {}
        resp = client.get(
            "/a2a/tasks/t1/status",
            headers={"X-Hermit-Signature-256": f"sha256={sig}"},
        )
    assert resp.status_code == 200


def test_task_status_kernel_store_unavailable() -> None:
    """When _kernel_store raises HTTPException, return unknown status."""
    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="store-fail")
    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=SimpleNamespace(),
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(side_effect=HTTPException(status_code=503)),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app)

    resp = client.get("/a2a/tasks/some-task/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "unknown"


def test_task_status_task_not_found() -> None:
    """When get_task returns None, return 404."""
    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="notfound")
    fake_store = MagicMock()
    fake_store.get_task.return_value = None

    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=SimpleNamespace(),
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(return_value=fake_store),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/a2a/tasks/nonexistent/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _on_dispatch_result — handler is None
# ---------------------------------------------------------------------------


def test_dispatch_result_handler_none() -> None:
    """When _handler is None, _on_dispatch_result returns immediately."""
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks

    old_handler = a2a_hooks._handler
    try:
        a2a_hooks._handler = None
        # Should not raise
        a2a_hooks._on_dispatch_result(
            source="test",
            result_text="done",
            success=True,
            metadata={"a2a_reply_to": "https://example.com/cb", "task_id": "t1"},
        )
    finally:
        a2a_hooks._handler = old_handler
