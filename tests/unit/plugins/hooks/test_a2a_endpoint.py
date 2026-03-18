"""Tests for the A2A (Agent-to-Agent) protocol endpoint."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermit.plugins.builtin.hooks.webhook.a2a import (
    A2ACapabilityCard,
    A2AHandler,
    A2ATaskResponse,
    policy_for_trust,
    resolve_sender_trust,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def handler() -> A2AHandler:
    return A2AHandler(
        agent_id="test-agent-001",
        agent_name="TestHermit",
        capabilities=["task_execution"],
        trust_records={"trusted-agent": "trusted", "known-agent": "known"},
    )


@pytest.fixture()
def fake_server(handler: A2AHandler) -> tuple[TestClient, Any]:
    """Build a minimal fake WebhookServer with a FastAPI app and register A2A routes."""
    import threading

    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=None,
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(side_effect=Exception("no store")),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app)
    return client, server


# ---------------------------------------------------------------------------
# Test: capability card endpoint returns valid JSON
# ---------------------------------------------------------------------------


def test_capability_card_returns_valid_json(fake_server: tuple[TestClient, Any]) -> None:
    client, _ = fake_server
    resp = client.get("/a2a/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "test-agent-001"
    assert data["agent_name"] == "TestHermit"
    assert "task_execution" in data["capabilities"]
    assert "task_request" in data["supported_actions"]
    assert data["trust_level"] == "standard"


# ---------------------------------------------------------------------------
# Test: task request creates a governed task
# ---------------------------------------------------------------------------


def test_task_request_creates_governed_task(fake_server: tuple[TestClient, Any]) -> None:
    client, server = fake_server
    enqueued: list[dict[str, Any]] = []

    class FakeRunner:
        def enqueue_ingress(self, session_id: str, text: str, **kwargs: Any) -> None:
            enqueued.append({"session_id": session_id, "text": text, **kwargs})

    server._runner = FakeRunner()

    resp = client.post(
        "/a2a/tasks",
        content=json.dumps(
            {
                "sender_agent_id": "remote-agent-42",
                "sender_agent_url": "https://remote.example.com",
                "task_description": "Summarize the quarterly report",
                "reply_to_url": "https://remote.example.com/results",
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["task_id"].startswith("a2a-remote-agent-42-")
    assert len(enqueued) == 1
    assert enqueued[0]["text"] == "Summarize the quarterly report"
    assert enqueued[0]["source_channel"] == "a2a"


# ---------------------------------------------------------------------------
# Test: unknown sender gets supervised policy
# ---------------------------------------------------------------------------


def test_unknown_sender_gets_supervised_policy(handler: A2AHandler) -> None:
    trust = resolve_sender_trust("unknown-agent-99", handler.trust_records)
    assert trust == "untrusted"
    assert policy_for_trust(trust) == "supervised"


def test_trusted_sender_gets_default_policy(handler: A2AHandler) -> None:
    trust = resolve_sender_trust("trusted-agent", handler.trust_records)
    assert trust == "trusted"
    assert policy_for_trust(trust) == "default"


# ---------------------------------------------------------------------------
# Test: HMAC signature verification on A2A routes
# ---------------------------------------------------------------------------


def test_hmac_signature_required_when_control_secret_set() -> None:
    """When control_secret is set, POST /a2a/tasks must require a valid signature."""
    import hashlib
    import hmac as hmac_mod
    import threading

    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="sig-test")
    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret="test-secret-key"),
        _runner=None,
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app, raise_server_exceptions=False)

    payload = json.dumps(
        {
            "sender_agent_id": "agent-x",
            "sender_agent_url": "https://x.example.com",
            "task_description": "do something",
        }
    ).encode()

    # Request without signature should fail
    resp = client.post("/a2a/tasks", content=payload, headers={"Content-Type": "application/json"})
    assert resp.status_code == 401

    # Request with valid signature should pass (will be 503 because runner is None)
    sig = hmac_mod.new(b"test-secret-key", payload, hashlib.sha256).hexdigest()
    resp = client.post(
        "/a2a/tasks",
        content=payload,
        headers={
            "X-Hermit-Signature-256": f"sha256={sig}",
            "Content-Type": "application/json",
        },
    )
    # 503 because runner is None, but auth passed
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Test: task status endpoint returns proof summary
# ---------------------------------------------------------------------------


def test_task_status_returns_proof_summary() -> None:
    import threading

    from hermit.plugins.builtin.hooks.webhook.a2a_hooks import _register_a2a_routes

    app = FastAPI()
    handler = A2AHandler(agent_id="status-test")

    fake_task = SimpleNamespace(task_id="task-123", status="completed")
    fake_store = MagicMock()
    fake_store.get_task.return_value = fake_task

    server = SimpleNamespace(
        _app=app,
        _config=SimpleNamespace(control_secret=None),
        _runner=SimpleNamespace(),
        _runner_lock=threading.Lock(),
        _kernel_store=MagicMock(return_value=fake_store),
    )
    _register_a2a_routes(server, handler)
    client = TestClient(app)

    with patch("hermit.kernel.verification.proofs.proofs.ProofService") as mock_proof_cls:
        mock_proof_cls.return_value.build_proof_summary.return_value = {
            "task_id": "task-123",
            "receipts": 3,
        }
        resp = client.get("/a2a/tasks/task-123/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "task-123"
    assert data["status"] == "completed"
    assert data["proof_summary"]["receipts"] == 3


# ---------------------------------------------------------------------------
# Test: result callback fires on task completion
# ---------------------------------------------------------------------------


def test_result_callback_fires_on_dispatch_result() -> None:
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks

    old_handler = a2a_hooks._handler

    try:
        sent: list[tuple[str, Any]] = []
        mock_handler = MagicMock()
        mock_handler.send_result.side_effect = lambda url, resp: sent.append((url, resp))
        a2a_hooks._handler = mock_handler

        a2a_hooks._on_dispatch_result(
            source="a2a/remote-agent",
            result_text="Task completed successfully",
            success=True,
            metadata={
                "a2a_reply_to": "https://remote.example.com/callback",
                "task_id": "task-abc",
            },
        )

        assert len(sent) == 1
        url, resp = sent[0]
        assert url == "https://remote.example.com/callback"
        assert resp.task_id == "task-abc"
        assert resp.status == "completed"
    finally:
        a2a_hooks._handler = old_handler


# ---------------------------------------------------------------------------
# Test: no callback when reply_to is empty
# ---------------------------------------------------------------------------


def test_no_callback_when_no_reply_to() -> None:
    from hermit.plugins.builtin.hooks.webhook import a2a_hooks

    old_handler = a2a_hooks._handler
    try:
        mock_handler = MagicMock()
        a2a_hooks._handler = mock_handler

        a2a_hooks._on_dispatch_result(
            source="a2a/agent",
            result_text="done",
            success=True,
            metadata={"task_id": "t1"},
        )

        mock_handler.send_result.assert_not_called()
    finally:
        a2a_hooks._handler = old_handler


# ---------------------------------------------------------------------------
# Test: build_capability_card returns correct dataclass
# ---------------------------------------------------------------------------


def test_build_capability_card_dataclass(handler: A2AHandler) -> None:
    card = handler.build_capability_card()
    assert isinstance(card, A2ACapabilityCard)
    assert card.agent_id == "test-agent-001"
    assert card.agent_name == "TestHermit"


# ---------------------------------------------------------------------------
# Test: send_result with no URL returns False
# ---------------------------------------------------------------------------


def test_send_result_empty_url() -> None:
    resp = A2ATaskResponse(task_id="t1", status="completed")
    assert A2AHandler.send_result("", resp) is False
