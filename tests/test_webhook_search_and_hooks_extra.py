from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hermit.builtin.web_tools import search
from hermit.builtin.webhook.models import WebhookConfig, WebhookRoute
from hermit.builtin.webhook.server import WebhookServer
from hermit.plugin.hooks import HooksEngine, _safe_call


@pytest.fixture(autouse=True)
def _force_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


def test_hooks_engine_safe_call_and_fire_first(monkeypatch) -> None:
    engine = HooksEngine()
    calls: list[str] = []

    def first_handler(source: str) -> None:
        calls.append(f"first:{source}")

    def second_handler(**kwargs):
        calls.append(f"second:{kwargs['source']}")
        return "handled"

    engine.register("dispatch", second_handler, priority=10)
    engine.register("dispatch", first_handler, priority=0)

    assert engine.has_handlers("dispatch") is True
    assert engine.has_handlers("missing") is False
    assert engine.fire("dispatch", source="webhook/test") == [None, "handled"]
    assert engine.fire_first("dispatch", source="webhook/test") == "handled"
    assert engine.fire_first("missing", source="webhook/test") is None
    assert calls == [
        "first:webhook/test",
        "second:webhook/test",
        "first:webhook/test",
        "second:webhook/test",
    ]

    monkeypatch.setattr("hermit.plugin.hooks.inspect.signature", lambda handler: (_ for _ in ()).throw(ValueError()))
    assert _safe_call(lambda **kwargs: kwargs["value"], {"value": 3}) == 3


def test_search_helpers_cover_parser_extract_and_instant_answer(monkeypatch) -> None:
    assert search._extract_real_url("/l/?kh=-1&uddg=https%3A%2F%2Fexample.com") == "https://example.com"
    assert search._extract_real_url("https://plain.example") == "https://plain.example"
    assert search._extract_real_url("/relative") == "/relative"

    parser = search._DDGLiteParser()
    parser.feed(
        """
<a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com">Example Title</a>
<td class="result-snippet">Snippet text</td>
""".strip()
    )
    assert parser.results == [
        {
            "href": "/l/?uddg=https%3A%2F%2Fexample.com",
            "title": "Example Title",
            "snippet": "Snippet text",
        }
    ]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "AbstractText": "A concise summary.",
                    "AbstractSource": "Example Source",
                    "AbstractURL": "https://example.com/source",
                    "Answer": "42",
                    "Definition": "The answer.",
                }
            ).encode("utf-8")

    monkeypatch.setattr(search.urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse())
    instant = search._ddg_instant_answer("life meaning")
    assert "Example Source" in instant
    assert "42" in instant
    assert "The answer." in instant


@pytest.mark.asyncio
async def test_webhook_server_helpers_cover_kernel_store_control_and_lifecycle(monkeypatch, tmp_path) -> None:
    route = WebhookRoute(name="test", path="/webhook/test", prompt_template="Hello {name}")
    server = WebhookServer(WebhookConfig(host="127.0.0.1", port=8123, routes=[route], control_secret=None), HooksEngine())

    with pytest.raises(HTTPException) as runner_missing:
        server._kernel_store()
    assert runner_missing.value.status_code == 503

    task_store = SimpleNamespace()
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=task_store))
    assert server._kernel_store() is task_store

    agent_store = SimpleNamespace()
    server._runner = SimpleNamespace(task_controller=None, agent=SimpleNamespace(kernel_store=agent_store))
    assert server._kernel_store() is agent_store

    server._runner = SimpleNamespace(task_controller=None, agent=SimpleNamespace(kernel_store=None))
    with pytest.raises(HTTPException) as kernel_missing:
        server._kernel_store()
    assert kernel_missing.value.status_code == 503

    async def request_body():
        return b'{"task_id":"task-1"}'

    request = SimpleNamespace(body=request_body, headers={})
    body = await server._verify_control_request(request)
    assert body == b'{"task_id":"task-1"}'

    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=SimpleNamespace(get_task=lambda task_id: None)))
    with pytest.raises(HTTPException) as task_missing:
        await server._show_task("task-1", request)
    assert task_missing.value.status_code == 404

    rebuild_calls: list[str] = []

    class FakeProjectionService:
        def __init__(self, store) -> None:
            self.store = store

        def rebuild_task(self, task_id: str):
            rebuild_calls.append(f"task:{task_id}")
            return {"task_id": task_id}

        def rebuild_all(self):
            rebuild_calls.append("all")
            return {"all": True}

    monkeypatch.setattr("hermit.builtin.webhook.server.ProjectionService", FakeProjectionService)
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=SimpleNamespace()))

    assert await server._rebuild_projections(request) == {"task_id": "task-1"}
    async def bad_request_body():
        return b"{"

    bad_request = SimpleNamespace(body=bad_request_body, headers={})
    assert await server._rebuild_projections(bad_request) == {"all": True}
    assert rebuild_calls == ["task:task-1", "all"]

    class FakeConfig:
        def __init__(self, app, host, port, log_level, access_log):
            self.app = app
            self.host = host
            self.port = port

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.run = lambda: None
            self.should_exit = False

    started: list[str] = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self.name)

    shutdowns: list[bool] = []
    server._executor = SimpleNamespace(shutdown=lambda wait=False: shutdowns.append(wait))
    monkeypatch.setattr("hermit.builtin.webhook.server.uvicorn.Config", FakeConfig)
    monkeypatch.setattr("hermit.builtin.webhook.server.uvicorn.Server", FakeServer)
    monkeypatch.setattr("hermit.builtin.webhook.server.threading.Thread", FakeThread)

    server.start(SimpleNamespace())
    assert started == ["webhook-http"]
    server.stop()
    assert server._server.should_exit is True
    assert shutdowns == [False]


