"""A2A protocol hooks — registers A2A routes on the webhook server at serve start."""

import json
import logging
from typing import Any

from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

_log = logging.getLogger(__name__)

_handler: Any = None


def _on_serve_start(*, settings: Any, runner: Any = None, **kw: Any) -> None:
    """Register A2A routes on the webhook server's FastAPI app."""
    global _handler

    from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks

    server = webhook_hooks._server  # pyright: ignore[reportPrivateUsage]
    if server is None:
        _log.debug("a2a_hooks: webhook server not available, skipping A2A route registration")
        return

    from hermit.plugins.builtin.hooks.webhook.a2a import A2AHandler

    agent_id = str(getattr(settings, "agent_id", "") or "")
    agent_name = str(getattr(settings, "agent_name", "") or "Hermit")
    _handler = A2AHandler(agent_id=agent_id, agent_name=agent_name)

    _register_a2a_routes(server, _handler)
    _log.info("a2a_routes_registered")


def _register_a2a_routes(server: Any, handler: Any) -> None:
    """Add A2A endpoints to the existing FastAPI app."""
    from fastapi import HTTPException, Request, Response

    from hermit.plugins.builtin.hooks.webhook.a2a import A2ATaskRequest
    from hermit.plugins.builtin.hooks.webhook.server import WebhookServer

    app = server._app

    # -- Capability card (public, no signature) ----------------------------

    async def capability_card() -> dict[str, Any]:
        card = handler.build_capability_card()
        return {
            "agent_id": card.agent_id,
            "agent_name": card.agent_name,
            "capabilities": card.capabilities,
            "supported_actions": card.supported_actions,
            "trust_level": card.trust_level,
        }

    app.add_api_route(
        "/a2a/.well-known/agent.json",
        capability_card,
        methods=["GET"],
    )

    # -- Task submission ---------------------------------------------------

    async def submit_task(request: Request) -> Response:
        body = await request.body()

        if server._config.control_secret:
            WebhookServer._verify_signature(  # pyright: ignore[reportPrivateUsage]
                body,
                server._config.control_secret,
                "X-Hermit-Signature-256",
                request.headers,
            )

        try:
            payload = json.loads(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON") from exc

        task_request = A2ATaskRequest(
            sender_agent_id=str(payload.get("sender_agent_id", "")),
            sender_agent_url=str(payload.get("sender_agent_url", "")),
            task_description=str(payload.get("task_description", "")),
            required_capabilities=list(payload.get("required_capabilities", [])),
            priority=str(payload.get("priority", "normal")),
            context_artifacts=list(payload.get("context_artifacts", [])),
            reply_to_url=str(payload.get("reply_to_url", "")),
        )

        if not task_request.sender_agent_id:
            raise HTTPException(status_code=400, detail="sender_agent_id is required")
        if not task_request.task_description:
            raise HTTPException(status_code=400, detail="task_description is required")

        with server._runner_lock:
            runner = server._runner
        if runner is None:
            raise HTTPException(status_code=503, detail="Runner is not attached")

        resp = handler.handle_task_request(task_request, runner)
        return Response(
            status_code=202,
            content=json.dumps(
                {
                    "task_id": resp.task_id,
                    "status": resp.status,
                    "result_summary": resp.result_summary,
                }
            ),
            media_type="application/json",
        )

    app.add_api_route("/a2a/tasks", submit_task, methods=["POST"])

    # -- Task status -------------------------------------------------------

    async def task_status(task_id: str, request: Request) -> dict[str, Any]:
        if server._config.control_secret:
            body = await request.body()
            WebhookServer._verify_signature(  # pyright: ignore[reportPrivateUsage]
                body,
                server._config.control_secret,
                "X-Hermit-Signature-256",
                request.headers,
            )

        try:
            store = server._kernel_store()
        except HTTPException:
            return {"task_id": task_id, "status": "unknown", "proof_summary": {}}

        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        from hermit.kernel.verification.proofs.proofs import ProofService

        proof_summary = ProofService(store).build_proof_summary(task_id)
        return {
            "task_id": task_id,
            "status": task.status,
            "proof_summary": proof_summary,
        }

    app.add_api_route("/a2a/tasks/{task_id}/status", task_status, methods=["GET"])


def _on_dispatch_result(
    *,
    source: str = "",
    result_text: str = "",
    success: bool = True,
    metadata: dict[str, Any] | None = None,
    **kw: Any,
) -> None:
    """Auto-send results back to reply_to_url when an A2A task completes."""
    if _handler is None:
        return

    meta = metadata or {}
    reply_to = str(meta.get("a2a_reply_to", ""))
    if not reply_to:
        return

    from hermit.plugins.builtin.hooks.webhook.a2a import A2ATaskResponse

    task_id = str(meta.get("task_id", meta.get("session_id", "")))
    response = A2ATaskResponse(
        task_id=task_id,
        status="completed" if success else "failed",
        result_summary=result_text[:500] if result_text else "",
    )
    _handler.send_result(reply_to, response)


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=25)
    ctx.add_hook(HookEvent.DISPATCH_RESULT, _on_dispatch_result, priority=10)
