from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from hermit.builtin.memory.engine import MemoryEngine, group_entries
from hermit.builtin.memory.hooks import (
    _bump_session_index,
    _clear_session_progress,
    _consolidate_category_entries,
    _format_transcript,
    _infer_confidence,
    _inject_relevant_memory,
    _mark_messages_processed,
    _parse_json,
    _pending_messages,
    _should_checkpoint,
    _should_merge_entries,
)
from hermit.builtin.memory.types import MemoryEntry


def test_memory_engine_save_and_load(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    source = {
        "用户偏好": [
            MemoryEntry(
                category="用户偏好",
                content="所有回答使用简体中文",
                score=8,
                locked=True,
                created_at=date(2026, 3, 9),
            )
        ]
    }

    engine.save(source)
    loaded = engine.load()

    assert loaded["用户偏好"][0].content == "所有回答使用简体中文"
    assert loaded["用户偏好"][0].locked is True


def test_memory_engine_loads_legacy_format_without_meta(tmp_path) -> None:
    path = tmp_path / "memories.md"
    path.write_text(
        "## 用户偏好\n- [2026-03-09] [s:8🔒] 所有回答使用简体中文\n",
        encoding="utf-8",
    )
    engine = MemoryEngine(path)
    loaded = engine.load()

    entry = loaded["用户偏好"][0]
    assert entry.content == "所有回答使用简体中文"
    assert entry.confidence == 0.5
    assert entry.supersedes == []
    assert entry.updated_at == date(2026, 3, 9)


def test_memory_engine_roundtrips_meta_comment(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(
                    category="项目约定",
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
    loaded = engine.load()["项目约定"][0]
    assert loaded.updated_at == date(2026, 3, 11)
    assert loaded.confidence == 0.82
    assert loaded.supersedes == ["默认工作目录改为 /old"]


def test_memory_engine_record_session_applies_decay_and_reference_boost(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "技术决策": [
                MemoryEntry(category="技术决策", content="SQLite 适合 Agent 记忆", score=5),
                MemoryEntry(category="技术决策", content="ChromaDB 对 MVP 过重", score=5),
            ]
        }
    )

    updated = engine.record_session(
        new_entries=[],
        used_keywords={"SQLite"},
        session_index=1,
    )

    assert updated["技术决策"][0].score == 6
    assert updated["技术决策"][1].score == 4


def test_memory_engine_record_session_keeps_locked_entries_and_locks_at_threshold(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="固定规则", score=8, locked=True),
            ],
            "技术决策": [
                MemoryEntry(category="技术决策", content="SQLite 方案", score=6, locked=False),
            ],
        }
    )

    updated = engine.record_session(
        new_entries=[],
        used_keywords={"SQLite"},
        session_index=1,
    )

    assert updated["项目约定"][0].score == 8
    assert updated["项目约定"][0].locked is True
    assert updated["技术决策"][0].score == 7
    assert updated["技术决策"][0].locked is True


def test_memory_engine_record_session_appends_unique_new_entry(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save({})

    updated = engine.record_session(
        new_entries=[MemoryEntry(category="环境与工具", content="默认使用 uv 管理依赖")],
        session_index=1,
    )

    assert [entry.content for entry in updated["环境与工具"]] == ["默认使用 uv 管理依赖"]


def test_memory_engine_prevents_substring_duplicates(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="H5Bridge 需兼容客户端参数"),
            ]
        }
    )

    updated = engine.record_session(
        new_entries=[MemoryEntry(category="项目约定", content="H5Bridge 需兼容客户端参数")],
        session_index=1,
    )

    assert len(updated["项目约定"]) == 1


def test_memory_engine_append_entries_only_adds_new_items(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "用户偏好": [
                MemoryEntry(category="用户偏好", content="统一使用中文", score=8, locked=True),
            ]
        }
    )

    updated = engine.append_entries([
        MemoryEntry(category="用户偏好", content="统一使用中文"),
        MemoryEntry(category="项目约定", content="默认在仓库根目录执行命令"),
    ])

    assert len(updated["用户偏好"]) == 1
    assert [entry.content for entry in updated["项目约定"]] == ["默认在仓库根目录执行命令"]


