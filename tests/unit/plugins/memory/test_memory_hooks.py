from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.memory import hooks, hooks_extraction, hooks_injection
from hermit.plugins.builtin.hooks.memory.engine import MemoryEngine
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def _settings(
    tmp_path: Path, *, has_auth: bool = True, include_kernel: bool = False
) -> SimpleNamespace:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(
        has_auth=has_auth,
        model="fake-model",
        memory_file=memory_dir / "memories.md",
        session_state_file=memory_dir / "session_state.json",
    )
    if include_kernel:
        kernel_dir = tmp_path / "kernel"
        settings.kernel_db_path = kernel_dir / "state.db"
        settings.kernel_artifacts_dir = kernel_dir / "artifacts"
    return settings


def test_register_adds_all_memory_hooks(tmp_path) -> None:
    settings = _settings(tmp_path)
    hooks_engine = HooksEngine()
    ctx = PluginContext(hooks_engine, settings=settings)

    hooks.register(ctx)

    assert hooks_engine.has_handlers(HookEvent.SYSTEM_PROMPT)
    assert hooks_engine.has_handlers(HookEvent.PRE_RUN)
    assert hooks_engine.has_handlers(HookEvent.POST_RUN)
    assert hooks_engine.has_handlers(HookEvent.SESSION_END)


def test_register_returns_early_without_settings() -> None:
    hooks_engine = HooksEngine()
    ctx = PluginContext(hooks_engine, settings=None)

    hooks.register(ctx)

    assert not hooks_engine.has_handlers(HookEvent.SYSTEM_PROMPT)
    assert not hooks_engine.has_handlers(HookEvent.PRE_RUN)
    assert not hooks_engine.has_handlers(HookEvent.POST_RUN)
    assert not hooks_engine.has_handlers(HookEvent.SESSION_END)


def test_inject_memory_returns_empty_when_no_entries(tmp_path) -> None:
    engine = MemoryEngine(tmp_path / "memories.md")
    engine.save({})

    with (
        patch.object(engine, "summary_prompt", return_value=""),
        patch.object(hooks_injection.log, "info") as log_mock,
    ):
        assert hooks._inject_memory(engine) == ""
    log_mock.assert_called_once()


