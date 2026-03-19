"""Memory extraction hooks: POST_RUN checkpoint and SESSION_END save."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.infra.storage import JsonStore
from hermit.infra.system.i18n import tr, tr_list_all_locales
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.hooks_promotion import promote_memories_via_kernel
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry
from hermit.runtime.provider_host.execution.services import (
    StructuredExtractionService,
    build_provider,
)

log = structlog.get_logger()

_MAX_TRANSCRIPT_CHARS = 16000
_MAX_MSG_CHARS = 800
_CHECKPOINT_MIN_CHARS = 300
_CHECKPOINT_MIN_MESSAGES = 6
_CHECKPOINT_MIN_USER_MESSAGES = 2

_explicit_memory_re_cache: re.Pattern[str] | None = None
_decision_signal_re_cache: re.Pattern[str] | None = None


def _build_memory_re(key: str) -> re.Pattern[str]:
    keywords = tr_list_all_locales(key)
    if not keywords:
        return re.compile(r"(?!)")
    escaped = [re.escape(k) for k in keywords]
    return re.compile(r"(" + "|".join(escaped) + r")", re.IGNORECASE)


def _get_explicit_memory_re() -> re.Pattern[str]:
    global _explicit_memory_re_cache
    if _explicit_memory_re_cache is None:
        _explicit_memory_re_cache = _build_memory_re("kernel.nlp.memory_signal_re")
    return _explicit_memory_re_cache


def _get_decision_signal_re() -> re.Pattern[str]:
    global _decision_signal_re_cache
    if _decision_signal_re_cache is None:
        _decision_signal_re_cache = _build_memory_re("kernel.nlp.decision_signal_re")
    return _decision_signal_re_cache


def save_memories(
    engine: MemoryEngine,
    settings: Any,
    session_id: str,
    messages: list[dict[str, Any]],
) -> None:
    """Final memory extraction and promotion on session end."""
    if not messages:
        log.info("memory_save_skipped", session_id=session_id, reason="no_messages")
        return
    if not settings.has_auth:
        log.info("memory_save_skipped", session_id=session_id, reason="no_auth")
        return
    try:
        _extract_and_save(engine, settings, messages, session_id=session_id)
    except Exception:
        log.exception("memory_save_failed", session_id=session_id)
    finally:
        _clear_session_progress(settings.session_state_file, session_id)


def checkpoint_memories(
    engine: MemoryEngine,
    settings: Any,
    session_id: str,
    messages: list[dict[str, Any]],
) -> None:
    """Checkpoint memory extraction during active conversation."""
    if not session_id:
        log.info("memory_checkpoint_skipped", reason="missing_session_id")
        return
    if session_id == "cli-oneshot":
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="cli_oneshot")
        return
    if not messages:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_messages")
        return
    if not settings.has_auth:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_auth")
        return

    delta, processed = _pending_messages(settings.session_state_file, session_id, messages)
    if not delta:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_pending_delta")
        return

    should_cp, reason = should_checkpoint(delta)
    if not should_cp:
        log.info(
            "memory_checkpoint_skipped",
            session_id=session_id,
            reason=reason,
            pending_messages=len(delta),
        )
        return

    try:
        extraction = extract_memory_payload(engine, settings, delta, max_tokens=1024)
    except Exception:
        log.exception("memory_checkpoint_failed", session_id=session_id, reason=reason)
        return

    new_entries = extraction["new_entries"]
    if not new_entries:
        log.info(
            "memory_checkpoint_no_entries",
            session_id=session_id,
            reason=reason,
            pending_messages=len(delta),
        )
        return

    if not promote_memories_via_kernel(
        engine,
        settings,
        session_id=session_id,
        messages=delta,
        used_keywords=set(extraction["used_keywords"]),
        new_entries=new_entries,
        mode="checkpoint",
    ):
        log.info(
            "memory_checkpoint_skipped",
            session_id=session_id,
            reason="kernel_promotion_unavailable",
        )
        return
    _mark_messages_processed(settings.session_state_file, session_id, len(messages))
    log.info(
        "memory_checkpoint_saved",
        session_id=session_id,
        reason=reason,
        new=len(new_entries),
        processed_before=processed,
        processed_after=len(messages),
    )


def _extract_and_save(
    engine: MemoryEngine,
    settings: Any,
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
) -> None:
    log.info("memory_extraction_started", mode="session_end", message_count=len(messages))
    extraction = extract_memory_payload(engine, settings, messages, max_tokens=2048)
    used_keywords = extraction["used_keywords"]
    new_entries = extraction["new_entries"]

    if not new_entries and not used_keywords:
        log.info("memory_nothing_to_save")
        return

    if promote_memories_via_kernel(
        engine,
        settings,
        session_id=session_id,
        messages=messages,
        used_keywords=used_keywords,
        new_entries=new_entries,
        mode="session_end",
    ):
        log.info(
            "memory_promoted", mode="session_end", new=len(new_entries), keywords=len(used_keywords)
        )
        return
    log.info("memory_save_skipped", session_id=session_id, reason="kernel_promotion_unavailable")


def extract_memory_payload(
    engine: MemoryEngine,
    settings: Any,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
) -> dict[str, Any]:
    """Extract memory candidates from conversation transcript."""
    from hermit.plugins.builtin.hooks.memory.hooks_injection import (
        _knowledge_categories,  # pyright: ignore[reportPrivateUsage]
    )

    transcript = format_transcript(messages)
    if len(transcript.strip()) < 20:
        log.info(
            "memory_extraction_empty", reason="short_transcript", transcript_chars=len(transcript)
        )
        return {"used_keywords": set(), "new_entries": []}

    existing = _knowledge_categories(engine, settings)
    existing_text = engine.summary_prompt(existing)
    user_content = (
        f"<existing_memories>\n{existing_text}\n</existing_memories>\n\n"
        f"<conversation>\n{transcript}\n</conversation>"
    )

    log.info(
        "memory_extraction_started",
        mode="checkpoint" if max_tokens <= 1024 else "session_end",
        message_count=len(messages),
        transcript_chars=len(transcript),
        existing_chars=len(existing_text),
        max_tokens=max_tokens,
    )
    provider = build_provider(settings, model=settings.model)
    service = StructuredExtractionService(provider, model=settings.model)
    data = service.extract_json(
        system_prompt=tr("prompt.memory.extraction"),
        user_content=user_content,
        max_tokens=max_tokens,
    )
    if not data:
        log.info(
            "memory_extraction_empty", reason="no_provider_data", transcript_chars=len(transcript)
        )
        return {"used_keywords": set(), "new_entries": []}

    used_keywords: set[str] = set(data.get("used_keywords", []))
    new_entries: list[MemoryEntry] = []
    for item in data.get("new_memories", []):
        content = item.get("content", "").strip()
        if content:
            new_entries.append(
                MemoryEntry(
                    category=item.get("category", "other"),
                    content=content,
                    confidence=_infer_confidence(content),
                )
            )
    log.info(
        "memory_extraction_result",
        used_keywords=len(used_keywords),
        new_entries=len(new_entries),
        categories=len({entry.category for entry in new_entries}),
    )
    return {"used_keywords": used_keywords, "new_entries": new_entries}


def should_checkpoint(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    """Determine if a checkpoint should be created."""
    user_text = _collect_role_text(messages, "user")
    assistant_text = _collect_role_text(messages, "assistant")
    transcript = format_transcript(messages)
    meaningful_count = sum(1 for msg in messages if _message_text(msg).strip())
    user_count = sum(
        1 for msg in messages if msg.get("role") == "user" and _message_text(msg).strip()
    )

    if _get_explicit_memory_re().search(user_text):
        return True, "explicit_memory_signal"
    if _get_decision_signal_re().search(user_text) or _get_decision_signal_re().search(
        assistant_text
    ):
        return True, "decision_signal"
    if len(transcript) >= _CHECKPOINT_MIN_CHARS and user_count >= _CHECKPOINT_MIN_USER_MESSAGES:
        return True, "conversation_batch"
    if meaningful_count >= _CHECKPOINT_MIN_MESSAGES:
        return True, "message_batch"
    return False, "below_threshold"


def format_transcript(messages: list[dict[str, Any]]) -> str:
    """Format conversation messages into a transcript string."""
    lines: list[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        text = _message_text(msg)
        if not text.strip():
            continue
        label = {"user": "User", "assistant": "Assistant"}.get(role, role)
        entry = f"[{label}] {text}"
        total += len(entry)
        if total > _MAX_TRANSCRIPT_CHARS:
            lines.append("[... conversation truncated ...]")
            break
        lines.append(entry)
    return "\n\n".join(lines)


def memory_entry_payload(entry: MemoryEntry) -> dict[str, Any]:
    """Convert a MemoryEntry to a dict for artifact storage."""
    return {
        "category": entry.category,
        "content": entry.content,
        "score": entry.score,
        "locked": entry.locked,
        "confidence": entry.confidence,
    }


def _infer_confidence(content: str) -> float:
    strong_signal = tuple(tr_list_all_locales("kernel.nlp.confidence.strong_signal"))
    if any(signal in content for signal in strong_signal):
        return 0.8
    if len(content) >= 20:
        return 0.65
    return 0.55


def _pending_messages(
    state_file: Path,
    session_id: str,
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    data = _read_state(state_file)
    raw_sessions = data.get("sessions", {})
    sessions: dict[str, Any] = (
        cast(dict[str, Any], raw_sessions) if isinstance(raw_sessions, dict) else {}
    )
    raw_meta = sessions.get(session_id, {})
    meta: dict[str, Any] = cast(dict[str, Any], raw_meta) if isinstance(raw_meta, dict) else {}
    processed = int(meta.get("processed_messages", 0))
    if processed < 0:
        processed = 0
    return messages[processed:], processed


def _mark_messages_processed(state_file: Path, session_id: str, count: int) -> None:
    store = JsonStore(state_file, default={"session_index": 0, "sessions": {}}, cross_process=True)
    with store.update() as data:
        raw_sessions = data.setdefault("sessions", {})
        if not isinstance(raw_sessions, dict):
            raw_sessions = {}
            data["sessions"] = raw_sessions
        sessions = cast(dict[str, Any], raw_sessions)
        raw_meta = sessions.get(session_id)
        if not isinstance(raw_meta, dict):
            raw_meta = {}
            sessions[session_id] = raw_meta
        meta = cast(dict[str, Any], raw_meta)
        meta["processed_messages"] = max(0, int(count))


def _clear_session_progress(state_file: Path, session_id: str) -> None:
    if not session_id:
        return
    store = JsonStore(state_file, default={"session_index": 0, "sessions": {}}, cross_process=True)
    with store.update() as data:
        raw_sessions = data.get("sessions", {})
        if isinstance(raw_sessions, dict):
            sessions = cast(dict[str, Any], raw_sessions)
            sessions.pop(session_id, None)


def _read_state(state_file: Path) -> dict[str, Any]:
    return JsonStore(
        state_file,
        default={"session_index": 0, "sessions": {}},
        cross_process=True,
    ).read()


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content[:_MAX_MSG_CHARS]
    if not isinstance(content, list):
        return str(content)[:_MAX_MSG_CHARS] if content else ""

    parts: list[str] = []
    for raw_block in cast(list[Any], content):
        if not isinstance(raw_block, dict):
            continue
        block = cast(dict[str, Any], raw_block)
        btype = str(block.get("type", ""))
        if btype == "text":
            parts.append(str(block.get("text", ""))[:_MAX_MSG_CHARS])
        elif btype == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)[:120]
            parts.append(f"[Tool: {block.get('name', '')}({inp})]")
        elif btype == "tool_result":
            parts.append(f"[Tool Result: {str(block.get('content', ''))[:200]}]")
    return "\n".join(parts).strip()


def _collect_role_text(messages: list[dict[str, Any]], role: str) -> str:
    return "\n".join(_message_text(msg) for msg in messages if msg.get("role") == role).strip()


__all__ = [
    "checkpoint_memories",
    "extract_memory_payload",
    "format_transcript",
    "memory_entry_payload",
    "save_memories",
    "should_checkpoint",
]
