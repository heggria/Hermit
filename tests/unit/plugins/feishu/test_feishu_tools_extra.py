"""Additional tests for Feishu tools to cover remaining uncovered branches.

Focuses on:
- Exception handling paths in tool handlers (generic Exception, not RuntimeError)
- Edge cases in validation
- register_tools entry point
- _all_tools list
- _readonly_feishu_tool and _mutating_feishu_tool helpers
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import hermit.plugins.builtin.adapters.feishu.tools as feishu_tools


def _ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class _Resp:
    def __init__(
        self,
        *,
        ok: bool = True,
        code: int = 0,
        msg: str = "ok",
        data: Any = None,
        raw: Any = None,
        log_id: str = "log-1",
    ) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data
        self.raw = raw
        self._log_id = log_id

    def success(self) -> bool:
        return self._ok

    def get_log_id(self) -> str:
        return self._log_id


# ---------------------------------------------------------------------------
# register_tools
# ---------------------------------------------------------------------------


def test_register_tools_adds_all_tools() -> None:
    ctx = MagicMock()
    feishu_tools.register_tools(ctx)
    assert ctx.add_tool.call_count == len(feishu_tools._all_tools())


def test_all_tools_returns_list_of_tool_specs() -> None:
    tools = feishu_tools._all_tools()
    assert len(tools) == 11
    names = {t.name for t in tools}
    assert "feishu_doc_create" in names
    assert "feishu_doc_read" in names
    assert "feishu_doc_append" in names
    assert "feishu_wiki_list" in names
    assert "feishu_wiki_create" in names
    assert "feishu_send_message" in names
    assert "feishu_bitable_query" in names
    assert "feishu_bitable_add" in names
    assert "feishu_sheet_read" in names
    assert "feishu_sheet_write" in names
    assert "feishu_calendar_create" in names


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_err_without_code() -> None:
    result = feishu_tools._err("some error")
    assert result == {"success": False, "error": "some error"}
    assert "code" not in result
    assert "hint" not in result


def test_err_with_non_permission_code() -> None:
    result = feishu_tools._err("some error", 12345)
    assert result == {"success": False, "error": "some error", "code": 12345}
    assert "hint" not in result


@pytest.mark.parametrize("code", [99991663, 91403, 1061004, 131006])
def test_err_with_permission_codes(code: int) -> None:
    result = feishu_tools._err("denied", code)
    assert result["code"] == code
    assert "hint" in result
    assert "Permission denied" in result["hint"]


# ---------------------------------------------------------------------------
# Tool spec properties
# ---------------------------------------------------------------------------


def test_readonly_tool_has_correct_properties() -> None:
    tool = feishu_tools._build_doc_read_tool()
    assert tool.readonly is True
    assert tool.action_class == "network_read"
    assert tool.idempotent is True
    assert tool.risk_hint == "low"
    assert tool.requires_receipt is False


def test_mutating_tool_has_correct_properties() -> None:
    tool = feishu_tools._build_doc_create_tool()
    assert tool.action_class == "credentialed_api_call"
    assert tool.risk_hint == "high"
    assert tool.requires_receipt is True


# ---------------------------------------------------------------------------
# Tool validation edge cases
# ---------------------------------------------------------------------------


def test_send_message_missing_receive_id() -> None:
    result = feishu_tools._build_send_message_tool().handler({"text": "hello"})
    assert result == {"success": False, "error": "receive_id is required"}


def test_send_message_missing_text() -> None:
    result = feishu_tools._build_send_message_tool().handler({"receive_id": "oc_1"})
    assert result == {"success": False, "error": "text is required"}


def test_bitable_query_missing_app_token() -> None:
    result = feishu_tools._build_bitable_query_tool().handler({"table_id": "tbl_1"})
    assert "app_token is required" in result["error"]


def test_bitable_query_missing_table_id() -> None:
    result = feishu_tools._build_bitable_query_tool().handler({"app_token": "app_1"})
    assert "table_id is required" in result["error"]


def test_bitable_add_missing_app_token() -> None:
    result = feishu_tools._build_bitable_add_tool().handler(
        {"table_id": "tbl_1", "fields": {"a": 1}}
    )
    assert "app_token is required" in result["error"]


def test_bitable_add_missing_table_id() -> None:
    result = feishu_tools._build_bitable_add_tool().handler(
        {"app_token": "app_1", "fields": {"a": 1}}
    )
    assert "table_id is required" in result["error"]


def test_bitable_add_empty_fields() -> None:
    result = feishu_tools._build_bitable_add_tool().handler(
        {"app_token": "app_1", "table_id": "tbl_1", "fields": {}}
    )
    assert "fields must be a non-empty object" in result["error"]


def test_sheet_read_missing_spreadsheet_token() -> None:
    result = feishu_tools._build_sheet_read_tool().handler({"range": "A1:B2"})
    assert "spreadsheet_token is required" in result["error"]


def test_sheet_read_missing_range() -> None:
    result = feishu_tools._build_sheet_read_tool().handler({"spreadsheet_token": "s1"})
    assert "range is required" in result["error"]


def test_sheet_write_missing_spreadsheet_token() -> None:
    result = feishu_tools._build_sheet_write_tool().handler({"range": "A1:B2", "values": [["a"]]})
    assert "spreadsheet_token is required" in result["error"]


def test_sheet_write_missing_range() -> None:
    result = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "s1", "values": [["a"]]}
    )
    assert "range is required" in result["error"]


def test_sheet_write_missing_values() -> None:
    result = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "s1", "range": "A1:B2"}
    )
    assert "values must be a non-empty 2D array" in result["error"]


def test_sheet_write_non_list_values() -> None:
    result = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "s1", "range": "A1:B2", "values": "not a list"}
    )
    assert "values must be a non-empty 2D array" in result["error"]


def test_calendar_missing_summary() -> None:
    result = feishu_tools._build_calendar_create_tool().handler(
        {"start_time": "1", "end_time": "2"}
    )
    assert "summary" in result["error"]


def test_calendar_missing_start_time() -> None:
    result = feishu_tools._build_calendar_create_tool().handler(
        {"summary": "Test", "end_time": "2"}
    )
    assert "start_time is required" in result["error"]


def test_calendar_missing_end_time() -> None:
    result = feishu_tools._build_calendar_create_tool().handler(
        {"summary": "Test", "start_time": "1"}
    )
    assert "end_time is required" in result["error"]


# ---------------------------------------------------------------------------
# Generic exception handling (not RuntimeError)
# ---------------------------------------------------------------------------


def test_doc_create_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_doc_create_tool().handler({"title": "test"})
    assert result["success"] is False
    assert "unexpected" in result["error"]


def test_doc_read_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_doc_read_tool().handler({"document_id": "doc-1"})
    assert result["success"] is False


def test_doc_append_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_doc_append_tool().handler(
        {"document_id": "doc-1", "content": "line"}
    )
    assert result["success"] is False


def test_wiki_list_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_wiki_list_tool().handler({})
    assert result["success"] is False


def test_wiki_create_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_wiki_create_tool().handler({"space_id": "sp-1", "title": "test"})
    assert result["success"] is False


def test_send_message_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_send_message_tool().handler(
        {"receive_id": "oc_1", "text": "hello"}
    )
    assert result["success"] is False


def test_bitable_query_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_bitable_query_tool().handler(
        {"app_token": "app-1", "table_id": "tbl-1"}
    )
    assert result["success"] is False


def test_bitable_add_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_bitable_add_tool().handler(
        {"app_token": "app-1", "table_id": "tbl-1", "fields": {"a": 1}}
    )
    assert result["success"] is False


def test_sheet_read_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_sheet_read_tool().handler(
        {"spreadsheet_token": "s1", "range": "A1:B2"}
    )
    assert result["success"] is False


def test_sheet_write_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "s1", "range": "A1:B2", "values": [["a"]]}
    )
    assert result["success"] is False


def test_calendar_create_handles_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    result = feishu_tools._build_calendar_create_tool().handler(
        {"summary": "Event", "start_time": "1", "end_time": "2"}
    )
    assert result["success"] is False


# ---------------------------------------------------------------------------
# Query without filter
# ---------------------------------------------------------------------------


def test_bitable_query_without_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        bitable=_ns(
            v1=_ns(
                app_table_record=_ns(search=lambda req: _Resp(data=_ns(has_more=False, items=[])))
            )
        )
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_bitable_query_tool().handler(
        {"app_token": "app-1", "table_id": "tbl-1"}
    )
    assert result["success"] is True
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# Wiki create without meta URL (failure path)
# ---------------------------------------------------------------------------


def test_wiki_create_meta_failure_returns_empty_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        wiki=_ns(
            v2=_ns(
                space_node=_ns(
                    create=lambda req: _Resp(
                        data=_ns(
                            node=_ns(
                                node_token="n1",
                                obj_token="o1",
                                obj_type="docx",
                                title="Title",
                            )
                        )
                    )
                )
            )
        ),
        drive=_ns(
            v1=_ns(
                meta=_ns(batch_query=lambda req: (_ for _ in ()).throw(RuntimeError("meta fail")))
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_wiki_create_tool().handler({"space_id": "sp-1", "title": "test"})
    assert result["success"] is True
    assert result["url"] == ""


# ---------------------------------------------------------------------------
# Doc create without folder_token
# ---------------------------------------------------------------------------


def test_doc_create_without_folder_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        docx=_ns(
            v1=_ns(
                document=_ns(create=lambda req: _Resp(data=_ns(document=_ns(document_id="doc-1"))))
            )
        ),
        drive=_ns(v1=_ns(meta=_ns(batch_query=lambda req: _Resp(data=_ns(metas=[]))))),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_doc_create_tool().handler({"title": "test"})
    assert result["success"] is True
    assert result["url"] == ""


# ---------------------------------------------------------------------------
# Doc append - single line content
# ---------------------------------------------------------------------------


def test_doc_append_single_line(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        docx=_ns(v1=_ns(document_block_children=_ns(create=lambda req: _Resp(data=_ns()))))
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_doc_append_tool().handler(
        {"document_id": "doc-1", "content": "single line"}
    )
    assert result["success"] is True
    assert result["blocks_added"] == 1


# ---------------------------------------------------------------------------
# Sheet read with failed response
# ---------------------------------------------------------------------------


def test_sheet_read_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        request=lambda req: _Resp(ok=False, code=500, msg="server error"),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_sheet_read_tool().handler(
        {"spreadsheet_token": "s1", "range": "A1:B2"}
    )
    assert result["success"] is False
    assert "server error" in result["error"]


# ---------------------------------------------------------------------------
# Calendar create without description
# ---------------------------------------------------------------------------


def test_calendar_create_without_description(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        calendar=_ns(
            v4=_ns(
                calendar_event=_ns(
                    create=lambda req: _Resp(
                        data=_ns(
                            event=_ns(
                                event_id="evt-1",
                                summary="Test",
                                app_link="",
                            )
                        )
                    )
                )
            )
        )
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)
    result = feishu_tools._build_calendar_create_tool().handler(
        {"summary": "Test", "start_time": "1", "end_time": "2"}
    )
    assert result["success"] is True
