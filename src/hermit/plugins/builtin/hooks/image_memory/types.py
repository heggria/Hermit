from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImageRecord:
    image_id: str
    primary_session_id: str
    session_ids: list[str]
    source_adapter: str
    original_message_id: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    local_path: str = ""
    original_file_name: str = ""
    mime_type: str = ""
    sha256: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list[str])
    ocr_text: str = ""
    analysis_status: str = "pending"
    feishu_image_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImageRecord":
        return cls(
            image_id=str(data["image_id"]),
            primary_session_id=str(data.get("primary_session_id", "")),
            session_ids=list(data.get("session_ids", [])),
            source_adapter=str(data.get("source_adapter", "")),
            original_message_id=str(data.get("original_message_id", "")),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            local_path=str(data.get("local_path", "")),
            original_file_name=str(data.get("original_file_name", "")),
            mime_type=str(data.get("mime_type", "")),
            sha256=str(data.get("sha256", "")),
            summary=str(data.get("summary", "")),
            tags=[str(tag) for tag in data.get("tags", [])],
            ocr_text=str(data.get("ocr_text", "")),
            analysis_status=str(data.get("analysis_status", "pending")),
            feishu_image_key=str(data.get("feishu_image_key", "")),
        )