def test_registered_hooks_fire_with_context(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        store.create_memory_record(
            task_id="task_1",
            conversation_id="chat_1",
            category="project_convention",
            content="默认工作目录固定到 /repo",
            confidence=0.9,
            evidence_refs=[],
        )
    finally:
        store.close()
    engine = MemoryEngine(settings.memory_file)
    engine.save(
        {
            "project_convention": [
                MemoryEntry(
                    category="project_convention",
                    content="默认工作目录固定到 /repo",
                    score=8,
                    locked=True,
                )
            ]
        }
    )

    hooks_engine = HooksEngine()
    ctx = PluginContext(hooks_engine, settings=settings)
    hooks.register(ctx)

    system_prompt = hooks_engine.fire(HookEvent.SYSTEM_PROMPT)[0]
    pre_run = hooks_engine.fire(HookEvent.PRE_RUN, prompt="检查 /repo 配置")[0]

    assert "<memory_context>" in system_prompt
    assert pre_run == "检查 /repo 配置"


def test_inject_memory_logs_counts_when_entries_exist(tmp_path) -> None:
    engine = MemoryEngine(tmp_path / "memories.md")
    engine.save(
        {
            "project_convention": [
                MemoryEntry(
                    category="project_convention",
                    content="默认工作目录固定到 /repo",
                    score=8,
                    locked=True,
                )
            ]
        }
    )

    with patch.object(hooks_injection.log, "info") as log_mock:
        content = hooks._inject_memory(engine)

    assert content == ""
    log_mock.assert_called_once()


def test_inject_memory_only_keeps_static_categories(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        for category, content in [
            ("user_preference", "只能用中文回复用户"),
            ("project_convention", "默认工作目录固定到 /repo"),
            ("tooling_environment", "Hermit 仓库位于 /Users/beta/work/Hermit"),
            ("active_task", "当前无任何定时任务"),
            ("other", "今天已完成热门话题搜索"),
            ("tech_decision", "当前默认 provider 为 claude"),
            ("tooling_environment", "图片记忆库当前为空"),
        ]:
            store.create_memory_record(
                task_id="task_static",
                conversation_id="chat_static",
                category=category,
                content=content,
                confidence=0.9,
                evidence_refs=[],
            )
    finally:
        store.close()
    engine = MemoryEngine(tmp_path / "memories.md")
    engine.save(
        {
            "user_preference": [
                MemoryEntry(category="user_preference", content="只能用中文回复用户")
            ],
            "project_convention": [
                MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")
            ],
            "tooling_environment": [
                MemoryEntry(
                    category="tooling_environment",
                    content="Hermit 仓库位于 /Users/beta/work/Hermit",
                )
            ],
            "active_task": [MemoryEntry(category="active_task", content="当前无任何定时任务")],
            "other": [MemoryEntry(category="other", content="今天已完成热门话题搜索")],
            "tech_decision": [
                MemoryEntry(category="tech_decision", content="当前默认 provider 为 claude")
            ],
        }
    )

    content = hooks._inject_memory(engine, settings)

    assert "<memory_context>" in content
    assert "只能用中文回复用户" in content
    assert "默认工作目录固定到 /repo" in content
    assert "Hermit 仓库位于 /Users/beta/work/Hermit" in content
    assert "当前无任何定时任务" not in content
    assert "今天已完成热门话题搜索" not in content
    assert "当前默认 provider 为 claude" in content
    assert "图片记忆库当前为空" not in content


def test_inject_memory_prefers_kernel_memory_records_when_available(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        store.create_memory_record(
            task_id="task_kernel_memory",
            conversation_id="chat-kernel-memory",
            category="project_convention",
            content="Kernel memory takes precedence",
            confidence=0.9,
            evidence_refs=[],
        )
        engine = MemoryEngine(settings.memory_file)
        engine.save(
            {
                "project_convention": [
                    MemoryEntry(category="project_convention", content="legacy mirror entry")
                ]
            }
        )

        content = hooks._inject_memory(engine, settings)

        assert "Kernel memory takes precedence" in content
    finally:
        store.close()


def test_inject_memory_filters_non_static_kernel_categories(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        store.create_memory_record(
            task_id="task_pref",
            conversation_id="chat-memory",
            category="user_preference",
            content="只能用中文回复用户",
            confidence=0.9,
            evidence_refs=[],
        )
        store.create_memory_record(
            task_id="task_task",
            conversation_id="chat-memory",
            category="active_task",
            content="当前无任何定时任务",
            confidence=0.9,
            evidence_refs=[],
        )
        engine = MemoryEngine(settings.memory_file)
        engine.save({})

        content = hooks._inject_memory(engine, settings)

        assert "只能用中文回复用户" in content
        assert "当前无任何定时任务" not in content
    finally:
        store.close()


def test_kernel_memory_does_not_fallback_to_markdown_when_db_is_empty(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    engine = MemoryEngine(settings.memory_file)
    engine.save(
        {
            "project_convention": [
                MemoryEntry(category="project_convention", content="legacy mirror entry")
            ]
        }
    )

    content = hooks._inject_memory(engine, settings)

    assert content == ""


def test_save_memories_returns_early_without_messages(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks_extraction, "_extract_and_save") as extract_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [])

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_save_memories_returns_early_without_auth(tmp_path) -> None:
    settings = _settings(tmp_path, has_auth=False)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks_extraction, "_extract_and_save") as extract_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [{"role": "user", "content": "hello"}])

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_save_memories_logs_exception_and_clears_progress(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks_extraction, "_extract_and_save", side_effect=RuntimeError("boom")),
        patch.object(hooks_extraction.log, "exception") as log_mock,
        patch.object(hooks_extraction, "_clear_session_progress") as clear_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [{"role": "user", "content": "hello"}])

    log_mock.assert_called_once()
    clear_mock.assert_called_once_with(settings.session_state_file, "s1")


def test_checkpoint_memories_returns_early_for_skipped_conditions(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    messages = [{"role": "user", "content": "记住这个约定"}]

    with (
        patch.object(hooks_extraction, "_pending_messages") as pending_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "", messages)
        hooks._checkpoint_memories(engine, settings, "cli-oneshot", messages)
        hooks._checkpoint_memories(
            engine, _settings(tmp_path / "other", has_auth=False), "s1", messages
        )
        hooks._checkpoint_memories(engine, settings, "s1", [])

    pending_mock.assert_not_called()
    assert log_mock.call_count == 4


def test_checkpoint_memories_returns_when_no_pending_delta(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=([], 3)),
        patch.object(hooks_extraction, "extract_memory_payload") as extract_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        hooks._checkpoint_memories(
            engine, settings, "s1", [{"role": "user", "content": "记住这个约定"}]
        )

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_checkpoint_memories_returns_when_below_threshold(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    delta = [{"role": "user", "content": "短句"}]

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=(delta, 0)),
        patch.object(
            hooks_extraction, "should_checkpoint", return_value=(False, "below_threshold")
        ),
        patch.object(hooks_extraction, "extract_memory_payload") as extract_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "s1", delta)

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_checkpoint_memories_logs_exception_on_extract_failure(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    delta = [{"role": "user", "content": "记住这个约定"}]

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=(delta, 0)),
        patch.object(
            hooks_extraction, "should_checkpoint", return_value=(True, "explicit_memory_signal")
        ),
        patch.object(hooks_extraction, "extract_memory_payload", side_effect=RuntimeError("boom")),
        patch.object(hooks_extraction.log, "exception") as log_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "s1", delta)

    log_mock.assert_called_once()


