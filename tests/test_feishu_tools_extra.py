from __future__ import annotations

import json
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

import hermit.builtin.feishu.tools as feishu_tools


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


def test_feishu_err_and_check_resp_surface_permission_hints() -> None:
    result = feishu_tools._err("denied", 91403)
    assert result["success"] is False
    assert "Permission denied" in result["hint"]

    resp = _Resp(ok=False, code=99991663, msg="forbidden", log_id="abc")
    checked = feishu_tools._check_resp(resp)
    assert checked == {
        "success": False,
        "error": "forbidden (log_id=abc)",
        "code": 99991663,
        "hint": result["hint"],
    }


def test_feishu_doc_tools_cover_success_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    client = _ns(
        docx=_ns(
            v1=_ns(
                document=_ns(
                    create=lambda req: _Resp(
                        data=_ns(document=_ns(document_id="doc-1"))
                    ),
                    raw_content=lambda req: _Resp(data=_ns(content="Hello Feishu")),
                ),
                document_block_children=_ns(
                    create=lambda req: _Resp(data=_ns())
                ),
            )
        ),
        drive=_ns(
            v1=_ns(
                meta=_ns(
                    batch_query=lambda req: _Resp(
                        data=_ns(metas=[_ns(url="https://feishu.example/doc-1")])
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    doc_create = feishu_tools._build_doc_create_tool().handler({"title": " Plan ", "folder_token": "fld"})
    doc_read = feishu_tools._build_doc_read_tool().handler({"document_id": "doc-1"})
    doc_append = feishu_tools._build_doc_append_tool().handler(
        {"document_id": "doc-1", "content": "line 1\nline 2"}
    )

    assert doc_create == {
        "success": True,
        "document_id": "doc-1",
        "title": "Plan",
        "url": "https://feishu.example/doc-1",
    }
    assert doc_read == {"success": True, "document_id": "doc-1", "content": "Hello Feishu"}
    assert doc_append == {"success": True, "document_id": "doc-1", "blocks_added": 2}
    assert feishu_tools._build_doc_read_tool().handler({}) == {
        "success": False,
        "error": "document_id is required",
    }
    assert feishu_tools._build_doc_append_tool().handler({"document_id": "doc-1", "content": ""}) == {
        "success": False,
        "error": "content is required",
    }


def test_feishu_doc_create_and_read_cover_api_and_meta_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        docx=_ns(
            v1=_ns(
                document=_ns(
                    create=lambda req: _Resp(data=_ns(document=_ns(document_id="doc-2"))),
                    raw_content=lambda req: _Resp(ok=False, code=131006, msg="forbidden"),
                )
            )
        ),
        drive=_ns(v1=_ns(meta=_ns(batch_query=lambda req: (_ for _ in ()).throw(RuntimeError("skip meta"))))),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    created = feishu_tools._build_doc_create_tool().handler({"title": "Doc"})
    read = feishu_tools._build_doc_read_tool().handler({"document_id": "doc-2"})

    assert created["url"] == ""
    assert read["code"] == 131006
    assert "Permission denied" in read["hint"]


def test_feishu_wiki_tools_cover_space_and_node_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        wiki=_ns(
            v2=_ns(
                space=_ns(
                    list=lambda req: _Resp(
                        data=_ns(items=[_ns(space_id="sp-1", name="Knowledge", description="Docs")])
                    )
                ),
                space_node=_ns(
                    list=lambda req: _Resp(
                        data=_ns(
                            items=[
                                _ns(
                                    node_token="node-1",
                                    obj_token="doc-1",
                                    obj_type="docx",
                                    title="Intro",
                                    parent_node_token="",
                                )
                            ]
                        )
                    ),
                    create=lambda req: _Resp(
                        data=_ns(
                            node=_ns(
                                node_token="node-2",
                                obj_token="doc-2",
                                obj_type="docx",
                                title="Roadmap",
                            )
                        )
                    ),
                ),
            )
        ),
        drive=_ns(
            v1=_ns(
                meta=_ns(
                    batch_query=lambda req: _Resp(
                        data=_ns(metas=[_ns(url="https://feishu.example/wiki")])
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    list_spaces = feishu_tools._build_wiki_list_tool().handler({})
    list_nodes = feishu_tools._build_wiki_list_tool().handler({"space_id": "sp-1"})
    create = feishu_tools._build_wiki_create_tool().handler(
        {"space_id": "sp-1", "title": "Roadmap", "parent_node_token": "parent", "obj_type": "docx"}
    )

    assert list_spaces["type"] == "spaces"
    assert list_spaces["spaces"][0]["space_id"] == "sp-1"
    assert list_nodes == {
        "success": True,
        "type": "nodes",
        "space_id": "sp-1",
        "nodes": [
            {
                "node_token": "node-1",
                "obj_token": "doc-1",
                "obj_type": "docx",
                "title": "Intro",
                "parent_node_token": "",
            }
        ],
    }
    assert create["url"] == "https://feishu.example/wiki"
    assert feishu_tools._build_wiki_create_tool().handler({"title": "x"}) == {
        "success": False,
        "error": "space_id is required (use feishu_wiki_list to find it)",
    }


def test_feishu_wiki_tools_cover_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        wiki=_ns(
            v2=_ns(
                space=_ns(list=lambda req: _Resp(ok=False, code=91403, msg="forbidden")),
                space_node=_ns(
                    list=lambda req: _Resp(ok=False, code=91403, msg="forbidden"),
                    create=lambda req: _Resp(ok=False, code=91403, msg="forbidden"),
                ),
            )
        ),
        drive=_ns(v1=_ns(meta=_ns(batch_query=lambda req: _Resp(data=_ns(metas=[]))))),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    assert feishu_tools._build_wiki_list_tool().handler({})["code"] == 91403
    assert feishu_tools._build_wiki_list_tool().handler({"space_id": "sp-1"})["code"] == 91403
    assert feishu_tools._build_wiki_create_tool().handler({"space_id": "sp-1", "title": "Roadmap"})["code"] == 91403


def test_feishu_message_and_bitable_tools_cover_happy_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        im=_ns(
            v1=_ns(
                message=_ns(create=lambda req: _Resp(data=_ns(message_id="msg-1")))
            )
        ),
        bitable=_ns(
            v1=_ns(
                app_table_record=_ns(
                    search=lambda req: _Resp(
                        data=_ns(
                            has_more=False,
                            items=[_ns(record_id="rec-1", fields={"Name": "Beta"})],
                        )
                    ),
                    create=lambda req: _Resp(
                        data=_ns(record=_ns(record_id="rec-2"))
                    ),
                )
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    send = feishu_tools._build_send_message_tool().handler(
        {"receive_id": "oc_1", "receive_id_type": "chat_id", "text": "hello"}
    )
    query = feishu_tools._build_bitable_query_tool().handler(
        {
            "app_token": "app-1",
            "table_id": "tbl-1",
            "filter_field": "Name",
            "filter_value": "Beta",
            "page_size": 999,
        }
    )
    add = feishu_tools._build_bitable_add_tool().handler(
        {"app_token": "app-1", "table_id": "tbl-1", "fields": {"Name": "New"}}
    )

    assert send == {"success": True, "message_id": "msg-1", "receive_id": "oc_1"}
    assert query == {
        "success": True,
        "app_token": "app-1",
        "table_id": "tbl-1",
        "total": 1,
        "has_more": False,
        "records": [{"record_id": "rec-1", "fields": {"Name": "Beta"}}],
    }
    assert add == {
        "success": True,
        "record_id": "rec-2",
        "app_token": "app-1",
        "table_id": "tbl-1",
    }
    assert feishu_tools._build_send_message_tool().handler(
        {"receive_id": "x", "receive_id_type": "bad", "text": "hello"}
    ) == {"success": False, "error": "Invalid receive_id_type: bad"}
    assert feishu_tools._build_bitable_add_tool().handler(
        {"app_token": "app-1", "table_id": "tbl-1", "fields": []}
    ) == {
        "success": False,
        "error": "fields must be a non-empty object mapping column names to values",
    }


def test_feishu_message_and_bitable_tools_cover_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ns(
        im=_ns(v1=_ns(message=_ns(create=lambda req: _Resp(ok=False, code=91403, msg="forbidden")))),
        bitable=_ns(
            v1=_ns(
                app_table_record=_ns(
                    search=lambda req: _Resp(ok=False, code=91403, msg="forbidden"),
                    create=lambda req: _Resp(ok=False, code=91403, msg="forbidden"),
                )
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    assert feishu_tools._build_send_message_tool().handler({"receive_id": "x", "text": "hi"})["code"] == 91403
    assert feishu_tools._build_bitable_query_tool().handler({"app_token": "app", "table_id": "tbl"})["code"] == 91403
    assert feishu_tools._build_bitable_add_tool().handler({"app_token": "app", "table_id": "tbl", "fields": {"A": 1}})["code"] == 91403


def test_feishu_sheet_and_calendar_tools_cover_success_and_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_log: list[Any] = []

    def request(req: Any) -> _Resp:
        request_log.append(req)
        method = req.http_method.name if isinstance(req.http_method, Enum) else str(req.http_method)
        if method == "GET":
            return _Resp(
                raw=_ns(
                    content=json.dumps(
                        {"data": {"valueRange": {"range": "Sheet1!A1:B2", "values": [["A", "B"]]}}}
                    ).encode("utf-8")
                )
            )
        return _Resp(code=91403, msg="forbidden", ok=False)

    client = _ns(
        request=request,
        calendar=_ns(
            v4=_ns(
                calendar_event=_ns(
                    create=lambda req: _Resp(
                        data=_ns(
                            event=_ns(
                                event_id="evt-1",
                                summary="Weekly Sync",
                                app_link="https://feishu.example/event",
                            )
                        )
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    sheet_read = feishu_tools._build_sheet_read_tool().handler(
        {"spreadsheet_token": "sheet-1", "range": "Sheet1!A1:B2"}
    )
    sheet_write = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "sheet-1", "range": "A1:B2", "values": [["A", "B"]]}
    )
    calendar = feishu_tools._build_calendar_create_tool().handler(
        {
            "summary": "Weekly Sync",
            "start_time": "1770641576",
            "end_time": "1770645176",
            "description": "Discuss progress",
            "timezone": "Asia/Shanghai",
        }
    )

    assert sheet_read == {
        "success": True,
        "spreadsheet_token": "sheet-1",
        "range": "Sheet1!A1:B2",
        "values": [["A", "B"]],
    }
    assert "Permission denied" in sheet_write["hint"]
    assert sheet_write["code"] == 91403
    assert calendar == {
        "success": True,
        "event_id": "evt-1",
        "summary": "Weekly Sync",
        "start_time": "1770641576",
        "end_time": "1770645176",
        "app_link": "https://feishu.example/event",
    }
    assert "/values/Sheet1%21A1%3AB2" in request_log[0].uri
    assert feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "sheet-1", "range": "A1", "values": []}
    ) == {"success": False, "error": "values must be a non-empty 2D array of rows"}
    assert feishu_tools._build_calendar_create_tool().handler({"summary": "", "start_time": "", "end_time": ""}) == {
        "success": False,
        "error": "summary (event title) is required",
    }


def test_feishu_sheet_and_calendar_tools_cover_success_variants_and_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_log: list[Any] = []

    def request(req: Any) -> _Resp:
        request_log.append(req)
        method = req.http_method.name if isinstance(req.http_method, Enum) else str(req.http_method)
        if method == "GET":
            return _Resp(raw=None)
        return _Resp(data=_ns())

    client = _ns(
        request=request,
        calendar=_ns(v4=_ns(calendar_event=_ns(create=lambda req: _Resp(ok=False, code=91403, msg="forbidden")))),
    )
    monkeypatch.setattr(feishu_tools, "build_lark_client", lambda: client)

    sheet_read = feishu_tools._build_sheet_read_tool().handler(
        {"spreadsheet_token": "sheet-1", "range": "A1:B2"}
    )
    sheet_write = feishu_tools._build_sheet_write_tool().handler(
        {"spreadsheet_token": "sheet-1", "range": "A1:B2", "values": [["A"], ["B"]]}
    )
    calendar = feishu_tools._build_calendar_create_tool().handler(
        {"summary": "Sync", "start_time": "1", "end_time": "2"}
    )

    assert sheet_read == {
        "success": True,
        "spreadsheet_token": "sheet-1",
        "range": "A1:B2",
        "values": [],
    }
    assert sheet_write == {
        "success": True,
        "spreadsheet_token": "sheet-1",
        "range": "A1:B2",
        "rows_written": 2,
    }
    assert calendar["code"] == 91403
    assert "Permission denied" in calendar["hint"]


def test_feishu_tools_handle_client_factory_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feishu_tools,
        "build_lark_client",
        lambda: (_ for _ in ()).throw(RuntimeError("missing credentials")),
    )

    result = feishu_tools._build_send_message_tool().handler(
        {"receive_id": "oc_1", "receive_id_type": "chat_id", "text": "hello"}
    )

    assert result == {"success": False, "error": "missing credentials"}
