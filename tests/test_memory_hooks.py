from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.builtin.memory import hooks
from hermit.builtin.memory.engine import MemoryEngine
from hermit.builtin.memory.types import MemoryEntry
from hermit.kernel.store import KernelStore
from hermit.plugin.base import HookEvent, PluginContext
from hermit.plugin.hooks import HooksEngine


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
        patch.object(hooks.log, "info") as log_mock,
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
            category="项目约定",
            content="默认工作目录固定到 /repo",
            confidence=0.9,
            evidence_refs=[],
        )
    finally:
        store.close()
    engine = MemoryEngine(settings.memory_file)
    engine.save(
        {
            "项目约定": [
                MemoryEntry(
                    category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True
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
            "项目约定": [
                MemoryEntry(
                    category="项目约定", content="默认工作目录固定到 /repo", score=8, locked=True
                )
            ]
        }
    )

    with patch.object(hooks.log, "info") as log_mock:
        content = hooks._inject_memory(engine)

    assert content == ""
    log_mock.assert_called_once()


def test_inject_memory_only_keeps_static_categories(tmp_path) -> None:
    settings = _settings(tmp_path, include_kernel=True)
    store = KernelStore(settings.kernel_db_path)
    try:
        for category, content in [
            ("用户偏好", "只能用中文回复用户"),
            ("项目约定", "默认工作目录固定到 /repo"),
            ("工具与环境", "Hermit 仓库位于 /Users/beta/work/Hermit"),
            ("进行中的任务", "当前无任何定时任务"),
            ("其他", "今天已完成热门话题搜索"),
            ("技术决策", "当前默认 provider 为 claude"),
            ("环境与工具", "图片记忆库当前为空"),
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
            "用户偏好": [MemoryEntry(category="用户偏好", content="只能用中文回复用户")],
            "项目约定": [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")],
            "工具与环境": [
                MemoryEntry(
                    category="工具与环境", content="Hermit 仓库位于 /Users/beta/work/Hermit"
                )
            ],
            "进行中的任务": [MemoryEntry(category="进行中的任务", content="当前无任何定时任务")],
            "其他": [MemoryEntry(category="其他", content="今天已完成热门话题搜索")],
            "技术决策": [MemoryEntry(category="技术决策", content="当前默认 provider 为 claude")],
            "环境与工具": [MemoryEntry(category="环境与工具", content="图片记忆库当前为空")],
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
            category="项目约定",
            content="Kernel memory takes precedence",
            confidence=0.9,
            evidence_refs=[],
        )
        engine = MemoryEngine(settings.memory_file)
        engine.save({"项目约定": [MemoryEntry(category="项目约定", content="legacy mirror entry")]})

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
            category="用户偏好",
            content="只能用中文回复用户",
            confidence=0.9,
            evidence_refs=[],
        )
        store.create_memory_record(
            task_id="task_task",
            conversation_id="chat-memory",
            category="进行中的任务",
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
    engine.save({"项目约定": [MemoryEntry(category="项目约定", content="legacy mirror entry")]})

    content = hooks._inject_memory(engine, settings)

    assert content == ""


def test_save_memories_returns_early_without_messages(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks, "_extract_and_save") as extract_mock,
        patch.object(hooks.log, "info") as log_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [])

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_save_memories_returns_early_without_auth(tmp_path) -> None:
    settings = _settings(tmp_path, has_auth=False)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks, "_extract_and_save") as extract_mock,
        patch.object(hooks.log, "info") as log_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [{"role": "user", "content": "hello"}])

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_save_memories_logs_exception_and_clears_progress(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)

    with (
        patch.object(hooks, "_extract_and_save", side_effect=RuntimeError("boom")),
        patch.object(hooks.log, "exception") as log_mock,
        patch.object(hooks, "_clear_session_progress") as clear_mock,
    ):
        hooks._save_memories(engine, settings, "s1", [{"role": "user", "content": "hello"}])

    log_mock.assert_called_once()
    clear_mock.assert_called_once_with(settings.session_state_file, "s1")