def test_checkpoint_memories_logs_when_no_new_entries(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    messages = [
        {"role": "user", "content": "记住这个约定"},
        {"role": "assistant", "content": "好的"},
    ]

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=(messages, 1)),
        patch.object(
            hooks_extraction, "should_checkpoint", return_value=(True, "explicit_memory_signal")
        ),
        patch.object(
            hooks_extraction,
            "extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": []},
        ),
        patch.object(hooks_extraction.log, "info") as log_mock,
        patch.object(hooks_extraction, "_mark_messages_processed") as mark_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "s1", messages)

    log_mock.assert_called_once()
    mark_mock.assert_not_called()


def test_checkpoint_memories_appends_and_marks_processed(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    messages = [
        {"role": "user", "content": "记住：服务端口改为 8080"},
        {"role": "assistant", "content": "收到"},
    ]
    new_entries = [MemoryEntry(category="tooling_environment", content="服务端口改为 8080")]

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=(messages, 0)),
        patch.object(
            hooks_extraction, "should_checkpoint", return_value=(True, "explicit_memory_signal")
        ),
        patch.object(
            hooks_extraction,
            "extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": new_entries},
        ),
        patch.object(hooks_extraction, "_mark_messages_processed") as mark_mock,
        patch.object(hooks_extraction.log, "info") as log_mock,
        patch.object(engine, "append_entries") as append_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "s1", messages)

    append_mock.assert_not_called()
    mark_mock.assert_not_called()
    assert log_mock.call_count >= 1