def test_memory_engine_append_entries_supersedes_old_version(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "环境与工具": [
                MemoryEntry(category="环境与工具", content="服务端口为 3000", score=5),
            ]
        }
    )

    with patch("hermit.builtin.memory.engine.log.info") as log_mock:
        updated = engine.append_entries([
            MemoryEntry(category="环境与工具", content="服务端口改为 8080", confidence=0.8),
        ])

    entries = updated["环境与工具"]
    assert len(entries) == 1
    assert entries[0].content == "服务端口改为 8080"
    assert entries[0].supersedes == ["服务端口为 3000"]
    log_mock.assert_called()


def test_memory_engine_uses_merge_function_when_threshold_exceeded(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "其他": [MemoryEntry(category="其他", content=f"entry-{index}", score=5) for index in range(9)]
        }
    )

    merged = engine.record_session(
        new_entries=[],
        session_index=1,
        merge_threshold=8,
        merge_fn=lambda category, entries: [MemoryEntry(category=category, content="merged", score=6)],
    )

    assert [entry.content for entry in merged["其他"]] == ["merged"]


def test_memory_engine_record_session_merges_similar_entries(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="默认工作目录使用 /repo", score=5),
                MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo", score=5),
                MemoryEntry(category="项目约定", content="统一在 /repo 根目录执行命令", score=5),
                MemoryEntry(category="项目约定", content="部署走 Render", score=5),
                MemoryEntry(category="项目约定", content="环境变量写入 .env", score=5),
                MemoryEntry(category="项目约定", content="不要把 secrets 写进文档", score=5),
                MemoryEntry(category="项目约定", content="统一使用中文", score=5),
            ]
        }
    )

    merged = engine.record_session(
        new_entries=[],
        session_index=1,
        merge_threshold=6,
        merge_fn=_consolidate_category_entries,
    )

    repo_entries = [entry for entry in merged["项目约定"] if "/repo" in entry.content]
    assert len(repo_entries) == 1
    assert "默认工作目录" in repo_entries[0].content or "/repo" in repo_entries[0].content


def test_memory_engine_retrieve_prefers_query_relevant_entries(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True),
                MemoryEntry(category="项目约定", content="部署走 Render", score=5),
            ],
            "环境与工具": [
                MemoryEntry(category="环境与工具", content="服务端口改为 8080", score=6),
                MemoryEntry(category="环境与工具", content="默认使用 uv 管理依赖", score=6),
            ],
        }
    )

    with patch("hermit.builtin.memory.engine.log.info") as log_mock:
        ranked = engine.retrieve("检查 /repo 项目的 8080 端口配置", limit=3)

    assert [entry.content for entry, _score in ranked[:2]] == [
        "默认工作目录固定到 /repo",
        "服务端口改为 8080",
    ]
    log_mock.assert_called_once()


def test_memory_engine_retrieval_prompt_respects_budget(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True),
                MemoryEntry(category="项目约定", content="统一在 /repo 根目录执行所有命令", score=7, locked=True),
            ],
            "环境与工具": [
                MemoryEntry(category="环境与工具", content="服务端口改为 8080", score=6),
            ],
        }
    )

    with patch("hermit.builtin.memory.engine.log.info") as log_mock:
        prompt = engine.retrieval_prompt("处理 /repo 的 8080 配置", limit=5, char_budget=80)

    assert "以下是与当前任务最相关的跨会话记忆" in prompt
    assert "服务端口改为 8080" not in prompt
    assert log_mock.call_count == 2


def test_memory_engine_retrieval_prompt_can_break_on_heading_budget(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    categories = {
        "项目约定": [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True)]
    }

    with patch("hermit.builtin.memory.engine.log.info") as log_mock:
        prompt = engine.retrieval_prompt("处理 /repo", categories=categories, limit=5, char_budget=30)

    assert "## 项目约定" not in prompt
    assert log_mock.call_count == 2


