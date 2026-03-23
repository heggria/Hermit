from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.hooks import (
    _bump_session_index,
    _clear_session_progress,
    _format_transcript,
    _infer_confidence,
    _inject_relevant_memory,
    _mark_messages_processed,
    _parse_json,
    _pending_messages,
    _should_checkpoint,
    _should_merge_entries,
)
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def test_memory_engine_save_and_load(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    source = {
        "user_preference": [
            MemoryEntry(
                category="user_preference",
                content="所有回答使用简体中文",
                score=8,
                locked=True,
                created_at=date(2026, 3, 9),
            )
        ]
    }

    engine.save(source)
    loaded = engine.load()

    assert loaded["user_preference"][0].content == "所有回答使用简体中文"
    assert loaded["user_preference"][0].locked is True


def test_memory_engine_loads_legacy_format_without_meta(tmp_path) -> None:
    path = tmp_path / "memories.md"
    path.write_text(
        "## user_preference\n- [2026-03-09] [s:8🔒] 所有回答使用简体中文\n",
        encoding="utf-8",
    )
    engine = MemoryEngine(path)
    loaded = engine.load()

    entry = loaded["user_preference"][0]
    assert entry.content == "所有回答使用简体中文"
    assert entry.confidence == 0.5
    assert entry.supersedes == []
    assert entry.updated_at == date(2026, 3, 9)


def test_memory_engine_roundtrips_meta_comment(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "project_convention": [
                MemoryEntry(
                    category="project_convention",
                    content="默认工作目录改为 /repo",
                    created_at=date(2026, 3, 9),
                    updated_at=date(2026, 3, 11),
                    confidence=0.82,
                    supersedes=["默认工作目录改为 /old"],
                )
            ]
        }
    )

    raw = path.read_text(encoding="utf-8")
    assert "<!--memory:" in raw
    loaded = engine.load()["project_convention"][0]
    assert loaded.updated_at == date(2026, 3, 11)
    assert loaded.confidence == 0.82
    assert loaded.supersedes == ["默认工作目录改为 /old"]


def test_memory_engine_retrieve_prefers_query_relevant_entries(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "project_convention": [
                MemoryEntry(
                    category="project_convention",
                    content="默认工作目录固定到 /repo",
                    score=8,
                    locked=True,
                ),
                MemoryEntry(category="project_convention", content="部署走 Render", score=5),
            ],
            "tooling_environment": [
                MemoryEntry(category="tooling_environment", content="服务端口改为 8080", score=6),
                MemoryEntry(
                    category="tooling_environment", content="默认使用 uv 管理依赖", score=6
                ),
            ],
        }
    )

    with patch("hermit.plugins.builtin.hooks.memory.engine.log.info") as log_mock:
        ranked = engine.retrieve("检查 /repo 项目的 8080 端口配置", limit=3)

    assert [entry.content for entry, _score in ranked[:2]] == [
        "默认工作目录固定到 /repo",
        "服务端口改为 8080",
    ]
    log_mock.assert_called_once()


# ── hooks helper tests ──────────────────────────────────


def test_format_transcript_handles_mixed_content() -> None:
    messages = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me search."},
                {"type": "tool_use", "name": "web_search", "input": {"query": "test"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "result data"},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Here you go."}]},
    ]
    result = _format_transcript(messages)
    assert "[User] Hello" in result
    assert "[Tool: web_search" in result
    assert "[Tool Result:" in result
    assert "[Assistant] Here you go." in result


def test_format_transcript_truncates_long_conversations() -> None:
    messages = [{"role": "user", "content": "x" * 800} for _ in range(30)]
    result = _format_transcript(messages)
    assert "[... conversation truncated ...]" in result


def test_parse_json_handles_clean_json() -> None:
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_strips_markdown_fences() -> None:
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_fixes_truncated_json() -> None:
    result = _parse_json('{"used_keywords": ["hello"]')
    assert result is not None
    assert result["used_keywords"] == ["hello"]


def test_parse_json_returns_none_on_garbage() -> None:
    assert _parse_json("not json at all") is None


