"""Agent-facing tools for managing webhook routes (webhooks.json CRUD)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermit.core.tools import ToolSpec
from hermit.i18n import resolve_locale, tr
from hermit.plugin.base import PluginContext

_settings: Any = None


def set_settings(settings: Any) -> None:
    global _settings
    _settings = settings


def _config_path() -> Path:
    base = Path(getattr(_settings, "base_dir", None) or Path.home() / ".hermit")
    return base / "webhooks.json"


def _locale() -> str:
    return resolve_locale(getattr(_settings, "locale", None))


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=_locale(), default=default, **kwargs)


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
        return _t(
            "tools.webhook.list.empty",
            config_path=_config_path(),
        )

    port = raw.get("port", 8321)
    lines = [
        _t("tools.webhook.list.port", port=port),
        _t("tools.webhook.list.title", count=len(routes)),
    ]
    for name, r in routes.items():
        secret_info = (
            _t("tools.webhook.list.signed", signature_header=r.get("signature_header", "X-Hub-Signature-256"))
            if r.get("secret")
            else ""
        )
        notify_info = r.get("notify", {})
        feishu = notify_info.get("feishu_chat_id", "")
        lines.append(
            f"  [{name}]\n"
            f"    {_t('tools.webhook.list.path_label')}:     {r.get('path', f'/webhook/{name}')}{secret_info}\n"
            f"    {_t('tools.webhook.list.template_label')}: {str(r.get('prompt_template', ''))[:80]}\n"
            f"    {_t('tools.webhook.list.feishu_label')}:   {feishu or _t('tools.webhook.common.none')}"
        )
    lines.append("\n" + _t("tools.webhook.common.restart_note"))
    return "\n".join(lines)


def _handle_add(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    prompt_template = str(payload.get("prompt_template", "")).strip()

    if not name:
        return _t("tools.webhook.add.error.name_required")
    if not prompt_template:
        return _t("tools.webhook.add.error.prompt_required")

    raw = _load_raw()
    routes = raw.setdefault("routes", {})

    if name in routes and not payload.get("overwrite"):
        return _t("tools.webhook.add.error.exists", name=name)

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

    return _t(
        "tools.webhook.add.success",
        name=name,
        path=path,
        prompt_template=prompt_template[:80],
        signed=_t("tools.webhook.common.yes") if secret else _t("tools.webhook.common.no"),
        feishu_chat_id=feishu_chat_id or _t("tools.webhook.common.none"),
    )


def _handle_delete(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    if not name:
        return _t("tools.webhook.delete.error.name_required")

    raw = _load_raw()
    routes = raw.get("routes", {})
    if name not in routes:
        existing = ", ".join(routes.keys()) or _t("tools.webhook.common.none")
        return _t("tools.webhook.common.not_found", name=name, existing=existing)

    del routes[name]
    _save_raw(raw)
    return _t("tools.webhook.delete.success", name=name)


def _handle_update(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", "")).strip()
    if not name:
        return _t("tools.webhook.update.error.name_required")

    raw = _load_raw()
    routes = raw.get("routes", {})
    if name not in routes:
        existing = ", ".join(routes.keys()) or _t("tools.webhook.common.none")
        return _t("tools.webhook.common.not_found", name=name, existing=existing)

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
        return _t("tools.webhook.update.error.no_fields")

    routes[name] = route
    _save_raw(raw)
    return _t("tools.webhook.update.success", name=name, changed=", ".join(changed))


def register(ctx: PluginContext) -> None:
    global _settings
    _settings = ctx.settings

    ctx.add_tool(ToolSpec(
        name="webhook_list",
        description=(
            "List all configured webhook routes from ~/.hermit/webhooks.json. "
            "Use to show the user what webhooks are set up, their paths, templates, and notification targets."
        ),
        description_key="tools.webhook.list.description",
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
        description_key="tools.webhook.add.description",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description_key": "tools.webhook.add.name",
                },
                "prompt_template": {
                    "type": "string",
                    "description_key": "tools.webhook.add.prompt_template",
                },
                "path": {
                    "type": "string",
                    "description_key": "tools.webhook.add.path",
                },
                "secret": {
                    "type": "string",
                    "description_key": "tools.webhook.add.secret",
                },
                "signature_header": {
                    "type": "string",
                    "description_key": "tools.webhook.add.signature_header",
                },
                "feishu_chat_id": {
                    "type": "string",
                    "description_key": "tools.webhook.add.feishu_chat_id",
                },
                "overwrite": {
                    "type": "boolean",
                    "description_key": "tools.webhook.add.overwrite",
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
        description_key="tools.webhook.delete.description",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description_key": "tools.webhook.delete.name",
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
        description_key="tools.webhook.update.description",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description_key": "tools.webhook.update.name",
                },
                "prompt_template": {
                    "type": "string",
                    "description_key": "tools.webhook.update.prompt_template",
                },
                "path": {
                    "type": "string",
                    "description_key": "tools.webhook.update.path",
                },
                "secret": {
                    "type": "string",
                    "description_key": "tools.webhook.update.secret",
                },
                "feishu_chat_id": {
                    "type": "string",
                    "description_key": "tools.webhook.update.feishu_chat_id",
                },
            },
            "required": ["name"],
        },
        handler=_handle_update,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    ))
