from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import structlog

from hermit.infra.system.i18n import tr, tr_list_all_locales
from hermit.plugins.builtin.hooks.image_memory.engine import ImageMemoryEngine
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec
from hermit.runtime.provider_host.execution.services import VisionAnalysisService, build_provider

log = structlog.get_logger()

_SUPPORTED_VISION_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
_image_reference_re_cache: re.Pattern[str] | None = None


def _get_image_reference_re() -> re.Pattern[str]:
    global _image_reference_re_cache
    if _image_reference_re_cache is None:
        keywords = tr_list_all_locales("tools.image_memory.reference_keywords")
        if not keywords:
            keywords = ["image", "photo", "picture", "screenshot"]
        _image_reference_re_cache = re.compile(
            "|".join(re.escape(k) for k in keywords), re.IGNORECASE
        )
    return _image_reference_re_cache


def register(ctx: PluginContext) -> None:
    settings = ctx.settings
    if settings is None:
        return

    engine = ImageMemoryEngine(settings.image_memory_dir)

    ctx.add_tool(_build_image_store_from_path_tool(engine, settings))
    ctx.add_tool(_build_image_store_from_feishu_tool(engine, settings))
    ctx.add_tool(_build_image_search_tool(engine))
    ctx.add_tool(_build_image_get_tool(engine))
    ctx.add_tool(_build_image_attach_to_feishu_tool(engine))

    ctx.add_hook(HookEvent.SYSTEM_PROMPT, _system_prompt_fragment, priority=20)

    def _pre_run_hook(prompt: str, session_id: str | None = None) -> str:
        return _inject_image_context(engine, settings, prompt, session_id=session_id)

    ctx.add_hook(
        HookEvent.PRE_RUN,
        _pre_run_hook,
        priority=20,
    )


def _system_prompt_fragment() -> str:
    return (
        "<image_memory_guidance>\n"
        "When users reference a previously shared image, screenshot, QR code, or photo, "
        "use image_search/image_get before guessing. "
        "In Feishu replies, image_attach_to_feishu returns a <feishu_image key='...'/>-style "
        "tag that should be preserved verbatim on its own line.\n"
        "</image_memory_guidance>"
    )


def _inject_image_context(
    engine: ImageMemoryEngine,
    settings: Any,
    prompt: str,
    *,
    session_id: str | None = None,
) -> str:
    if not session_id or not _get_image_reference_re().search(prompt):
        return prompt

    records = engine.search(session_id=session_id, limit=settings.image_context_limit)
    if not records:
        records = engine.search(query=prompt, limit=settings.image_context_limit)
    if not records:
        return prompt

    lines: list[str] = []
    for index, record in enumerate(records, start=1):
        tags = ", ".join(record.tags[:5]) or tr("tools.image_memory.no_tags")
        summary = str(record.summary or tr("tools.image_memory.no_summary"))
        lines.append(
            tr(
                "tools.image_memory.context_line",
                index=index,
                image_id=record.image_id,
                session_id=record.primary_session_id,
                summary=summary,
                tags=tags,
            )
        )

    return (
        "<image_context>\n"
        f"{tr('tools.image_memory.retrieval_intro')}"
        f"{chr(10).join(lines)}\n"
        "</image_context>\n\n"
        f"{prompt}"
    )


def _build_image_store_from_path_tool(engine: ImageMemoryEngine, settings: Any) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id", "")).strip() or "manual"
        path = Path(str(payload["path"])).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Image file not found: {path}")
        image_bytes = path.read_bytes()
        mime_type = engine.detect_mime_type(path.name, image_bytes=image_bytes)
        record, created = engine.upsert_asset(
            session_id=session_id,
            source_adapter="local",
            message_id=str(payload.get("message_id", "")),
            file_name=path.name,
            mime_type=mime_type,
            image_bytes=image_bytes,
        )
        if created or not record.summary:
            record = _analyze_and_persist(engine, settings, record, image_bytes)
        return _record_public_dict(record)

    return ToolSpec(
        name="image_store_from_path",
        description="Store a local image into cross-session image memory and analyze it immediately.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative local image path"},
                "session_id": {"type": "string", "description": "Session that owns this image"},
                "message_id": {"type": "string", "description": "Optional source message id"},
            },
            "required": ["path", "session_id"],
        },
        handler=handler,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
    )


