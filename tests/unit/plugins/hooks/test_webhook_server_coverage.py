"""Tests for webhook/server.py — coverage for missed lines.

Covers: _flatten_payload, FlattenDict, _verify_signature edge cases,
_kernel_store, _verify_control_request, control endpoints with missing
runner/task/approval, start/stop lifecycle, swap_runner, and _process
error paths.
"""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.webhook.models import WebhookConfig, WebhookRoute
from hermit.plugins.builtin.hooks.webhook.server import (
    FlattenDict,
    WebhookServer,
    _flatten_payload,
)
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# _flatten_payload
# ---------------------------------------------------------------------------


class TestFlattenPayload:
    def test_simple_flat_dict(self) -> None:
        result = _flatten_payload({"a": "1", "b": "2"})
        assert result == {"a": "1", "b": "2"}

    def test_nested_dict(self) -> None:
        result = _flatten_payload({"a": {"b": "v"}})
        assert result["a.b"] == "v"

    def test_deeply_nested(self) -> None:
        result = _flatten_payload({"a": {"b": {"c": "deep"}}})
        assert result["a.b.c"] == "deep"

    def test_none_values_skipped(self) -> None:
        result = _flatten_payload({"a": None, "b": "ok"})
        assert "a" not in result
        assert result["b"] == "ok"

    def test_empty_dict(self) -> None:
        result = _flatten_payload({})
        assert result == {}

    def test_numeric_values_converted_to_str(self) -> None:
        result = _flatten_payload({"count": 42})
        assert result["count"] == "42"

    def test_mixed_nested_and_flat(self) -> None:
        result = _flatten_payload({"x": "1", "y": {"z": "2"}})
        assert result["x"] == "1"
        assert result["y.z"] == "2"


# ---------------------------------------------------------------------------
# FlattenDict.render
# ---------------------------------------------------------------------------


class TestFlattenDictRender:
    def test_simple_placeholder(self) -> None:
        fd = FlattenDict({"action": "opened"})
        assert fd.render("Event: {action}") == "Event: opened"

    def test_nested_placeholder(self) -> None:
        fd = FlattenDict({"repo": {"name": "test"}})
        assert fd.render("Repo: {repo.name}") == "Repo: test"

    def test_missing_placeholder_preserved(self) -> None:
        fd = FlattenDict({})
        result = fd.render("Value: {unknown}")
        assert result == "Value: {unknown}"

    def test_multiple_placeholders(self) -> None:
        fd = FlattenDict({"a": "1", "b": {"c": "2"}})
        result = fd.render("{a} and {b.c}")
        assert result == "1 and 2"

    def test_no_placeholders(self) -> None:
        fd = FlattenDict({"x": "y"})
        assert fd.render("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _verify_signature edge cases
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def test_plain_hex_signature_accepted(self) -> None:
        body = b"test body"
        secret = "mysecret"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"X-Sig": expected}
        # Should not raise
        WebhookServer._verify_signature(body, secret, "X-Sig", headers)

    def test_sha256_prefix_signature_accepted(self) -> None:
        body = b"test body"
        secret = "s"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"X-Sig": f"sha256={expected}"}
        WebhookServer._verify_signature(body, secret, "X-Sig", headers)

    def test_missing_header_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            WebhookServer._verify_signature(b"body", "secret", "X-Missing", {})
        assert exc_info.value.status_code == 401
        assert "Missing" in str(exc_info.value.detail)

    def test_empty_header_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            WebhookServer._verify_signature(b"body", "secret", "X-Sig", {"X-Sig": ""})
        assert exc_info.value.status_code == 401

    def test_invalid_signature_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            WebhookServer._verify_signature(b"body", "secret", "X-Sig", {"X-Sig": "sha256=bad"})
        assert exc_info.value.status_code == 401
        assert "Invalid" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# _kernel_store
# ---------------------------------------------------------------------------


