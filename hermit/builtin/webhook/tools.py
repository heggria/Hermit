"""Agent-facing tools for managing webhook routes (webhooks.json CRUD)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext

_settings: Any = None


def set_settings(settings: Any) -> None:
    global _settings
    _settings = settings


def _config_path() -> Path:
    base = Path(getattr(_settings, "base_dir", None) or Path.home() / ".hermit")
    return base / "webhooks.json"


def _load_raw() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {"host": "0.0.0.0", "port": 8321, "routes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"host": "0.0.0.0", "port": 8321, "routes": {}}


def _save_raw(data: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _handle_list(payload: dict[str, Any]) -> str:
    raw = _load_raw()
    routes = raw.get("routes", {})
    if not routes:
        return (
            f"No webhook routes configured.\n"
            f"Config file: {_config_path()}\n"
            "Use `webhook_add` to create a route."
        )

    port = raw.get("port", 8321)
    lines = [f"Webhook server port: {port}", f"Routes ({len(routes)}):"]
    for name, r in routes.items():
        secret_info = f"  (signed: {r.get('signature_header', 'X-Hub-Signature-256')})" if r.get("secret") else ""
        notify_info = r.get("notify", {})
        feishu = notify_info.get("feishu_chat_id", "")
        lines.append(
            f"  [{name}]\n"
            f"    Path:     {r.get('path', f'/webhook/{name}')}{secret_info}\n"
            f"    Template: {str(r.get('prompt_template', ''))[:80]}\n"
            f"    Feishu:   {feishu or '(none)'}"
        )
    lines.append("\nNote: Restart `hermit serve` to pick up config changes.")
    return "\n".join(lines)


def _handle_add(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    prompt_template = str(payload.get("prompt_template", "")).strip()

    if not name:
        return "Error: name is required."
    if not prompt_template:
        return "Error: prompt_template is required."

    raw = _load_raw()
    routes = raw.setdefault("routes", {})

    if name in routes and not payload.get("overwrite"):
        return (
            f"Error: route '{name}' already exists. "
            "Pass overwrite=true to replace it."
        )

    path = str(payload.get("path", f"/webhook/{name}")).strip()
    secret = str(payload.get("secret", "")).strip() or None
    signature_header = str(payload.get("signature_header", "X-Hub-Signature-256")).strip()
    feishu_chat_id = str(payload.get("feishu_chat_id", "")).strip() or None

    route: dict[str, Any] = {
        "path": path,
        "prompt_template": prompt_template,
    }
    if secret:
        route["secret"] = secret
        route["signature_header"] = signature_header
    if feishu_chat_id:
        route["notify"] = {"feishu_chat_id": feishu_chat_id}

    routes[name] = route
    _save_raw(raw)

    return (
        f"Webhook route '{name}' added:\n"
        f"  Path:     {path}\n"
        f"  Template: {prompt_template[:80]}\n"
        f"  Signed:   {'yes' if secret else 'no'}\n"
        f"  Feishu:   {feishu_chat_id or '(none)'}\n\n"
        "Restart `hermit serve` for the change to take effect."
    )


def _handle_delete(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    if not name:
        return "Error: name is required."

    raw = _load_raw()
    routes = raw.get("routes", {})
    if name not in routes:
        existing = ", ".join(routes.keys()) or "(none)"
        return f"Error: route '{name}' not found. Existing routes: {existing}"

    del routes[name]
    _save_raw(raw)
    return (
        f"Webhook route '{name}' deleted.\n"
        "Restart `hermit serve` for the change to take effect."
    )


def _handle_update(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    if not name:
        return "Error: name is required."

    raw = _load_raw()
    routes = raw.get("routes", {})
    if name not in routes:
        existing = ", ".join(routes.keys()) or "(none)"
        return f"Error: route '{name}' not found. Existing routes: {existing}"

    route = routes[name]
    changed: list[str] = []

    if "prompt_template" in payload and payload["prompt_template"]:
        route["prompt_template"] = str(payload["prompt_template"])
        changed.append("prompt_template")
    if "path" in payload and payload["path"]:
        route["path"] = str(payload["path"])
        changed.append("path")
    if "secret" in payload:
        secret = str(payload["secret"]).strip() or None
        if secret:
            route["secret"] = secret
            route.setdefault("signature_header", "X-Hub-Signature-256")
        else:
            route.pop("secret", None)
            route.pop("signature_header", None)
        changed.append("secret")
    if "feishu_chat_id" in payload:
        fid = str(payload["feishu_chat_id"]).strip() or None
        if fid:
            route.setdefault("notify", {})["feishu_chat_id"] = fid
        else:
            route.get("notify", {}).pop("feishu_chat_id", None)
        changed.append("feishu_chat_id")

    if not changed:
        return "Error: no fields to update. Provide prompt_template, path, secret, or feishu_chat_id."

    routes[name] = route
    _save_raw(raw)
    return (
        f"Webhook route '{name}' updated: {', '.join(changed)}.\n"
        "Restart `hermit serve` for the change to take effect."
    )


def register(ctx: PluginContext) -> None:
    global _settings
    _settings = ctx.settings

    ctx.add_tool(ToolSpec(
        name="webhook_list",
        description=(
            "List all configured webhook routes from ~/.hermit/webhooks.json. "
            "Use to show the user what webhooks are set up, their paths, templates, and notification targets."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_handle_list,
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    ))

    ctx.add_tool(ToolSpec(
        name="webhook_add",
        description=(
            "Add a new webhook route to ~/.hermit/webhooks.json. "
            "The route receives HTTP POST events and triggers an agent task using the prompt_template. "
            "Requires restarting `hermit serve` to take effect."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short identifier for the route (e.g. 'github', 'zendesk'). Used in the URL path.",
                },
                "prompt_template": {
                    "type": "string",
                    "description": (
                        "Template for the agent prompt. Use {field} to inject payload values, "
                        "{nested.field} for nested JSON. E.g. 'PR: {pull_request.title} on {repository.full_name}'"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "HTTP path override. Defaults to /webhook/{name}.",
                },
                "secret": {
                    "type": "string",
                    "description": "HMAC-SHA256 secret for signature verification. Leave empty to skip verification.",
                },
                "signature_header": {
                    "type": "string",
                    "description": "Header name containing the signature. Defaults to X-Hub-Signature-256.",
                },
                "feishu_chat_id": {
                    "type": "string",
                    "description": (
                        "Feishu chat_id to push results to (oc_xxx for groups, ou_xxx for DMs). "
                        "Read from <feishu_chat_id>...</feishu_chat_id> in the current message context when in Feishu."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace existing route with the same name. Default false.",
                },
            },
            "required": ["name", "prompt_template"],
        },
        handler=_handle_add,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))

    ctx.add_tool(ToolSpec(
        name="webhook_delete",
        description="Delete a webhook route from ~/.hermit/webhooks.json by name. Requires restarting serve.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the webhook route to delete.",
                },
            },
            "required": ["name"],
        },
        handler=_handle_delete,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))

    ctx.add_tool(ToolSpec(
        name="webhook_update",
        description=(
            "Update an existing webhook route's prompt_template, path, secret, or feishu_chat_id. "
            "Requires restarting serve."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the webhook route to update.",
                },
                "prompt_template": {
                    "type": "string",
                    "description": "New prompt template.",
                },
                "path": {
                    "type": "string",
                    "description": "New HTTP path.",
                },
                "secret": {
                    "type": "string",
                    "description": "New HMAC secret. Pass empty string to remove signature verification.",
                },
                "feishu_chat_id": {
                    "type": "string",
                    "description": "New Feishu chat_id. Pass empty string to disable push.",
                },
            },
            "required": ["name"],
        },
        handler=_handle_update,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))