def _build_image_store_from_feishu_tool(engine: ImageMemoryEngine, settings: Any) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload["session_id"])
        message_id = str(payload["message_id"])
        image_key = str(payload["image_key"])
        client = _build_lark_client()

        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        response = client.im.v1.message_resource.get(
            GetMessageResourceRequest.builder()
            .type("image")
            .message_id(message_id)
            .file_key(image_key)
            .build()
        )
        if not response.success() or response.file is None:
            raise RuntimeError(f"Failed to download Feishu image {image_key}: {response.msg}")

        file_name = response.file_name or f"{image_key}.png"
        image_bytes = response.file.read()
        mime_type = engine.detect_mime_type(file_name, image_bytes=image_bytes)
        record, created = engine.upsert_asset(
            session_id=session_id,
            source_adapter="feishu",
            message_id=message_id,
            file_name=file_name,
            mime_type=mime_type,
            image_bytes=image_bytes,
        )
        if created or not record.summary:
            record = _analyze_and_persist(engine, settings, record, image_bytes)
        return _record_public_dict(record)

    return ToolSpec(
        name="image_store_from_feishu",
        description="Download a Feishu image message into cross-session image memory and analyze it.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "message_id": {"type": "string"},
                "image_key": {"type": "string"},
            },
            "required": ["session_id", "message_id", "image_key"],
        },
        handler=handler,
        action_class="attachment_ingest",
        risk_hint="high",
        requires_receipt=True,
    )


def _build_image_search_tool(engine: ImageMemoryEngine) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(payload.get("query", ""))
        session_id = str(payload.get("session_id", "")).strip() or None
        limit = max(1, min(10, int(payload.get("limit", 5))))
        return [
            _record_public_dict(record)
            for record in engine.search(query=query, session_id=session_id, limit=limit)
        ]

    return ToolSpec(
        name="image_search",
        description="Search stored images across sessions using summary, tags, OCR text, or session id.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        handler=handler,
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    )


def _build_image_get_tool(engine: ImageMemoryEngine) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        image_id = str(payload["image_id"])
        record = engine.load_record(image_id)
        if record is None:
            raise KeyError(f"Unknown image_id: {image_id}")
        return _record_public_dict(record, include_local_path=True)

    return ToolSpec(
        name="image_get",
        description="Get a stored image record by image_id.",
        input_schema={
            "type": "object",
            "properties": {"image_id": {"type": "string"}},
            "required": ["image_id"],
        },
        handler=handler,
        readonly=True,
        action_class="read_local",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    )


def _build_image_attach_to_feishu_tool(engine: ImageMemoryEngine) -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        image_id = str(payload["image_id"])
        record = engine.load_record(image_id)
        if record is None:
            raise KeyError(f"Unknown image_id: {image_id}")
        image_key = record.feishu_image_key
        if not image_key:
            client = _build_lark_client()
            image_key = _upload_image_to_feishu(client, Path(record.local_path))
            record = engine.set_feishu_image_key(image_id, image_key)
        return {
            "image_id": record.image_id,
            "feishu_image_key": image_key,
            "tag": f"<feishu_image key='{image_key}'/>",
        }

    return ToolSpec(
        name="image_attach_to_feishu",
        description="Upload a stored image to Feishu if needed and return a <feishu_image .../> tag for replies.",
        input_schema={
            "type": "object",
            "properties": {"image_id": {"type": "string"}},
            "required": ["image_id"],
        },
        handler=handler,
        action_class="credentialed_api_call",
        risk_hint="high",
        requires_receipt=True,
    )


