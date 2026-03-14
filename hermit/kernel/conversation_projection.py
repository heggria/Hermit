from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.store import KernelStore
from hermit.kernel.store_support import _canonical_json, _sha256_hex

_CONVERSATION_PROJECTION_SCHEMA_VERSION = "conversation-v1"
_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_FEISHU_TAG_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)


class ConversationProjectionService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def rebuild(self, conversation_id: str) -> dict[str, Any]:
        payload = self._build_payload(conversation_id)
        payload["schema_version"] = _CONVERSATION_PROJECTION_SCHEMA_VERSION
        payload["event_head_hash"] = self._event_head_hash(conversation_id)
        artifact_ref = self._store_projection_artifact(conversation_id, payload)
        payload["artifact_ref"] = artifact_ref
        self.store.upsert_conversation_projection_cache(
            conversation_id,
            schema_version=_CONVERSATION_PROJECTION_SCHEMA_VERSION,
            event_head_hash=payload["event_head_hash"],
            payload=payload,
        )
        return payload

    def ensure(self, conversation_id: str) -> dict[str, Any]:
        cached = self.store.get_conversation_projection_cache(conversation_id)
        head_hash = self._event_head_hash(conversation_id)
        if (
            cached is not None
            and cached["schema_version"] == _CONVERSATION_PROJECTION_SCHEMA_VERSION
            and cached["event_head_hash"] == head_hash
        ):
            return cached["payload"]
        return self.rebuild(conversation_id)

    def _build_payload(self, conversation_id: str) -> dict[str, Any]:
        tasks = list(reversed(self.store.list_tasks(conversation_id=conversation_id, limit=200)))
        active_task = next((task for task in reversed(tasks) if task.status in {"queued", "running", "blocked"}), None)
        latest_task = tasks[-1] if tasks else None
        recent_notes: list[str] = []
        recent_decisions: list[str] = []
        latest_artifact_refs: list[str] = []
        open_loops: list[str] = []
        last_event_seq = 0

        for task in tasks:
            if task.status in {"queued", "running", "blocked", "planning_ready"}:
                open_loops.append(f"{task.title} [{task.status}]")
            events = self.store.list_events(task_id=task.task_id, limit=500)
            if events:
                last_event_seq = max(last_event_seq, int(events[-1]["event_seq"]))
            for event in reversed(events):
                if event["event_type"] == "task.note.appended" and len(recent_notes) < 5:
                    excerpt = self._sanitize_note_excerpt(
                        str(event["payload"].get("inline_excerpt") or event["payload"].get("raw_text") or "")
                    )
                    if excerpt:
                        recent_notes.append(excerpt[:240])
                if event["event_type"] == "decision.recorded" and len(recent_decisions) < 5:
                    payload = dict(event["payload"])
                    verdict = str(payload.get("verdict") or "").strip()
                    reason = str(payload.get("reason") or "").strip()
                    action_type = str(payload.get("action_type") or "").strip()
                    summary = " ".join(part for part in [action_type, verdict, reason] if part).strip()
                    if summary:
                        recent_decisions.append(summary[:240])
            for artifact in reversed(self.store.list_artifacts(task_id=task.task_id, limit=50)):
                if artifact.artifact_id in latest_artifact_refs:
                    continue
                latest_artifact_refs.append(artifact.artifact_id)
                if len(latest_artifact_refs) >= 10:
                    break

        summary_parts: list[str] = []
        if active_task is not None:
            summary_parts.append(f"Active task: {active_task.title} [{active_task.status}]")
        elif latest_task is not None:
            summary_parts.append(f"Latest task: {latest_task.title} [{latest_task.status}]")
        if recent_notes:
            summary_parts.append(f"Recent notes: {' | '.join(recent_notes[:2])}")
        if recent_decisions:
            summary_parts.append(f"Recent decisions: {' | '.join(recent_decisions[:2])}")
        if not summary_parts:
            summary_parts.append("No active task context.")

        return {
            "conversation_id": conversation_id,
            "summary": " ".join(summary_parts),
            "open_loops": open_loops[:8],
            "active_task_id": active_task.task_id if active_task is not None else (latest_task.task_id if latest_task else ""),
            "recent_decisions": recent_decisions[:5],
            "latest_artifact_refs": latest_artifact_refs[:10],
            "recent_notes": recent_notes[:5],
            "last_event_seq": last_event_seq,
        }

    @staticmethod
    def _sanitize_note_excerpt(text: str) -> str:
        cleaned = _SESSION_TIME_RE.sub("", str(text or ""))
        cleaned = _FEISHU_TAG_RE.sub("", cleaned)
        cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
        return cleaned.strip()

    def _event_head_hash(self, conversation_id: str) -> str:
        tasks = self.store.list_tasks(conversation_id=conversation_id, limit=500)
        heads: list[dict[str, Any]] = []
        for task in tasks:
            events = self.store.list_events(task_id=task.task_id, limit=1)
            if not events:
                continue
            event = events[-1]
            heads.append(
                {
                    "task_id": task.task_id,
                    "event_seq": int(event["event_seq"]),
                    "event_hash": event["event_hash"] or "",
                }
            )
        return _sha256_hex(_canonical_json(heads))

    def _store_projection_artifact(self, conversation_id: str, payload: dict[str, Any]) -> str | None:
        if self.artifact_store is None:
            return None
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=None,
            step_id=None,
            kind="conversation.projection/v1",
            uri=uri,
            content_hash=content_hash,
            producer="conversation_projection",
            retention_class="audit",
            trust_tier="derived",
            metadata={"conversation_id": conversation_id},
        )
        return artifact.artifact_id


__all__ = ["ConversationProjectionService"]
