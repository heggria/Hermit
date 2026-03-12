"""Webhook HTTP server — FastAPI-based event receiver for agent dispatch."""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

from hermit.builtin.webhook.models import WebhookConfig, WebhookRoute
from hermit.plugin.base import HookEvent

if TYPE_CHECKING:
    from hermit.core.runner import AgentRunner
    from hermit.plugin.hooks import HooksEngine

_log = structlog.get_logger()

_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def _flatten_payload(d: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Recursively flatten a nested dict into dotted-key pairs.

    ``{"a": {"b": "v"}}`` → ``{"a.b": "v", "a": "{a}"}``

    Top-level dict values are kept as-is so templates that reference only the
    top-level key still work via the placeholder fallback.
    """
    result: dict[str, str] = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten_payload(v, full_key))
        elif v is not None:
            result[full_key] = str(v)
    return result


class FlattenDict:
    """Renders a prompt template against a nested payload dict.

    Python's ``str.format_map`` interprets ``{a.b}`` as attribute access on
    ``a``, not as a dotted dictionary key.  This helper flattens the payload
    first and then uses regex-based substitution so that ``{a.b}`` resolves
    to ``payload["a"]["b"]``.  Unknown placeholders are preserved verbatim.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._flat = _flatten_payload(payload)

    def render(self, template: str) -> str:
        def replace(m: re.Match) -> str:
            key = m.group(1)
            return self._flat.get(key, m.group(0))

        return _PLACEHOLDER_RE.sub(replace, template)


class WebhookServer:
    def __init__(self, config: WebhookConfig, hooks: "HooksEngine") -> None:
        self._config = config
        self._hooks = hooks
        self._runner: "AgentRunner | None" = None
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="webhook"
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        self._app = FastAPI(title="Hermit Webhook", docs_url=None, redoc_url=None)
        self._app.add_api_route("/health", self._health, methods=["GET"])
        self._app.add_api_route("/routes", self._list_routes, methods=["GET"])
        self._app.add_api_route("/tasks", self._list_tasks, methods=["GET"])
        self._app.add_api_route("/tasks/{task_id}", self._show_task, methods=["GET"])
        self._app.add_api_route("/tasks/{task_id}/events", self._task_events, methods=["GET"])
        self._app.add_api_route("/approvals/pending", self._list_pending_approvals, methods=["GET"])
        self._app.add_api_route("/approvals/{approval_id}/approve", self._approve, methods=["POST"])
        self._app.add_api_route("/approvals/{approval_id}/deny", self._deny, methods=["POST"])

        for route in config.routes:
            self._register_route(route)

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_route(self, route: WebhookRoute) -> None:
        route_ref = route  # capture for closure

        async def handle(request: Request) -> Response:
            body = await request.body()

            if route_ref.secret:
                self._verify_signature(
                    body,
                    route_ref.secret,
                    route_ref.signature_header,
                    request.headers,
                )

            try:
                import json
                payload: dict[str, Any] = json.loads(body) if body else {}
            except Exception:
                payload = {}

            self._executor.submit(self._process, route_ref, payload)
            return Response(status_code=202, content="Accepted")

        self._app.add_api_route(route.path, handle, methods=["POST"])
        _log.info("webhook_route_registered", name=route.name, path=route.path)  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_signature(
        body: bytes,
        secret: str,
        header_name: str,
        headers: Any,
    ) -> None:
        sig_header = headers.get(header_name, "")
        if not sig_header:
            raise HTTPException(status_code=401, detail="Missing signature header")

        # Support both "sha256=<hex>" and plain hex formats
        if "=" in sig_header:
            _, _, sig_hex = sig_header.partition("=")
        else:
            sig_hex = sig_header

        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, sig_hex):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _process(self, route: WebhookRoute, payload: dict[str, Any]) -> None:
        if self._runner is None:
            _log.error("webhook_no_runner", route=route.name)  # type: ignore[call-arg]
            return

        prompt = FlattenDict(payload).render(route.prompt_template)

        session_id = f"webhook-{route.name}-{uuid4().hex[:8]}"
        _log.info("webhook_dispatch", route=route.name, session_id=session_id)  # type: ignore[call-arg]

        result_text = ""
        success = True
        error: str | None = None

        try:
            result = self._runner.dispatch(session_id, prompt)
            result_text = result.text or ""
        except Exception as exc:
            success = False
            error = str(exc)
            _log.exception("webhook_dispatch_error", route=route.name, error=error)  # type: ignore[call-arg]
        finally:
            try:
                self._runner.close_session(session_id)
            except Exception:
                _log.exception("webhook_close_session_error", route=route.name, session_id=session_id)  # type: ignore[call-arg]

        self._hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source=f"webhook/{route.name}",
            title=f"Webhook: {route.name}",
            result_text=result_text,
            success=success,
            error=error,
            notify=route.notify,
            metadata={"payload_keys": list(payload.keys())},
        )

    def _kernel_store(self) -> Any:
        if self._runner is None:
            raise HTTPException(status_code=503, detail="Runner is not attached")
        task_controller = getattr(self._runner, "task_controller", None)
        if task_controller is not None:
            return task_controller.store
        store = getattr(getattr(self._runner, "agent", None), "kernel_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="Task kernel is not available")
        return store

    async def _verify_control_request(self, request: Request) -> bytes:
        body = await request.body()
        if self._config.control_secret:
            self._verify_signature(
                body,
                self._config.control_secret,
                "X-Hermit-Signature-256",
                request.headers,
            )
        return body

    async def _list_tasks(self, request: Request, limit: int = 20) -> dict[str, Any]:
        await self._verify_control_request(request)
        store = self._kernel_store()
        return {
            "tasks": [task.__dict__ for task in store.list_tasks(limit=limit)]
        }

    async def _show_task(self, task_id: str, request: Request) -> dict[str, Any]:
        await self._verify_control_request(request)
        store = self._kernel_store()
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task": task.__dict__,
            "approvals": [approval.__dict__ for approval in store.list_approvals(task_id=task_id, limit=20)],
        }

    async def _task_events(self, task_id: str, request: Request, limit: int = 100) -> dict[str, Any]:
        await self._verify_control_request(request)
        store = self._kernel_store()
        return {"events": store.list_events(task_id=task_id, limit=limit)}

    async def _list_pending_approvals(
        self,
        request: Request,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        await self._verify_control_request(request)
        store = self._kernel_store()
        approvals = store.list_approvals(conversation_id=conversation_id, status="pending", limit=limit)
        return {"approvals": [approval.__dict__ for approval in approvals]}

    async def _approve(self, approval_id: str, request: Request) -> dict[str, Any]:
        body = await self._verify_control_request(request)
        resolution = {}
        if body:
            try:
                import json
                resolution = json.loads(body)
            except Exception:
                resolution = {}

        if self._runner is None:
            raise HTTPException(status_code=503, detail="Runner is not attached")
        store = self._kernel_store()
        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        task = store.get_task(approval.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        result = self._runner._resolve_approval(  # type: ignore[attr-defined]
            task.conversation_id,
            action="approve",
            approval_id=approval_id,
        )
        return {
            "status": "approved",
            "approval_id": approval_id,
            "text": result.text,
            "resolution": resolution,
        }

    async def _deny(self, approval_id: str, request: Request) -> dict[str, Any]:
        body = await self._verify_control_request(request)
        reason = ""
        if body:
            try:
                import json
                payload = json.loads(body)
                reason = str(payload.get("reason", "")).strip()
            except Exception:
                reason = ""

        if self._runner is None:
            raise HTTPException(status_code=503, detail="Runner is not attached")
        store = self._kernel_store()
        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        task = store.get_task(approval.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        result = self._runner._resolve_approval(  # type: ignore[attr-defined]
            task.conversation_id,
            action="deny",
            approval_id=approval_id,
            reason=reason,
        )
        return {"status": "denied", "approval_id": approval_id, "text": result.text}

    # ------------------------------------------------------------------
    # Utility endpoints
    # ------------------------------------------------------------------

    async def _health(self) -> dict[str, str]:
        return {"status": "ok"}

    async def _list_routes(self) -> dict[str, Any]:
        return {
            "routes": [
                {"name": r.name, "path": r.path, "has_secret": bool(r.secret)}
                for r in self._config.routes
            ]
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, runner: "AgentRunner") -> None:
        self._runner = runner
        uv_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="webhook-http",
            daemon=True,
        )
        self._thread.start()
        _log.info(  # type: ignore[call-arg]
            "webhook_server_started",
            host=self._config.host,
            port=self._config.port,
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._executor.shutdown(wait=False)
        _log.info("webhook_server_stopped")  # type: ignore[call-arg]