def test_memory_engine_retrieve_returns_empty_for_non_informative_query(tmp_path) -> None:
    engine = MemoryEngine(tmp_path / "memories.md")
    with patch("hermit.builtin.memory.engine.log.info") as log_mock:
        assert engine.retrieval_prompt("?!", limit=3) == ""
    log_mock.assert_called_once()


# ── hooks helper tests ──────────────────────────────────


def test_format_transcript_handles_mixed_content() -> None:
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me search."},
            {"type": "tool_use", "name": "web_search", "input": {"query": "test"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "result data"},
        ]},
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
    should_checkpoint, reason = _should_checkpoint([
        {"role": "user", "content": "记住：以后统一用中文回复"},
        {"role": "assistant", "content": [{"type": "text", "text": "收到"}]},
    ])
    assert should_checkpoint is True
    assert reason == "explicit_memory_signal"


def test_should_checkpoint_on_batched_conversation() -> None:
    should_checkpoint, reason = _should_checkpoint([
        {"role": "user", "content": "我们把默认工作目录固定到 /repo"},
        {"role": "assistant", "content": [{"type": "text", "text": "好的，我会按这个约定执行。"}]},
        {"role": "user", "content": "另外部署走 render，环境变量放 .env，不写进文档。"},
    ])
    assert should_checkpoint is True
    assert reason in {"decision_signal", "conversation_batch"}


def test_should_merge_entries_detects_shared_topic() -> None:
    left = MemoryEntry(category="项目约定", content="默认工作目录使用 /repo")
    right = MemoryEntry(category="项目约定", content="统一在 /repo 根目录执行命令")
    assert _should_merge_entries(left, right) is True


def test_infer_confidence_prefers_hard_constraints() -> None:
    assert _infer_confidence("默认工作目录固定到 /repo，必须执行") == 0.8
    assert _infer_confidence("这里有一条比较长的普通经验总结，用于后续参考和复用。") == 0.65
    assert _infer_confidence("简短备注") == 0.55


def test_inject_relevant_memory_only_when_query_matches(tmp_path) -> None:
    path = tmp_path / "memories.md"
    engine = MemoryEngine(path)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True),
            ]
        }
    )

    enriched = _inject_relevant_memory(engine, "请检查 /repo 的配置")
    plain = _inject_relevant_memory(engine, "帮我写一首诗")

    assert "<relevant_memory>" in enriched
    assert plain == "帮我写一首诗"


def test_memory_engine_duplicate_detection_supports_substring_overlap() -> None:
    existing = [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo 根目录")]
    assert MemoryEngine._is_duplicate(existing, "工作目录固定到 /repo") is True


def test_memory_engine_parse_meta_handles_invalid_json() -> None:
    assert MemoryEngine._parse_meta("{bad json}") == {}


def test_memory_engine_resolve_supersedes_skips_non_override() -> None:
    existing = [MemoryEntry(category="项目约定", content="部署走 Render", score=5)]
    new_entry = MemoryEntry(category="项目约定", content="统一使用中文", score=5)

    MemoryEngine._resolve_supersedes(existing, new_entry)

    assert existing[0].score == 5
    assert new_entry.supersedes == []


def test_memory_engine_looks_like_override_covers_path_and_directional_terms() -> None:
    assert MemoryEngine._looks_like_override("默认工作目录为 /old", "默认工作目录改为 /new") is True
    assert MemoryEngine._looks_like_override("部署方式 render", "现在部署方式 cloudrun") is True
    assert MemoryEngine._looks_like_override("部署走 Render", "统一使用中文") is False


def test_memory_engine_shares_topic_handles_empty_and_substring() -> None:
    assert MemoryEngine._shares_topic("", "abc") is False
    assert MemoryEngine._shares_topic("默认工作目录固定到 /repo", "工作目录固定到 /repo 根目录") is True


def test_group_entries_groups_by_category() -> None:
    grouped = group_entries([
        MemoryEntry(category="项目约定", content="A"),
        MemoryEntry(category="环境与工具", content="B"),
    ])

    assert sorted(grouped) == ["环境与工具", "项目约定"]


def test_memory_entry_post_init_clamps_updated_at_to_created_at() -> None:
    entry = MemoryEntry(
        category="项目约定",
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
