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
_MAX_USER_MSG_CHARS = 2000  # Higher limit for user messages to preserve preference signals
_CHECKPOINT_MIN_CHARS = 800
_CHECKPOINT_MIN_MESSAGES = 6
_CHECKPOINT_MIN_USER_MESSAGES = 3
_IMPORTANCE_THRESHOLD = 5  # Discard memories with importance < 5

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
    skipped_low_importance = 0
    for item in data.get("new_memories", []):
        content = item.get("content", "").strip()
        if not content:
            continue
        importance = int(item.get("importance", 5))
        importance = max(1, min(10, importance))
        if importance < _IMPORTANCE_THRESHOLD:
            skipped_low_importance += 1
            continue
        raw_entities = item.get("entities", [])
        entities = (
            [str(e).strip() for e in raw_entities if str(e).strip()]
            if isinstance(raw_entities, list)
            else []
        )
        if not entities:
            entities = _extract_entities_fallback(content)
        new_entries.append(
            MemoryEntry(
                category=item.get("category", "other"),
                content=content,
                score=importance,
                confidence=_infer_confidence(content, importance),
                entities=entities,
            )
        )
    log.info(
        "memory_extraction_result",
        used_keywords=len(used_keywords),
        new_entries=len(new_entries),
        skipped_low_importance=skipped_low_importance,
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
    """Format conversation messages into a transcript string.

    User messages get a higher character budget (_MAX_USER_MSG_CHARS) to
    preserve preference signals that would otherwise be truncated.
    """
    lines: list[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        text = _message_text(msg, role_hint=role)
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


def _extract_entities_fallback(text: str) -> list[str]:
    """Extract likely entity names from text using simple heuristics."""
    entities: list[str] = []
    # File paths
    for match in re.findall(r"(?:/[\w./-]+)", text):
        if len(match) > 3:
            entities.append(match)
    # PascalCase or camelCase identifiers (likely class/tool/project names)
    for match in re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text):
        entities.append(match)
    # Quoted terms
    for match in re.findall(r'["\u201c]([^"\u201d]{2,30})["\u201d]', text):
        entities.append(match.strip())
    return list(dict.fromkeys(entities))[:10]  # deduplicate, limit to 10


def _infer_confidence(content: str, importance: int = 5) -> float:
    """5-tier confidence calibration combining importance score with signal keywords."""
    strong_signal = tuple(tr_list_all_locales("kernel.nlp.confidence.strong_signal"))
    has_strong_signal = any(signal in content for signal in strong_signal)

    # Base confidence from importance (primary signal)
    if importance >= 9:
        base = 0.90
    elif importance >= 7:
        base = 0.75
    elif importance >= 5:
        base = 0.60
    else:
        base = 0.45  # Should not reach here due to threshold gate

    # Boost for strong signal keywords
    if has_strong_signal:
        base = min(base + 0.10, 0.95)

    # Slight penalty for very short content (likely vague)
    if len(content) < 15:
        base = max(base - 0.10, 0.40)

    return round(base, 2)


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


def _message_text(msg: dict[str, Any], *, role_hint: str = "") -> str:
    role = role_hint or msg.get("role", "")
    char_limit = _MAX_USER_MSG_CHARS if role == "user" else _MAX_MSG_CHARS
    content = msg.get("content", "")
    if isinstance(content, str):
        return content[:char_limit]
    if not isinstance(content, list):
        return str(content)[:char_limit] if content else ""

    parts: list[str] = []
    for raw_block in cast(list[Any], content):
        if not isinstance(raw_block, dict):
            continue
        block = cast(dict[str, Any], raw_block)
        btype = str(block.get("type", ""))
        if btype == "text":
            parts.append(str(block.get("text", ""))[:char_limit])
        elif btype == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)[:120]
            parts.append(f"[Tool: {block.get('name', '')}({inp})]")
        elif btype == "tool_result":
            parts.append(f"[Tool Result: {str(block.get('content', ''))[:200]}]")
    return "\n".join(parts).strip()


def _collect_role_text(messages: list[dict[str, Any]], role: str) -> str:
    return "\n".join(
        _message_text(msg, role_hint=role) for msg in messages if msg.get("role") == role
    ).strip()


__all__ = [
    "checkpoint_memories",
    "extract_memory_payload",
    "format_transcript",
    "memory_entry_payload",
    "save_memories",
    "should_checkpoint",
]
