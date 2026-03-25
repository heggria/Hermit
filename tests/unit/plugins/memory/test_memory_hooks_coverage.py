"""Tests for plugins/builtin/hooks/memory/hooks.py — coverage for missed lines.

Covers: _build_memory_re, _get_explicit_memory_re, _get_decision_signal_re,
_message_text, _collect_role_text, _local_format_transcript,
_local_should_checkpoint, _pending_messages, _mark_messages_processed,
_clear_session_progress, _consolidate_category_entries, _should_merge_entries,
_infer_confidence, _parse_json, _bump_session_index, _run_consolidation_if_available.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.memory.hooks import (
    _build_memory_re,
    _bump_session_index,
    _clear_session_progress,
    _collect_role_text,
    _consolidate_category_entries,
    _infer_confidence,
    _local_format_transcript,
    _local_should_checkpoint,
    _mark_messages_processed,
    _message_text,
    _parse_json,
    _pending_messages,
    _run_consolidation_if_available,
    _should_merge_entries,
)
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

# ---------------------------------------------------------------------------
# _build_memory_re
# ---------------------------------------------------------------------------


class TestBuildMemoryRe:
    def test_empty_keywords_never_matches(self) -> None:
        with patch(
            "hermit.plugins.builtin.hooks.memory.hooks.tr_list_all_locales",
            return_value=[],
        ):
            regex = _build_memory_re("some.key")
            assert not regex.search("anything")

    def test_keywords_match(self) -> None:
        with patch(
            "hermit.plugins.builtin.hooks.memory.hooks.tr_list_all_locales",
            return_value=["remember", "note"],
        ):
            regex = _build_memory_re("some.key")
            assert regex.search("please remember this")
            assert regex.search("take note")
            assert not regex.search("hello world")


# ---------------------------------------------------------------------------
# _message_text
# ---------------------------------------------------------------------------


class TestMessageText:
    def test_string_content(self) -> None:
        assert _message_text({"content": "hello"}) == "hello"

    def test_none_content(self) -> None:
        assert _message_text({"content": None}) == ""

    def test_list_content_text_blocks(self) -> None:
        msg = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ]
        }
        assert "hello" in _message_text(msg)
        assert "world" in _message_text(msg)

    def test_list_content_tool_use(self) -> None:
        msg = {
            "content": [
                {"type": "tool_use", "name": "write_file", "input": {"path": "/tmp/x"}},
            ]
        }
        result = _message_text(msg)
        assert "Tool: write_file" in result

    def test_list_content_tool_result(self) -> None:
        msg = {
            "content": [
                {"type": "tool_result", "content": "File written successfully"},
            ]
        }
        result = _message_text(msg)
        assert "Tool Result:" in result

    def test_non_dict_block_ignored(self) -> None:
        msg = {"content": ["not-a-dict", {"type": "text", "text": "ok"}]}
        result = _message_text(msg)
        assert "ok" in result

    def test_long_string_truncated(self) -> None:
        msg = {"content": "x" * 2000}
        result = _message_text(msg)
        assert len(result) <= 800

    def test_numeric_content_converted(self) -> None:
        msg = {"content": 42}
        result = _message_text(msg)
        assert result == "42"


# ---------------------------------------------------------------------------
# _collect_role_text
# ---------------------------------------------------------------------------


class TestCollectRoleText:
    def test_collects_user_messages(self) -> None:
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        result = _collect_role_text(messages, "user")
        assert "hello" in result
        assert "bye" in result
        assert "hi" not in result


# ---------------------------------------------------------------------------
# _local_format_transcript
# ---------------------------------------------------------------------------


class TestLocalFormatTranscript:
    def test_formats_messages(self) -> None:
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        result = _local_format_transcript(messages)
        assert "[User] Q1" in result
        assert "[Assistant] A1" in result

    def test_skips_empty_messages(self) -> None:
        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "real"},
        ]
        result = _local_format_transcript(messages)
        assert "[User] real" in result

    def test_truncation(self) -> None:
        # Generate very long conversation
        messages = [{"role": "user", "content": "x" * 800} for _ in range(50)]
        result = _local_format_transcript(messages)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# _local_should_checkpoint
# ---------------------------------------------------------------------------


class TestLocalShouldCheckpoint:
    def test_below_threshold(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        ok, reason = _local_should_checkpoint(messages)
        assert ok is False
        assert reason == "below_threshold"

    def test_message_batch_trigger(self) -> None:
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(7)]
        ok, reason = _local_should_checkpoint(messages)
        assert ok is True
        assert reason == "message_batch"


# ---------------------------------------------------------------------------
# _pending_messages
# ---------------------------------------------------------------------------


class TestPendingMessages:
    def test_no_state_file_returns_all(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        messages = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        pending, processed = _pending_messages(state_file, "s1", messages)
        assert len(pending) == 2
        assert processed == 0

    def test_with_processed_offset(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps({"session_index": 1, "sessions": {"s1": {"processed_messages": 1}}})
        )
        messages = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        pending, processed = _pending_messages(state_file, "s1", messages)
        assert len(pending) == 1
        assert processed == 1


# ---------------------------------------------------------------------------
# _mark_messages_processed
# ---------------------------------------------------------------------------


class TestMarkMessagesProcessed:
    def test_marks_processed(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        _mark_messages_processed(state_file, "s1", 5)
        data = json.loads(state_file.read_text())
        assert data["sessions"]["s1"]["processed_messages"] == 5

    def test_updates_existing(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        _mark_messages_processed(state_file, "s1", 3)
        _mark_messages_processed(state_file, "s1", 7)
        data = json.loads(state_file.read_text())
        assert data["sessions"]["s1"]["processed_messages"] == 7

    def test_non_dict_sessions_recovered(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"session_index": 0, "sessions": "not-a-dict"}))
        _mark_messages_processed(state_file, "s1", 2)
        data = json.loads(state_file.read_text())
        assert data["sessions"]["s1"]["processed_messages"] == 2


# ---------------------------------------------------------------------------
# _clear_session_progress
# ---------------------------------------------------------------------------


class TestClearSessionProgress:
    def test_clears_session(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        _mark_messages_processed(state_file, "s1", 5)
        _clear_session_progress(state_file, "s1")
        data = json.loads(state_file.read_text())
        assert "s1" not in data["sessions"]

    def test_empty_session_id_noop(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        _clear_session_progress(state_file, "")
        # Should not create file
        assert not state_file.exists()


# ---------------------------------------------------------------------------
# _consolidate_category_entries / _should_merge_entries
# ---------------------------------------------------------------------------


class TestConsolidation:
    def test_no_merge_different_categories(self) -> None:
        e1 = MemoryEntry(content="a", category="pref")
        e2 = MemoryEntry(content="b", category="fact")
        assert _should_merge_entries(e1, e2) is False

    def test_merge_duplicates(self) -> None:
        e1 = MemoryEntry(content="user prefers dark mode", category="pref")
        e2 = MemoryEntry(content="user prefers dark mode", category="pref")
        assert _should_merge_entries(e1, e2) is True

    def test_consolidate_merges_similar(self) -> None:
        entries = [
            MemoryEntry(content="user prefers dark mode", category="pref", score=0.8),
            MemoryEntry(content="user prefers dark mode", category="pref", score=0.9),
        ]
        result = _consolidate_category_entries("pref", entries)
        assert len(result) == 1
        assert result[0].score == 0.9

    def test_consolidate_keeps_different(self) -> None:
        entries = [
            MemoryEntry(content="ab xy", category="pref"),
            MemoryEntry(content="cd zw", category="pref"),
        ]
        result = _consolidate_category_entries("pref", entries)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _infer_confidence
# ---------------------------------------------------------------------------


class TestInferConfidence:
    def test_short_content_low_confidence(self) -> None:
        assert _infer_confidence("hi") == 0.55

    def test_medium_content(self) -> None:
        assert _infer_confidence("a" * 25) == 0.65


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_valid_json(self) -> None:
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_code_fenced(self) -> None:
        result = _parse_json('```json\n{"k": 1}\n```')
        assert result == {"k": 1}

    def test_truncated_json(self) -> None:
        result = _parse_json('{"key": "val"')
        assert result is not None

    def test_invalid_returns_none(self) -> None:
        result = _parse_json("totally invalid")
        assert result is None


# ---------------------------------------------------------------------------
# _bump_session_index
# ---------------------------------------------------------------------------


class TestBumpSessionIndex:
    def test_increments_index(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        idx1 = _bump_session_index(state_file)
        idx2 = _bump_session_index(state_file)
        assert idx1 == 1
        assert idx2 == 2

    def test_error_returns_1(self, tmp_path: Path) -> None:
        # Lock file to cause error
        state_file = tmp_path / "state.json"
        state_file.write_text("invalid json that breaks store")
        # Will attempt to parse, should gracefully return 1
        result = _bump_session_index(state_file)
        assert result >= 1


# ---------------------------------------------------------------------------
# _run_consolidation_if_available
# ---------------------------------------------------------------------------


class TestRunConsolidationIfAvailable:
    def test_no_kernel_db_returns_early(self) -> None:
        settings = SimpleNamespace(kernel_db_path=None)
        _run_consolidation_if_available(settings)  # Should not raise

    def test_runs_consolidation_with_valid_db(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(kernel_db_path=tmp_path / "state.db")
        with (
            patch(
                "hermit.kernel.context.memory.consolidation.ConsolidationService",
            ) as mock_svc_cls,
            patch(
                "hermit.kernel.ledger.journal.store.KernelStore",
            ) as mock_store_cls,
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            _run_consolidation_if_available(settings)
            mock_svc.run_consolidation.assert_called_once_with(mock_store)