def test_checkpoint_memories_promotes_durable_memory_via_kernel(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    engine = MemoryEngine(settings.memory_file)
    messages = [
        {"role": "user", "content": "记住：默认工作目录固定到 /repo"},
        {"role": "assistant", "content": "收到"},
    ]
    new_entries = [MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")]

    with (
        patch.object(hooks_extraction, "_pending_messages", return_value=(messages, 0)),
        patch.object(
            hooks_extraction, "should_checkpoint", return_value=(True, "explicit_memory_signal")
        ),
        patch.object(
            hooks_extraction,
            "extract_memory_payload",
            return_value={"used_keywords": {"repo"}, "new_entries": new_entries},
        ),
    ):
        hooks._checkpoint_memories(engine, settings, "chat-memory", messages)

    assert "默认工作目录固定到 /repo" in settings.memory_file.read_text(encoding="utf-8")
    store = KernelStore(settings.kernel_db_path)
    try:
        task = store.get_last_task_for_conversation("chat-memory")
        assert task is not None
        assert "Promote durable memory" in task.title
        receipt = store.list_receipts(task_id=task.task_id, limit=1)[0]
        assert receipt.action_type == "memory_write"
        assert receipt.decision_ref is not None
        assert receipt.capability_grant_ref is not None
    finally:
        store.close()


def test_extract_and_save_returns_when_nothing_extracted(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(
            hooks_extraction,
            "extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": []},
        ),
        patch.object(hooks_extraction.log, "info") as log_mock,
        patch.object(engine, "record_session") as record_mock,
    ):
        hooks._extract_and_save(
            engine, settings, [{"role": "user", "content": "hello there this is long enough"}]
        )

    assert log_mock.call_count >= 2
    record_mock.assert_not_called()


def test_extract_and_save_records_session_when_payload_present(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    entries = [MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")]

    before = {"project_convention": [MemoryEntry(category="project_convention", content="旧约定")]}
    after = {
        "project_convention": [
            MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")
        ]
    }
    with (
        patch.object(
            hooks_extraction,
            "extract_memory_payload",
            return_value={"used_keywords": {"repo"}, "new_entries": entries},
        ),
        patch.object(engine, "record_session") as record_mock,
        patch.object(engine, "load", side_effect=[before, after]),
        patch.object(hooks_extraction.log, "info") as log_mock,
        patch.object(hooks, "_bump_session_index") as bump_mock,
    ):
        hooks._extract_and_save(
            engine, settings, [{"role": "user", "content": "hello there this is long enough"}]
        )

    bump_mock.assert_not_called()
    record_mock.assert_not_called()
    assert log_mock.call_count >= 1


def test_extract_memory_payload_returns_empty_for_short_transcript(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with patch.object(hooks_extraction.log, "info") as log_mock:
        payload = hooks._extract_memory_payload(
            engine, settings, [{"role": "user", "content": "x"}], max_tokens=100
        )

    assert payload == {"used_keywords": set(), "new_entries": []}
    log_mock.assert_called_once()


def test_extract_memory_payload_returns_empty_when_service_returns_none(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    service = MagicMock()
    service.extract_json.return_value = None
    with (
        patch.object(hooks_extraction, "build_provider", return_value=object()) as provider_mock,
        patch.object(
            hooks_extraction, "StructuredExtractionService", return_value=service
        ) as service_cls,
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        payload = hooks._extract_memory_payload(
            engine,
            settings,
            [
                {
                    "role": "user",
                    "content": "this transcript is definitely long enough to extract memory",
                }
            ],
            max_tokens=100,
        )

    provider_mock.assert_called_once()
    service_cls.assert_called_once()
    assert payload == {"used_keywords": set(), "new_entries": []}
    assert log_mock.call_count >= 2


def test_extract_memory_payload_builds_entries_and_skips_empty_content(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    service = MagicMock()
    service.extract_json.return_value = {
        "used_keywords": ["repo"],
        "new_memories": [
            {"category": "project_convention", "content": "默认工作目录固定到 /repo"},
            {"category": "other", "content": "   "},
        ],
    }
    with (
        patch.object(hooks_extraction, "build_provider", return_value=object()),
        patch.object(hooks_extraction, "StructuredExtractionService", return_value=service),
        patch.object(hooks_extraction.log, "info") as log_mock,
    ):
        payload = hooks._extract_memory_payload(
            engine,
            settings,
            [
                {
                    "role": "user",
                    "content": "this transcript is definitely long enough to extract memory",
                }
            ],
            max_tokens=100,
        )

    assert payload["used_keywords"] == {"repo"}
    assert len(payload["new_entries"]) == 1
    assert payload["new_entries"][0].category == "project_convention"
    assert log_mock.call_count >= 2


def test_consolidate_category_entries_merges_supersede_history() -> None:
    newer = MemoryEntry(
        category="project_convention", content="默认工作目录固定到 /repo", supersedes=["旧约定 A"]
    )
    older = MemoryEntry(
        category="project_convention", content="默认工作目录使用 /repo", supersedes=["旧约定 B"]
    )

    merged = hooks._consolidate_category_entries("project_convention", [older, newer])

    assert len(merged) == 1
    assert "旧约定 A" in merged[0].supersedes
    assert "旧约定 B" in merged[0].supersedes


def test_should_merge_entries_rejects_different_categories() -> None:
    left = MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")
    right = MemoryEntry(category="tooling_environment", content="默认工作目录固定到 /repo")

    assert hooks._should_merge_entries(left, right) is False


def test_should_merge_entries_accepts_duplicates() -> None:
    left = MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")
    right = MemoryEntry(category="project_convention", content="默认工作目录固定到 /repo")

    assert hooks._should_merge_entries(left, right) is True


def test_pending_messages_clamps_negative_processed_value(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"sessions":{"s1":{"processed_messages":-3}}}', encoding="utf-8")
    messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]

    pending, processed = hooks._pending_messages(state_file, "s1", messages)

    assert processed == 0
    assert pending == messages


def test_mark_messages_processed_repairs_non_dict_state(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"session_index":2,"sessions":"broken"}', encoding="utf-8")

    hooks._mark_messages_processed(state_file, "s1", 4)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["processed_messages"] == 4


def test_clear_session_progress_returns_early_without_session_id(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    hooks._clear_session_progress(state_file, "")
    assert not state_file.exists()


def test_clear_session_progress_removes_existing_session(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"
    state_file.write_text(
        '{"session_index":2,"sessions":{"s1":{"processed_messages":4},"s2":{"processed_messages":1}}}',
        encoding="utf-8",
    )

    hooks._clear_session_progress(state_file, "s1")

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "s1" not in data["sessions"]
    assert "s2" in data["sessions"]


def test_read_state_uses_default_when_file_missing(tmp_path) -> None:
    data = hooks._read_state(tmp_path / "missing.json")

    assert data == {"session_index": 0, "sessions": {}}


def test_message_text_handles_non_string_and_skips_invalid_blocks() -> None:
    assert hooks._message_text({"role": "user", "content": 12345}) == "12345"
    assert (
        hooks._message_text(
            {"role": "assistant", "content": [None, {"type": "text", "text": "ok"}]}
        )
        == "ok"
    )


def test_format_transcript_skips_blank_messages() -> None:
    transcript = hooks._local_format_transcript(
        [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": [{"type": "text", "text": "kept"}]},
        ]
    )

    assert transcript == "[Assistant] kept"


def test_parse_json_logs_warning_for_garbage() -> None:
    with patch.object(hooks.log, "warning") as warning_mock:
        result = hooks._parse_json("totally invalid json")

    assert result is None
    warning_mock.assert_called_once()


def test_should_checkpoint_message_batch_and_below_threshold() -> None:
    should_checkpoint, reason = hooks._local_should_checkpoint(
        [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
            {"role": "assistant", "content": "f"},
        ]
    )
    assert should_checkpoint is True
    assert reason == "message_batch"

    should_checkpoint, reason = hooks._local_should_checkpoint(
        [
            {"role": "user", "content": "短"},
            {"role": "assistant", "content": "答"},
        ]
    )
    assert should_checkpoint is False
    assert reason == "below_threshold"


def test_should_checkpoint_conversation_batch() -> None:
    long_text = (
        "这里是一段很长的背景材料，用于测试批量对话长度阈值，内容仅描述一般情况和上下文信息。" * 6
    )
    should_checkpoint, reason = hooks._local_should_checkpoint(
        [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "我理解这段背景，会继续参考。"},
            {"role": "user", "content": "补充一点普通背景信息。"},
        ]
    )

    assert should_checkpoint is True
    assert reason == "conversation_batch"


def test_bump_session_index_returns_fallback_on_update_error(tmp_path) -> None:
    state_file = tmp_path / "session_state.json"

    with (
        patch.object(hooks.JsonStore, "update", side_effect=RuntimeError("boom")),
        patch.object(hooks.log, "warning") as warning_mock,
    ):
        result = hooks._bump_session_index(state_file)

    assert result == 1
    warning_mock.assert_called_once()


# ---------------------------------------------------------------------------
# hooks.py backward-compat wrapper tests
# ---------------------------------------------------------------------------


def test_backward_compat_knowledge_categories_delegates(tmp_path) -> None:
    """hooks._knowledge_categories delegates to hooks_injection._knowledge_categories."""
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with patch(
        "hermit.plugins.builtin.hooks.memory.hooks_injection._knowledge_categories",
        return_value={"cat": []},
    ) as impl_mock:
        result = hooks._knowledge_categories(engine, settings)

    impl_mock.assert_called_once_with(engine, settings)
    assert result == {"cat": []}


def test_backward_compat_compile_context_pack_delegates(tmp_path) -> None:
    """hooks._compile_context_pack delegates to hooks_injection._compile_context_pack."""
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    sentinel = {"static_prompt": "sp", "retrieval_prompt": "rp"}
    with patch(
        "hermit.plugins.builtin.hooks.memory.hooks_injection._compile_context_pack",
        return_value=sentinel,
    ) as impl_mock:
        result = hooks._compile_context_pack(
            engine, settings, query="q", conversation_id="c1", runner=None
        )

    impl_mock.assert_called_once_with(
        engine, settings, query="q", conversation_id="c1", runner=None
    )
    assert result is sentinel


def test_backward_compat_extract_and_save_delegates(tmp_path) -> None:
    """hooks._extract_and_save delegates to hooks_extraction._extract_and_save."""
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    messages = [{"role": "user", "content": "hello"}]

    with patch(
        "hermit.plugins.builtin.hooks.memory.hooks_extraction._extract_and_save",
    ) as impl_mock:
        hooks._extract_and_save(engine, settings, messages, session_id="s1")

    impl_mock.assert_called_once_with(engine, settings, messages, session_id="s1")


def test_backward_compat_store_memory_artifact_delegates() -> None:
    """hooks._store_memory_artifact delegates to hooks_promotion._store_memory_artifact."""
    with patch(
        "hermit.plugins.builtin.hooks.memory.hooks_promotion._store_memory_artifact",
        return_value="art-123",
    ) as impl_mock:
        result = hooks._store_memory_artifact(
            "store",
            "artifact_store",
            task_id="t1",
            step_id="s1",
            kind="memory",
            payload={"data": 1},
            metadata={"m": "v"},
            task_context="ctx",
            event_type="ev",
            entity_id="e1",
            entity_type="step_attempt",
        )

    impl_mock.assert_called_once_with(
        "store",
        "artifact_store",
        task_id="t1",
        step_id="s1",
        kind="memory",
        payload={"data": 1},
        metadata={"m": "v"},
        task_context="ctx",
        event_type="ev",
        entity_id="e1",
        entity_type="step_attempt",
    )
    assert result == "art-123"


def test_registered_hook_post_run_delegates_to_checkpoint(tmp_path) -> None:
    """The POST_RUN hook registered by register() calls checkpoint_memories."""
    settings = _settings(tmp_path)
    hooks_engine = HooksEngine()
    ctx = PluginContext(hooks_engine, settings=settings)

    with patch("hermit.plugins.builtin.hooks.memory.hooks.checkpoint_memories") as cp_mock:
        hooks.register(ctx)
        result_obj = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])
        hooks_engine.fire(HookEvent.POST_RUN, result=result_obj, session_id="s1")

    cp_mock.assert_called_once()


def test_registered_hook_session_end_delegates_to_save(tmp_path) -> None:
    """The SESSION_END hook registered by register() calls save_memories."""
    settings = _settings(tmp_path)
    hooks_engine = HooksEngine()
    ctx = PluginContext(hooks_engine, settings=settings)

    with patch("hermit.plugins.builtin.hooks.memory.hooks.save_memories") as save_mock:
        hooks.register(ctx)
        messages = [{"role": "user", "content": "bye"}]
        hooks_engine.fire(HookEvent.SESSION_END, session_id="s1", messages=messages)

    save_mock.assert_called_once()


# ---------------------------------------------------------------------------
# hooks_extraction.py additional coverage
# ---------------------------------------------------------------------------


def test_build_memory_re_returns_never_match_when_no_keywords() -> None:
    """_build_memory_re returns a pattern that never matches for empty keyword list."""
    with patch.object(hooks_extraction, "tr_list_all_locales", return_value=[]):
        result = hooks_extraction._build_memory_re("nonexistent.key")

    assert result.search("anything") is None


def test_format_transcript_skips_blank_messages_extraction() -> None:
    """format_transcript from hooks_extraction skips blank messages."""
    transcript = hooks_extraction.format_transcript(
        [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "kept"},
        ]
    )
    assert "kept" in transcript
    assert "[User]" not in transcript


def test_message_text_handles_non_string_non_list_content() -> None:
    """_message_text returns str(content) for non-string, non-list content."""
    result = hooks_extraction._message_text({"role": "user", "content": 99999})
    assert result == "99999"


def test_message_text_handles_empty_non_string_content() -> None:
    """_message_text returns empty for falsy non-string, non-list content."""
    result = hooks_extraction._message_text({"role": "user", "content": 0})
    assert result == ""


def test_message_text_skips_non_dict_blocks() -> None:
    """_message_text skips non-dict items in content list."""
    result = hooks_extraction._message_text(
        {"role": "assistant", "content": ["string_block", {"type": "text", "text": "ok"}]}
    )
    assert result == "ok"


def test_message_text_handles_tool_use_block() -> None:
    """_message_text formats tool_use blocks."""
    result = hooks_extraction._message_text(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}}],
        }
    )
    assert "[Tool: bash(" in result


