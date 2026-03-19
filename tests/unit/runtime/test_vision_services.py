"""Tests for vision_services.py — _parse_json_response, StructuredExtractionService, VisionAnalysisService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermit.runtime.provider_host.execution.vision_services import (
    StructuredExtractionService,
    VisionAnalysisService,
    _parse_json_response,
)
from hermit.runtime.provider_host.shared.contracts import (
    ProviderFeatures,
    ProviderResponse,
)


def _make_response(text: str) -> ProviderResponse:
    return ProviderResponse(content=[{"type": "text", "text": text}])


# ── _parse_json_response ──────────────────────────────────────────


def test_parse_json_response_valid_dict() -> None:
    resp = _make_response('{"key": "value", "num": 42}')
    result = _parse_json_response(resp)
    assert result == {"key": "value", "num": 42}


def test_parse_json_response_json_code_block() -> None:
    resp = _make_response('```json\n{"title": "hello"}\n```')
    result = _parse_json_response(resp)
    assert result == {"title": "hello"}


def test_parse_json_response_code_block_no_language() -> None:
    resp = _make_response('```\n{"a": 1}\n```')
    result = _parse_json_response(resp)
    assert result == {"a": 1}


def test_parse_json_response_empty_content() -> None:
    resp = ProviderResponse(content=[])
    result = _parse_json_response(resp)
    assert result is None


def test_parse_json_response_empty_text() -> None:
    resp = _make_response("")
    result = _parse_json_response(resp)
    assert result is None


def test_parse_json_response_non_dict_json_list() -> None:
    resp = _make_response("[1, 2, 3]")
    result = _parse_json_response(resp)
    assert result is None


def test_parse_json_response_non_dict_json_string() -> None:
    resp = _make_response('"just a string"')
    result = _parse_json_response(resp)
    assert result is None


def test_parse_json_response_truncated_missing_brace() -> None:
    resp = _make_response('{"key": "value"')
    result = _parse_json_response(resp)
    assert result == {"key": "value"}


def test_parse_json_response_truncated_missing_bracket_brace() -> None:
    resp = _make_response('{"items": ["a", "b"')
    result = _parse_json_response(resp)
    assert result == {"items": ["a", "b"]}


def test_parse_json_response_completely_unparseable() -> None:
    resp = _make_response("This is not JSON at all.")
    result = _parse_json_response(resp)
    assert result is None


def test_parse_json_response_text_before_json() -> None:
    resp = _make_response('Here is the result: {"answer": 42}')
    result = _parse_json_response(resp)
    assert result == {"answer": 42}


def test_parse_json_response_truncated_string_value() -> None:
    resp = _make_response('{"key": "incomplete')
    result = _parse_json_response(resp)
    assert result == {"key": "incomplete"}


def test_parse_json_response_whitespace_around_code_block() -> None:
    resp = _make_response('  ```json  \n  {"x": 1}  \n  ```  ')
    result = _parse_json_response(resp)
    assert result == {"x": 1}


def test_parse_json_response_no_brace_in_garbage() -> None:
    resp = _make_response("no json here, no braces either!")
    result = _parse_json_response(resp)
    assert result is None


# ── StructuredExtractionService ────────────────────────────────────


def test_structured_extraction_service_extract_json_valid() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response('{"result": "ok"}')
    svc = StructuredExtractionService(provider, model="test-model")

    result = svc.extract_json(system_prompt="Extract data", user_content="some content")

    assert result == {"result": "ok"}
    provider.generate.assert_called_once()
    call_args = provider.generate.call_args[1] if provider.generate.call_args[1] else {}
    request = provider.generate.call_args[0][0] if not call_args else call_args.get("request")
    assert request.model == "test-model"
    assert request.system_prompt == "Extract data"


def test_structured_extraction_service_extract_json_none_on_bad_response() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response("not json")
    svc = StructuredExtractionService(provider, model="m")

    result = svc.extract_json(system_prompt="sys", user_content="user")
    assert result is None


def test_structured_extraction_service_custom_max_tokens() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response('{"a": 1}')
    svc = StructuredExtractionService(provider, model="m")

    svc.extract_json(system_prompt="sys", user_content="user", max_tokens=512)

    # generate is called with request= keyword argument
    call_kwargs = provider.generate.call_args
    request = call_kwargs[1].get("request") or call_kwargs[0][0]
    assert request.max_tokens == 512


def test_structured_extraction_request_has_user_message() -> None:
    provider = MagicMock()
    provider.generate.return_value = _make_response('{"a": 1}')
    svc = StructuredExtractionService(provider, model="m")

    svc.extract_json(system_prompt="sys", user_content="my input")

    call_kwargs = provider.generate.call_args
    request = call_kwargs[1].get("request") or call_kwargs[0][0]
    assert request.messages == [{"role": "user", "content": "my input"}]


# ── VisionAnalysisService ──────────────────────────────────────────


def test_vision_analysis_service_analyze_image_valid() -> None:
    provider = MagicMock()
    provider.name = "test"
    provider.features = ProviderFeatures(supports_images=True)
    provider.generate.return_value = _make_response('{"description": "a cat"}')
    svc = VisionAnalysisService(provider, model="vision-model")

    result = svc.analyze_image(
        system_prompt="Describe image",
        text="What is this?",
        image_block={"type": "image", "source": {"data": "base64data"}},
    )

    assert result == {"description": "a cat"}
    provider.generate.assert_called_once()


def test_vision_analysis_service_no_image_support_raises() -> None:
    provider = MagicMock()
    provider.name = "no-vision"
    provider.features = ProviderFeatures(supports_images=False)
    svc = VisionAnalysisService(provider, model="m")

    with pytest.raises(RuntimeError, match="does not support image analysis"):
        svc.analyze_image(
            system_prompt="sys",
            text="text",
            image_block={"type": "image", "source": {}},
        )


def test_vision_analysis_service_returns_none_on_bad_response() -> None:
    provider = MagicMock()
    provider.name = "test"
    provider.features = ProviderFeatures(supports_images=True)
    provider.generate.return_value = _make_response("not json at all")
    svc = VisionAnalysisService(provider, model="m")

    result = svc.analyze_image(
        system_prompt="sys",
        text="text",
        image_block={"type": "image", "source": {}},
    )

    assert result is None


def test_vision_analysis_service_custom_max_tokens() -> None:
    provider = MagicMock()
    provider.name = "test"
    provider.features = ProviderFeatures(supports_images=True)
    provider.generate.return_value = _make_response('{"ok": true}')
    svc = VisionAnalysisService(provider, model="m")

    svc.analyze_image(
        system_prompt="sys",
        text="text",
        image_block={"type": "image", "source": {}},
        max_tokens=1024,
    )

    request = provider.generate.call_args[0][0]
    assert request.max_tokens == 1024


def test_vision_analysis_service_message_structure() -> None:
    provider = MagicMock()
    provider.name = "test"
    provider.features = ProviderFeatures(supports_images=True)
    provider.generate.return_value = _make_response('{"ok": true}')
    svc = VisionAnalysisService(provider, model="m")

    image_block = {"type": "image", "source": {"data": "abc"}}
    svc.analyze_image(
        system_prompt="sys",
        text="describe",
        image_block=image_block,
    )

    request = provider.generate.call_args[0][0]
    assert len(request.messages) == 1
    content = request.messages[0]["content"]
    assert content[0] == image_block
    assert content[1] == {"type": "text", "text": "describe"}
