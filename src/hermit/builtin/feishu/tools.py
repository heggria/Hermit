"""Feishu agent tools — document, wiki, messaging, bitable, sheets, calendar."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from hermit.builtin.feishu._client import build_lark_client
from hermit.core.tools import ToolSpec
from hermit.plugin.base import PluginContext

_log = logging.getLogger(__name__)


def register_tools(ctx: PluginContext) -> None:
    for tool in _all_tools():
        ctx.add_tool(tool)


def _all_tools() -> list[ToolSpec]:
    return [
        _build_doc_create_tool(),
        _build_doc_read_tool(),
        _build_doc_append_tool(),
        _build_wiki_list_tool(),
        _build_wiki_create_tool(),
        _build_send_message_tool(),
        _build_bitable_query_tool(),
        _build_bitable_add_tool(),
        _build_sheet_read_tool(),
        _build_sheet_write_tool(),
        _build_calendar_create_tool(),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(msg: str, code: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"success": False, "error": msg}
    if code is not None:
        result["code"] = code
        if code in (99991663, 91403, 1061004, 131006):
            result["hint"] = (
                "Permission denied. Enable the required scope for this app at "
                "https://open.feishu.cn → App Management → Permissions & Scopes, "
                "then republish the app."
            )
    return result


def _check_resp(resp: Any) -> dict[str, Any] | None:
    """Return an error dict if resp is not successful, else None."""
    if not resp.success():
        return _err(f"{resp.msg} (log_id={resp.get_log_id()})", resp.code)
    return None


def _readonly_feishu_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Any,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        readonly=True,
        action_class="network_read",
        idempotent=True,
        risk_hint="low",
        requires_receipt=False,
    )


def _mutating_feishu_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Any,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        action_class="credentialed_api_call",
        risk_hint="high",
        requires_receipt=True,
    )


# ---------------------------------------------------------------------------
# 1. feishu_doc_create
# ---------------------------------------------------------------------------


def _build_doc_create_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "Untitled")).strip()
        folder_token = str(payload.get("folder_token", "")).strip()

        try:
            from lark_oapi.api.docx.v1 import (
                CreateDocumentRequest,
                CreateDocumentRequestBody,
            )
            from lark_oapi.api.drive.v1 import (
                BatchQueryMetaRequest,
                MetaRequest,
                RequestDoc,
            )

            client = build_lark_client()
            body = CreateDocumentRequestBody.builder().title(title)
            if folder_token:
                body = body.folder_token(folder_token)
            req = CreateDocumentRequest.builder().request_body(body.build()).build()
            resp = client.docx.v1.document.create(req)
            if err := _check_resp(resp):
                return err
            doc = resp.data.document

            # Fetch the proper web URL via Drive meta API
            url = ""
            try:
                req_doc = RequestDoc.builder().doc_token(doc.document_id).doc_type("docx").build()
                meta_req = (
                    BatchQueryMetaRequest.builder()
                    .request_body(
                        MetaRequest.builder().request_docs([req_doc]).with_url(True).build()
                    )
                    .build()
                )
                meta_resp = client.drive.v1.meta.batch_query(meta_req)
                if meta_resp.success() and meta_resp.data.metas:
                    url = meta_resp.data.metas[0].url or ""
            except Exception:
                _log.debug("Could not fetch doc URL via drive meta, skipping")

            return {
                "success": True,
                "document_id": doc.document_id,
                "title": title,
                "url": url,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_doc_create failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_doc_create",
        description=(
            "Create a new Feishu document (docx). Returns the document_id and URL. "
            "Requires scope: docx:document. "
            "Use this when the user asks to 'create a Feishu doc', 'write a document', or similar."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Document title.",
                },
                "folder_token": {
                    "type": "string",
                    "description": (
                        "Optional. Target folder token (from the URL of a Drive folder). "
                        "Leave empty to create in the bot's root Drive."
                    ),
                },
            },
            "required": ["title"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 2. feishu_doc_read
# ---------------------------------------------------------------------------


def _build_doc_read_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        document_id = str(payload.get("document_id", "")).strip()
        if not document_id:
            return _err("document_id is required")

        try:
            from lark_oapi.api.docx.v1 import RawContentDocumentRequest

            client = build_lark_client()
            req = RawContentDocumentRequest.builder().document_id(document_id).build()
            resp = client.docx.v1.document.raw_content(req)
            if err := _check_resp(resp):
                return err
            return {
                "success": True,
                "document_id": document_id,
                "content": resp.data.content,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_doc_read failed")
            return _err(str(exc))

    return _readonly_feishu_tool(
        name="feishu_doc_read",
        description=(
            "Read the plain-text content of a Feishu document (docx). "
            "Requires scope: docx:document:readonly. "
            "Extract the document_id from the document URL "
            "(e.g. https://xxx.feishu.cn/docx/<document_id>)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Feishu document ID (the token after /docx/ in the URL).",
                },
            },
            "required": ["document_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 3. feishu_doc_append
# ---------------------------------------------------------------------------


def _build_doc_append_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        document_id = str(payload.get("document_id", "")).strip()
        content = str(payload.get("content", "")).strip()
        if not document_id:
            return _err("document_id is required")
        if not content:
            return _err("content is required")

        try:
            from lark_oapi.api.docx.v1 import (
                Block,
                CreateDocumentBlockChildrenRequest,
                CreateDocumentBlockChildrenRequestBody,
                Text,
                TextElement,
                TextRun,
            )

            client = build_lark_client()

            # Build a paragraph block for each line of content
            blocks: list[Any] = []
            for line in content.splitlines():
                text_run = TextRun.builder().content(line or " ").build()
                element = TextElement.builder().text_run(text_run).build()
                text = Text.builder().elements([element]).build()
                block = Block.builder().block_type(2).text(text).build()  # 2 = text paragraph
                blocks.append(block)

            if not blocks:
                return _err("No content to append")

            # Append to the document root block (block_id == document_id for root)
            req = (
                CreateDocumentBlockChildrenRequest.builder()
                .document_id(document_id)
                .block_id(document_id)
                .request_body(
                    CreateDocumentBlockChildrenRequestBody.builder().children(blocks).build()
                )
                .build()
            )
            resp = client.docx.v1.document_block_children.create(req)
            if err := _check_resp(resp):
                return err
            return {
                "success": True,
                "document_id": document_id,
                "blocks_added": len(blocks),
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_doc_append failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_doc_append",
        description=(
            "Append text content to an existing Feishu document as new paragraph blocks. "
            "Each newline becomes a separate paragraph. "
            "Requires scope: docx:document. "
            "Use after feishu_doc_create to populate a document with content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Feishu document ID.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to append. Newlines create separate paragraphs.",
                },
            },
            "required": ["document_id", "content"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 4. feishu_wiki_list
# ---------------------------------------------------------------------------


def _build_wiki_list_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        space_id = str(payload.get("space_id", "")).strip()

        try:
            from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest, ListSpaceRequest

            client = build_lark_client()

            if not space_id:
                # List all wiki spaces
                req = ListSpaceRequest.builder().page_size(50).build()
                resp = client.wiki.v2.space.list(req)
                if err := _check_resp(resp):
                    return err
                spaces: list[dict[str, Any]] = [
                    {
                        "space_id": getattr(s, "space_id", ""),
                        "name": getattr(s, "name", ""),
                        "description": getattr(s, "description", ""),
                    }
                    for s in cast(list[Any], resp.data.items or [])
                ]
                return {
                    "success": True,
                    "type": "spaces",
                    "spaces": spaces,
                    "tip": "Pass space_id to list nodes within a specific space.",
                }
            else:
                # List nodes in the given space
                req = ListSpaceNodeRequest.builder().space_id(space_id).page_size(50).build()
                resp = client.wiki.v2.space_node.list(req)
                if err := _check_resp(resp):
                    return err
                nodes: list[dict[str, Any]] = [
                    {
                        "node_token": getattr(n, "node_token", ""),
                        "obj_token": getattr(n, "obj_token", ""),
                        "obj_type": getattr(n, "obj_type", ""),
                        "title": getattr(n, "title", ""),
                        "parent_node_token": getattr(n, "parent_node_token", ""),
                    }
                    for n in cast(list[Any], resp.data.items or [])
                ]
                return {
                    "success": True,
                    "type": "nodes",
                    "space_id": space_id,
                    "nodes": nodes,
                }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_wiki_list failed")
            return _err(str(exc))

    return _readonly_feishu_tool(
        name="feishu_wiki_list",
        description=(
            "List Feishu Wiki spaces or nodes. "
            "Without space_id: returns all wiki spaces the bot has access to. "
            "With space_id: returns top-level nodes (pages/docs) in that space. "
            "Requires scope: wiki:wiki. "
            "Use this to discover space IDs before creating wiki pages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "space_id": {
                    "type": "string",
                    "description": (
                        "Optional. Wiki space ID to list nodes from. "
                        "Leave empty to list all spaces."
                    ),
                },
            },
            "required": [],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 5. feishu_wiki_create
# ---------------------------------------------------------------------------


def _build_wiki_create_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        space_id = str(payload.get("space_id", "")).strip()
        title = str(payload.get("title", "Untitled")).strip()
        parent_node_token = str(payload.get("parent_node_token", "")).strip()
        obj_type = str(payload.get("obj_type", "docx")).strip()

        if not space_id:
            return _err("space_id is required (use feishu_wiki_list to find it)")

        try:
            from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node

            client = build_lark_client()
            node_builder = Node.builder().title(title).obj_type(obj_type).node_type("origin")
            if parent_node_token:
                node_builder = node_builder.parent_node_token(parent_node_token)

            req = (
                CreateSpaceNodeRequest.builder()
                .space_id(space_id)
                .request_body(node_builder.build())
                .build()
            )
            resp = client.wiki.v2.space_node.create(req)
            if err := _check_resp(resp):
                return err
            node = resp.data.node

            # Fetch proper web URL via Drive meta API
            url = ""
            try:
                from lark_oapi.api.drive.v1 import (
                    BatchQueryMetaRequest,
                    MetaRequest,
                    RequestDoc,
                )

                req_doc = (
                    RequestDoc.builder()
                    .doc_token(node.obj_token)
                    .doc_type(node.obj_type or "docx")
                    .build()
                )
                meta_req = (
                    BatchQueryMetaRequest.builder()
                    .request_body(
                        MetaRequest.builder().request_docs([req_doc]).with_url(True).build()
                    )
                    .build()
                )
                meta_resp = client.drive.v1.meta.batch_query(meta_req)
                if meta_resp.success() and meta_resp.data.metas:
                    url = meta_resp.data.metas[0].url or ""
            except Exception:
                _log.debug("Could not fetch wiki URL via drive meta, skipping")

            return {
                "success": True,
                "node_token": node.node_token,
                "obj_token": node.obj_token,
                "obj_type": node.obj_type,
                "title": node.title,
                "space_id": space_id,
                "url": url,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_wiki_create failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_wiki_create",
        description=(
            "Create a new document node in a Feishu Wiki space. "
            "Requires scope: wiki:wiki. "
            "Use feishu_wiki_list to find space_id and parent_node_token first. "
            "After creating, use feishu_doc_append to add content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "space_id": {
                    "type": "string",
                    "description": "Wiki space ID (from feishu_wiki_list).",
                },
                "title": {
                    "type": "string",
                    "description": "Title of the new document.",
                },
                "parent_node_token": {
                    "type": "string",
                    "description": (
                        "Optional. Parent node token to create this page under. "
                        "Leave empty to create at the space root."
                    ),
                },
                "obj_type": {
                    "type": "string",
                    "description": "Document type: 'docx' (default), 'doc', 'sheet', 'bitable'.",
                    "default": "docx",
                },
            },
            "required": ["space_id", "title"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 6. feishu_send_message
# ---------------------------------------------------------------------------


def _build_send_message_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        receive_id = str(payload.get("receive_id", "")).strip()
        receive_id_type = str(payload.get("receive_id_type", "chat_id")).strip()
        text = str(payload.get("text", "")).strip()

        if not receive_id:
            return _err("receive_id is required")
        if not text:
            return _err("text is required")
        if receive_id_type not in ("chat_id", "open_id", "user_id", "email", "union_id"):
            return _err(f"Invalid receive_id_type: {receive_id_type}")

        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            client = build_lark_client()
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                )
                .build()
            )
            resp = client.im.v1.message.create(req)
            if err := _check_resp(resp):
                return err
            return {
                "success": True,
                "message_id": resp.data.message_id,
                "receive_id": receive_id,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_send_message failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_send_message",
        description=(
            "Send a text message to a Feishu chat or user. "
            "Use receive_id_type='chat_id' for group chats, 'open_id' for individual users. "
            "For Markdown formatting, use the existing reply mechanism instead. "
            "Prefer this over desktop automation for routine Feishu messaging whenever "
            "you know the target receive_id. Useful for proactively notifying teams "
            "about results or summaries."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "receive_id": {
                    "type": "string",
                    "description": "Chat ID (oc_xxx), open_id (ou_xxx), or email of the recipient.",
                },
                "receive_id_type": {
                    "type": "string",
                    "description": "Type of receive_id: 'chat_id' (default), 'open_id', 'user_id', 'email', 'union_id'.",
                    "default": "chat_id",
                },
                "text": {
                    "type": "string",
                    "description": "Plain text message content.",
                },
            },
            "required": ["receive_id", "text"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 7. feishu_bitable_query
# ---------------------------------------------------------------------------


def _build_bitable_query_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        app_token = str(payload.get("app_token", "")).strip()
        table_id = str(payload.get("table_id", "")).strip()
        filter_field = str(payload.get("filter_field", "")).strip()
        filter_value = payload.get("filter_value")
        page_size = min(int(payload.get("page_size", 20)), 100)

        if not app_token:
            return _err("app_token is required (from Bitable URL: /base/<app_token>)")
        if not table_id:
            return _err("table_id is required")

        try:
            from lark_oapi.api.bitable.v1 import (
                Condition,
                FilterInfo,
                SearchAppTableRecordRequest,
                SearchAppTableRecordRequestBody,
            )

            client = build_lark_client()
            body_builder = SearchAppTableRecordRequestBody.builder()

            if filter_field and filter_value is not None:
                condition = (
                    Condition.builder()
                    .field_name(filter_field)
                    .operator("is")
                    .value([str(filter_value)])
                    .build()
                )
                filter_info = (
                    FilterInfo.builder().conjunction("and").conditions([condition]).build()
                )
                body_builder = body_builder.filter(filter_info)

            req = (
                SearchAppTableRecordRequest.builder()
                .app_token(app_token)
                .table_id(table_id)
                .page_size(page_size)
                .request_body(body_builder.build())
                .build()
            )
            resp = client.bitable.v1.app_table_record.search(req)
            if err := _check_resp(resp):
                return err
            items: list[Any] = cast(list[Any], resp.data.items or [])
            records: list[dict[str, Any]] = [
                {"record_id": getattr(r, "record_id", ""), "fields": getattr(r, "fields", {})}
                for r in items
            ]
            return {
                "success": True,
                "app_token": app_token,
                "table_id": table_id,
                "total": len(records),
                "has_more": resp.data.has_more,
                "records": records,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_bitable_query failed")
            return _err(str(exc))

    return _readonly_feishu_tool(
        name="feishu_bitable_query",
        description=(
            "Query records from a Feishu Bitable (multi-dimensional table). "
            "Requires scope: bitable:app. "
            "Extract app_token from the Bitable URL: https://xxx.feishu.cn/base/<app_token>. "
            "Get table_id from the Bitable UI (each table has a unique ID)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "app_token": {
                    "type": "string",
                    "description": "Bitable app token from URL: /base/<app_token>.",
                },
                "table_id": {
                    "type": "string",
                    "description": "Table ID (tbl_xxx) within the Bitable app.",
                },
                "filter_field": {
                    "type": "string",
                    "description": "Optional. Field name to filter by.",
                },
                "filter_value": {
                    "type": "string",
                    "description": "Optional. Value to match for filter_field.",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of records to return (default 20, max 100).",
                    "default": 20,
                },
            },
            "required": ["app_token", "table_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 8. feishu_bitable_add
# ---------------------------------------------------------------------------


def _build_bitable_add_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        app_token = str(payload.get("app_token", "")).strip()
        table_id = str(payload.get("table_id", "")).strip()
        fields = payload.get("fields")

        if not app_token:
            return _err("app_token is required")
        if not table_id:
            return _err("table_id is required")
        if not isinstance(fields, dict) or not fields:
            return _err("fields must be a non-empty object mapping column names to values")

        try:
            from lark_oapi.api.bitable.v1 import (
                AppTableRecord,
                CreateAppTableRecordRequest,
            )

            client = build_lark_client()
            record = AppTableRecord.builder().fields(cast(dict[str, Any], fields)).build()
            req = (
                CreateAppTableRecordRequest.builder()
                .app_token(app_token)
                .table_id(table_id)
                .request_body(record)
                .build()
            )
            resp = client.bitable.v1.app_table_record.create(req)
            if err := _check_resp(resp):
                return err
            return {
                "success": True,
                "record_id": resp.data.record.record_id,
                "app_token": app_token,
                "table_id": table_id,
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_bitable_add failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_bitable_add",
        description=(
            "Add a new record to a Feishu Bitable table. "
            "Requires scope: bitable:app. "
            "fields must map exact column names (as they appear in the table header) to values."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "app_token": {
                    "type": "string",
                    "description": "Bitable app token from URL: /base/<app_token>.",
                },
                "table_id": {
                    "type": "string",
                    "description": "Table ID (tbl_xxx) within the Bitable app.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of column name → value. "
                        'Example: {"任务名": "完成接入", "状态": "进行中", "优先级": "高"}'
                    ),
                },
            },
            "required": ["app_token", "table_id", "fields"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 9. feishu_sheet_read
# ---------------------------------------------------------------------------


def _build_sheet_read_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        spreadsheet_token = str(payload.get("spreadsheet_token", "")).strip()
        range_str = str(payload.get("range", "")).strip()

        if not spreadsheet_token:
            return _err("spreadsheet_token is required (from URL: /sheets/<spreadsheet_token>)")
        if not range_str:
            return _err("range is required (e.g. 'Sheet1!A1:D10' or just 'A1:D10')")

        try:
            import urllib.parse

            from lark_oapi.core.enum import AccessTokenType, HttpMethod
            from lark_oapi.core.model import BaseRequest

            client = build_lark_client()
            encoded_range = urllib.parse.quote(range_str, safe="")
            req = BaseRequest()
            req.http_method = HttpMethod.GET
            req.uri = (
                f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{encoded_range}"
            )
            req.token_types = {AccessTokenType.TENANT}
            resp = client.request(req)
            if not resp.success():
                return _err(f"{resp.msg} (log_id={resp.get_log_id()})", resp.code)

            body: dict[str, Any] = (
                cast(dict[str, Any], json.loads(resp.raw.content)) if resp.raw else {}
            )
            data_section: dict[str, Any] = cast(dict[str, Any], body.get("data") or {})
            value_range: dict[str, Any] = cast(dict[str, Any], data_section.get("valueRange") or {})
            return {
                "success": True,
                "spreadsheet_token": spreadsheet_token,
                "range": value_range.get("range", range_str),
                "values": value_range.get("values", []),
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_sheet_read failed")
            return _err(str(exc))

    return _readonly_feishu_tool(
        name="feishu_sheet_read",
        description=(
            "Read cell values from a Feishu spreadsheet. "
            "Requires scope: sheets:spreadsheet. "
            "Extract spreadsheet_token from URL: https://xxx.feishu.cn/sheets/<spreadsheet_token>. "
            "range format: 'SheetId!A1:C10' or just 'A1:C10' for the first sheet."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "spreadsheet_token": {
                    "type": "string",
                    "description": "Spreadsheet token from URL (the part after /sheets/).",
                },
                "range": {
                    "type": "string",
                    "description": "Cell range, e.g. 'Sheet1!A1:D10' or 'A1:B5'.",
                },
            },
            "required": ["spreadsheet_token", "range"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 10. feishu_sheet_write
# ---------------------------------------------------------------------------


def _build_sheet_write_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        spreadsheet_token = str(payload.get("spreadsheet_token", "")).strip()
        range_str = str(payload.get("range", "")).strip()
        values = payload.get("values")

        if not spreadsheet_token:
            return _err("spreadsheet_token is required")
        if not range_str:
            return _err("range is required")
        if not isinstance(values, list) or not values:
            return _err("values must be a non-empty 2D array of rows")
        values_list: list[Any] = cast(list[Any], values)

        try:
            from lark_oapi.core.enum import AccessTokenType, HttpMethod
            from lark_oapi.core.model import BaseRequest

            client = build_lark_client()
            req = BaseRequest()
            req.http_method = HttpMethod.PUT
            req.uri = f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
            req.token_types = {AccessTokenType.TENANT}
            req.body = {"valueRange": {"range": range_str, "values": values_list}}
            resp = client.request(req)
            if not resp.success():
                return _err(f"{resp.msg} (log_id={resp.get_log_id()})", resp.code)

            return {
                "success": True,
                "spreadsheet_token": spreadsheet_token,
                "range": range_str,
                "rows_written": len(values_list),
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_sheet_write failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_sheet_write",
        description=(
            "Write cell values to a Feishu spreadsheet. "
            "Requires scope: sheets:spreadsheet. "
            "values is a 2D array where each inner array is a row of cells. "
            "Existing data in the range will be overwritten."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "spreadsheet_token": {
                    "type": "string",
                    "description": "Spreadsheet token from URL (the part after /sheets/).",
                },
                "range": {
                    "type": "string",
                    "description": "Cell range to write to, e.g. 'Sheet1!A1:C3'.",
                },
                "values": {
                    "type": "array",
                    "description": (
                        "2D array of values. Example: "
                        '[["Name", "Score"], ["Alice", 95], ["Bob", 87]]'
                    ),
                    "items": {"type": "array"},
                },
            },
            "required": ["spreadsheet_token", "range", "values"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 11. feishu_calendar_create
# ---------------------------------------------------------------------------


def _build_calendar_create_tool() -> ToolSpec:
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        summary = str(payload.get("summary", "")).strip()
        start_time = str(payload.get("start_time", "")).strip()
        end_time = str(payload.get("end_time", "")).strip()
        description = str(payload.get("description", "")).strip()
        timezone = str(payload.get("timezone", "Asia/Shanghai")).strip()

        if not summary:
            return _err("summary (event title) is required")
        if not start_time:
            return _err("start_time is required (Unix timestamp in seconds, e.g. '1770641576')")
        if not end_time:
            return _err("end_time is required (Unix timestamp in seconds)")

        try:
            from lark_oapi.api.calendar.v4 import (
                CalendarEvent,
                CreateCalendarEventRequest,
                TimeInfo,
            )

            client = build_lark_client()
            start = TimeInfo.builder().timestamp(start_time).timezone(timezone).build()
            end = TimeInfo.builder().timestamp(end_time).timezone(timezone).build()

            event_builder = CalendarEvent.builder().summary(summary).start_time(start).end_time(end)
            if description:
                event_builder = event_builder.description(description)

            req = (
                CreateCalendarEventRequest.builder()
                .calendar_id("primary")
                .request_body(event_builder.build())
                .build()
            )
            resp = client.calendar.v4.calendar_event.create(req)
            if err := _check_resp(resp):
                return err
            event = resp.data.event
            return {
                "success": True,
                "event_id": event.event_id,
                "summary": event.summary,
                "start_time": start_time,
                "end_time": end_time,
                "app_link": getattr(event, "app_link", ""),
            }
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.exception("feishu_calendar_create failed")
            return _err(str(exc))

    return _mutating_feishu_tool(
        name="feishu_calendar_create",
        description=(
            "Create a calendar event in the bot's primary Feishu calendar. "
            "Requires scope: calendar:calendar. "
            "start_time and end_time are Unix timestamps in seconds (not milliseconds). "
            "Use Python: int(datetime(...).timestamp()) to convert from datetime."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title.",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time as Unix timestamp in seconds (string), e.g. '1770641576'.",
                },
                "end_time": {
                    "type": "string",
                    "description": "End time as Unix timestamp in seconds (string).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description.",
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone (default: Asia/Shanghai).",
                    "default": "Asia/Shanghai",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
        handler=handler,
    )