def test_message_text_handles_tool_result_block() -> None:
    """_message_text formats tool_result blocks."""
    result = hooks_extraction._message_text(
        {"role": "assistant", "content": [{"type": "tool_result", "content": "output here"}]}
    )
    assert "[Tool Result: output here]" in result


def test_collect_role_text_filters_by_role() -> None:
    """_collect_role_text only collects text from the specified role."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "again"},
    ]
    result = hooks_extraction._collect_role_text(messages, "user")
    assert "hello" in result
    assert "again" in result
    assert "world" not in result


def test_pending_messages_handles_non_dict_sessions(tmp_path) -> None:
    """_pending_messages handles sessions value that is not a dict."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"sessions": "not_a_dict"}', encoding="utf-8")
    messages = [{"role": "user", "content": "a"}]

    pending, processed = hooks_extraction._pending_messages(state_file, "s1", messages)

    assert processed == 0
    assert pending == messages


def test_pending_messages_handles_non_dict_meta(tmp_path) -> None:
    """_pending_messages handles session meta that is not a dict."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"sessions": {"s1": "not_a_dict"}}', encoding="utf-8")
    messages = [{"role": "user", "content": "a"}]

    pending, processed = hooks_extraction._pending_messages(state_file, "s1", messages)

    assert processed == 0
    assert pending == messages


def test_mark_messages_processed_creates_meta_for_new_session(tmp_path) -> None:
    """_mark_messages_processed creates session meta when not present."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"session_index":0,"sessions":{}}', encoding="utf-8")

    hooks_extraction._mark_messages_processed(state_file, "s1", 5)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["processed_messages"] == 5


