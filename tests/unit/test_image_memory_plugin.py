from __future__ import annotations

import json
from pathlib import Path

from hermit.plugins.builtin.hooks.image_memory.hooks import _parse_json, register
from hermit.runtime.assembly.config import Settings
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.registry.tools import ToolRegistry


def _build_registry(tmp_path: Path, monkeypatch):
    settings = Settings(base_dir=tmp_path, auth_token="token", model="fake-model")
    ctx = PluginContext(HooksEngine(), settings=settings)
    register(ctx)

    registry = ToolRegistry()
    for tool in ctx.tools:
        registry.register(tool)
    return settings, ctx, registry


def test_image_store_from_path_persists_asset_and_indexes(tmp_path: Path, monkeypatch) -> None:
    settings, _ctx, registry = _build_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image",
        lambda *_args, **_kwargs: {
            "summary": "一张包含架构图的截图",
            "tags": ["架构图", "系统设计"],
            "ocr_text": "API Gateway",
        },
    )

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png-bytes")

    result = registry.call(
        "image_store_from_path",
        {"path": str(image_path), "session_id": "s1"},
    )

    assert result["summary"] == "一张包含架构图的截图"
    assert result["analysis_status"] == "ready"
    record_path = settings.image_memory_dir / "records" / f"{result['image_id']}.json"
    session_index_path = settings.image_memory_dir / "indexes" / "session" / "s1.json"
    global_index_path = settings.image_memory_dir / "indexes" / "global.json"
    assert record_path.exists()
    assert session_index_path.exists()
    assert global_index_path.exists()

    session_index = json.loads(session_index_path.read_text(encoding="utf-8"))
    assert result["image_id"] in session_index["image_ids"]


def test_image_search_and_pre_run_context(tmp_path: Path, monkeypatch) -> None:
    _settings, ctx, registry = _build_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image",
        lambda *_args, **_kwargs: {
            "summary": "二维码截图，用于登录企业后台",
            "tags": ["二维码", "登录"],
            "ocr_text": "Scan to login",
        },
    )

    image_path = tmp_path / "qr.png"
    image_path.write_bytes(b"fake-qr-bytes")
    stored = registry.call(
        "image_store_from_path",
        {"path": str(image_path), "session_id": "session-1"},
    )

    matches = registry.call("image_search", {"query": "二维码", "limit": 3})
    assert matches[0]["image_id"] == stored["image_id"]

    prompt = ctx._hooks.fire(  # type: ignore[attr-defined]
        HookEvent.PRE_RUN,
        prompt="把刚才那张图再解释一下",
        session_id="session-1",
    )[0]
    assert "<image_context>" in prompt
    assert stored["image_id"] in prompt


def test_image_attach_to_feishu_returns_custom_tag(tmp_path: Path, monkeypatch) -> None:
    _settings, _ctx, registry = _build_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image",
        lambda *_args, **_kwargs: {
            "summary": "产品界面截图",
            "tags": ["界面", "产品"],
            "ocr_text": "",
        },
    )
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._build_lark_client",
        lambda: object(),
    )
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._upload_image_to_feishu",
        lambda _client, _path: "img_v2_attached",
    )

    image_path = tmp_path / "ui.png"
    image_path.write_bytes(b"fake-ui-bytes")
    stored = registry.call(
        "image_store_from_path",
        {"path": str(image_path), "session_id": "session-attach"},
    )

    attached = registry.call("image_attach_to_feishu", {"image_id": stored["image_id"]})
    assert attached["feishu_image_key"] == "img_v2_attached"
    assert attached["tag"] == "<feishu_image key='img_v2_attached'/>"


def test_empty_analysis_result_marks_empty_response(tmp_path: Path, monkeypatch) -> None:
    settings, _ctx, registry = _build_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image",
        lambda *_args, **_kwargs: {"summary": "", "tags": [], "ocr_text": ""},
    )

    image_path = tmp_path / "blank.png"
    image_path.write_bytes(b"fake-blank-bytes")

    result = registry.call(
        "image_store_from_path",
        {"path": str(image_path), "session_id": "s-empty"},
    )

    assert result["analysis_status"] == "empty_response"
    assert result["summary"] == ""


def test_parse_json_raw_object() -> None:
    assert _parse_json('{"summary": "test", "tags": []}') == {"summary": "test", "tags": []}


def test_parse_json_code_fenced() -> None:
    text = '```json\n{"summary": "fenced", "tags": ["a"]}\n```'
    assert _parse_json(text) == {"summary": "fenced", "tags": ["a"]}


def test_parse_json_embedded_in_prose() -> None:
    text = 'Here is the result:\n{"summary": "embedded", "tags": []}\nDone.'
    result = _parse_json(text)
    assert result is not None
    assert result["summary"] == "embedded"


def test_parse_json_truncated() -> None:
    text = '{"summary": "truncated", "tags": ["x"], "ocr_text": ""'
    result = _parse_json(text)
    assert result is not None
    assert result["summary"] == "truncated"


def test_parse_json_empty_returns_none() -> None:
    assert _parse_json("") is None
    assert _parse_json("   ") is None


def test_parse_json_complete_json_not_double_braced() -> None:
    """Regression: old code prepended '{' causing '{{...' parse failure."""
    raw = '{"summary": "icon", "tags": ["ui"], "ocr_text": ""}'
    result = _parse_json(raw)
    assert result is not None
    assert result["summary"] == "icon"