def test_bump_session_index_initializes_and_increments(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    assert _bump_session_index(state_file) == 1
    assert _bump_session_index(state_file) == 2
    assert _bump_session_index(state_file) == 3
    data = json.loads(state_file.read_text())
    assert data["session_index"] == 3


def test_should_checkpoint_on_explicit_memory_signal() -> None:
    should_checkpoint, reason = _should_checkpoint(
        [
            {"role": "user", "content": "Remember this: always reply in Chinese from now on"},
            {"role": "assistant", "content": [{"type": "text", "text": "Got it."}]},
        ]
    )
    assert should_checkpoint is True
    assert reason == "explicit_memory_signal"


def test_should_checkpoint_on_batched_conversation() -> None:
    should_checkpoint, reason = _should_checkpoint(
        [
            {
                "role": "user",
                "content": "We decided to switch to /repo as the default working directory",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "OK, I will follow this convention."}],
            },
            {
                "role": "user",
                "content": "Also deploy via render, put env vars in .env, don't write them into docs.",
            },
        ]
    )
    assert should_checkpoint is True
    assert reason in {"decision_signal", "conversation_batch"}


def test_should_merge_entries_detects_shared_topic() -> None:
    left = MemoryEntry(category="project_convention", content="默认工作目录使用 /repo")
    right = MemoryEntry(category="project_convention", content="统一在 /repo 根目录执行命令")
    assert _should_merge_entries(left, right) is True


def test_infer_confidence_prefers_hard_constraints() -> None:
    assert _infer_confidence("Default working directory fixed to /repo, must execute") == 0.8
    assert (
        _infer_confidence(
            "Here is a longer general experience summary for future reference and reuse."
        )
        == 0.65
    )
    assert _infer_confidence("short note") == 0.55


def test_inject_relevant_memory_fails_closed_without_kernel_context(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "project_convention": [
                MemoryEntry(
                    category="project_convention",
                    content="默认工作目录固定到 /repo",
                    score=8,
                    locked=True,
                ),
            ]
        }
    )

    enriched = _inject_relevant_memory(engine, "请检查 /repo 的配置")
    plain = _inject_relevant_memory(engine, "帮我写一首诗")

    assert enriched == "请检查 /repo 的配置"
    assert plain == "帮我写一首诗"


def test_memory_engine_duplicate_detection_supports_substring_overlap() -> None:
    existing = [
        MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo 根目录")
    ]
    assert MemoryEngine._is_duplicate(existing, "工作目录固定到 /repo") is True


def test_memory_engine_parse_meta_handles_invalid_json() -> None:
    assert MemoryEngine._parse_meta("{bad json}") == {}


def test_memory_engine_resolve_supersedes_skips_non_override() -> None:
    existing = [MemoryEntry(category="project_convention", content="部署走 Render", score=5)]
    new_entry = MemoryEntry(category="project_convention", content="统一使用中文", score=5)

    MemoryEngine._resolve_supersedes(existing, new_entry)

    assert existing[0].score == 5
    assert new_entry.supersedes == []


def test_memory_engine_looks_like_override_covers_path_and_directional_terms() -> None:
    assert MemoryEngine._looks_like_override("默认工作目录为 /old", "默认工作目录改为 /new") is True
    assert (
        MemoryEngine._looks_like_override(
            "deploy method render", "switch to deploy method cloudrun"
        )
        is True
    )
    assert MemoryEngine._looks_like_override("部署走 Render", "统一使用中文") is False


def test_memory_engine_shares_topic_handles_empty_and_substring() -> None:
    assert MemoryEngine._shares_topic("", "abc") is False
    assert (
        MemoryEngine._shares_topic("默认工作目录固定到 /repo", "工作目录固定到 /repo 根目录")
        is True
    )


def test_memory_entry_post_init_clamps_updated_at_to_created_at() -> None:
    entry = MemoryEntry(
        category="project_convention",
        content="A",
        created_at=date(2026, 3, 11),
        updated_at=date(2026, 3, 9),
    )

    assert entry.updated_at == date(2026, 3, 11)


def test_pending_messages_tracks_processed_offsets(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": [{"type": "text", "text": "回复一"}]},
        {"role": "user", "content": "第二条"},
    ]

    pending, processed = _pending_messages(state_file, "s1", messages)
    assert processed == 0
    assert pending == messages

    _mark_messages_processed(state_file, "s1", 2)
    pending, processed = _pending_messages(state_file, "s1", messages)
    assert processed == 2
    assert pending == messages[2:]

    _clear_session_progress(state_file, "s1")
    pending, processed = _pending_messages(state_file, "s1", messages)
    assert processed == 0
    assert pending == messages
