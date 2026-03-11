from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermit.provider.messages import normalize_messages
from hermit.storage import atomic_write


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
    """Manages per-chat sessions with file persistence and idle timeout."""

    def __init__(self, sessions_dir: Path, idle_timeout_seconds: int = 1800) -> None:
        self.sessions_dir = sessions_dir
        self.idle_timeout_seconds = idle_timeout_seconds
        self._active: Dict[str, Session] = {}
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create(self, session_id: str) -> Session:
        if session_id in self._active:
            session = self._active[session_id]
            if not session.is_expired(self.idle_timeout_seconds):
                return session
            self._finalize(session)

        session = self._load_from_disk(session_id)
        if session is not None and not session.is_expired(self.idle_timeout_seconds):
            self._active[session_id] = session
            return session

        if session is not None:
            self._finalize(session)

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
            session = self._load_from_disk(session_id)
        if session is not None:
            self._finalize(session)
        return session

    def list_sessions(self) -> List[str]:
        on_disk = {
            path.stem for path in self.sessions_dir.glob("*.json")
        }
        return sorted(on_disk | set(self._active.keys()))

    def _persist(self, session: Session) -> None:
        """Atomically write the session to disk.

        Session files are isolated by session_id so no cross-session locking
        is needed; atomic_write alone prevents partial-write corruption.
        """
        path = self._session_path(session.session_id)
        atomic_write(path, json.dumps(session.to_dict(), ensure_ascii=False, indent=2))

    def _load_from_disk(self, session_id: str) -> Optional[Session]:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def _finalize(self, session: Session) -> None:
        """Archive the session and remove the active file.

        Write the archive first; only unlink the active file after the archive
        is safely on disk.  Both writes use atomic_write to prevent corruption
        if the process is interrupted mid-write.
        """
        self._active.pop(session.session_id, None)
        path = self._session_path(session.session_id)
        archive_dir = self.sessions_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{session.session_id}_{int(session.created_at)}.json"
        content = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        atomic_write(archive_path, content)
        if path.exists():
            path.unlink()

    def _session_path(self, session_id: str) -> Path:
        safe_name = session_id.replace("/", "_").replace("..", "_")
        return self.sessions_dir / f"{safe_name}.json"
