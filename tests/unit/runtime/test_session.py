from __future__ import annotations

import time

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.runtime.control.lifecycle.session import (
    Session,
    SessionManager,
    sanitize_session_messages,
)


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


def test_session_manager_messages_are_not_persisted_across_instances(tmp_path) -> None:
    """Session messages are ephemeral: they live only in the active manager's memory.
    A new SessionManager instance reloads metadata from KernelStore but starts with
    an empty message list — messages are never written to disk or the kernel DB."""
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("chat-b")
    session.append_user("test message")
    manager.save(session)

    manager2 = SessionManager(tmp_path / "sessions")
    reloaded = manager2.get_or_create("chat-b")

    # Messages are ephemeral — only metadata (tokens, timestamps) persists.
    assert reloaded.messages == []
    # But the conversation entry exists in KernelStore (metadata was saved).
    store = KernelStore(tmp_path / "kernel" / "state.db")
    assert "chat-b" in store.list_conversations()


def test_session_manager_expires_and_resets_projection(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions", idle_timeout_seconds=1)
    session = manager.get_or_create("chat-c")
    session.append_user("old message")
    session.last_active_at = time.time() - 10
    manager.save(session)

    manager._active["chat-c"].last_active_at = time.time() - 10
    new_session = manager.get_or_create("chat-c")

    assert new_session.messages == []


def test_session_manager_close_clears_projection(tmp_path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("chat-d")
    session.append_user("hi")
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

    reloaded = manager._active["chat-repair"]
    assert reloaded.messages[1]["role"] == "user"
    assert reloaded.messages[1]["content"][0]["tool_use_id"] == "call_1"


# ---------------------------------------------------------------------------
# Bug fix: SessionManager._persist hardcoded source_channel="chat"
# ---------------------------------------------------------------------------


def test_infer_source_channel_webhook() -> None:
    assert SessionManager._infer_source_channel("webhook-abc123") == "webhook"


def test_infer_source_channel_scheduler() -> None:
    assert SessionManager._infer_source_channel("schedule-daily-report") == "scheduler"


def test_infer_source_channel_cli() -> None:
    assert SessionManager._infer_source_channel("cli") == "cli"
    assert SessionManager._infer_source_channel("cli-session-1") == "cli"


def test_infer_source_channel_feishu_colon() -> None:
    assert SessionManager._infer_source_channel("oc_abc:123") == "feishu"


def test_infer_source_channel_feishu_oc_prefix() -> None:
    assert SessionManager._infer_source_channel("oc_abcdef") == "feishu"


def test_infer_source_channel_default_chat() -> None:
    assert SessionManager._infer_source_channel("some-random-session") == "chat"
    assert SessionManager._infer_source_channel("chat-abc") == "chat"


def test_persist_uses_inferred_source_channel_for_feishu(tmp_path) -> None:
    """_persist must store the conversation with the correct source_channel, not 'chat'."""
    manager = SessionManager(tmp_path / "sessions")

    # Simulate a Feishu session (session_id contains ":")
    feishu_session_id = "oc_group:user123"
    session = manager.get_or_create(feishu_session_id)
    manager.save(session)

    store = KernelStore(tmp_path / "kernel" / "state.db")
    conversation = store.get_conversation(feishu_session_id)
    assert conversation is not None
    assert conversation.source_channel == "feishu"


def test_persist_uses_inferred_source_channel_for_scheduler(tmp_path) -> None:
    """Scheduled task sessions must record source_channel='scheduler', not 'chat'."""
    manager = SessionManager(tmp_path / "sessions")

    sched_session_id = "schedule-morning-digest"
    session = manager.get_or_create(sched_session_id)
    manager.save(session)

    store = KernelStore(tmp_path / "kernel" / "state.db")
    conversation = store.get_conversation(sched_session_id)
    assert conversation is not None
    assert conversation.source_channel == "scheduler"


def test_persist_uses_inferred_source_channel_for_webhook(tmp_path) -> None:
    """Webhook sessions must record source_channel='webhook', not 'chat'."""
    manager = SessionManager(tmp_path / "sessions")

    wh_session_id = "webhook-github-push"
    session = manager.get_or_create(wh_session_id)
    manager.save(session)

    store = KernelStore(tmp_path / "kernel" / "state.db")
    conversation = store.get_conversation(wh_session_id)
    assert conversation is not None
    assert conversation.source_channel == "webhook"