@pytest.mark.asyncio
async def test_webhook_server_approve_and_deny_cover_error_paths() -> None:
    route = WebhookRoute(name="test", path="/webhook/test", prompt_template="Hello")
    server = WebhookServer(WebhookConfig(host="127.0.0.1", port=8123, routes=[route], control_secret=None), HooksEngine())

    async def empty_body():
        return b"{}"

    request = SimpleNamespace(body=empty_body, headers={})

    with pytest.raises(HTTPException) as runner_missing:
        await server._approve("approval-1", request)
    assert runner_missing.value.status_code == 503

    store_missing_approval = SimpleNamespace(
        get_approval=lambda approval_id: None,
        get_task=lambda task_id: None,
    )
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store_missing_approval))
    with pytest.raises(HTTPException) as approval_missing:
        await server._approve("approval-1", request)
    assert approval_missing.value.status_code == 404

    approval = SimpleNamespace(approval_id="approval-1", task_id="task-1")
    store_missing_task = SimpleNamespace(
        get_approval=lambda approval_id: approval,
        get_task=lambda task_id: None,
    )
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=store_missing_task))
    with pytest.raises(HTTPException) as task_missing:
        await server._approve("approval-1", request)
    assert task_missing.value.status_code == 404

    server._runner = None
    with pytest.raises(HTTPException) as deny_runner_missing:
        await server._deny("approval-1", request)
    assert deny_runner_missing.value.status_code == 503

    async def bad_reason_body():
        return b"{"

    bad_reason_request = SimpleNamespace(body=bad_reason_body, headers={})
    task = SimpleNamespace(task_id="task-1", conversation_id="conv-1")
    deny_store = SimpleNamespace(
        get_approval=lambda approval_id: approval,
        get_task=lambda task_id: task,
    )
    called: list[tuple[str, str, str, str]] = []
    server._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=deny_store),
        _resolve_approval=lambda conversation_id, action, approval_id, reason="": called.append((conversation_id, action, approval_id, reason)) or SimpleNamespace(text="denied"),
    )

    missing_approval_store = SimpleNamespace(
        get_approval=lambda approval_id: None,
        get_task=lambda task_id: None,
    )
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=missing_approval_store))
    with pytest.raises(HTTPException) as deny_missing_approval:
        await server._deny("approval-1", request)
    assert deny_missing_approval.value.status_code == 404

    missing_task_store = SimpleNamespace(
        get_approval=lambda approval_id: approval,
        get_task=lambda task_id: None,
    )
    server._runner = SimpleNamespace(task_controller=SimpleNamespace(store=missing_task_store))
    with pytest.raises(HTTPException) as deny_missing_task:
        await server._deny("approval-1", request)
    assert deny_missing_task.value.status_code == 404

    server._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=deny_store),
        _resolve_approval=lambda conversation_id, action, approval_id, reason="": called.append((conversation_id, action, approval_id, reason)) or SimpleNamespace(text="denied"),
    )
    response = await server._deny("approval-1", bad_reason_request)
    assert response == {"status": "denied", "approval_id": "approval-1", "text": "denied"}
    assert called == [("conv-1", "deny", "approval-1", "")]