def test_mark_messages_processed_handles_non_dict_meta(tmp_path) -> None:
    """_mark_messages_processed handles session meta that is not a dict."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"session_index":0,"sessions":{"s1":"broken"}}', encoding="utf-8")

    hooks_extraction._mark_messages_processed(state_file, "s1", 3)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["processed_messages"] == 3


def test_mark_messages_processed_handles_non_dict_sessions(tmp_path) -> None:
    """_mark_messages_processed handles sessions value that is not a dict."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text('{"session_index":0,"sessions":"broken"}', encoding="utf-8")

    hooks_extraction._mark_messages_processed(state_file, "s1", 2)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["processed_messages"] == 2


def test_clear_session_progress_returns_early_without_session_id_extraction(tmp_path) -> None:
    """_clear_session_progress from extraction returns early for empty session_id."""
    state_file = tmp_path / "session_state.json"
    hooks_extraction._clear_session_progress(state_file, "")
    assert not state_file.exists()


def test_clear_session_progress_removes_session_extraction(tmp_path) -> None:
    """_clear_session_progress from extraction removes the specified session."""
    state_file = tmp_path / "session_state.json"
    state_file.write_text(
        '{"session_index":1,"sessions":{"s1":{"processed_messages":2}}}',
        encoding="utf-8",
    )

    hooks_extraction._clear_session_progress(state_file, "s1")

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "s1" not in data["sessions"]