def _analyze_and_persist(
    engine: ImageMemoryEngine,
    settings: Any,
    record: Any,
    image_bytes: bytes,
):
    try:
        analysis = _analyze_image(settings, record.mime_type, image_bytes)
        summary = analysis["summary"]
        tags = analysis["tags"]
        ocr_text = analysis["ocr_text"]
        has_content = bool(summary or tags or ocr_text)
        return engine.mark_analysis(
            record.image_id,
            summary=summary,
            tags=tags,
            ocr_text=ocr_text,
            status="ready" if has_content else "empty_response",
        )
    except Exception as exc:
        log.warning("image_analysis_failed", image_id=record.image_id, error=str(exc))
        return engine.mark_analysis(
            record.image_id,
            summary="",
            tags=[],
            ocr_text="",
            status=f"failed:{type(exc).__name__}",
        )


def _detect_mime_from_bytes(image_bytes: bytes) -> str | None:
    """Detect actual image format from magic bytes, ignoring file extension."""
    if len(image_bytes) < 12:
        return None
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"GIF8":
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _analyze_image(settings: Any, mime_type: str, image_bytes: bytes) -> dict[str, Any]:
    detected = _detect_mime_from_bytes(image_bytes)
    if detected and detected != mime_type:
        log.info(
            "image_mime_corrected",
            declared=mime_type,
            detected=detected,
        )
        mime_type = detected
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in _SUPPORTED_VISION_MIME_TYPES:
        raise ValueError(f"Unsupported image mime type for analysis: {mime_type}")

    model = settings.image_model or settings.model
    provider = build_provider(settings, model=model)
    service = VisionAnalysisService(provider, model=model)
    data = service.analyze_image(
        system_prompt=tr("prompt.image_memory.vision"),
        text=tr("prompt.image_memory.analyze"),
        image_block={
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            },
        },
        max_tokens=512,
    )
    if not data:
        log.warning("image_analysis_empty_response", model=model)
        return {"summary": "", "tags": [], "ocr_text": ""}
    summary = str(data.get("summary", "")).strip()
    tags = [str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()]
    ocr_text = str(data.get("ocr_text", "")).strip()
    return {
        "summary": summary,
        "tags": tags[:8],
        "ocr_text": ocr_text,
    }


def _build_lark_client() -> Any:
    from hermit.plugins.builtin.adapters.feishu._client import build_lark_client

    try:
        return build_lark_client()
    except RuntimeError as exc:
        raise RuntimeError("Feishu app credentials are required for image operations") from exc


def _upload_image_to_feishu(client: Any, path: Path) -> str:
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

    with path.open("rb") as fh:
        response = client.im.v1.image.create(
            CreateImageRequest.builder()
            .request_body(CreateImageRequestBody.builder().image_type("message").image(fh).build())
            .build()
        )
    if not response.success() or response.data is None or not response.data.image_key:
        raise RuntimeError(f"Failed to upload image to Feishu: {response.msg}")
    return str(response.data.image_key)


def _record_public_dict(record: Any, *, include_local_path: bool = False) -> dict[str, Any]:
    data = {
        "image_id": record.image_id,
        "primary_session_id": record.primary_session_id,
        "session_ids": record.session_ids,
        "source_adapter": record.source_adapter,
        "original_message_id": record.original_message_id,
        "original_file_name": record.original_file_name,
        "mime_type": record.mime_type,
        "summary": record.summary,
        "tags": record.tags,
        "ocr_text": record.ocr_text,
        "analysis_status": record.analysis_status,
        "feishu_image_key": record.feishu_image_key,
    }
    if include_local_path:
        data["local_path"] = record.local_path
    return data


def _parse_json(text: str) -> Any:  # pyright: ignore[reportUnusedFunction]
    """Extract a JSON object from model response text.

    Handles: raw JSON, code-fenced JSON, JSON embedded in prose,
    and truncated JSON missing trailing braces.
    """
    if not text or not text.strip():
        return None

    stripped = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", stripped)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    for candidate in (cleaned, stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    brace_start = cleaned.find("{")
    if brace_start >= 0:
        fragment = cleaned[brace_start:]
        for suffix in ("", "}", "]}", '"}', '"]}', '"]}'):
            try:
                return json.loads(fragment + suffix)
            except json.JSONDecodeError:
                continue

    return None
