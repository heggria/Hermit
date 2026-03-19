"""Memory plugin hooks — registration entry point.

Delegates to focused modules:
- hooks_injection: SYSTEM_PROMPT and PRE_RUN context injection
- hooks_extraction: POST_RUN checkpoint and SESSION_END extraction
- hooks_promotion: governed kernel promotion pipeline
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.infra.storage import JsonStore
from hermit.infra.system.i18n import tr_list_all_locales
from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.text import is_duplicate, shares_topic
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
    checkpoint_memories,
    extract_memory_payload,
    format_transcript,
    memory_entry_payload,
    save_memories,
    should_checkpoint,
)
from hermit.plugins.builtin.hooks.memory.hooks_injection import (
    inject_memory,
    inject_relevant_memory,
)
from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
    promote_memories_via_kernel,
)
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

log = structlog.get_logger()

_MAX_TRANSCRIPT_CHARS = 16000
_MAX_MSG_CHARS = 800
_CHECKPOINT_MIN_CHARS = 300
_CHECKPOINT_MIN_MESSAGES = 6
_CHECKPOINT_MIN_USER_MESSAGES = 2
_GOVERNANCE = MemoryGovernanceService()


def _build_memory_re(key: str) -> re.Pattern[str]:
    keywords = tr_list_all_locales(key)
    if not keywords:
        return re.compile(r"(?!)")  # never matches
    escaped = [re.escape(k) for k in keywords]
    return re.compile(r"(" + "|".join(escaped) + r")", re.IGNORECASE)


_explicit_memory_re_cache: re.Pattern[str] | None = None
_decision_signal_re_cache: re.Pattern[str] | None = None


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


def register(ctx: PluginContext) -> None:
    settings = ctx.settings
    if settings is None:
        return

    engine = MemoryEngine(settings.memory_file)

    def _hook_system_prompt(**_: Any) -> str:
        return inject_memory(engine, settings)

    def _hook_pre_run(
        prompt: str = "",
        session_id: str = "",
        runner: Any | None = None,
        **kwargs: Any,
    ) -> str:
        return inject_relevant_memory(
            engine,
            settings,
            prompt=prompt,
            session_id=session_id,
            runner=runner,
            **kwargs,
        )

    def _hook_post_run(result: Any, session_id: str = "", **kwargs: Any) -> None:
        checkpoint_memories(
            engine,
            settings,
            session_id,
            getattr(result, "messages", []) or [],
        )

    def _hook_session_end(session_id: str, messages: list[dict[str, Any]]) -> None:
        save_memories(engine, settings, session_id, messages)

    ctx.add_hook(HookEvent.SYSTEM_PROMPT, _hook_system_prompt, priority=20)
    ctx.add_hook(HookEvent.PRE_RUN, _hook_pre_run, priority=20)
    ctx.add_hook(HookEvent.POST_RUN, _hook_post_run, priority=20)
    ctx.add_hook(HookEvent.SESSION_END, _hook_session_end, priority=90)


# --- Backward-compatible aliases ---
# Re-export from submodules so external code importing from hooks.py still works.

_inject_memory = inject_memory
_inject_relevant_memory = inject_relevant_memory
_checkpoint_memories = checkpoint_memories
_save_memories = save_memories
_should_checkpoint = should_checkpoint
_format_transcript = format_transcript
_memory_entry_payload = memory_entry_payload
_extract_memory_payload = extract_memory_payload
_promote_memories_via_kernel = promote_memories_via_kernel


def _knowledge_categories(  # pyright: ignore[reportUnusedFunction]
    engine: MemoryEngine, settings: Any | None
) -> dict[str, list[MemoryEntry]]:
    from hermit.plugins.builtin.hooks.memory.hooks_injection import (
        _knowledge_categories as knowledge_categories_impl,  # pyright: ignore[reportPrivateUsage]
    )

    return knowledge_categories_impl(engine, settings)


def _compile_context_pack(  # pyright: ignore[reportUnusedFunction]
    engine: MemoryEngine,
    settings: Any | None,
    *,
    query: str,
    conversation_id: str | None,
    runner: Any | None,
) -> dict[str, Any] | None:
    from hermit.plugins.builtin.hooks.memory.hooks_injection import (
        _compile_context_pack as compile_context_pack_impl,  # pyright: ignore[reportPrivateUsage]
    )

    return compile_context_pack_impl(
        engine, settings, query=query, conversation_id=conversation_id, runner=runner
    )


def _extract_and_save(  # pyright: ignore[reportUnusedFunction]
    engine: MemoryEngine,
    settings: Any,
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
) -> None:
    from hermit.plugins.builtin.hooks.memory.hooks_extraction import (
        _extract_and_save as extract_and_save_impl,  # pyright: ignore[reportPrivateUsage]
    )

    extract_and_save_impl(engine, settings, messages, session_id=session_id)


def _store_memory_artifact(  # pyright: ignore[reportUnusedFunction]
    store: Any,
    artifact_store: Any,
    *,
    task_id: str,
    step_id: str,
    kind: str,
    payload: Any,
    metadata: dict[str, Any],
    task_context: Any,
    event_type: str | None,
    entity_id: str,
    entity_type: str = "step_attempt",
) -> str:
    from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
        _store_memory_artifact as store_memory_artifact_impl,  # pyright: ignore[reportPrivateUsage]
    )

    return store_memory_artifact_impl(
        store,
        artifact_store,
        task_id=task_id,
        step_id=step_id,
        kind=kind,
        payload=payload,
        metadata=metadata,
        task_context=task_context,
        event_type=event_type,
        entity_id=entity_id,
        entity_type=entity_type,
    )


def _consolidate_category_entries(  # pyright: ignore[reportUnusedFunction]
    category: str, entries: list[MemoryEntry]
) -> list[MemoryEntry]:
    consolidated: list[MemoryEntry] = []
    for entry in sorted(
        entries,
        key=lambda item: (item.updated_at, item.created_at, item.score, item.confidence),
        reverse=True,
    ):
        merged = False
        for existing in consolidated:
            if not _should_merge_entries(existing, entry):
                continue
            existing.score = max(existing.score, entry.score)
            existing.confidence = max(existing.confidence, entry.confidence)
            existing.updated_at = max(
                existing.updated_at or existing.created_at,
                entry.updated_at or entry.created_at,
                entry.created_at,
            )
            if entry.content != existing.content and entry.content not in existing.supersedes:
                existing.supersedes.append(entry.content)
            for value in entry.supersedes:
                if value not in existing.supersedes:
                    existing.supersedes.append(value)
            merged = True
            break
        if not merged:
            consolidated.append(entry)
    return consolidated


def _should_merge_entries(left: MemoryEntry, right: MemoryEntry) -> bool:
    if left.category != right.category:
        return False
    if is_duplicate([left], right.content):
        return True
    return shares_topic(left.content, right.content)


def _infer_confidence(content: str) -> float:  # pyright: ignore[reportUnusedFunction]
    strong_signal = tuple(tr_list_all_locales("kernel.nlp.confidence.strong_signal"))
    if any(signal in content for signal in strong_signal):
        return 0.8
    if len(content) >= 20:
        return 0.65
    return 0.55


def _local_should_checkpoint(messages: list[dict[str, Any]]) -> tuple[bool, str]:  # pyright: ignore[reportUnusedFunction]
    user_text = _collect_role_text(messages, "user")
    assistant_text = _collect_role_text(messages, "assistant")
    transcript = _local_format_transcript(messages)
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


def _pending_messages(  # pyright: ignore[reportUnusedFunction]
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


def _mark_messages_processed(state_file: Path, session_id: str, count: int) -> None:  # pyright: ignore[reportUnusedFunction]
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


def _clear_session_progress(state_file: Path, session_id: str) -> None:  # pyright: ignore[reportUnusedFunction]
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


def _local_format_transcript(messages: list[dict[str, Any]]) -> str:
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


def _parse_json(text: str) -> Any:  # pyright: ignore[reportUnusedFunction]
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        for suffix in ("}", "]}", "]}"):
            try:
                return json.loads(cleaned + suffix)
            except json.JSONDecodeError:
                continue
        log.warning("memory_json_parse_failed", text=text[:200])
        return None


def _bump_session_index(state_file: Path) -> int:  # pyright: ignore[reportUnusedFunction]
    store = JsonStore(
        state_file,
        default={"session_index": 0, "sessions": {}},
        cross_process=True,
    )
    try:
        with store.update() as data:
            idx = data.get("session_index", 0) + 1
            data["session_index"] = idx
        return idx
    except Exception:
        log.warning("session_state_update_failed")
        return 1


# ---------------------------------------------------------------------------
# Consolidation trigger
# ---------------------------------------------------------------------------

_CONSOLIDATION_THROTTLE_SECONDS = 3 * 3600  # run at most once every 3 hours
_CONSOLIDATION_THROTTLE_FILE = ".last_consolidation"


def _maybe_consolidate(settings: Any) -> None:
    """Run the memory consolidation dream cycle, throttled to avoid over-firing.

    Skips silently when:
    - ``settings.kernel_db_path`` is absent or falsy
    - A throttle file written by the previous run is recent enough
    """
    import time
    from pathlib import Path as _Path

    kernel_db_path = getattr(settings, "kernel_db_path", None)
    if not kernel_db_path:
        return

    memory_file = getattr(settings, "memory_file", None)
    if memory_file is None:
        return

    throttle_file = _Path(memory_file).parent / _CONSOLIDATION_THROTTLE_FILE
    if throttle_file.exists():
        try:
            last_run = float(throttle_file.read_text().strip())
            if (time.time() - last_run) < _CONSOLIDATION_THROTTLE_SECONDS:
                log.debug("memory_consolidation_throttled", throttle_file=str(throttle_file))
                return
        except Exception:
            pass  # corrupt throttle file → proceed with consolidation

    try:
        from hermit.kernel.ledger.journal.store import KernelStore
        from hermit.plugins.builtin.hooks.memory.services import get_services

        store = KernelStore(_Path(kernel_db_path))
        try:
            services = get_services(store)
            services.consolidation.run_consolidation(store)
        finally:
            store.close()

        throttle_file.write_text(str(time.time()))
        log.info("memory_consolidation_complete")
    except Exception:
        log.warning("memory_consolidation_failed", exc_info=True)