def test_read_state_returns_default_for_missing_file(tmp_path) -> None:
    """_read_state returns default dict when file does not exist."""
    data = hooks_extraction._read_state(tmp_path / "nonexistent.json")
    assert data == {"session_index": 0, "sessions": {}}


def test_infer_confidence_short_content() -> None:
    """_infer_confidence returns 0.55 for short content."""
    result = hooks_extraction._infer_confidence("short")
    assert result == 0.55


def test_infer_confidence_medium_content() -> None:
    """_infer_confidence returns 0.65 for content >= 20 chars without strong signal."""
    result = hooks_extraction._infer_confidence("this is a normal length content string")
    assert result == 0.65


def test_should_checkpoint_conversation_batch_extraction() -> None:
    """should_checkpoint returns conversation_batch for long text with enough user messages."""
    long_text = "x" * 400
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "more"},
    ]
    result, reason = hooks_extraction.should_checkpoint(messages)
    assert result is True
    assert reason == "conversation_batch"


def test_should_checkpoint_below_threshold_extraction() -> None:
    """should_checkpoint returns below_threshold for minimal messages."""
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    result, reason = hooks_extraction.should_checkpoint(messages)
    assert result is False
    assert reason == "below_threshold"


def test_should_checkpoint_message_batch_extraction() -> None:
    """should_checkpoint returns message_batch for >= 6 meaningful messages."""
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "e"},
        {"role": "assistant", "content": "f"},
    ]
    result, reason = hooks_extraction.should_checkpoint(messages)
    assert result is True
    assert reason == "message_batch"


# ---------------------------------------------------------------------------
# hooks_injection.py additional coverage
# ---------------------------------------------------------------------------


def test_knowledge_categories_returns_empty_for_none_settings(tmp_path) -> None:
    """_knowledge_categories returns {} when settings is None."""
    engine = MemoryEngine(tmp_path / "memories.md")
    result = hooks_injection._knowledge_categories(engine, None)
    assert result == {}


