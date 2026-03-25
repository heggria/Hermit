from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.runtime.provider_host.shared.messages import normalize_block, normalize_messages


def sanitize_session_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair orphaned tool-use blocks before a session is reused.

    Claude requires every assistant ``tool_use`` block to be followed by a user
    message containing the matching ``tool_result`` blocks. Approval pauses and
    some interrupted tool flows can leave behind an assistant message without
    that follow-up. We synthesize an error ``tool_result`` so the next turn can
    continue instead of failing the entire conversation.
    """

    cleaned: list[dict[str, Any]] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if isinstance(content, list):
            blocks = [
                normalize_block(block)
                for block in cast(list[Any], content)
                if isinstance(block, dict)
            ]
            cleaned.append({"role": role, "content": blocks})
        else:
            cleaned.append({"role": role, "content": content})

    if not cleaned:
        return cleaned

    tail = cleaned[-1]
    if tail.get("role") == "assistant" and isinstance(tail.get("content"), list):
        tail_blocks: list[dict[str, Any]] = [
            cast(dict[str, Any], block)
            for block in cast(list[Any], tail["content"])
            if isinstance(block, dict)
        ]
        has_tool_use = any(block.get("type") == "tool_use" for block in tail_blocks)
        has_text = any(block.get("type") == "text" and block.get("text") for block in tail_blocks)
        if has_tool_use and not has_text:
            cleaned.pop()

    index = 0
    while index < len(cleaned):
        message = cleaned[index]
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            index += 1
            continue

        tool_use_ids = [
            str(cast(dict[str, Any], block).get("id"))
            for block in cast(list[Any], message["content"])
            if isinstance(block, dict)
            and cast(dict[str, Any], block).get("type") == "tool_use"
            and cast(dict[str, Any], block).get("id")
        ]
        if not tool_use_ids:
            index += 1
            continue

        next_message = cleaned[index + 1] if index + 1 < len(cleaned) else None
        next_is_user = isinstance(next_message, dict) and next_message.get("role") == "user"
        if (
            next_is_user
            and next_message is not None
            and isinstance(next_message.get("content"), list)
        ):
            next_blocks = [
                normalize_block(block)
                for block in cast(list[Any], next_message["content"])
                if isinstance(block, dict)
            ]
        elif (
            next_is_user
            and next_message is not None
            and isinstance(next_message.get("content"), str)
        ):
            next_blocks = [{"type": "text", "text": cast(str, next_message["content"])}]
        else:
            next_blocks = []

        result_ids = {
            str(block.get("tool_use_id"))
            for block in next_blocks
            if block.get("type") == "tool_result" and block.get("tool_use_id")
        }
        missing_ids = [tool_use_id for tool_use_id in tool_use_ids if tool_use_id not in result_ids]
        if not missing_ids:
            index += 1
            continue

        synthetic_results = [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "[session repair: missing tool result]",
                "is_error": True,
            }
            for tool_use_id in missing_ids
        ]

        if next_is_user and next_message is not None:
            next_message["content"] = next_blocks + synthetic_results
        else:
            cleaned.insert(index + 1, {"role": "user", "content": synthetic_results})
            index += 1

        index += 1

    return cleaned


@dataclass
class Session:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.last_active_at = time.time()

    def append_assistant(self, blocks: list[Any]) -> None:
        self.messages.append({"role": "assistant", "content": blocks})
        self.last_active_at = time.time()

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})
        self.last_active_at = time.time()

    def is_expired(self, idle_timeout_seconds: int) -> bool:
        return (time.time() - self.last_active_at) > idle_timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            session_id=str(data["session_id"]),
            messages=sanitize_session_messages(normalize_messages(list(data.get("messages", [])))),
            created_at=float(data.get("created_at", time.time())),
            last_active_at=float(data.get("last_active_at", time.time())),
            total_input_tokens=int(data.get("total_input_tokens", 0)),
            total_output_tokens=int(data.get("total_output_tokens", 0)),
            total_cache_read_tokens=int(data.get("total_cache_read_tokens", 0)),
            total_cache_creation_tokens=int(data.get("total_cache_creation_tokens", 0)),
        )


class SessionManager:
    """Manages live per-chat sessions; conversation records remain UX metadata only."""

    def __init__(
        self,
        sessions_dir: Path,
        idle_timeout_seconds: int = 1800,
        *,
        store: KernelStore | None = None,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.idle_timeout_seconds = idle_timeout_seconds
        self._active: dict[str, Session] = {}
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._store = store or KernelStore(self.sessions_dir.parent / "kernel" / "state.db")

    @staticmethod
    def _infer_source_channel(session_id: str) -> str:
        """Infer source_channel from session_id prefix conventions.

        Mirrors ``TaskController.source_from_session`` so conversation records
        are stored with the correct channel without requiring a TaskController
        dependency.
        """
        if session_id.startswith("webhook-"):
            return "webhook"
        if session_id.startswith("schedule-"):
            return "scheduler"
        if session_id.startswith("cli"):
            return "cli"
        if ":" in session_id or session_id.startswith("oc_"):
            return "feishu"
        return "chat"

    def get_or_create(self, session_id: str) -> Session:
        if session_id in self._active:
            session = self._active[session_id]
            if not session.is_expired(self.idle_timeout_seconds):
                return session
            self._finalize(session)

        session = self._load_from_store(session_id)
        if session is not None and not session.is_expired(self.idle_timeout_seconds):
            self._active[session_id] = session
            return session

        if session is not None:
            self._finalize(session)

        self._store.ensure_conversation(
            session_id, source_channel=self._infer_source_channel(session_id)
        )
        new_session = Session(session_id=session_id)
        self._active[session_id] = new_session
        return new_session

    def save(self, session: Session) -> None:
        session.last_active_at = time.time()
        self._active[session.session_id] = session
        self._persist(session)

    def close(self, session_id: str) -> Session | None:
        session = self._active.pop(session_id, None)
        if session is None:
            session = self._load_from_store(session_id)
        if session is not None:
            self._finalize(session)
        return session

    def list_sessions(self) -> list[str]:
        return sorted(set(self._store.list_conversations()) | set(self._active.keys()))

    def _persist(self, session: Session) -> None:
        session.messages = sanitize_session_messages(normalize_messages(session.messages))
        self._store.ensure_conversation(
            session.session_id, source_channel=self._infer_source_channel(session.session_id)
        )
        self._store.update_conversation_usage(
            session.session_id,
            input_tokens=session.total_input_tokens,
            output_tokens=session.total_output_tokens,
            cache_read_tokens=session.total_cache_read_tokens,
            cache_creation_tokens=session.total_cache_creation_tokens,
            last_task_id=None,
        )

    def _load_from_store(self, session_id: str) -> Session | None:
        conversation = self._store.get_conversation(session_id)
        if conversation is None:
            return None
        return Session(
            session_id=session_id,
            messages=[],
            created_at=conversation.created_at,
            last_active_at=conversation.updated_at,
            total_input_tokens=conversation.total_input_tokens,
            total_output_tokens=conversation.total_output_tokens,
            total_cache_read_tokens=conversation.total_cache_read_tokens,
            total_cache_creation_tokens=conversation.total_cache_creation_tokens,
        )

    def _finalize(self, session: Session) -> None:
        self._active.pop(session.session_id, None)
