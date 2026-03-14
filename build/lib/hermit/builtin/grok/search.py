"""xAI Grok search — uses the Agent Tools API (web_search + x_search)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from hermit.core.budgets import get_runtime_budget
from hermit.i18n import resolve_locale, tr

_XAI_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-4-1-fast-non-reasoning"


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def _get_api_key() -> str:
    return os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or ""


def handle_grok_search(payload: dict[str, Any]) -> str:
    query = str(payload.get("query", "")).strip()
    if not query:
        return _t("tools.grok.search.error.empty_query")

    api_key = _get_api_key()
    if not api_key:
        return _t("tools.grok.search.error.missing_key")

    model = str(payload.get("model", _DEFAULT_MODEL))
    max_tokens = int(payload.get("max_tokens", 2048))
    # search_type: "web" | "x" | "both" (default)
    search_type = str(payload.get("search_type", "both"))

    tools: list[dict[str, Any]] = []
    if search_type in ("web", "both"):
        tools.append({"type": "web_search"})
    if search_type in ("x", "both"):
        tools.append({"type": "x_search"})
    if not tools:
        tools = [{"type": "web_search"}]

    request_body: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": query}],
        "max_output_tokens": max_tokens,
        "tools": tools,
    }

    data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{_XAI_BASE_URL}/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Hermit/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=get_runtime_budget().provider_read_timeout) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429:
            try:
                err_msg = json.loads(body).get("error") or body[:300]
            except Exception:
                err_msg = body[:300]
            return _t("tools.grok.search.error.out_of_credit", error=err_msg)
        if exc.code == 401:
            return _t("tools.grok.search.error.invalid_key")
        return _t("tools.grok.search.error.http", code=exc.code, body=body[:500])
    except Exception as exc:
        return _t("tools.grok.search.error.api", error=exc)

    # /v1/responses returns output as a list of message objects
    content = ""
    output = response_data.get("output") or []
    for item in output:
        if isinstance(item, dict) and item.get("type") == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    content += block.get("text") or ""

    # Append citations
    citations = response_data.get("citations") or []
    if citations and content:
        lines = [_t("tools.grok.search.citations.title")]
        for i, c in enumerate(citations, 1):
            title = c.get("title") or c.get("url") or f"来源 {i}"
            url = c.get("url") or ""
            lines.append(
                _t("tools.grok.search.citations.item", index=i, title=title, url=url)
                if url
                else _t("tools.grok.search.citations.item_text", index=i, title=title)
            )
        content += "\n".join(lines)

    return content or _t("tools.grok.search.empty_response")