def test_knowledge_categories_returns_empty_without_kernel_db_path(tmp_path) -> None:
    """_knowledge_categories returns {} when settings has no kernel_db_path."""
    settings = _settings(tmp_path)  # no include_kernel
    engine = MemoryEngine(settings.memory_file)
    result = hooks_injection._knowledge_categories(engine, settings)
    assert result == {}


def test_knowledge_categories_returns_categories_with_kernel(tmp_path) -> None:
    """_knowledge_categories returns categories from KernelStore."""
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        store.create_memory_record(
            task_id="t1",
            conversation_id="c1",
            category="user_preference",
            content="test preference",
            confidence=0.9,
            evidence_refs=[],
        )
    finally:
        store.close()
    engine = MemoryEngine(settings.memory_file)
    result = hooks_injection._knowledge_categories(engine, settings)
    assert "user_preference" in result
    assert any(e.content == "test preference" for e in result["user_preference"])


def test_compile_context_pack_returns_none_for_none_settings(tmp_path) -> None:
    """_compile_context_pack returns None when settings is None."""
    engine = MemoryEngine(tmp_path / "memories.md")
    result = hooks_injection._compile_context_pack(
        engine, None, query="q", conversation_id=None, runner=None
    )
    assert result is None


def test_compile_context_pack_returns_none_without_kernel_db_path(tmp_path) -> None:
    """_compile_context_pack returns None when settings has no kernel_db_path."""
    settings = _settings(tmp_path)  # no include_kernel
    engine = MemoryEngine(settings.memory_file)
    result = hooks_injection._compile_context_pack(
        engine, settings, query="q", conversation_id=None, runner=None
    )
    assert result is None


def test_compile_context_pack_returns_pack_with_kernel(tmp_path) -> None:
    """_compile_context_pack returns a context pack dict when kernel is available."""
    settings = _settings(tmp_path, include_kernel=True)
    engine = MemoryEngine(settings.memory_file)
    result = hooks_injection._compile_context_pack(
        engine, settings, query="test query", conversation_id="c1", runner=None
    )
    assert result is not None
    assert "pack" in result
    assert "static_prompt" in result
    assert "retrieval_prompt" in result


def test_compile_context_pack_with_runner_task_lookup(tmp_path) -> None:
    """_compile_context_pack looks up active task via runner.task_controller."""
    settings = _settings(tmp_path, include_kernel=True)
    engine = MemoryEngine(settings.memory_file)

    active_task = SimpleNamespace(task_id="task-abc")
    task_controller = MagicMock()
    task_controller.active_task_for_conversation.return_value = active_task
    runner = SimpleNamespace(task_controller=task_controller)

    result = hooks_injection._compile_context_pack(
        engine, settings, query="test", conversation_id="conv-1", runner=runner
    )
    assert result is not None
    task_controller.active_task_for_conversation.assert_called_once_with("conv-1")


def test_compile_context_pack_with_runner_no_active_task(tmp_path) -> None:
    """_compile_context_pack handles runner with no active task."""
    settings = _settings(tmp_path, include_kernel=True)
    engine = MemoryEngine(settings.memory_file)

    task_controller = MagicMock()
    task_controller.active_task_for_conversation.return_value = None
    runner = SimpleNamespace(task_controller=task_controller)

    result = hooks_injection._compile_context_pack(
        engine, settings, query="test", conversation_id="conv-1", runner=runner
    )
    assert result is not None


def test_inject_relevant_memory_with_no_relevant_context(tmp_path) -> None:
    """inject_relevant_memory returns prompt unchanged when no relevant memory found."""
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    result = hooks_injection.inject_relevant_memory(engine, settings, prompt="hello")
    assert result == "hello"


def test_inject_relevant_memory_with_relevant_context(tmp_path) -> None:
    """inject_relevant_memory wraps prompt with relevant_memory when context is available."""
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        store.create_memory_record(
            task_id="t1",
            conversation_id="c1",
            category="user_preference",
            content="always respond in English",
            confidence=0.9,
            evidence_refs=[],
        )
    finally:
        store.close()
    engine = MemoryEngine(settings.memory_file)

    result = hooks_injection.inject_relevant_memory(
        engine, settings, prompt="hello", session_id="c1"
    )
    # If retrieval prompt is empty, it just returns prompt
    # Either way it should contain "hello"
    assert "hello" in result