def test_checkpoint_memories_returns_early_for_skipped_conditions(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    messages = [{"role": "user", "content": "记住这个约定"}]

    with (
        patch.object(hooks, "_pending_messages") as pending_mock,
        patch.object(hooks.log, "info") as log_mock,
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
        patch.object(hooks, "_pending_messages", return_value=([], 3)),
        patch.object(hooks, "_extract_memory_payload") as extract_mock,
        patch.object(hooks.log, "info") as log_mock,
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
        patch.object(hooks, "_pending_messages", return_value=(delta, 0)),
        patch.object(hooks, "_should_checkpoint", return_value=(False, "below_threshold")),
        patch.object(hooks, "_extract_memory_payload") as extract_mock,
        patch.object(hooks.log, "info") as log_mock,
    ):
        hooks._checkpoint_memories(engine, settings, "s1", delta)

    extract_mock.assert_not_called()
    log_mock.assert_called_once()


def test_checkpoint_memories_logs_exception_on_extract_failure(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = MemoryEngine(settings.memory_file)
    delta = [{"role": "user", "content": "记住这个约定"}]

    with (
        patch.object(hooks, "_pending_messages", return_value=(delta, 0)),
        patch.object(hooks, "_should_checkpoint", return_value=(True, "explicit_memory_signal")),
        patch.object(hooks, "_extract_memory_payload", side_effect=RuntimeError("boom")),
        patch.object(hooks.log, "exception") as log_mock,
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
        patch.object(hooks, "_pending_messages", return_value=(messages, 1)),
        patch.object(hooks, "_should_checkpoint", return_value=(True, "explicit_memory_signal")),
        patch.object(
            hooks,
            "_extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": []},
        ),
        patch.object(hooks.log, "info") as log_mock,
        patch.object(hooks, "_mark_messages_processed") as mark_mock,
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
    new_entries = [MemoryEntry(category="环境与工具", content="服务端口改为 8080")]

    with (
        patch.object(hooks, "_pending_messages", return_value=(messages, 0)),
        patch.object(hooks, "_should_checkpoint", return_value=(True, "explicit_memory_signal")),
        patch.object(
            hooks,
            "_extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": new_entries},
        ),
        patch.object(hooks, "_mark_messages_processed") as mark_mock,
        patch.object(hooks.log, "info") as log_mock,
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
    new_entries = [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")]

    with (
        patch.object(hooks, "_pending_messages", return_value=(messages, 0)),
        patch.object(hooks, "_should_checkpoint", return_value=(True, "explicit_memory_signal")),
        patch.object(
            hooks,
            "_extract_memory_payload",
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
            hooks,
            "_extract_memory_payload",
            return_value={"used_keywords": set(), "new_entries": []},
        ),
        patch.object(hooks.log, "info") as log_mock,
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
    entries = [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")]

    before = {"项目约定": [MemoryEntry(category="项目约定", content="旧约定")]}
    after = {"项目约定": [MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")]}
    with (
        patch.object(
            hooks,
            "_extract_memory_payload",
            return_value={"used_keywords": {"repo"}, "new_entries": entries},
        ),
        patch.object(engine, "record_session") as record_mock,
        patch.object(engine, "load", side_effect=[before, after]),
        patch.object(hooks.log, "info") as log_mock,
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

    with patch.object(hooks.log, "info") as log_mock:
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
        patch.object(hooks, "build_provider", return_value=object()) as provider_mock,
        patch.object(hooks, "StructuredExtractionService", return_value=service) as service_cls,
        patch.object(hooks.log, "info") as log_mock,
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
            {"category": "项目约定", "content": "默认工作目录固定到 /repo"},
            {"category": "其他", "content": "   "},
        ],
    }
    with (
        patch.object(hooks, "build_provider", return_value=object()),
        patch.object(hooks, "StructuredExtractionService", return_value=service),
        patch.object(hooks.log, "info") as log_mock,
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
    assert payload["new_entries"][0].category == "项目约定"
    assert log_mock.call_count >= 2


def test_consolidate_category_entries_merges_supersede_history() -> None:
    newer = MemoryEntry(
        category="项目约定", content="默认工作目录固定到 /repo", supersedes=["旧约定 A"]
    )
    older = MemoryEntry(
        category="项目约定", content="默认工作目录使用 /repo", supersedes=["旧约定 B"]
    )

    merged = hooks._consolidate_category_entries("项目约定", [older, newer])

    assert len(merged) == 1
    assert "旧约定 A" in merged[0].supersedes
    assert "旧约定 B" in merged[0].supersedes


def test_should_merge_entries_rejects_different_categories() -> None:
    left = MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")
    right = MemoryEntry(category="环境与工具", content="默认工作目录固定到 /repo")

    assert hooks._should_merge_entries(left, right) is False


def test_should_merge_entries_accepts_duplicates() -> None:
    left = MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")
    right = MemoryEntry(category="项目约定", content="默认工作目录固定到 /repo")

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
    transcript = hooks._format_transcript(
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
    should_checkpoint, reason = hooks._should_checkpoint(
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

    should_checkpoint, reason = hooks._should_checkpoint(
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
    should_checkpoint, reason = hooks._should_checkpoint(
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
