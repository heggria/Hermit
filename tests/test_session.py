from __future__ import annotations

import time

from hermit.core.session import Session, SessionManager, sanitize_session_messages
from hermit.kernel.store import KernelStore


def test_session_append_and_serialize() -> None:
    session = Session(session_id="test-1")

    session.append_user("hello")
    session.append_assistant([{"type": "text", "text": "hi"}])

    assert len(session.messages) == 2
    data = session.to_dict()
    restored = Session.from_dict(data)
    assert restored.messages == session.messages
    assert restored.session_id == "test-1"


def test_session_is_expired() -> None:
    session = Session(session_id="x", last_active_at=time.time() - 100)

    assert session.is_expired(idle_timeout_seconds=50) is True
    assert session.is_expired(idle_timeout_seconds=200) is False


def test_session_manager_creates_new_session(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")

    session = manager.get_or_create("chat-a")

    assert session.session_id == "chat-a"
    assert session.messages == []


def test_session_manager_persists_and_reloads(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("chat-b")
    session.append_user("test message")
    manager.save(session)

    manager2 = SessionManager(tmp_path / "sessions")
    reloaded = manager2.get_or_create("chat-b")

    assert len(reloaded.messages) == 1
    assert reloaded.messages[0]["content"] == "test message"


def test_session_manager_expires_and_resets_projection(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions", idle_timeout_seconds=1)
    session = manager.get_or_create("chat-c")
    session.append_user("old message")
    session.last_active_at = time.time() - 10
    manager.save(session)

    manager._active["chat-c"].last_active_at = time.time() - 10
    new_session = manager.get_or_create("chat-c")

    assert new_session.messages == []
    store = KernelStore(tmp_path / "kernel" / "state.db")
    assert store.load_messages("chat-c") == []


def test_session_manager_close_clears_projection(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("chat-d")
    session.append_user("hello")
    manager.save(session)

    closed = manager.close("chat-d")

    assert closed is not None
    assert "chat-d" not in manager._active
    assert manager.get_or_create("chat-d").messages == []


def test_session_manager_list_sessions(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    manager.get_or_create("alpha")
    manager.save(manager.get_or_create("beta"))

    result = manager.list_sessions()

    assert "alpha" in result
    assert "beta" in result


def test_session_manager_preserves_session_id_in_kernel(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("oc_abc/123")
    manager.save(session)

    store = KernelStore(tmp_path / "kernel" / "state.db")
    assert "oc_abc/123" in store.list_conversations()
    assert list((tmp_path / "sessions").glob("*.json")) == []


def test_sanitize_session_messages_inserts_missing_tool_result() -> None:
    cleaned = sanitize_session_messages(
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call_1", "name": "demo", "input": {}}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "需要先审批。"}],
            },
        ]
    )

    assert cleaned[1]["role"] == "user"
    assert cleaned[1]["content"][0]["type"] == "tool_result"
    assert cleaned[1]["content"][0]["tool_use_id"] == "call_1"
    assert cleaned[2]["role"] == "assistant"


def test_session_manager_save_repairs_orphaned_tool_use(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("chat-repair")
    session.messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "demo", "input": {}}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "工具执行被暂停。"}],
        },
    ]

    manager.save(session)

    reloaded = manager.get_or_create("chat-repair")
    assert reloaded.messages[1]["role"] == "user"
    assert reloaded.messages[1]["content"][0]["tool_use_id"] == "call_1"
