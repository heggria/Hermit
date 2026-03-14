"""DuckDuckGo Lite HTML search + Instant Answer API."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from hermit.core.budgets import get_runtime_budget
from hermit.i18n import resolve_locale, tr

# DDG date-filter codes
_TIME_FILTER_MAP = {
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
}


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def handle_search(payload: dict[str, Any]) -> str:
    query = str(payload.get("query", "")).strip()
    if not query:
        return _t("tools.web.search.error.empty_query")

    max_results = min(int(payload.get("max_results", 8)), 20)
    region = str(payload.get("region", "wt-wt"))
    time_filter = str(payload.get("time_filter", "")).strip().lower()
    search_type = str(payload.get("search_type", "web")).strip().lower()

    parts: list[str] = []

    # Only use Instant Answer for general factual queries (not news/recent events)
    if search_type != "news" and not time_filter:
        instant = _ddg_instant_answer(query)
        if instant:
            parts.append(instant)

    try:
        results = _ddg_lite_search(
            query,
            max_results=max_results,
            region=region,
            time_filter=time_filter,
            search_type=search_type,
        )
        if results:
            parts.append(results)
    except Exception as exc:
        parts.append(_t("tools.web.search.error.search", error=exc))

    if not parts:
        return _t("tools.web.search.no_results", query=query)

    return "\n\n".join(parts)


def _ddg_lite_search(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    time_filter: str = "",
    search_type: str = "web",
) -> str:
    params: dict[str, str] = {"q": query, "kl": region}

    df_code = _TIME_FILTER_MAP.get(time_filter, "")
    if df_code:
        params["df"] = df_code

    if search_type == "news":
        params["ia"] = "news"

    encoded = urllib.parse.urlencode(params)
    url = f"https://lite.duckduckgo.com/lite?{encoded}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=get_runtime_budget().provider_read_timeout) as resp:
        html = resp.read().decode("utf-8")

    parser = _DDGLiteParser()
    parser.feed(html)

    results = parser.results[:max_results]
    if not results:
        return ""

    lines = [_t("tools.web.search.results.title")]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        snippet = r.get("snippet", "")
        href = _extract_real_url(r.get("href", ""))
        lines.append(f"\n### {i}. {title}")
        if snippet:
            lines.append(snippet)
        if href:
            lines.append(f"URL: {href}")

    return "\n".join(lines)


def _extract_real_url(ddg_redirect: str) -> str:
    match = re.search(r"uddg=([^&]+)", ddg_redirect)
    if match:
        return urllib.parse.unquote(match.group(1))
    if ddg_redirect.startswith("http"):
        return ddg_redirect
    return ddg_redirect


class _DDGLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result_link = False
        self._in_snippet = False
        self._current: dict[str, str] = {}
        self._text_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("class") == "result-link":
            self._in_result_link = True
            self._current = {"href": attrs_dict.get("href", "")}
            self._text_buf = []
        elif tag == "td" and attrs_dict.get("class") == "result-snippet":
            self._in_snippet = True
            self._text_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
            self._current["title"] = " ".join(self._text_buf).strip()
        elif tag == "td" and self._in_snippet:
            self._in_snippet = False
            self._current["snippet"] = " ".join(self._text_buf).strip()
            if self._current.get("title"):
                self.results.append(dict(self._current))
            self._current = {}

    def handle_data(self, data: str) -> None:
        if self._in_result_link or self._in_snippet:
            self._text_buf.append(data.strip())


def _ddg_instant_answer(query: str) -> str:
    try:
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "no_html": "1", "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Hermit/0.1"})
        with urllib.request.urlopen(req, timeout=get_runtime_budget().provider_read_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""

    parts: list[str] = []

    if data.get("AbstractText"):
        source = data.get("AbstractSource", "")
        source_url = data.get("AbstractURL", "")
        parts.append(_t("tools.web.search.instant.summary", source=source))
        parts.append(data["AbstractText"])
        if source_url:
            parts.append(_t("tools.web.search.instant.source", url=source_url))

    if data.get("Answer"):
        parts.append(_t("tools.web.search.instant.answer", answer=data["Answer"]))

    if data.get("Definition"):
        parts.append(_t("tools.web.search.instant.definition", definition=data["Definition"]))

    return "\n\n".join(parts) if parts else ""
