"""Webhook plugin data models and configuration loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WebhookRoute:
    name: str
    path: str
    prompt_template: str
    secret: str | None = None
    signature_header: str = "X-Hub-Signature-256"
    notify: dict[str, str] = field(default_factory=dict[str, str])


@dataclass
class WebhookConfig:
    host: str = "0.0.0.0"
    port: int = 8321
    routes: list[WebhookRoute] = field(default_factory=list[WebhookRoute])
    control_secret: str | None = None


def load_config(settings: Any = None) -> WebhookConfig:
    """Load webhook configuration from ~/.hermit/webhooks.json."""
    config_path = _resolve_config_path(settings)
    if not config_path.exists():
        return WebhookConfig()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return WebhookConfig()

    host_override = getattr(settings, "webhook_host", None)
    port_override = getattr(settings, "webhook_port", None)
    control_secret_override = getattr(settings, "webhook_control_secret", None)
    host = str(host_override or raw.get("host", "0.0.0.0"))
    port = int(port_override or raw.get("port", 8321))
    control_secret = str(control_secret_override or raw.get("control_secret") or "").strip() or None

    routes: list[WebhookRoute] = []
    for name, route_raw in raw.get("routes", {}).items():
        routes.append(
            WebhookRoute(
                name=name,
                path=route_raw.get("path", f"/webhook/{name}"),
                prompt_template=route_raw.get("prompt_template", "{message}"),
                secret=route_raw.get("secret") or None,
                signature_header=route_raw.get("signature_header", "X-Hub-Signature-256"),
                notify=route_raw.get("notify", {}),
            )
        )

    return WebhookConfig(host=host, port=port, routes=routes, control_secret=control_secret)


def _resolve_config_path(settings: Any) -> Path:
    base = Path(getattr(settings, "base_dir", None) or Path.home() / ".hermit")
    return base / "webhooks.json"