class TestKernelStore:
    def test_no_runner_raises_503(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = None
        with pytest.raises(HTTPException) as exc_info:
            server._kernel_store()
        assert exc_info.value.status_code == 503

    def test_task_controller_path(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        mock_store = MagicMock()
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=mock_store))
        assert server._kernel_store() is mock_store

    def test_agent_kernel_store_fallback(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        mock_store = MagicMock()
        server._runner = SimpleNamespace(
            task_controller=None,
            agent=SimpleNamespace(kernel_store=mock_store),
        )
        assert server._kernel_store() is mock_store

    def test_no_store_available_raises_503(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(
            task_controller=None,
            agent=SimpleNamespace(kernel_store=None),
        )
        with pytest.raises(HTTPException) as exc_info:
            server._kernel_store()
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Control endpoint edge cases
# ---------------------------------------------------------------------------


class TestControlEndpointEdgeCases:
    @staticmethod
    def _sign(body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_show_task_not_found(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        config = WebhookConfig(control_secret="sec")
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
        client = TestClient(server._app, raise_server_exceptions=False)
        resp = client.get(
            "/tasks/nonexistent",
            headers={"X-Hermit-Signature-256": self._sign(b"", "sec")},
        )
        assert resp.status_code == 404

    def test_task_events_returns_events(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t", goal="g", source_channel="test")
        config = WebhookConfig(control_secret="sec")
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
        client = TestClient(server._app)
        resp = client.get(
            f"/tasks/{task.task_id}/events",
            headers={"X-Hermit-Signature-256": self._sign(b"", "sec")},
        )
        assert resp.status_code == 200
        assert "events" in resp.json()

    def test_rebuild_projections_without_task_id(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        config = WebhookConfig(control_secret="sec")
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
        client = TestClient(server._app)
        resp = client.post(
            "/projections/rebuild",
            headers={"X-Hermit-Signature-256": self._sign(b"", "sec")},
        )
        assert resp.status_code == 200

    def test_approve_no_runner_raises_503(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = None
        client = TestClient(server._app, raise_server_exceptions=False)
        resp = client.post("/approvals/some-id/approve")
        assert resp.status_code == 503

    def test_deny_approval_not_found(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app, raise_server_exceptions=False)
        resp = client.post("/approvals/nonexistent/deny")
        assert resp.status_code == 404

    def test_receipt_rollback_missing_receipt(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
        client = TestClient(server._app, raise_server_exceptions=False)
        resp = client.post("/receipts/nonexistent/rollback")
        assert resp.status_code == 500  # KeyError propagates


# ---------------------------------------------------------------------------
# swap_runner
# ---------------------------------------------------------------------------


class TestSwapRunner:
    def test_swap_runner_updates_runner(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        assert server._runner is None
        new_runner = MagicMock()
        server.swap_runner(new_runner)
        assert server._runner is new_runner

    def test_swap_runner_replaces_existing(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        old = MagicMock()
        new = MagicMock()
        server._runner = old
        server.swap_runner(new)
        assert server._runner is new


# ---------------------------------------------------------------------------
# _process with no runner
# ---------------------------------------------------------------------------


class TestProcessEdgeCases:
    def test_process_with_no_runner_returns_early(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        server._runner = None
        route = WebhookRoute(name="test", path="/test", prompt_template="hi")
        # Should not raise
        server._process(route, {})

    def test_process_with_json_parse_failure(self) -> None:
        config = WebhookConfig(routes=[WebhookRoute(name="t", path="/t", prompt_template="msg")])
        server = WebhookServer(config, HooksEngine())
        server._runner = MagicMock()
        server._runner.dispatch.return_value = SimpleNamespace(text="ok")
        client = TestClient(server._app)
        # Post non-JSON body
        resp = client.post("/t", content=b"not-json", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# _verify_control_request without control_secret
# ---------------------------------------------------------------------------


class TestVerifyControlRequestNoSecret:
    def test_no_control_secret_allows_request(self, kernel_store: KernelStore) -> None:
        store = kernel_store
        config = WebhookConfig(control_secret=None)
        server = WebhookServer(config, HooksEngine())
        server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store))
        client = TestClient(server._app)
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lifecycle — start/stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_stop_without_start(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        # Should not raise
        server.stop()

    def test_stop_sets_should_exit(self) -> None:
        config = WebhookConfig()
        server = WebhookServer(config, HooksEngine())
        mock_uv = MagicMock()
        server._server = mock_uv
        server.stop()
        assert mock_uv.should_exit is True
