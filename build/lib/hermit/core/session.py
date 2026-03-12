from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermit.kernel.store import KernelStore
from hermit.provider.messages import normalize_messages


@dataclass
class Session:
    session_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.last_active_at = time.time()

    def append_assistant(self, blocks: List[Any]) -> None:
        self.messages.append({"role": "assistant", "content": blocks})
        self.last_active_at = time.time()

    def append_tool_results(self, results: List[Dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})
        self.last_active_at = time.time()

    def is_expired(self, idle_timeout_seconds: int) -> bool:
        return (time.time() - self.last_active_at) > idle_timeout_seconds

    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> Session:
        return cls(
            session_id=str(data["session_id"]),
            messages=normalize_messages(list(data.get("messages", []))),
            created_at=float(data.get("created_at", time.time())),
            last_active_at=float(data.get("last_active_at", time.time())),
            total_input_tokens=int(data.get("total_input_tokens", 0)),
            total_output_tokens=int(data.get("total_output_tokens", 0)),
            total_cache_read_tokens=int(data.get("total_cache_read_tokens", 0)),
            total_cache_creation_tokens=int(data.get("total_cache_creation_tokens", 0)),
        )


class SessionManager:
    """Manages per-chat sessions backed by the kernel conversation projection."""

    def __init__(
        self,
        sessions_dir: Path,
        idle_timeout_seconds: int = 1800,
        *,
        store: KernelStore | None = None,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.idle_timeout_seconds = idle_timeout_seconds
        self._active: Dict[str, Session] = {}
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._store = store or KernelStore(self.sessions_dir.parent / "kernel" / "state.db")

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

        self._store.ensure_conversation(session_id, source_channel="chat")
        new_session = Session(session_id=session_id)
        self._active[session_id] = new_session
        return new_session

    def save(self, session: Session) -> None:
        session.last_active_at = time.time()
        self._active[session.session_id] = session
        self._persist(session)

    def close(self, session_id: str) -> Optional[Session]:
        session = self._active.pop(session_id, None)
        if session is None:
            session = self._load_from_store(session_id)
        if session is not None:
            self._finalize(session)
        return session

    def list_sessions(self) -> List[str]:
        return sorted(set(self._store.list_conversations()) | set(self._active.keys()))

    def _persist(self, session: Session) -> None:
        self._store.ensure_conversation(session.session_id, source_channel="chat")
        self._store.replace_messages(session.session_id, session.messages)
        self._store.update_conversation_usage(
            session.session_id,
            input_tokens=session.total_input_tokens,
            output_tokens=session.total_output_tokens,
            cache_read_tokens=session.total_cache_read_tokens,
            cache_creation_tokens=session.total_cache_creation_tokens,
            last_task_id=None,
        )

    def _load_from_store(self, session_id: str) -> Optional[Session]:
        conversation = self._store.get_conversation(session_id)
        if conversation is None:
            return None
        return Session(
            session_id=session_id,
            messages=normalize_messages(self._store.load_messages(session_id)),
            created_at=conversation.created_at,
            last_active_at=conversation.updated_at,
            total_input_tokens=conversation.total_input_tokens,
            total_output_tokens=conversation.total_output_tokens,
            total_cache_read_tokens=conversation.total_cache_read_tokens,
            total_cache_creation_tokens=conversation.total_cache_creation_tokens,
        )

    def _finalize(self, session: Session) -> None:
        self._active.pop(session.session_id, None)
        self._store.clear_messages(session.session_id)

    def _session_path(self, session_id: str) -> Path:
        safe_name = session_id.replace("/", "_").replace("..", "_")
        return self.sessions_dir / f"{safe_name}.json"
