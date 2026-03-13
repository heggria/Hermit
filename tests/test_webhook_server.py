"""Tests for the webhook plugin — routes, signature verification, dispatch."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermit.builtin.webhook.models import WebhookConfig, WebhookRoute, load_config
from hermit.builtin.webhook.server import WebhookServer
from hermit.core.runner import AgentRunner
from hermit.kernel.store import KernelStore
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
def control_config() -> WebhookConfig:
    return WebhookConfig(
        host="127.0.0.1",
        port=8321,
        routes=[],
        control_secret="control-secret",
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


def _make_real_runner() -> AgentRunner:
    return AgentRunner(
        agent=SimpleNamespace(workspace_root="/tmp/workspace"),
        session_manager=SimpleNamespace(),
        plugin_manager=SimpleNamespace(settings=SimpleNamespace(locale="en-US")),
        task_controller=SimpleNamespace(source_from_session=lambda _session_id: "webhook"),
    )


def _seed_kernel_records(store: KernelStore) -> tuple[str, str]:
    store.ensure_conversation("webhook-control", source_channel="webhook")
    task = store.create_task(
        conversation_id="webhook-control",
        title="Webhook control test",
        goal="Approve a pending action",
        source_channel="webhook",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    approval = store.create_approval(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        approval_type="write_local",
        requested_action={"tool_name": "write_file"},
        request_packet_ref=None,
    )
    return task.task_id, approval.approval_id


def _seed_proof_records(store: KernelStore) -> tuple[str, str]:
    store.ensure_conversation("webhook-proof", source_channel="webhook")
    task = store.create_task(
        conversation_id="webhook-proof",
        title="Webhook proof test",
        goal="Inspect proof output",
        source_channel="webhook",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed this write.",
        evidence_refs=["artifact_action"],
        action_type="write_local",
    )
    permit = store.create_execution_permit(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_1",
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={"target_paths": ["workspace/example.txt"]},
        idempotency_key="idem_webhook_proof",
        expires_at=None,
    )
    receipt = store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="webhook proof receipt",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        permit_ref=permit.permit_id,
        policy_ref="policy_1",
    )
    return task.task_id, receipt.receipt_id


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

    def test_process_enqueues_async_ingress_for_agent_runner(self, simple_config, hooks) -> None:
        server = WebhookServer(simple_config, hooks)
        runner = _make_real_runner()
        enqueue_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        dispatch_events: list[dict[str, Any]] = []
        hooks.register(str(HookEvent.DISPATCH_RESULT), lambda **kw: dispatch_events.append(kw))
        runner.enqueue_ingress = lambda *args, **kwargs: enqueue_calls.append((args, kwargs)) or SimpleNamespace(task_id="task_1")  # type: ignore[method-assign]
        server._runner = runner

        payload = {"action": "opened", "repository": {"full_name": "org/repo"}}
        server._process(simple_config.routes[0], payload)

        assert len(enqueue_calls) == 1
        args, kwargs = enqueue_calls[0]
        assert args[1] == "Event: opened on org/repo"
        assert kwargs["source_channel"] == "webhook"
        assert kwargs["notify"] == {"feishu_chat_id": "oc_abc"}
        assert kwargs["source_ref"] == "webhook/test"
        assert kwargs["ingress_metadata"]["webhook_route"] == "test"
        assert kwargs["ingress_metadata"]["payload_keys"] == ["action", "repository"]
        assert dispatch_events == []


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


class TestControlEndpoints:
    @staticmethod
    def _sign(body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_list_tasks_requires_valid_signature(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id, _approval_id = _seed_kernel_records(store)
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app)

        resp = client.get(
            "/tasks",
            headers={"X-Hermit-Signature-256": self._sign(b"", "control-secret")},
        )

        assert resp.status_code == 200
        assert resp.json()["tasks"][0]["task_id"] == task_id

    def test_pending_approvals_endpoint_lists_kernel_records(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        _task_id, approval_id = _seed_kernel_records(store)
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app)

        resp = client.get(
            "/approvals/pending?conversation_id=webhook-control",
            headers={"X-Hermit-Signature-256": self._sign(b"", "control-secret")},
        )

        assert resp.status_code == 200
        assert resp.json()["approvals"][0]["approval_id"] == approval_id

    def test_proof_endpoints_return_summary_and_export(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id, receipt_id = _seed_proof_records(store)
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app)

        summary = client.get(
            f"/tasks/{task_id}/proof",
            headers={"X-Hermit-Signature-256": self._sign(b"", "control-secret")},
        )
        assert summary.status_code == 200
        assert summary.json()["chain_verification"]["valid"] is True
        assert summary.json()["missing_receipt_bundle_count"] == 1

        export = client.post(
            f"/tasks/{task_id}/proof/export",
            headers={"X-Hermit-Signature-256": self._sign(b"", "control-secret")},
        )
        assert export.status_code == 200
        assert export.json()["status"] == "verified"
        assert export.json()["proof_bundle_ref"]
        assert store.get_receipt(receipt_id).receipt_bundle_ref is not None

    def test_case_and_projection_endpoints_return_operator_payload(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id, _receipt_id = _seed_proof_records(store)
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app)

        case_resp = client.get(
            f"/tasks/{task_id}/case",
            headers={"X-Hermit-Signature-256": self._sign(b"", "control-secret")},
        )
        rebuild_body = json.dumps({"task_id": task_id}).encode()
        rebuild_resp = client.post(
            "/projections/rebuild",
            content=rebuild_body,
            headers={
                "Content-Type": "application/json",
                "X-Hermit-Signature-256": self._sign(rebuild_body, "control-secret"),
            },
        )

        assert case_resp.status_code == 200
        assert case_resp.json()["task"]["task_id"] == task_id
        assert rebuild_resp.status_code == 200
        assert rebuild_resp.json()["task"]["task_id"] == task_id

    def test_proof_endpoints_require_valid_signature(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id, _receipt_id = _seed_proof_records(store)
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=MagicMock(),
        )
        client = TestClient(server._app, raise_server_exceptions=False)

        resp = client.get(
            f"/tasks/{task_id}/proof",
            headers={"X-Hermit-Signature-256": "sha256=badhash"},
        )
        assert resp.status_code == 401

    def test_approve_endpoint_uses_task_conversation(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        _task_id, approval_id = _seed_kernel_records(store)
        resolve = MagicMock(return_value=SimpleNamespace(text="approved"))
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=resolve,
        )
        client = TestClient(server._app)
        body = json.dumps({"source": "test"}).encode()

        resp = client.post(
            f"/approvals/{approval_id}/approve",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hermit-Signature-256": self._sign(body, "control-secret"),
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        resolve.assert_called_once_with("webhook-control", action="approve", approval_id=approval_id)

    def test_deny_endpoint_forwards_reason(self, control_config, hooks, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        _task_id, approval_id = _seed_kernel_records(store)
        resolve = MagicMock(return_value=SimpleNamespace(text="denied"))
        server = WebhookServer(control_config, hooks)
        server._runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            _resolve_approval=resolve,
        )
        client = TestClient(server._app)
        body = json.dumps({"reason": "not safe"}).encode()

        resp = client.post(
            f"/approvals/{approval_id}/deny",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hermit-Signature-256": self._sign(body, "control-secret"),
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "denied"
        resolve.assert_called_once_with(
            "webhook-control",
            action="deny",
            approval_id=approval_id,
            reason="not safe",
        )


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