@pytest.mark.asyncio
async def test_webhook_server_task_and_approval_success_helpers(monkeypatch) -> None:
    route = WebhookRoute(name="test", path="/webhook/test", prompt_template="Hello")
    server = WebhookServer(WebhookConfig(host="127.0.0.1", port=8123, routes=[route], control_secret=None), HooksEngine())

    async def body():
        return b'{"ok": true}'

    request = SimpleNamespace(body=body, headers={})
    approval = SimpleNamespace(approval_id="approval-1", task_id="task-1")
    task = SimpleNamespace(task_id="task-1", conversation_id="conv-1", status="running")
    store = SimpleNamespace(
        list_events=lambda task_id, limit=100: [{"event_type": "task.running"}],
        get_task=lambda task_id: task,
        list_approvals=lambda task_id=None, limit=20, **kwargs: [approval],
        get_approval=lambda approval_id: approval,
    )
    server._runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        _resolve_approval=lambda conversation_id, action, approval_id, reason="": SimpleNamespace(text=f"{action}:{approval_id}:{reason}"),
    )

    class FakeSupervisionService:
        def __init__(self, attached_store) -> None:
            self.store = attached_store

        def build_task_case(self, task_id: str):
            return {"case": task_id}

    class FakeProofService:
        def __init__(self, attached_store) -> None:
            self.store = attached_store

        def build_proof_summary(self, task_id: str):
            return {"proof": task_id}

        def export_task_proof(self, task_id: str):
            return {"export": task_id}

    class FakeRollbackService:
        def __init__(self, attached_store) -> None:
            self.store = attached_store

        def execute(self, receipt_id: str):
            return {"rollback": receipt_id}

    monkeypatch.setattr("hermit.builtin.webhook.server.SupervisionService", FakeSupervisionService)
    monkeypatch.setattr("hermit.builtin.webhook.server.ProofService", FakeProofService)
    monkeypatch.setattr("hermit.builtin.webhook.server.RollbackService", FakeRollbackService)

    assert await server._task_events("task-1", request) == {"events": [{"event_type": "task.running"}]}
    shown = await server._show_task("task-1", request)
    assert shown["task"]["task_id"] == "task-1"
    assert shown["approvals"][0]["approval_id"] == "approval-1"
    assert await server._task_case("task-1", request) == {"case": "task-1"}
    assert await server._task_proof("task-1", request) == {"proof": "task-1"}
    assert await server._task_proof_export("task-1", request) == {"export": "task-1"}
    assert await server._receipt_rollback("receipt-1", request) == {"rollback": "receipt-1"}

    async def bad_json_body():
        return b"{"

    approved = await server._approve("approval-1", SimpleNamespace(body=bad_json_body, headers={}))
    assert approved["status"] == "approved"
    assert approved["text"] == "approve:approval-1:"


def test_webhook_process_logs_enqueue_failure_and_search_lite_no_results(monkeypatch) -> None:
    route = WebhookRoute(name="test", path="/webhook/test", prompt_template="Hello {name}")
    server = WebhookServer(WebhookConfig(host="127.0.0.1", port=8123, routes=[route], control_secret=None), HooksEngine())
    logged: list[str] = []

    class FakeAgentRunner:
        def enqueue_ingress(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("hermit.core.runner.AgentRunner", FakeAgentRunner)
    monkeypatch.setattr("hermit.builtin.webhook.server._log.exception", lambda event, **kwargs: logged.append(f"{event}:{kwargs['route']}"))

    server._runner = FakeAgentRunner()
    server._process(route, {"name": "Hermit"})
    assert logged == ["webhook_dispatch_error:test"]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"<html><body>No results</body></html>"

    monkeypatch.setattr(search.urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse())
    assert search._ddg_lite_search("empty", max_results=1) == ""
