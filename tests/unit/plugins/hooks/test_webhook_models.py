"""Tests for webhook models and config loading."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hermit.plugins.builtin.hooks.webhook.models import (
    WebhookConfig,
    WebhookRoute,
    _resolve_config_path,
    load_config,
)

# ── WebhookRoute ──


def test_webhook_route_defaults() -> None:
    route = WebhookRoute(name="test", path="/webhook/test", prompt_template="Handle: {msg}")
    assert route.secret is None
    assert route.signature_header == "X-Hub-Signature-256"
    assert route.notify == {}


def test_webhook_route_with_all_fields() -> None:
    route = WebhookRoute(
        name="gh",
        path="/webhook/gh",
        prompt_template="Process: {data}",
        secret="s3cret",
        signature_header="X-Custom-Sig",
        notify={"feishu_chat_id": "chat123"},
    )
    assert route.secret == "s3cret"
    assert route.signature_header == "X-Custom-Sig"
    assert route.notify == {"feishu_chat_id": "chat123"}


# ── WebhookConfig ──


def test_webhook_config_defaults() -> None:
    config = WebhookConfig()
    assert config.host == "0.0.0.0"
    assert config.port == 8321
    assert config.routes == []
    assert config.control_secret is None


# ── _resolve_config_path ──


def test_resolve_config_path_with_base_dir() -> None:
    settings = SimpleNamespace(base_dir="/tmp/test")
    path = _resolve_config_path(settings)
    assert path == Path("/tmp/test/webhooks.json")


def test_resolve_config_path_without_base_dir() -> None:
    settings = SimpleNamespace(base_dir=None)
    path = _resolve_config_path(settings)
    assert path == Path.home() / ".hermit" / "webhooks.json"


def test_resolve_config_path_no_settings() -> None:
    path = _resolve_config_path(None)
    assert path == Path.home() / ".hermit" / "webhooks.json"


# ── load_config ──


def test_load_config_no_file(tmp_path: Path) -> None:
    settings = SimpleNamespace(base_dir=str(tmp_path))
    config = load_config(settings)
    assert isinstance(config, WebhookConfig)
    assert config.routes == []


def test_load_config_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / "webhooks.json").write_text("not json", encoding="utf-8")
    settings = SimpleNamespace(base_dir=str(tmp_path))
    config = load_config(settings)
    assert isinstance(config, WebhookConfig)
    assert config.routes == []


def test_load_config_valid_file(tmp_path: Path) -> None:
    data = {
        "host": "127.0.0.1",
        "port": 9000,
        "control_secret": "ctrl-secret",
        "routes": {
            "github": {
                "path": "/webhook/github",
                "prompt_template": "Handle: {msg}",
                "secret": "gh-secret",
                "signature_header": "X-Hub-Signature-256",
                "notify": {"feishu_chat_id": "chat123"},
            },
            "simple": {
                "prompt_template": "Process: {data}",
            },
        },
    }
    (tmp_path / "webhooks.json").write_text(json.dumps(data), encoding="utf-8")
    settings = SimpleNamespace(base_dir=str(tmp_path))
    config = load_config(settings)

    assert config.host == "127.0.0.1"
    assert config.port == 9000
    assert config.control_secret == "ctrl-secret"
    assert len(config.routes) == 2

    gh = next(r for r in config.routes if r.name == "github")
    assert gh.path == "/webhook/github"
    assert gh.secret == "gh-secret"
    assert gh.notify == {"feishu_chat_id": "chat123"}

    simple = next(r for r in config.routes if r.name == "simple")
    assert simple.path == "/webhook/simple"
    assert simple.secret is None


def test_load_config_with_settings_overrides(tmp_path: Path) -> None:
    data = {
        "host": "127.0.0.1",
        "port": 9000,
        "control_secret": "file-secret",
        "routes": {},
    }
    (tmp_path / "webhooks.json").write_text(json.dumps(data), encoding="utf-8")
    settings = SimpleNamespace(
        base_dir=str(tmp_path),
        webhook_host="0.0.0.0",
        webhook_port=7777,
        webhook_control_secret="override-secret",
    )
    config = load_config(settings)

    assert config.host == "0.0.0.0"
    assert config.port == 7777
    assert config.control_secret == "override-secret"


def test_load_config_control_secret_empty_string(tmp_path: Path) -> None:
    data = {"host": "0.0.0.0", "port": 8321, "control_secret": "", "routes": {}}
    (tmp_path / "webhooks.json").write_text(json.dumps(data), encoding="utf-8")
    settings = SimpleNamespace(base_dir=str(tmp_path))
    config = load_config(settings)
    assert config.control_secret is None


def test_load_config_route_defaults(tmp_path: Path) -> None:
    data = {
        "routes": {
            "minimal": {},
        },
    }
    (tmp_path / "webhooks.json").write_text(json.dumps(data), encoding="utf-8")
    settings = SimpleNamespace(base_dir=str(tmp_path))
    config = load_config(settings)
    assert len(config.routes) == 1
    route = config.routes[0]
    assert route.name == "minimal"
    assert route.path == "/webhook/minimal"
    assert route.prompt_template == "{message}"
    assert route.secret is None
