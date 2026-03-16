from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from hermit.builtin.image_memory.types import ImageRecord, utc_now_iso


class ImageMemoryEngine:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.assets_dir = root_dir / "assets"
        self.records_dir = root_dir / "records"
        self.indexes_dir = root_dir / "indexes"
        self.session_indexes_dir = self.indexes_dir / "session"
        self.global_index_file = self.indexes_dir / "global.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for directory in (
            self.root_dir,
            self.assets_dir,
            self.records_dir,
            self.indexes_dir,
            self.session_indexes_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def sha256(self, image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    def image_id_for_bytes(self, image_bytes: bytes) -> str:
        return self.sha256(image_bytes)[:16]

    def detect_mime_type(
        self,
        file_name: str,
        fallback: str = "image/png",
        image_bytes: bytes | None = None,
    ) -> str:
        if image_bytes and len(image_bytes) >= 12:
            if image_bytes[:3] == b"\xff\xd8\xff":
                return "image/jpeg"
            if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                return "image/png"
            if image_bytes[:4] == b"GIF8":
                return "image/gif"
            if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
                return "image/webp"
        guessed, _ = mimetypes.guess_type(file_name)
        return guessed or fallback

    def pick_extension(self, file_name: str, mime_type: str) -> str:
        ext = Path(file_name).suffix.lower()
        if ext:
            return ext
        guessed = mimetypes.guess_extension(mime_type) or ".img"
        return guessed

    def record_path(self, image_id: str) -> Path:
        return self.records_dir / f"{image_id}.json"

    def session_index_path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("..", "_")
        return self.session_indexes_dir / f"{safe}.json"

    def asset_path_for(self, image_id: str, file_name: str, mime_type: str) -> Path:
        ext = self.pick_extension(file_name, mime_type)
        return self.assets_dir / f"{image_id}{ext}"

    def load_record(self, image_id: str) -> ImageRecord | None:
        path = self.record_path(image_id)
        if not path.exists():
            return None
        return ImageRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_record(self, record: ImageRecord) -> None:
        record.updated_at = utc_now_iso()
        self.record_path(record.image_id).write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._update_session_indexes(record)
        self._update_global_index(record)

    def load_all_records(self) -> list[ImageRecord]:
        records: list[ImageRecord] = []
        for path in sorted(self.records_dir.glob("*.json")):
            try:
                records.append(ImageRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return records

    def upsert_asset(
        self,
        *,
        session_id: str,
        source_adapter: str,
        message_id: str,
        file_name: str,
        mime_type: str,
        image_bytes: bytes,
    ) -> tuple[ImageRecord, bool]:
        digest = self.sha256(image_bytes)
        image_id = digest[:16]
        existing = self.load_record(image_id)
        if existing is not None:
            if session_id and session_id not in existing.session_ids:
                existing.session_ids.append(session_id)
            if not existing.primary_session_id:
                existing.primary_session_id = session_id
            if not existing.original_message_id:
                existing.original_message_id = message_id
            self.save_record(existing)
            return existing, False

        asset_path = self.asset_path_for(image_id, file_name, mime_type)
        asset_path.write_bytes(image_bytes)
        record = ImageRecord(
            image_id=image_id,
            primary_session_id=session_id,
            session_ids=[session_id] if session_id else [],
            source_adapter=source_adapter,
            original_message_id=message_id,
            local_path=str(asset_path),
            original_file_name=file_name,
            mime_type=mime_type,
            sha256=digest,
        )
        self.save_record(record)
        return record, True

    def mark_analysis(
        self,
        image_id: str,
        *,
        summary: str,
        tags: list[str],
        ocr_text: str,
        status: str,
    ) -> ImageRecord:
        record = self.load_record(image_id)
        if record is None:
            raise KeyError(f"Unknown image_id: {image_id}")
        record.summary = summary
        record.tags = tags
        record.ocr_text = ocr_text
        record.analysis_status = status
        self.save_record(record)
        return record

    def set_feishu_image_key(self, image_id: str, image_key: str) -> ImageRecord:
        record = self.load_record(image_id)
        if record is None:
            raise KeyError(f"Unknown image_id: {image_id}")
        record.feishu_image_key = image_key
        self.save_record(record)
        return record

    def search(
        self,
        *,
        query: str = "",
        session_id: str | None = None,
        limit: int = 5,
    ) -> list[ImageRecord]:
        records = self.load_all_records()
        if session_id:
            records = [record for record in records if session_id in record.session_ids]
        if not query.strip():
            return sorted(records, key=lambda item: item.updated_at, reverse=True)[:limit]

        lowered_terms = [term for term in query.lower().split() if term]
        ranked: list[tuple[int, ImageRecord]] = []
        for record in records:
            haystack = " ".join(
                [
                    record.summary.lower(),
                    " ".join(tag.lower() for tag in record.tags),
                    record.ocr_text.lower(),
                    record.original_file_name.lower(),
                ]
            )
            score = 0
            for term in lowered_terms:
                if term in haystack:
                    score += 2
                if term in record.summary.lower():
                    score += 1
                if term in (tag.lower() for tag in record.tags):
                    score += 2
            if score > 0:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [record for _score, record in ranked[:limit]]

    def build_session_context(self, session_id: str, limit: int = 3) -> str:
        records = self.search(session_id=session_id, limit=limit)
        lines: list[str] = []
        for index, record in enumerate(records, start=1):
            tags = ", ".join(record.tags[:5]) or "无标签"
            summary = record.summary or "暂无摘要"
            lines.append(
                f"- 图片{index}（image_id={record.image_id}，文件={record.original_file_name}）："
                f"{summary}；标签：{tags}"
            )
        return "\n".join(lines)

    def _load_session_index(self, session_id: str) -> dict[str, Any]:
        path = self.session_index_path(session_id)
        if not path.exists():
            return {"session_id": session_id, "image_ids": [], "updated_at": utc_now_iso()}
        return json.loads(path.read_text(encoding="utf-8"))

    def _update_session_indexes(self, record: ImageRecord) -> None:
        for session_id in record.session_ids:
            index = self._load_session_index(session_id)
            image_ids = list(index.get("image_ids", []))
            if record.image_id not in image_ids:
                image_ids.append(record.image_id)
            index["image_ids"] = image_ids
            index["updated_at"] = utc_now_iso()
            self.session_index_path(session_id).write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _load_global_index(self) -> dict[str, Any]:
        if not self.global_index_file.exists():
            return {"images": []}
        return json.loads(self.global_index_file.read_text(encoding="utf-8"))

    def _update_global_index(self, record: ImageRecord) -> None:
        data = self._load_global_index()
        images = [
            item for item in data.get("images", []) if item.get("image_id") != record.image_id
        ]
        images.append(
            {
                "image_id": record.image_id,
                "session_ids": record.session_ids,
                "summary": record.summary,
                "tags": record.tags,
                "updated_at": record.updated_at,
                "analysis_status": record.analysis_status,
            }
        )
        images.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        data["images"] = images
        self.global_index_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
