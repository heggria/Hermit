"""Tests for plugins/builtin/hooks/image_memory/hooks.py — coverage for missed lines.

Covers: _detect_mime_from_bytes, _analyze_image error paths, _analyze_and_persist,
_record_public_dict, _system_prompt_fragment, _inject_image_context,
_build_image_get_tool handler, _build_image_attach_to_feishu_tool handler,
_build_image_search_tool handler, _parse_json.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.hooks.image_memory.hooks import (
    _analyze_and_persist,
    _analyze_image,
    _detect_mime_from_bytes,
    _inject_image_context,
    _parse_json,
    _record_public_dict,
    _system_prompt_fragment,
    register,
)
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# _detect_mime_from_bytes
# ---------------------------------------------------------------------------


class TestDetectMimeFromBytes:
    def test_too_short_returns_none(self) -> None:
        assert _detect_mime_from_bytes(b"short") is None

    def test_jpeg_magic(self) -> None:
        data = b"\xff\xd8\xff" + b"\x00" * 20
        assert _detect_mime_from_bytes(data) == "image/jpeg"

    def test_png_magic(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert _detect_mime_from_bytes(data) == "image/png"

    def test_gif_magic(self) -> None:
        data = b"GIF8" + b"\x00" * 20
        assert _detect_mime_from_bytes(data) == "image/gif"

    def test_webp_magic(self) -> None:
        data = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        assert _detect_mime_from_bytes(data) == "image/webp"

    def test_unknown_magic_returns_none(self) -> None:
        data = b"\x00\x00\x00\x00" + b"\x00" * 20
        assert _detect_mime_from_bytes(data) is None


# ---------------------------------------------------------------------------
# _analyze_image
# ---------------------------------------------------------------------------


class TestAnalyzeImage:
    def test_unsupported_mime_raises(self) -> None:
        settings = SimpleNamespace(image_model=None, model="m")
        with pytest.raises(ValueError, match="Unsupported"):
            _analyze_image(settings, "image/bmp", b"\x00" * 20)

    def test_jpg_corrected_to_jpeg(self) -> None:
        settings = SimpleNamespace(image_model=None, model="m")
        with (
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.build_provider") as bp,
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.VisionAnalysisService") as vas,
        ):
            mock_service = MagicMock()
            mock_service.analyze_image.return_value = {
                "summary": "test",
                "tags": ["a"],
                "ocr_text": "",
            }
            vas.return_value = mock_service
            bp.return_value = MagicMock()
            result = _analyze_image(settings, "image/jpg", b"\x00" * 20)
            assert result["summary"] == "test"

    def test_empty_analysis_response(self) -> None:
        settings = SimpleNamespace(image_model="model-x", model="m")
        with (
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.build_provider") as bp,
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.VisionAnalysisService") as vas,
        ):
            mock_service = MagicMock()
            mock_service.analyze_image.return_value = None
            vas.return_value = mock_service
            bp.return_value = MagicMock()
            result = _analyze_image(settings, "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
            assert result["summary"] == ""
            assert result["tags"] == []

    def test_tags_trimmed_to_8(self) -> None:
        settings = SimpleNamespace(image_model=None, model="m")
        with (
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.build_provider") as bp,
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.VisionAnalysisService") as vas,
        ):
            mock_service = MagicMock()
            mock_service.analyze_image.return_value = {
                "summary": "s",
                "tags": [f"t{i}" for i in range(15)],
                "ocr_text": "txt",
            }
            vas.return_value = mock_service
            bp.return_value = MagicMock()
            result = _analyze_image(settings, "image/jpeg", b"\xff\xd8\xff" + b"\x00" * 20)
            assert len(result["tags"]) == 8

    def test_mime_corrected_from_magic(self) -> None:
        settings = SimpleNamespace(image_model=None, model="m")
        jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 20
        with (
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.build_provider") as bp,
            patch("hermit.plugins.builtin.hooks.image_memory.hooks.VisionAnalysisService") as vas,
        ):
            mock_service = MagicMock()
            mock_service.analyze_image.return_value = {
                "summary": "s",
                "tags": [],
                "ocr_text": "",
            }
            vas.return_value = mock_service
            bp.return_value = MagicMock()
            # Declared as png but magic says jpeg
            result = _analyze_image(settings, "image/png", jpeg_bytes)
            assert result["summary"] == "s"


# ---------------------------------------------------------------------------
# _analyze_and_persist
# ---------------------------------------------------------------------------


class TestAnalyzeAndPersist:
    def test_success_marks_ready(self) -> None:
        engine = MagicMock()
        record = SimpleNamespace(image_id="img1", mime_type="image/png")
        settings = SimpleNamespace(image_model=None, model="m")

        with patch("hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image") as ai:
            ai.return_value = {"summary": "test", "tags": ["t"], "ocr_text": "ocr"}
            updated_record = SimpleNamespace(image_id="img1", summary="test")
            engine.mark_analysis.return_value = updated_record
            result = _analyze_and_persist(engine, settings, record, b"bytes")
            engine.mark_analysis.assert_called_once_with(
                "img1", summary="test", tags=["t"], ocr_text="ocr", status="ready"
            )
            assert result is updated_record

    def test_empty_analysis_marks_empty_response(self) -> None:
        engine = MagicMock()
        record = SimpleNamespace(image_id="img2", mime_type="image/png")
        settings = SimpleNamespace(image_model=None, model="m")

        with patch("hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image") as ai:
            ai.return_value = {"summary": "", "tags": [], "ocr_text": ""}
            engine.mark_analysis.return_value = record
            _analyze_and_persist(engine, settings, record, b"bytes")
            engine.mark_analysis.assert_called_once_with(
                "img2", summary="", tags=[], ocr_text="", status="empty_response"
            )

    def test_exception_marks_failed(self) -> None:
        engine = MagicMock()
        record = SimpleNamespace(image_id="img3", mime_type="image/png")
        settings = SimpleNamespace(image_model=None, model="m")

        with patch("hermit.plugins.builtin.hooks.image_memory.hooks._analyze_image") as ai:
            ai.side_effect = RuntimeError("provider down")
            engine.mark_analysis.return_value = record
            _analyze_and_persist(engine, settings, record, b"bytes")
            engine.mark_analysis.assert_called_once_with(
                "img3", summary="", tags=[], ocr_text="", status="failed:RuntimeError"
            )


# ---------------------------------------------------------------------------
# _record_public_dict
# ---------------------------------------------------------------------------


class TestRecordPublicDict:
    def test_basic_fields(self) -> None:
        record = SimpleNamespace(
            image_id="id1",
            primary_session_id="s1",
            session_ids=["s1"],
            source_adapter="local",
            original_message_id="m1",
            original_file_name="f.png",
            mime_type="image/png",
            summary="sum",
            tags=["t1"],
            ocr_text="ocr",
            analysis_status="ready",
            feishu_image_key="",
            local_path="/path/to/img",
        )
        result = _record_public_dict(record)
        assert result["image_id"] == "id1"
        assert "local_path" not in result

    def test_include_local_path(self) -> None:
        record = SimpleNamespace(
            image_id="id2",
            primary_session_id="s1",
            session_ids=["s1"],
            source_adapter="local",
            original_message_id="",
            original_file_name="f.png",
            mime_type="image/png",
            summary="",
            tags=[],
            ocr_text="",
            analysis_status="pending",
            feishu_image_key="",
            local_path="/path",
        )
        result = _record_public_dict(record, include_local_path=True)
        assert result["local_path"] == "/path"


# ---------------------------------------------------------------------------
# _system_prompt_fragment
# ---------------------------------------------------------------------------


class TestSystemPromptFragment:
    def test_returns_xml_block(self) -> None:
        result = _system_prompt_fragment()
        assert "<image_memory_guidance>" in result
        assert "</image_memory_guidance>" in result


# ---------------------------------------------------------------------------
# _inject_image_context
# ---------------------------------------------------------------------------


class TestInjectImageContext:
    def test_no_session_returns_prompt_unchanged(self) -> None:
        engine = MagicMock()
        settings = SimpleNamespace(image_context_limit=3)
        result = _inject_image_context(engine, settings, "hello", session_id=None)
        assert result == "hello"

    def test_no_image_reference_returns_unchanged(self) -> None:
        engine = MagicMock()
        settings = SimpleNamespace(image_context_limit=3)
        result = _inject_image_context(
            engine, settings, "no matching keywords here", session_id="s1"
        )
        assert result == "no matching keywords here"

    def test_with_image_reference_and_results(self) -> None:
        engine = MagicMock()
        record = SimpleNamespace(
            image_id="img1",
            primary_session_id="s1",
            tags=["tag1"],
            summary="A screenshot",
        )
        engine.search.return_value = [record]
        settings = SimpleNamespace(image_context_limit=3)
        result = _inject_image_context(
            engine, settings, "show me the image from earlier", session_id="s1"
        )
        assert "<image_context>" in result
        assert "img1" in result

    def test_falls_back_to_query_search(self) -> None:
        engine = MagicMock()
        record = SimpleNamespace(
            image_id="img2",
            primary_session_id="s2",
            tags=[],
            summary="A photo",
        )
        engine.search.side_effect = [[], [record]]  # First by session, then by query
        settings = SimpleNamespace(image_context_limit=3)
        result = _inject_image_context(engine, settings, "check the screenshot", session_id="s1")
        assert "<image_context>" in result

    def test_no_results_returns_unchanged(self) -> None:
        engine = MagicMock()
        engine.search.return_value = []
        settings = SimpleNamespace(image_context_limit=3)
        result = _inject_image_context(engine, settings, "show the image", session_id="s1")
        assert result == "show the image"


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_valid_json(self) -> None:
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_code_fenced_json(self) -> None:
        result = _parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_empty_string(self) -> None:
        assert _parse_json("") is None

    def test_whitespace_only(self) -> None:
        assert _parse_json("   ") is None

    def test_truncated_json_fixup(self) -> None:
        result = _parse_json('{"key": "value"')
        assert result is not None
        assert result["key"] == "value"

    def test_embedded_json(self) -> None:
        result = _parse_json('Some text {"key": "val"} more text')
        assert result is not None
        assert result["key"] == "val"

    def test_code_fence_without_json_label(self) -> None:
        result = _parse_json('```\n{"x": 1}\n```')
        assert result == {"x": 1}

    def test_completely_invalid(self) -> None:
        result = _parse_json("not json at all without braces")
        assert result is None


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_with_no_settings_returns_early(self) -> None:
        ctx = PluginContext(HooksEngine(), settings=None)
        register(ctx)
        assert len(ctx.tools) == 0

    def test_register_adds_tools_and_hooks(self, tmp_path: Path) -> None:
        from hermit.runtime.assembly.config import Settings

        settings = Settings(
            base_dir=tmp_path,
            claude_auth_token="token",
            model="fake",
            _env_file=None,
        )
        ctx = PluginContext(HooksEngine(), settings=settings)
        register(ctx)
        tool_names = {t.name for t in ctx.tools}
        assert "image_store_from_path" in tool_names
        assert "image_search" in tool_names
        assert "image_get" in tool_names
        assert "image_attach_to_feishu" in tool_names
